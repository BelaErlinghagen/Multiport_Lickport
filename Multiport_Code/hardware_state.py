"""hardware_state.py — Live actuator state, shared across processes.

The session CSV (data_saving.py) records what the *sensors* saw; this module
records what the setup was *doing* at the same instant: which pumps/LEDs were
on, what the beamer was projecting, which touch-screen patterns were shown,
which BNC lines were active, and whether a tone was playing. Each actuator is
written by whichever process controls it — serial_controls.sensor_process for
pumps/LEDs/BNC pulses, state_machine.py for BNC trains, the beamer, screens
and speaker — and read by data_saving.py when it builds each CSV row.

Uses plain multiprocessing primitives rather than a Manager, since a Manager
is itself an extra process that could die and hang every reader. Build one
HardwareState in main() and pass it to child processes via Process(args=...)
— it is shared by inheritance, so never send it through a Queue.

`reward_state` is the odd field out: not raw actuator state but the state
machine's per-port verdict on each reward. A reward is now a volume delivered
over several licks (see pump_calibration.py), so "the pump fired" no longer
implies "the reward was collected" — the two are tracked separately. One
digit per port:

    0  not a reward port this trial (or no trial running)
    1  reward available, no contact yet
    2  delivering — probability roll succeeded, target volume not yet reached
    3  complete   — full volume delivered
    4  partial    — delivery stopped early (stall timeout or trial end)
    5  miss       — probability roll failed, nothing delivered
    6  blocked    — a release-delay lick was ignored (reverts to 1)

Set only by StateMachine; codes 3-5 persist to the end of the trial.
"""

from multiprocessing import Array, Value

# Channel counts, matching the hardware: 16 lickports (pump + LED each) split
# across 2 Arduinos, 4 BNC connectors, 2 HDMI touch screens.
PUMP_COUNT   = 16
LED_COUNT    = 16
BNC_COUNT    = 4
SCREEN_COUNT = 2
BOARD_COUNT  = 2

# Touch-screen pattern codes stored in `screens`; names come from
# screen_controls.PATTERN_* via normalize_pattern().
SCREEN_PATTERNS = {0: "black", 1: "circles", 2: "zigzag"}

# Per-port codes stored in `reward_state` — see the table in the module docstring.
REWARD_NONE       = 0
REWARD_AVAILABLE  = 1
REWARD_DELIVERING = 2
REWARD_COMPLETE   = 3
REWARD_PARTIAL    = 4
REWARD_MISS       = 5
REWARD_BLOCKED    = 6

# Beamer projection modes, stored as a code in `beamer_mode`. Defined here
# rather than in beamer_controls.py because the state machine, the saver and
# the GUI all need it, and this module has no PyQt5 dependency to drag in.
#
#     light           black arena; target is a bright sphere
#     shadow          lit arena; target is a black sphere (a hole in the field)
#     lit_background  lit arena for the whole session; target is a brighter
#                     sphere projected on top of that field
BEAMER_LIGHT          = 0
BEAMER_SHADOW         = 1
BEAMER_LIT_BACKGROUND = 2

BEAMER_MODE_NAMES = {BEAMER_LIGHT:          "light",
                     BEAMER_SHADOW:         "shadow",
                     BEAMER_LIT_BACKGROUND: "lit_background"}
BEAMER_MODE_CODES = {v: k for k, v in BEAMER_MODE_NAMES.items()}

# Canonical mode list, for menus and validation.
BEAMER_MODES = tuple(BEAMER_MODE_CODES)

# The two modes where the arena is lit (as opposed to black). Both render
# through the same path — only the target sphere's colour differs — so code
# should branch on membership here, not on one mode name.
BEAMER_LIT_MODES = ("shadow", "lit_background")


class HardwareState:
    """Shared actuator state. Create one in main() and pass it to every child process."""

    def __init__(self):
        # Written by serial_controls.sensor_process.
        self.pumps     = Array('b', PUMP_COUNT)   # latched on for the pulse duration
        self.leds      = Array('b', LED_COUNT)    # plain on/off level
        self.bnc_pulse = Array('b', BNC_COUNT)    # latched on for the pulse duration

        # Written by state_machine._BncScheduler: 1 for the whole span of a train.
        # Kept separate from bnc_pulse so the two writers never race; bnc_bits()
        # below ORs them together into one column for the CSV.
        self.bnc_train = Array('b', BNC_COUNT)

        # Written by StateMachine.
        self.speaker       = Value('b', 0)
        self.beamer_on     = Value('b', 0)
        self.beamer_mode   = Value('b', BEAMER_LIGHT)   # BEAMER_* code
        self.beamer_xyd    = Array('d', 3)        # x_cm, y_cm, diameter_cm
        self.beamer_seq    = Value('i', 0)        # seqlock guarding the 3 fields above
        self.screens_used  = Value('b', 0)        # True if protocol screens.mode != "none"
        self.screens       = Array('i', SCREEN_COUNT)
        self.reward_state  = Array('b', PUMP_COUNT)  # REWARD_* code per lickport
        # Which reward (protocol id 1-16, 0 = none) currently sits on each
        # lickport. Set once the layout is assigned and again on every switch,
        # so the CSV records *which* reward a port was offering, not just that
        # one was — the port alone stops identifying the reward as soon as
        # sporadic switching is enabled.
        self.reward_id     = Array('b', PUMP_COUNT)

        # Per-Arduino liveness, written by serial_controls.sensor_process: 1 while
        # that board is reporting sensor status, 0 once it goes silent or stops
        # accepting commands. Not an actuator, but it rides here because this is
        # the one object every process already shares — the GUI reads it to refuse
        # to start a recording on half a rig.
        self.boards_ok = Array('b', BOARD_COUNT)


# ── Beamer seqlock ────────────────────────────────────────────────────────────
# The beamer's fields live in separate shared cells but must be read as one
# consistent set. write_beamer/read_beamer implement a seqlock: the writer
# bumps a counter before and after writing (odd = write in progress), and the
# reader retries if it catches the counter odd or changing mid-read.


def write_beamer(hw, on, mode="light", x_cm=0.0, y_cm=0.0, diameter_cm=0.0):
    """Publish the current beamer projection (on=False for a blank beamer).

    An unknown mode name is recorded as "light" rather than raising, since a
    logging call must never abort a running session.
    """
    if hw is None:
        return
    hw.beamer_seq.value += 1                       # odd: write in progress
    hw.beamer_on.value   = 1 if on else 0
    hw.beamer_mode.value = BEAMER_MODE_CODES.get(mode, BEAMER_LIGHT)
    hw.beamer_xyd[0] = float(x_cm)
    hw.beamer_xyd[1] = float(y_cm)
    hw.beamer_xyd[2] = float(diameter_cm)
    hw.beamer_seq.value += 1                       # even: consistent again


def read_beamer(hw, retries=8):
    """Return (on, mode, x_cm, y_cm, diameter_cm), or None if nothing is projected.

    mode is returned as its name (e.g. "shadow"), not the raw BEAMER_* code.
    """
    if hw is None:
        return None
    for _ in range(retries):
        seq_before = hw.beamer_seq.value
        if seq_before % 2:                         # a write is in flight
            continue
        on   = bool(hw.beamer_on.value)
        mode = BEAMER_MODE_NAMES.get(hw.beamer_mode.value, "light")
        xyd  = (hw.beamer_xyd[0], hw.beamer_xyd[1], hw.beamer_xyd[2])
        if hw.beamer_seq.value == seq_before:
            return (on, mode) + xyd if on else None
    return None


# ── Formatting helpers ────────────────────────────────────────────────────────


def bits(seq):
    """Pack an iterable of 0/1 into a fixed-width string, e.g. "0010000000000000"."""
    return "".join('1' if v else '0' for v in seq)


def screen_names(hw):
    """Current touch-screen patterns as "black|circles", or None if screens aren't in use."""
    if hw is None or not hw.screens_used.value:
        return None
    return "|".join(SCREEN_PATTERNS.get(code, "black") for code in hw.screens)


def bnc_bits(hw):
    """Per-connector BNC activity, as bits: a running train OR a latched single pulse."""
    if hw is None:
        return bits([0] * BNC_COUNT)
    return bits(t or p for t, p in zip(hw.bnc_train, hw.bnc_pulse))


def reward_codes(hw):
    """Per-port reward state as a 16-digit string, one REWARD_* code per lickport.

    Unlike bits(), these are 0-6 codes, not flags — read with
    `[int(c) for c in s]`, not a truthiness test.
    """
    if hw is None:
        return "0" * PUMP_COUNT
    return "".join(str(int(v)) for v in hw.reward_state)


def reward_ids(hw):
    """Which reward sits on each lickport, as "0|1|0|…|2|0" (0 = no reward).

    Pipe-separated rather than one digit per port like reward_codes(): reward
    ids run to 16, which does not fit in a single character.
    """
    if hw is None:
        return "|".join(["0"] * PUMP_COUNT)
    return "|".join(str(int(v)) for v in hw.reward_id)


def set_reward_layout(hw, locations):
    """Publish the whole {reward_id: port} layout, clearing any previous one."""
    if hw is None:
        return
    for i in range(PUMP_COUNT):
        hw.reward_id[i] = 0
    for rid, port in (locations or {}).items():
        if 1 <= int(port) <= PUMP_COUNT:
            hw.reward_id[int(port) - 1] = int(rid)


def set_reward_state(hw, port, code):
    """Publish one port's REWARD_* code (1-based port number)."""
    if hw is None or not (1 <= port <= PUMP_COUNT):
        return
    hw.reward_state[port - 1] = code


def clear_reward_states(hw):
    """Reset every port to REWARD_NONE. Called at the end of each trial.

    Leaves reward_id alone: the reward still lives on that port between trials,
    it just isn't on offer.
    """
    if hw is None:
        return
    for i in range(PUMP_COUNT):
        hw.reward_state[i] = REWARD_NONE


def reset(hw):
    """Zero every actuator field. Called once at session start."""
    if hw is None:
        return
    for i in range(PUMP_COUNT):
        hw.pumps[i] = 0
        hw.reward_state[i] = REWARD_NONE
        hw.reward_id[i] = 0
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
