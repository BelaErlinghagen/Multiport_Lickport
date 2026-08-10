"""Tone generation for the headphone-jack speakers. Mirrors
serial_controls.py: a single control class (SpeakerControls) whose one public
method, produce_sound(), renders and plays a tone.

The speakers are quiet, so `volume` is an overdrive factor rather than a
plain 0-1 gain: values above 1.0 push the sine past full scale where it
hard-clips, raising perceived loudness (toward a square wave) at the cost of
a harsher timbre.

Playback goes through ALSA's `aplay` (no extra Python dependency needed) on
shared_states.speaker_device ("default" = the system's current output; set
e.g. "plughw:0,0" to force the headphone jack if the default output is the
HDMI beamer instead).
"""

import subprocess
import threading
import time

import numpy as np

try:
    import shared_states
except Exception:
    shared_states = None

SAMPLE_RATE = 44100


class SpeakerControls:
    """Owns the tone rendering and playback for one speaker device."""

    def __init__(self, sample_rate=SAMPLE_RATE, device=None):
        print("Initializing Speaker Controls.")
        self.sample_rate = int(sample_rate)
        # ALSA device string for aplay; falls back to shared_states, then "default".
        self.device = device or getattr(shared_states, "speaker_device", "default")
        self._procs = []          # live aplay subprocesses (so stop() can end them)
        self._lock = threading.Lock()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, length, frequency, volume):
        """Return mono 16-bit PCM (little-endian) for a sine tone.

        `volume` scales the unit-amplitude sine before it is clipped to full scale,
        so volume > 1 overdrives it (louder, more square) which is how the quiet
        speakers get driven hard. Clipping happens *before* the int16 cast so the
        signal saturates cleanly instead of wrapping around into noise.
        """
        length = max(0.0, float(length))
        n = int(self.sample_rate * length)
        if n <= 0:
            return np.zeros(0, dtype="<i2")
        t = np.arange(n) / self.sample_rate
        wave = np.sin(2.0 * np.pi * float(frequency) * t) * float(volume)
        # 5 ms raised-cosine fades so the tone starts/stops without an audible click.
        fade = min(n // 2, int(self.sample_rate * 0.005))
        if fade > 0:
            ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade)))
            wave[:fade] *= ramp
            wave[-fade:] *= ramp[::-1]
        wave = np.clip(wave, -1.0, 1.0)
        return (wave * 32767.0).astype("<i2")

    # ── Playback ──────────────────────────────────────────────────────────────

    def _play_blocking(self, pcm):
        """Feed rendered PCM to `aplay` and wait for it to finish (runs in a thread)."""
        cmd = ["aplay", "-q", "-t", "raw", "-f", "S16_LE",
               "-c", "1", "-r", str(self.sample_rate), "-D", self.device]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        except FileNotFoundError:
            print("[Speaker] 'aplay' not found — install alsa-utils to play sound.")
            return
        except Exception as exc:
            print(f"[Speaker] could not start aplay: {exc}")
            return

        with self._lock:
            self._procs.append(proc)
        try:
            proc.stdin.write(pcm.tobytes())
            proc.stdin.close()
            proc.wait()
        except Exception as exc:
            print(f"[Speaker] playback error: {exc}")
        finally:
            with self._lock:
                if proc in self._procs:
                    self._procs.remove(proc)

    def produce_sound(self, length, frequency, volume):
        """Play a tone: `length` seconds, `frequency` Hz, `volume` overdrive factor.

        Non-blocking — playback runs in a daemon thread so a caller (e.g. the GUI)
        is never frozen for the tone's duration. The thread is returned so callers
        that want to wait (like the test snippet below) can join it.
        """
        pcm = self._render(length, frequency, volume)
        thread = threading.Thread(target=self._play_blocking, args=(pcm,), daemon=True)
        thread.start()
        return thread

    def stop(self):
        """Stop any tones currently playing."""
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    speaker = SpeakerControls()
    print(f"Playing test tones on device '{speaker.device}'.")
    # (length_s, frequency_Hz, volume)
    tests = [
        (0.5, 440, 1.0),   # clean A4
        (0.5, 880, 1.0),   # clean A5, one octave up
        (0.5, 660, 7.0),   # overdriven (clipped) — noticeably louder / harsher
    ]
    for length, freq, vol in tests:
        print(f"  {freq} Hz for {length}s at volume {vol}")
        speaker.produce_sound(length, freq, vol).join()   # wait so tones don't overlap
        time.sleep(0.2)
    print("Done.")
