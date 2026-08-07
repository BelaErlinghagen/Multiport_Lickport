"""hardware_state.py — Live actuator state, shared across processes.

The session CSV records what the *sensors* saw; this module records what the setup
was *doing* at the same instant — which pumps and LEDs were on, whether a tone was
playing, what the beamer was projecting, which patterns the touch screens showed and
which BNC lines were active.

Why it can be done cheaply: every actuator command already funnels through exactly
one chokepoint, so mirroring the state at four places captures everything.

    pumps / LEDs / BNC pulses   serial_controls.sensor_process, which drains the one
                                command_queue that both the state machine and the
                                Cleaning tab write to
    BNC trains                  state_machine._BncScheduler._reconcile_locked
    beamer                      StateMachine._beamer
    screens / speaker           StateMachine._screen / StateMachine._play_sound

Plain multiprocessing primitives are used rather than a Manager: there is no extra
process that can die and hang every reader. The container is built once in main()
and handed to the child processes through Process(args=...) — it is shared by
inheritance, so it must never be sent through a Queue.

Two separate BNC arrays are deliberate. A train fires short pulses (5 ms at 10 Hz),
which a 20 Hz CSV sampler would alias into a meaningless flicker, so the scheduler
publishes the *span* a train is running in `bnc_train` while sensor_process latches
individual pulses in `bnc_pulse`. Each array has exactly one writer, so they never
fight, and the reader ORs them into one column.

`reward_state` is the odd one out: not an actuator but the state machine's per-port
verdict on each reward, so the CSV can say *why* a port stopped being pumped. A
reward is now a volume delivered over several licks (see pump_calibration.py), so
"the pump fired" no longer implies "the reward was collected" and the two have to be
recorded separately. One digit per port:

    0  not a reward port this trial (or no trial running)
    1  reward available, no contact yet
    2  delivering — probability roll hit, target volume not yet reached
    3  complete   — full volume delivered
    4  partial    — closed out short, by the stall timeout or at trial end
    5  miss       — probability roll failed, nothing delivered
    6  blocked    — a release-delay lick was ignored (transient, reverts to 1)

Codes 3-5 are terminal and persist to the end of the trial, so any row within a trial
reports what happened at every port. It has exactly one writer, StateMachine.
"""

from multiprocessing import Array, Value

# Channel counts, matching the hardware: 16 lickports (pump + LED each), 4 BNC
# connectors, 2 HDMI touch screens.
PUMP_COUNT   = 16
LED_COUNT    = 16
BNC_COUNT    = 4
SCREEN_COUNT = 2

# Touch-screen pattern codes stored in `screens`. -1 means "nothing set yet"; the
# names come from screen_controls.PATTERN_* via normalize_pattern().
SCREEN_PATTERNS = {0: "black", 1: "circles", 2: "zigzag"}

# Per-port reward codes stored in `reward_state` — see the module docstring. Named
# constants because the state machine sets them from four different places and a
# bare 4 in the middle of a trial loop says nothing.
REWARD_NONE       = 0
REWARD_AVAILABLE  = 1
REWARD_DELIVERING = 2
REWARD_COMPLETE   = 3
REWARD_PARTIAL    = 4
REWARD_MISS       = 5
REWARD_BLOCKED    = 6


class HardwareState:
    """Container of shared actuator state. Build one in main(), pass it to children."""

    def __init__(self):
        # Written by serial_controls.sensor_process, parsed out of the command stream.
        self.pumps     = Array('b', PUMP_COUNT)   # MOS: latched for the pulse duration
        self.leds      = Array('b', LED_COUNT)    # LED: plain ON/OFF level
        self.bnc_pulse = Array('b', BNC_COUNT)    # BNC: latched for the pulse duration

        # Written by state_machine._BncScheduler: 1 for the whole span of a train.
        self.bnc_train = Array('b', BNC_COUNT)

        # Written by StateMachine.
        self.speaker       = Value('b', 0)
        self.beamer_on     = Value('b', 0)
        self.beamer_shadow = Value('b', 0)
        self.beamer_xyd    = Array('d', 3)        # x_cm, y_cm, diameter_cm
        self.beamer_seq    = Value('i', 0)        # seqlock guarding the three above
        self.screens_used  = Value('b', 0)        # protocol screens.mode != "none"
        self.screens       = Array('i', SCREEN_COUNT)
        self.reward_state  = Array('b', PUMP_COUNT)  # REWARD_* code per lickport


# ── Beamer seqlock ────────────────────────────────────────────────────────────
#
# The beamer's four fields must be read as one consistent set, but they live in
# separate shared cells. A seqlock is the cheapest fix that needs no lock in the
# reader: the writer bumps a counter before and after its write (making it odd while
# the write is in flight), and the reader retries while the counter is odd or has
# changed under it. Writes happen about once per trial, so a retry is vanishingly
# rare — this just makes a torn read impossible rather than merely unlikely.


def write_beamer(hw, on, shadow=False, x_cm=0.0, y_cm=0.0, diameter_cm=0.0):
    """Publish the current beamer projection (on=False for a blank beamer)."""
    if hw is None:
        return
    hw.beamer_seq.value += 1                       # odd: write in progress
    hw.beamer_on.value     = 1 if on else 0
    hw.beamer_shadow.value = 1 if shadow else 0
    hw.beamer_xyd[0] = float(x_cm)
    hw.beamer_xyd[1] = float(y_cm)
    hw.beamer_xyd[2] = float(diameter_cm)
    hw.beamer_seq.value += 1                       # even: consistent again


def read_beamer(hw, retries=8):
    """Return (on, shadow, x_cm, y_cm, diameter_cm), or None if nothing is projected."""
    if hw is None:
        return None
    for _ in range(retries):
        seq_before = hw.beamer_seq.value
        if seq_before % 2:                         # a write is in flight
            continue
        on     = bool(hw.beamer_on.value)
        shadow = bool(hw.beamer_shadow.value)
        xyd    = (hw.beamer_xyd[0], hw.beamer_xyd[1], hw.beamer_xyd[2])
        if hw.beamer_seq.value == seq_before:
            return (on, shadow) + xyd if on else None
    return None


# ── Formatting helpers ────────────────────────────────────────────────────────


def bits(seq):
    """Pack an iterable of 0/1 into a string like "0010000000000000".

    Chosen over a numpy repr (what the Sensors column used to write) because it is
    fixed-width, unambiguous and parses with list(s) — no whitespace guessing.
    """
    return "".join('1' if v else '0' for v in seq)


def screen_names(hw):
    """Current touch-screen patterns as "black|circles", or None if unused."""
    if hw is None or not hw.screens_used.value:
        return None
    return "|".join(SCREEN_PATTERNS.get(code, "black") for code in hw.screens)


def bnc_bits(hw):
    """BNC activity per connector: a train span OR a latched single pulse."""
    if hw is None:
        return bits([0] * BNC_COUNT)
    return bits(t or p for t, p in zip(hw.bnc_train, hw.bnc_pulse))


def reward_codes(hw):
    """Per-port reward state as "0030100000000000" — one REWARD_* digit per lickport.

    Deliberately not bits(): these are 0-6 codes, not flags, so a reader wants
    `[int(c) for c in s]` and not a truthiness test. Same fixed-width string shape as
    the other 16-item columns, and the same leading-zero hazard when read back.
    """
    if hw is None:
        return "0" * PUMP_COUNT
    return "".join(str(int(v)) for v in hw.reward_state)


def set_reward_state(hw, port, code):
    """Publish one port's REWARD_* code (1-based port, ignored when hw is None)."""
    if hw is None or not (1 <= port <= PUMP_COUNT):
        return
    hw.reward_state[port - 1] = code


def clear_reward_states(hw):
    """Back to REWARD_NONE everywhere — called at the end of every trial."""
    if hw is None:
        return
    for i in range(PUMP_COUNT):
        hw.reward_state[i] = REWARD_NONE


def reset(hw):
    """Zero every actuator field. Called at session start so nothing carries over."""
    if hw is None:
        return
    for i in range(PUMP_COUNT):
        hw.pumps[i] = 0
        hw.reward_state[i] = REWARD_NONE
    for i in range(LED_COUNT):
        hw.leds[i] = 0
    for i in range(BNC_COUNT):
        hw.bnc_pulse[i] = 0
        hw.bnc_train[i] = 0
    for i in range(SCREEN_COUNT):
        hw.screens[i] = 0
    hw.speaker.value      = 0
    hw.screens_used.value = 0
    write_beamer(hw, on=False)
