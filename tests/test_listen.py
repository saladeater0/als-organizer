#!/usr/bin/env python3
"""
Tests for listen_als.py using synthesized sounds (no sample packs needed).

  python3 -m unittest discover tests
"""

import math
import os
import random
import re
import struct
import sys
import tempfile
import unittest
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import listen_als as la  # noqa: E402

SR = 44100
BEAT = 60.0 / 128          # 128 bpm


# ---------------------------------------------------------------------------
# tiny synth
# ---------------------------------------------------------------------------
def silence(sec):
    return [0.0] * int(sec * SR)


def noise(n, rnd):
    return [rnd.uniform(-1, 1) for _ in range(n)]


def highpass(x, hz):
    a = 1.0 / (1.0 + 2 * math.pi * hz / SR)
    out, prev_x, prev_y = [], 0.0, 0.0
    for v in x:
        prev_y = a * (prev_y + v - prev_x)
        prev_x = v
        out.append(prev_y)
    return out


def lowpass(x, hz):
    a = 2 * math.pi * hz / SR / (1 + 2 * math.pi * hz / SR)
    out, y = [], 0.0
    for v in x:
        y += a * (v - y)
        out.append(y)
    return out


def saw(freq, n, harmonics=8):
    return [sum(math.sin(2 * math.pi * freq * h * i / SR) / h for h in range(1, harmonics + 1))
            for i in range(n)]


def env_decay(x, tau):
    return [v * math.exp(-i / (tau * SR)) for i, v in enumerate(x)]


def mix_at(dst, src, at, gain=1.0):
    s = int(at * SR)
    for i, v in enumerate(src):
        if s + i < len(dst):
            dst[s + i] += gain * v


def normalize(x, peak=0.8):
    m = max(abs(v) for v in x) or 1.0
    return [v * peak / m for v in x]


def midi_hz(n):
    return 440.0 * 2 ** ((n - 69) / 12.0)


def kick():
    n = int(0.5 * SR)
    ph, out = 0.0, []
    for i in range(n):
        f = 45 + 105 * math.exp(-i / (0.03 * SR))
        ph += 2 * math.pi * f / SR
        out.append(math.sin(ph) * math.exp(-i / (0.12 * SR)))
    return out


def hat(rnd, tau):
    n = int(min(1.0, tau * 6) * SR)
    return env_decay(highpass(highpass(noise(n, rnd), 7000), 7000), tau)


def clap(rnd):
    n = int(0.35 * SR)
    x = lowpass(highpass(noise(n, rnd), 900), 2500)
    return env_decay(x, 0.07)


def crash(rnd):
    return env_decay(highpass(highpass(noise(int(2.2 * SR), rnd), 4000), 4000), 0.4)


def chord_tone(notes, n):
    out = [0.0] * n
    for m in notes:
        for i, v in enumerate(saw(midi_hz(m), n, 6)):
            out[i] += v
    return out


def sounds():
    rnd = random.Random(7)
    bars = 4 * 4 * BEAT
    s = {}
    s["kick"] = kick() + silence(0.2)

    loop = silence(bars)
    for k in range(64):
        mix_at(loop, hat(rnd, 0.02), k * BEAT / 4)
    s["closed hats"] = loop

    loop = silence(bars)
    for k in range(16):
        mix_at(loop, hat(rnd, 0.12), k * BEAT + BEAT / 2)
    s["open hats"] = loop

    s["crash"] = crash(rnd)
    s["clap/snare"] = clap(rnd)

    loop = silence(bars)
    for k in range(4):
        mix_at(loop, [math.sin(2 * math.pi * 52 * i / SR) for i in range(int(3.8 * BEAT * SR))],
               k * 4 * BEAT)
    s["sub"] = loop

    loop = silence(bars)
    stab = env_decay(lowpass(chord_tone([62, 65, 69], int(0.3 * SR)), 3000), 0.06)
    for k in range(16):
        mix_at(loop, stab, k * BEAT + BEAT / 2)
    s["stabs"] = loop

    s["pad"] = lowpass(chord_tone([57, 60, 64], int(7 * SR)), 2500)
    s["drone"] = lowpass(saw(midi_hz(45), int(7 * SR), 10), 1500)

    loop = silence(bars)
    seq = [69, 72, 76, 81]
    for k in range(64):
        mix_at(loop, env_decay(saw(midi_hz(seq[k % 4]), int(0.1 * SR), 6), 0.04), k * BEAT / 4)
    s["arp"] = loop

    n = int(6 * SR)
    nz = highpass(noise(n, rnd), 1500)
    s["riser"] = [v * (i / n) ** 2 for i, v in enumerate(nz)]
    s["reverse cymbal"] = list(reversed(crash(rnd)))

    loop = silence(bars)
    for k in range(16):
        mix_at(loop, kick(), k * BEAT)
        mix_at(loop, hat(rnd, 0.05), k * BEAT + BEAT / 2, 0.5)
        if k % 2:
            mix_at(loop, clap(rnd), k * BEAT, 0.6)
    s["drum loop"] = loop

    loop = silence(bars)
    for k in range(16):
        mix_at(loop, kick(), k * BEAT)
    s["kick#loop"] = loop

    loop = silence(bars)
    line = [33, 33, 36, 31]
    for k in range(32):
        note = env_decay(lowpass(saw(midi_hz(line[(k // 8) % 4]), int(0.2 * SR), 8), 600), 0.08)
        mix_at(loop, note, k * BEAT / 2 + BEAT / 4)
    s["bass"] = loop

    s["noise"] = lowpass(highpass(noise(int(6 * SR), rnd), 200), 6000)
    return dict((k, normalize(v)) for k, v in s.items())


# ---------------------------------------------------------------------------
# writers in several formats
# ---------------------------------------------------------------------------
def write_wav16(path, x, stereo=True):
    with wave.open(path, "wb") as w:
        w.setnchannels(2 if stereo else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        for v in x:
            s = struct.pack("<h", int(max(-1, min(1, v)) * 32767))
            frames += s * (2 if stereo else 1)
        w.writeframes(bytes(frames))


def write_wav24(path, x):
    data = bytearray()
    for v in x:
        s = int(max(-1, min(1, v)) * 8388607)
        b = struct.pack("<i", s)[:3]
        data += b + b
    fmt = struct.pack("<HHIIHH", 1, 2, SR, SR * 6, 6, 24)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        f.write(b"data" + struct.pack("<I", len(data)) + data)


def write_wav_float(path, x):
    data = b"".join(struct.pack("<ff", v, v) for v in x)
    fmt = struct.pack("<HHIIHH", 3, 2, SR, SR * 8, 8, 32)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        f.write(b"data" + struct.pack("<I", len(data)) + data)


def _extended(rate):
    exp = 16383 + 63
    mant = int(rate)
    while mant < (1 << 63):
        mant <<= 1
        exp -= 1
    return struct.pack(">HQ", exp, mant)


def write_aiff24(path, x):
    data = bytearray()
    for v in x:
        b = struct.pack(">i", int(max(-1, min(1, v)) * 8388607))[1:]
        data += b + b
    comm = struct.pack(">hIh", 2, len(x), 24) + _extended(SR)
    ssnd = struct.pack(">II", 0, 0) + bytes(data)
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + \
        b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    with open(path, "wb") as f:
        f.write(b"FORM" + struct.pack(">I", len(body)) + body)


WRITERS = [write_wav16, write_wav24, write_wav_float, write_aiff24]


class ListenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="listen_test_")
        cls.files = {}
        for n, (label, x) in enumerate(sorted(sounds().items())):
            writer = WRITERS[n % len(WRITERS)]
            ext = ".aif" if writer is write_aiff24 else ".wav"
            path = os.path.join(cls.dir, re.sub(r"[^a-z]+", "_", label) + ext)
            writer(path, x)
            cls.files[label] = path

    def test_labels(self):
        wrong = []
        for label, path in sorted(self.files.items()):
            v, feats, err = la.judge_file(path)
            self.assertIsNone(err, "%s: %s" % (label, err))
            want = label.split("#")[0]
            if v is None or v["label"] != want:
                wrong.append("%s -> %s (%s)" % (label, v and v["label"], v and "; ".join(v["why"])))
        self.assertEqual(wrong, [], "\n" + "\n".join(wrong))

    def test_formats_agree(self):
        x = sounds()["open hats"]
        labels = []
        for w in WRITERS:
            p = os.path.join(self.dir, "fmt_%s%s" % (w.__name__, ".aif" if w is write_aiff24 else ".wav"))
            w(p, x)
            labels.append(la.judge_file(p)[0]["label"])
        self.assertEqual(set(labels), {"open hats"})

    def test_midi(self):
        chords = [[(b, 0.25, k) for b in range(0, 16, 1) for k in (62, 65, 69)]]
        self.assertEqual(la.judge_midi(chords, 16)["label"], "stabs")
        bass = [[(b * 0.5, 0.4, 36) for b in range(32)]]
        self.assertEqual(la.judge_midi(bass, 16)["label"], "bass")
        drone = [[(0, 16, 57)]]
        self.assertEqual(la.judge_midi(drone, 16)["label"], "drone")
        arp = [[(b * 0.25, 0.2, 60 + (b % 4) * 4) for b in range(64)]]
        self.assertEqual(la.judge_midi(arp, 16)["label"], "arp")
        self.assertEqual(la.judge_midi([], 0, drum_rack=True)["label"], "drum kit")

    def test_long_loop(self):
        # a long loop must not have its analysis frames lock onto the beat (all kick, no hats)
        p = os.path.join(self.dir, "long_drum_loop.wav")
        write_wav16(p, sounds()["drum loop"] * 12, stereo=False)
        self.assertEqual(la.judge_file(p)[0]["label"], "drum loop")

    def test_bad_file(self):
        p = os.path.join(self.dir, "junk.wav")
        with open(p, "wb") as f:
            f.write(b"not audio at all")
        v, feats, err = la.judge_file(p)
        self.assertIsNone(v)
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
