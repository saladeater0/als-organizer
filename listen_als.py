#!/usr/bin/env python3
"""
listen_als.py — listen to the audio in an Ableton Live Set (.als) and say what each track is.

For every track it finds the samples the set uses (audio clips, and the samples inside Simpler /
Sampler), opens the actual .wav / .aif files and measures how they sound: where the energy sits
(sub, bass, mids, highs), how noisy or tonal it is, how many notes ring at once, how often it hits,
how fast the hits die away, and whether it swells up like a riser. From that it labels the track:

    DRUMS   kick, closed hats, open hats, crash, clap/snare, perc, drum loop
    BASS    sub, bass
    CHORDS  chords, stabs
    SYNTH   lead, arp
    ATMOS   pad, drone, noise
    FX      riser, reverse cymbal

Plugin synths (Diva, Serum, Pigments ...) make their sound inside Live, so there's no file to
listen to. For those it reads the MIDI notes instead (how low, how many at once, how long, how
busy) and says so: those verdicts are marked "notes" and count for less.

On its own it only reports — nothing is saved:
  python3 listen_als.py "My Track.als"                 # what every track sounds like
  python3 listen_als.py "My Track.als" -v              # ... and the measurements behind it
  python3 listen_als.py kick.wav "pad loop.aif"        # single sample files
  python3 listen_als.py "My Track.als" --samples ~/Splice   # also look here for moved samples

Use it while organizing:
  python3 organize_als.py "My Track.als" --listen

Standard library only (Python 3.8+). Reads WAV (16/24/32-bit, float) and AIFF/AIFC.
"""

import argparse
import array
import cmath
import gzip
import math
import os
import random
import re
import struct
import sys
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# YOUR RULES — edit freely.
# ---------------------------------------------------------------------------
# label -> role. Labels are what auto-named tracks get renamed to.
LABEL_ROLE = {
    "kick": "DRUMS", "closed hats": "DRUMS", "open hats": "DRUMS", "hats": "DRUMS",
    "crash": "DRUMS", "clap/snare": "DRUMS", "perc": "DRUMS", "drum loop": "DRUMS",
    "drum kit": "DRUMS",
    "sub": "BASS", "bass": "BASS",
    "chords": "CHORDS", "stabs": "CHORDS",
    "lead": "SYNTH", "arp": "SYNTH",
    "pad": "ATMOS", "drone": "ATMOS", "noise": "ATMOS",
    "riser": "FX", "reverse cymbal": "FX",
}

# Frequency bands (Hz) the energy is split into.
BANDS = [("sub", 20, 70), ("bass", 70, 250), ("lowmid", 250, 1000), ("mid", 1000, 4000),
         ("high", 4000, 20000)]

# Thresholds. Band shares are fractions of the total energy (0-1).
ONE_SHOT_SECONDS = 2.0      # a sound shorter than this with 1-2 hits is a one-shot
PERCUSSIVE_RANGE_DB = 12    # loud-vs-quiet spread above which a loop counts as hits, not a held sound
NOISY_FLATNESS = 0.30       # spectral flatness above this = noise-like (hats, claps, white noise)
OPEN_HAT_DECAY_MS = 110     # hats ringing longer than this are open hats
CRASH_DECAY_MS = 450        # a high one-shot ringing longer than this is a crash / cymbal
RISER_DB = 8                # end this much louder than the start (and peaking late) = riser
REVERSE_MAX_SECONDS = 4     # a noisy swell shorter than this is a reversed cymbal, longer = riser
KICK_GLIDE = 1.25           # low hits whose pitch falls by more than this ratio are kicks, else bass
TOPS_RATIO = 0.25           # highs hitting between the kicks this often (per kick) = full drum loop
CHORD_NOTES = 2.5           # this many separate notes at once (median) = chords / pad
MIDI_BASS_KEY = 60          # MIDI: a single line mostly below this (C3, middle C) = bass
MIDI_ARP_RATE = 2.0         # MIDI: more than this many notes per beat = arp

MAX_SECONDS = 90            # only the first 90 s of long recordings are analysed
TARGET_RATE = 22050         # audio is averaged down to about this rate before analysis
HOP = 256                   # envelope resolution in samples (~12 ms)
FFT_SIZE = 2048
MAX_FRAMES = 48             # spectra averaged per file

AUDIO_EXT = (".wav", ".wave", ".aif", ".aiff", ".aifc")
SAMPLE_DEVICES = ("OriginalSimpler", "MultiSampler", "DrumGroupDevice", "InstrumentImpulse")


# ---------------------------------------------------------------------------
# reading audio (no third-party libraries)
# ---------------------------------------------------------------------------
class AudioError(Exception):
    pass


def _ieee_extended(b):
    """80-bit IEEE 754 extended float (AIFF sample rate) -> float."""
    exp, mant = struct.unpack(">HQ", b)
    sign = -1 if exp & 0x8000 else 1
    exp &= 0x7FFF
    if exp == 0 and mant == 0:
        return 0.0
    return sign * mant * 2.0 ** (exp - 16383 - 63)


def _decode(raw, width, big_endian, is_float, signed8, channels, rate):
    """Interleaved PCM bytes -> mono floats in -1..1, averaged down towards TARGET_RATE."""
    host_big = sys.byteorder == "big"
    if is_float:
        a = array.array("f" if width == 4 else "d")
        raw = raw[:len(raw) - len(raw) % a.itemsize]
        a.frombytes(raw)
        if big_endian != host_big:
            a.byteswap()
        scale = 1.0
    elif width == 1:
        a = array.array("b" if signed8 else "B")
        a.frombytes(raw)
        scale = 1 / 128.0
        if not signed8:
            a = array.array("i", (x - 128 for x in a))
    elif width == 2:
        a = array.array("h")
        raw = raw[:len(raw) - len(raw) % 2]
        a.frombytes(raw)
        if big_endian != host_big:
            a.byteswap()
        scale = 1 / 32768.0
    elif width in (3, 4):
        n = len(raw) // width
        if width == 3:              # widen to 32-bit, sample in the top three bytes
            wide = bytearray(n * 4)
            if big_endian:
                wide[0::4], wide[1::4], wide[2::4] = raw[0:n * 3:3], raw[1:n * 3:3], raw[2:n * 3:3]
            else:
                wide[1::4], wide[2::4], wide[3::4] = raw[0:n * 3:3], raw[1:n * 3:3], raw[2:n * 3:3]
            raw = bytes(wide)
        a = array.array("i")
        if a.itemsize != 4:
            a = array.array("l")
        a.frombytes(raw[:n * 4])
        if big_endian != host_big:
            a.byteswap()
        scale = 1 / 2147483648.0
    else:
        raise AudioError("unsupported sample width %d bytes" % width)

    step = max(1, int(round(rate / float(TARGET_RATE))))
    block = channels * step
    usable = len(a) - len(a) % block
    if usable <= 0:
        return [], rate / float(step)
    parts = [a[k:usable:block] for k in range(block)]
    k = scale / block
    mono = [sum(v) * k for v in zip(*parts)]
    return mono, rate / float(step)


def read_audio(path, max_seconds=MAX_SECONDS):
    """Returns (mono samples, sample rate, full length in seconds)."""
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12:
            raise AudioError("file too short")
        if head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE":
            return _read_wav(f, max_seconds)
        if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
            return _read_aiff(f, head[8:12] == b"AIFC", max_seconds)
    raise AudioError("not a WAV or AIFF file")


def _read_wav(f, max_seconds):
    fmt = None
    while True:
        h = f.read(8)
        if len(h) < 8:
            raise AudioError("no audio data found")
        cid, size = h[:4], struct.unpack("<I", h[4:])[0]
        if cid == b"fmt ":
            body = f.read(size)
            tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:          # WAVE_FORMAT_EXTENSIBLE
                tag = struct.unpack("<H", body[24:26])[0]
            if tag not in (1, 3):
                raise AudioError("compressed WAV (format %d) not supported" % tag)
            fmt = (tag == 3, ch, rate, bits)
        elif cid == b"data":
            if fmt is None:
                raise AudioError("data before format")
            is_float, ch, rate, bits = fmt
            width = (bits + 7) // 8
            if size in (0, 0xFFFFFFFF):                    # streaming / RF64
                size = 1 << 62
            frames = size // (width * ch)
            take = min(frames, int(max_seconds * rate)) * width * ch
            mono, sr = _decode(f.read(take), width, False, is_float, False, ch, rate)
            return mono, sr, frames / float(rate)
        else:
            f.seek(size + (size & 1), 1)


def _read_aiff(f, aifc, max_seconds):
    comm = None
    while True:
        h = f.read(8)
        if len(h) < 8:
            raise AudioError("no audio data found")
        cid, size = h[:4], struct.unpack(">I", h[4:])[0]
        if cid == b"COMM":
            body = f.read(size + (size & 1))
            ch, frames, bits = struct.unpack(">hIh", body[:8])
            rate = _ieee_extended(body[8:18])
            comp = body[18:22] if aifc and len(body) >= 22 else b"NONE"
            comm = (ch, frames, bits, rate, comp)
        elif cid == b"SSND":
            if comm is None:
                raise AudioError("data before format")
            ch, frames, bits, rate, comp = comm
            offset = struct.unpack(">II", f.read(8))[0]
            f.seek(offset, 1)
            if comp in (b"NONE", b"twos"):
                big, is_float = True, False
            elif comp == b"sowt":
                big, is_float = False, False
            elif comp in (b"fl32", b"FL32"):
                big, is_float, bits = True, True, 32
            elif comp in (b"fl64", b"FL64"):
                big, is_float, bits = True, True, 64
            else:
                raise AudioError("compressed AIFF (%s) not supported" % comp.decode("latin-1"))
            width = (bits + 7) // 8
            take = min(frames, int(max_seconds * rate)) * width * ch
            mono, sr = _decode(f.read(take), width, big, is_float, True, ch, rate)
            return mono, sr, frames / float(rate)
        else:
            f.seek(size + (size & 1), 1)


# ---------------------------------------------------------------------------
# measuring
# ---------------------------------------------------------------------------
_TWIDDLE = {}
_WINDOW = {}


def _fft(x):
    n = len(x)
    a = list(x)
    j = 0
    for i in range(1, n):                       # bit-reversal permutation
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    tw = _TWIDDLE.get(n)
    if tw is None:
        tw = _TWIDDLE[n] = [cmath.exp(-2j * math.pi * k / n) for k in range(n // 2)]
    size = 2
    while size <= n:
        half, step = size // 2, n // size
        w = tw[::step][:half]
        for start in range(0, n, size):
            for k in range(half):
                u = a[start + k]
                v = a[start + k + half] * w[k]
                a[start + k] = u + v
                a[start + k + half] = u - v
        size *= 2
    return a


def _db(x):
    return 10 * math.log10(x) if x > 1e-20 else -200.0


def _median(xs):
    s = sorted(xs)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


def _percentile(xs, q):
    s = sorted(xs)
    if not s:
        return 0.0
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def _count_notes(power, bin_hz):
    """How many separate pitches are sounding: strong peaks that aren't harmonics of a lower one."""
    n = len(power)
    lo, hi = max(2, int(50 / bin_hz)), min(n - 2, int(5000 / bin_hz))
    if hi <= lo:
        return 0
    db = [_db(p) for p in power]
    top = max(db[lo:hi])
    peaks = []
    for k in range(lo, hi):
        if db[k] > db[k - 1] and db[k] >= db[k + 1] and db[k] > top - 30:
            local = _median(db[max(0, k - 15):k + 16])
            if db[k] - local > 10:
                a, b, c = db[k - 1], db[k], db[k + 1]       # parabolic peak interpolation
                d = a - 2 * b + c
                off = 0.5 * (a - c) / d if d else 0.0
                peaks.append(((k + off) * bin_hz, db[k]))
    peaks = sorted(peaks, key=lambda p: -p[1])[:15]
    roots = []
    for f, _ in sorted(peaks):
        tol = max(0.03 * f, 1.5 * bin_hz)
        if not any(round(f / r) >= 2 and abs(f - round(f / r) * r) <= tol for r in roots):
            roots.append(f)
    return len(roots)


def analyze(path):
    """Measure one audio file. Returns a dict of features, or None if it's silent."""
    x, sr, length = read_audio(path)
    if len(x) < FFT_SIZE:
        x = x + [0.0] * (FFT_SIZE - len(x))

    # loudness envelope
    env = []
    for i in range(0, len(x) - HOP + 1, HOP):
        blk = x[i:i + HOP]
        env.append(_db(sum(v * v for v in blk) / HOP))
    peak = max(env) if env else -200
    if peak < -70:
        return None
    env = [max(e, peak - 80) for e in env]
    floor = peak - 40
    active = [i for i, e in enumerate(env) if e > floor]
    first, last = active[0], active[-1]
    act = env[first:last + 1]
    hop_s = HOP / sr
    active_s = (last - first + 1) * hop_s

    # hits: sudden jumps in loudness
    onsets, gap = [], int(0.08 / hop_s) + 1
    for i in range(first, last + 1):
        if env[i] < peak - 35:
            continue
        prev = min(env[max(first, i - 3):i] or [env[i]])
        if env[i] - prev > 6 and (not onsets or i - onsets[-1] >= gap):
            onsets.append(i)
    if not onsets:
        onsets = [first]

    # hits in the highs (2nd difference ~ +12 dB/oct) that land between the main hits
    hi_env = []
    for i in range(0, len(x) - HOP + 1, HOP):
        blk = x[max(2, i):i + HOP]
        hi_env.append(_db(sum((blk[k] - 2 * blk[k - 1] + blk[k - 2]) ** 2
                              for k in range(2, len(blk))) / HOP))
    hi_peak = max(hi_env) if hi_env else -200
    hi_on = []
    for i in range(first, min(last + 1, len(hi_env))):
        if hi_env[i] < hi_peak - 30:
            continue
        prev = min(hi_env[max(first, i - 3):i] or [hi_env[i]])
        if hi_env[i] - prev > 6 and (not hi_on or i - hi_on[-1] >= gap):
            hi_on.append(i)
    near = int(0.04 / hop_s) + 1
    off_beat_hi = [h for h in hi_on if all(abs(h - o) > near for o in onsets)]

    # pitch drop inside each hit: kicks fall in pitch, bass notes hold theirs
    glides = []
    for o in onsets[:16]:
        a = o * HOP
        seg = x[max(0, a - int(0.005 * sr)):a + int(0.14 * sr)]
        if len(seg) < int(0.1 * sr):
            continue
        y, y2, c = 0.0, 0.0, 2 * math.pi * 300 / sr / (1 + 2 * math.pi * 300 / sr)
        lp = []
        for v in seg:                               # two one-pole low-passes at 300 Hz
            y += c * (v - y)
            y2 += c * (y - y2)
            lp.append(y2)
        cross = [k for k in range(1, len(lp)) if (lp[k - 1] < 0) != (lp[k] < 0)]

        def period(t0, t1):
            ks = [k for k in cross if t0 * sr <= k < t1 * sr]
            return (ks[-1] - ks[0]) / float(len(ks) - 1) if len(ks) >= 3 else None
        early, late = period(0.0, 0.035), period(0.05, 0.14)
        if early and late:
            glides.append(late / early)

    # how fast each hit dies away (to 12 dB under its peak, or the next hit)
    decays = []
    for n, o in enumerate(onsets):
        end = onsets[n + 1] if n + 1 < len(onsets) else last + 1
        seg = env[o:end]
        if not seg:
            continue
        top = max(range(len(seg)), key=lambda k: seg[k])
        t = top
        while t < len(seg) and seg[t] > seg[top] - 12:
            t += 1
        decays.append((t - top) * hop_s * 1000)
    decay_ms = _median(decays)

    # swelling up (risers, reversed cymbals)
    third = max(1, len(act) // 3)
    lin = [10 ** (e / 10) for e in act]
    start_db = _db(sum(lin[:third]) / third)
    end_db = _db(sum(lin[-third:]) / third)
    peak_pos = max(range(len(act)), key=lambda k: act[k]) / float(max(1, len(act) - 1))

    # spectra
    n2 = FFT_SIZE // 2
    bin_hz = sr / FFT_SIZE
    win = _WINDOW.get(FFT_SIZE)
    if win is None:
        win = _WINDOW[FFT_SIZE] = [0.5 - 0.5 * math.cos(2 * math.pi * i / (FFT_SIZE - 1))
                                   for i in range(FFT_SIZE)]
    loud = [i for i in range(first, last + 1) if env[i] > peak - 30]
    starts = sorted(set(min(len(x) - FFT_SIZE, i * HOP) for i in loud))
    if len(starts) > MAX_FRAMES:      # random spread (fixed seed) so frames can't lock to the beat
        starts = sorted(random.Random(len(x)).sample(starts, MAX_FRAMES))
    avg = [0.0] * n2
    flat, notes, cents = [], [], []
    flat_bands = [(max(1, int(lo / bin_hz)), min(n2, int(hi / bin_hz)))
                  for lo, hi in ((200, 1000), (1000, 4000), (4000, min(12000, 0.45 * sr)))]
    for s in starts:
        spec = _fft([x[s + i] * win[i] for i in range(FFT_SIZE)])
        p = [abs(c) ** 2 for c in spec[:n2]]
        tot = sum(p) or 1e-20
        for k in range(n2):
            avg[k] += p[k] / tot
        fw, fs = 0.0, 0.0
        for lo, hi in flat_bands:
            band = [v + 1e-20 for v in p[lo:hi]]
            if len(band) < 4:
                continue
            e = sum(band)
            fs += e * math.exp(sum(math.log(v) for v in band) / len(band)) / (e / len(band))
            fw += e
        flat.append(fs / fw if fw else 0.0)
        cents.append(sum(k * bin_hz * p[k] for k in range(n2)) / tot)
        notes.append(_count_notes(p, bin_hz))
    total = sum(avg) or 1e-20
    bands = {}
    for name, lo, hi in BANDS:
        bands[name] = sum(avg[k] for k in range(n2) if lo <= k * bin_hz < hi) / total

    return {
        "length_s": length,
        "active_s": active_s,
        "hits": len(onsets),
        "hits_per_s": len(onsets) / max(active_s, 1e-3),
        "tops": len(off_beat_hi) / float(len(onsets)),
        "glide": _median(glides) if glides else 1.0,
        "range_db": _percentile(act, 0.9) - _percentile(act, 0.2),
        "decay_ms": decay_ms,
        "rise_db": end_db - start_db,
        "peak_pos": peak_pos,
        "flatness": _median(flat),
        "centroid_hz": _median(cents),
        "notes": _median(notes),
        "bands": bands,
    }


# ---------------------------------------------------------------------------
# deciding
# ---------------------------------------------------------------------------
def verdict(label, conf, why, source="audio"):
    return {"label": label, "role": LABEL_ROLE[label], "conf": conf, "why": why, "source": source}


def judge(f):
    """Features -> verdict. Each rule says why it fired."""
    b = f["bands"]
    low = b["sub"] + b["bass"]
    high = b["high"]
    noisy = f["flatness"] >= NOISY_FLATNESS
    one_shot = f["active_s"] < ONE_SHOT_SECONDS and f["hits"] <= 2
    hits = one_shot or (f["range_db"] >= PERCUSSIVE_RANGE_DB and f["hits_per_s"] >= 1.0)
    why = ["%d%% low end, %d%% highs" % (100 * low, 100 * high),
           "noisy" if noisy else "tonal"]

    if f["rise_db"] >= RISER_DB and f["peak_pos"] > 0.7 and f["active_s"] > 0.75:
        why.append("swells up %d dB and peaks at the end" % f["rise_db"])
        if high > 0.35 and noisy and f["active_s"] < REVERSE_MAX_SECONDS:
            return verdict("reverse cymbal", "high", why)
        return verdict("riser", "high", why)

    if hits:
        why.append("one-shot" if one_shot else "%.1f hits/s" % f["hits_per_s"])
        why.append("hits ring %d ms" % f["decay_ms"])
        if (low >= 0.25 and not one_shot and f["flatness"] >= 0.15
                and (f["tops"] >= TOPS_RATIO or high + b["mid"] >= 0.12)):
            why.append("highs hit between the low hits")
            return verdict("drum loop", "medium", why)
        if low >= 0.55 and high < 0.08:
            if f["glide"] < KICK_GLIDE:
                why.append("pitch holds steady in each hit")
                return verdict("bass", "medium", why)
            why.append("pitch drops %.1fx in each hit" % f["glide"])
            return verdict("kick", "high" if low > 0.7 else "medium", why)
        if high >= 0.45 or (noisy and f["centroid_hz"] > 5000):
            if one_shot and f["decay_ms"] >= CRASH_DECAY_MS:
                return verdict("crash", "high", why)
            if f["decay_ms"] >= OPEN_HAT_DECAY_MS:
                return verdict("open hats", "medium", why)
            return verdict("closed hats", "medium", why)
        if noisy and b["mid"] + b["lowmid"] >= 0.45:
            return verdict("clap/snare", "medium", why)
        if not noisy and f["notes"] >= CHORD_NOTES:
            why.append("~%d notes at once" % round(f["notes"]))
            return verdict("stabs", "medium", why)
        if not noisy and low >= 0.6:
            return verdict("bass", "medium", why)
        if not noisy and not one_shot:
            return verdict("arp" if f["hits_per_s"] >= 4 else "lead", "medium", why)
        return verdict("perc", "low", why)

    why.append("held sound" if f["hits_per_s"] < 1 else "%.1f notes/s" % f["hits_per_s"])
    if noisy:
        return verdict("noise", "medium", why)
    if low >= 0.6 and f["centroid_hz"] < 400:
        if b["sub"] > b["bass"]:
            return verdict("sub", "high", why)
        if f["hits"] <= 1 and f["active_s"] >= 4:
            why.append("one low note held %d s" % f["active_s"])
            return verdict("drone", "medium", why)
        return verdict("bass", "high", why)
    if f["notes"] >= CHORD_NOTES:
        why.append("~%d notes at once" % round(f["notes"]))
        if f["hits_per_s"] < 1:
            return verdict("pad", "medium", why)
        return verdict("chords", "medium", why)
    if f["hits_per_s"] < 1:
        return verdict("drone", "medium", why)
    if f["hits_per_s"] >= 4:
        return verdict("arp", "medium", why)
    return verdict("lead", "medium", why)


def judge_midi(notes, beats, drum_rack=False):
    """notes: [(start_beat, length_beats, midi_key)] per clip. Plugin synths only — low confidence."""
    if drum_rack:
        return verdict("drum kit", "high", ["Drum Rack"], "notes")
    flat = [n for clip in notes for n in clip]
    if not flat:
        return None
    keys = [k for _, _, k in flat]
    lens = [d for _, d, _ in flat]
    poly = []
    for clip in notes:
        for s, _, _ in clip:
            poly.append(sum(1 for s2, d2, _ in clip if s2 <= s + 1e-6 < s2 + d2))
    med_key, avg_len, avg_poly = _median(keys), sum(lens) / len(lens), _median(poly)
    rate = len(flat) / max(beats, 1e-3)
    why = ["notes only (can't hear the synth)", "median note %s" % note_name(med_key),
           "~%d at once" % round(avg_poly), "notes last %.1f beats" % avg_len]
    if avg_poly >= CHORD_NOTES:
        return verdict("pad" if avg_len >= 2 else ("stabs" if avg_len < 0.5 else "chords"),
                       "low", why, "notes")
    if avg_len >= 4 and rate < 0.5:
        return verdict("drone", "low", why, "notes")
    if med_key < MIDI_BASS_KEY:
        return verdict("bass", "low", why, "notes")
    if rate > MIDI_ARP_RATE:
        return verdict("arp", "low", why, "notes")
    return verdict("lead", "low", why, "notes")


def note_name(key):
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    key = int(round(key))
    return "%s%d" % (names[key % 12], key // 12 - 2)        # Live: 60 = C3


# ---------------------------------------------------------------------------
# finding samples on disk
# ---------------------------------------------------------------------------
def _v(el, path, default=""):
    x = el.find(path)
    return x.get("Value", default) if x is not None else default


class SampleFinder(object):
    """Resolves a set's sample references to files on this computer."""

    def __init__(self, als_path, search_dirs=()):
        self.base = os.path.dirname(os.path.abspath(als_path))
        self.dirs = [self.base] + [os.path.expanduser(d) for d in search_dirs]
        self._index = None

    def _build_index(self):
        self._index = {}
        for d in self.dirs:
            for root, _, files in os.walk(d):
                for fn in files:
                    if fn.lower().endswith(AUDIO_EXT):
                        self._index.setdefault(fn.lower(), os.path.join(root, fn))

    def find(self, fileref):
        path = _v(fileref, "Path")
        rel = _v(fileref, "RelativePath")
        for cand in (path, os.path.normpath(os.path.join(self.base, rel)) if rel else ""):
            if cand and os.path.isfile(cand):
                return cand
        name = os.path.basename(path or rel)
        if not name:
            return None
        if self._index is None:
            self._build_index()
        return self._index.get(name.lower())

    @staticmethod
    def label(fileref):
        return os.path.basename(_v(fileref, "Path") or _v(fileref, "RelativePath")) or "?"


# ---------------------------------------------------------------------------
# a whole set
# ---------------------------------------------------------------------------
_CACHE = {}


def judge_file(path):
    if path not in _CACHE:
        try:
            feats = analyze(path)
            _CACHE[path] = (judge(feats) if feats else None, feats, None)
        except (AudioError, OSError, struct.error, ValueError) as e:
            _CACHE[path] = (None, None, str(e))
    return _CACHE[path]


def _track_samples(track):
    """[(FileRef, weight)] — audio clips count once each, instrument samples once."""
    out = []
    for clip in track.iter("AudioClip"):
        fr = clip.find("SampleRef/FileRef")
        if fr is not None:
            out.append((fr, 1))
    devices = track.find("DeviceChain/DeviceChain/Devices")
    if devices is not None:
        for dev in devices.iter():
            if dev.tag in SAMPLE_DEVICES:
                for ref in dev.iter("SampleRef"):
                    fr = ref.find("FileRef")
                    if fr is not None:
                        out.append((fr, 1))
    return out


def _midi_notes(track):
    notes, beats = [], 0.0
    for clip in track.iter("MidiClip"):
        cn = []
        for kt in clip.iter("KeyTrack"):
            key = int(_v(kt, "MidiKey", "0"))
            for ev in kt.iter("MidiNoteEvent"):
                if ev.get("IsEnabled", "true") == "false":
                    continue
                cn.append((float(ev.get("Time", 0)), float(ev.get("Duration", 0)), key))
        if cn:
            notes.append(cn)
            beats += max(float(_v(clip, "CurrentEnd", "0")) - float(_v(clip, "CurrentStart", "0")),
                         max(s + d for s, d, _ in cn))
    return notes, beats


def listen(root, als_path, search_dirs=(), progress=None):
    """Listen to every track. Returns {track Id: info}, info has 'verdict' (or None),
    'files' [(sample name, verdict, error)], 'missing' [sample names]."""
    finder = SampleFinder(als_path, search_dirs)
    tracks = [t for t in root.find("LiveSet/Tracks") if t.tag in ("AudioTrack", "MidiTrack")]
    result = {}
    for t in tracks:
        info = {"verdict": None, "files": [], "missing": []}
        weights, seen = {}, {}
        for fr, w in _track_samples(t):
            name = SampleFinder.label(fr)
            path = finder.find(fr)
            if path is None:
                if name not in info["missing"]:
                    info["missing"].append(name)
                continue
            if path not in seen:
                if progress and path not in _CACHE:
                    progress(name)
                v, feats, err = judge_file(path)
                seen[path] = v
                info["files"].append((name, v, err, feats))
            v = seen[path]
            if v:
                weights[v["label"]] = weights.get(v["label"], 0) + w
        devices = t.find("DeviceChain/DeviceChain/Devices")
        drum_rack = devices is not None and devices.find("DrumGroupDevice") is not None
        if weights:
            best = max(weights, key=lambda k: weights[k])
            pick = next(v for _, v, _, _ in info["files"] if v and v["label"] == best)
            info["verdict"] = dict(pick)
            if len(weights) > 1:
                info["verdict"]["why"] = pick["why"] + ["mixed: " + ", ".join(sorted(weights))]
        elif t.tag == "MidiTrack" and (drum_rack or not (info["files"] or info["missing"])):
            notes, beats = _midi_notes(t)
            info["verdict"] = judge_midi(notes, beats, drum_rack)
        result[t.get("Id")] = info
    return result


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def _show_feats(f, indent):
    b = f["bands"]
    print("%s%.1fs long, %.1f hits/s, tops %.2f, glide %.2f, range %d dB, hits ring %d ms, rise %+d dB, flatness %.2f, "
          "centroid %d Hz, ~%.1f notes" % (indent, f["length_s"], f["hits_per_s"], f["tops"], f["glide"], f["range_db"],
                                           f["decay_ms"], f["rise_db"], f["flatness"],
                                           f["centroid_hz"], f["notes"]))
    print("%sbands: %s" % (indent, "  ".join("%s %d%%" % (k, 100 * b[k]) for k, _, _ in BANDS)))


def _fmt(v):
    if not v:
        return "?"
    return "%-6s %-15s (%s%s)" % (v["role"], v["label"], v["conf"],
                                  ", notes only" if v["source"] == "notes" else "")


def main():
    ap = argparse.ArgumentParser(description="Listen to an Ableton Live Set's audio and say what each track is.")
    ap.add_argument("files", nargs="+", help=".als set(s) or .wav/.aif sample(s)")
    ap.add_argument("--samples", action="append", default=[], metavar="DIR",
                    help="extra folder to look in for samples that moved (can repeat)")
    ap.add_argument("-v", "--verbose", action="store_true", help="show the measurements")
    args = ap.parse_args()

    for path in args.files:
        if path.lower().endswith(AUDIO_EXT):
            v, feats, err = judge_file(path)
            print("%-40s %s" % (os.path.basename(path)[:40], err or _fmt(v)))
            if v:
                print("   " + "; ".join(v["why"]))
            if feats and args.verbose:
                _show_feats(feats, "   ")
            continue
        if not path.lower().endswith(".als"):
            print("skip: %s" % path)
            continue
        with gzip.open(path, "rb") as f:
            root = ET.fromstring(f.read())
        print("\n%s\n%s" % (os.path.basename(path), "-" * 78))
        res = listen(root, path, args.samples)
        for t in root.find("LiveSet/Tracks"):
            if t.get("Id") not in res:
                continue
            name = _v(t, "Name/UserName") or _v(t, "Name/EffectiveName")
            info = res[t.get("Id")]
            print("%-24s %s" % (name[:24], _fmt(info["verdict"])))
            if info["verdict"]:
                print("   " + "; ".join(info["verdict"]["why"]))
            for fname, _, err, feats in info["files"]:
                if err:
                    print("   ! %s: %s" % (fname, err))
                elif feats and args.verbose:
                    print("   %s" % fname)
                    _show_feats(feats, "      ")
            for m in info["missing"]:
                print("   ! sample not found: %s" % m)


if __name__ == "__main__":
    main()
