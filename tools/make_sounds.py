"""
Generates the reminder sounds in sounds/ (plain 16-bit WAV, standard library only):
    python tools/make_sounds.py

    arrive.wav          two-note chime when Buddy stops and asks
    buddy_in.wav        soft footsteps while Buddy walks in
    minnal_in.wav       lightning whoosh and zap as Minnal runs in, then the chime
"""

import math
import os
import random
import struct
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import task_reminder as app

RATE = 22050
rng = random.Random(7)      # same noise every run: the files don't change unless this script does


def buffer(seconds):
    return [0.0] * int(seconds * RATE)


def add(buf, start, samples, gain=1.0):
    i0 = int(start * RATE)
    for i, s in enumerate(samples):
        if i0 + i < len(buf):
            buf[i0 + i] += s * gain


def chime(seconds=0.9):
    """E6 then C6, bell-like: a few harmonics with a fast decay."""
    out = [0.0] * int((0.16 + seconds) * RATE)
    for freq, at in ((1318.5, 0.0), (1046.5, 0.16)):
        note = []
        for i in range(int(seconds * RATE)):
            t = i / RATE
            env = min(1.0, t / 0.005) * math.exp(-t * 5)
            note.append(env * (math.sin(2 * math.pi * freq * t)
                               + 0.3 * math.sin(2 * math.pi * 2 * freq * t)
                               + 0.08 * math.sin(2 * math.pi * 3 * freq * t)))
        add(out, at, note)
    return out


def footstep(pitch):
    """A low thump with a little scuff of filtered noise."""
    out, low = [], 0.0
    for i in range(int(0.14 * RATE)):
        t = i / RATE
        low += 0.15 * (rng.uniform(-1, 1) - low)
        out.append(math.sin(2 * math.pi * pitch * t) * math.exp(-t * 32)
                   + 0.5 * low * math.exp(-t * 60))
    return out


def whoosh(seconds=0.5):
    """Noise through a low-pass filter that opens and closes: air rushing past."""
    out, low = [], 0.0
    n = int(seconds * RATE)
    for i in range(n):
        x = i / n
        swell = math.sin(math.pi * x)
        low += (0.02 + 0.5 * swell ** 2) * (rng.uniform(-1, 1) - low)
        out.append(low * swell)
    return out


def zap(seconds=0.22):
    """A falling, crackling buzz: the lightning."""
    out, phase = [], 0.0
    n = int(seconds * RATE)
    for i in range(n):
        x = i / n
        freq = 1600 * (1 - x) + 120
        phase += freq / RATE
        saw = 2 * (phase % 1) - 1
        crackle = 1.0 if rng.random() > 0.25 else 0.2
        out.append(saw * crackle * (1 - x) ** 1.5)
    return out


def save(name, buf, peak):
    top = max(abs(s) for s in buf) or 1.0
    frames = b"".join(struct.pack("<h", int(s / top * peak * 32767)) for s in buf)
    with wave.open(os.path.join(app.SOUND_DIR, name + ".wav"), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(frames)
    print("wrote", name)


os.makedirs(app.SOUND_DIR, exist_ok=True)

save("arrive", chime(), 0.55)

# Buddy walks in from the screen edge: one step per half stride for the whole walk, plus a
# little extra (the chime on arrival cuts it off).
walk = (app.POPUP_W + 20) / app.WALK_SPEED * app.FRAME_MS / 1000 + 0.6
steps = buffer(walk)
for n in range(int(walk / 0.42)):
    add(steps, n * 0.42, footstep(85 if n % 2 else 70))
save("buddy_in", steps, 0.4)

# Minnal is on screen in a blink, so his one sound carries the chime too.
bolt = buffer(1.6)
add(bolt, 0.0, whoosh(), 0.8)
add(bolt, 0.3, zap(), 0.5)
add(bolt, 0.6, chime(), 0.7)
save("minnal_in", bolt, 0.55)
