#!/usr/bin/env python3
"""
eq_als.py — add a basic cleanup EQ to every track of an Ableton Live Set (.als).

It's a starting point, not a mix: one EQ Eight per track, named "AUTO EQ", with a low cut
(and a gentle high cut on pads/atmos) chosen from what the track is — kick, hats, synth, pad...
Everything is a normal EQ Eight band you can move, tweak or switch off in Live.

Rules it follows:
  * tracks that already have any EQ (EQ Eight, EQ Three, Channel EQ, Pro-Q, any plugin with
    "EQ" in its name) are left alone — your own EQ always wins
  * group buses, returns and master are left alone; so are empty tracks and MISC tracks
  * audio tracks: EQ goes first in the chain; MIDI tracks: right after the instrument
  * never touches the original: saves "<name> (eq).als" next to it

Usage:
  python3 eq_als.py "My Track.als"
  python3 eq_als.py "My Track.als" --dry-run
  python3 organize_als.py "My Track.als" --eq      # organize + EQ in one go
"""

import argparse
import base64
import copy
import gzip
import os
import re
import sys
import xml.etree.ElementTree as ET
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import organize_als as org  # noqa: E402  (shared role detection)

# ---------------------------------------------------------------------------
# YOUR EQ RULES — edit freely. Frequencies in Hz. high_cut None = no high cut.
# Specific sounds are matched first (track name, then sample names) ...
# ---------------------------------------------------------------------------
SOUND_RULES = [
    # label            keywords                                                  low_cut high_cut
    ("kick",          ["kick", "kik", "bd"],                                       30,   None),
    ("bass",          ["bass", "sub", "subbass", "303", "reese", "bassline"],      30,   None),
    ("hats/cymbals",  ["hat", "hats", "hh", "hihat", "oh", "ch", "ride", "cymbal",
                       "cymbals", "cym", "crash", "shaker", "shk", "tamb",
                       "tambourine"],                                             350,   None),
    ("snare/clap",    ["snare", "sd", "clap", "clp", "rim", "rimshot", "snap"],   150,   None),
    ("perc",          ["perc", "percs", "percussion", "conga", "bongo", "tom",
                       "toms", "cowbell"],                                        150,   None),
]
# ... otherwise the track's role decides.
ROLE_RULES = {
    "DRUMS":  (30,  None),     # full drum loops keep their kick
    "BASS":   (30,  None),
    "CHORDS": (150, None),
    "SYNTH":  (120, None),
    "ATMOS":  (200, 12000),
    "VOX":    (100, None),
    "FX":     (200, None),
    # "MISC": not touched
}
DEVICE_NAME = "AUTO EQ"
LOW_CUT_MODE = 1     # EQ Eight band modes: 0 low cut 48dB, 1 low cut 12dB, 2 low shelf,
HIGH_CUT_MODE = 6    # 3 bell, 4 notch, 5 high shelf, 6 high cut 12dB, 7 high cut 48dB
Q = 0.7071

NATIVE_EQS = ("Eq8", "FilterEQ3", "ChannelEq")
INSTRUMENTS = ("OriginalSimpler", "MultiSampler", "Operator", "InstrumentVector", "UltraAnalog",
               "Drift", "InstrumentMeld", "DrumGroupDevice", "InstrumentGroupDevice", "Collision",
               "LoungeLizard", "StringStudio", "InstrumentImpulse", "ProxyInstrumentDevice")
AU_INSTRUMENT = "1635085685"   # 'aumu'

# A default EQ Eight from Live 11, used only when a Live 11 set has none of its own to copy.
EQ8_TEMPLATE_LIVE11 = (
    "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"
)


# ---------------------------------------------------------------------------
def plugin_info(dev):
    desc = dev.find("PluginDesc")
    return desc[0] if desc is not None and len(desc) else None


def plugin_name(dev):
    info = plugin_info(dev)
    if info is None:
        return ""
    return org.val(info, "Name") or org.val(info, "PlugName")


def is_instrument(dev):
    if dev.tag in INSTRUMENTS:
        return True
    info = plugin_info(dev)
    if info is None:
        return False
    if org.val(info, "NumAudioInputs") == "0":
        return True
    if info.tag == "AuPluginInfo" and org.val(info, "ComponentType") == AU_INSTRUMENT:
        return True
    if info.tag == "Vst3PluginInfo" and org.val(info, "DeviceType") == "1":
        return True
    if info.tag == "VstPluginInfo" and org.val(info, "Category") == "2":
        return True
    return False


def existing_eq(devices):
    """Name of an EQ already on the track (also looks inside racks), else None."""
    for d in devices.iter():
        if d.tag in NATIVE_EQS:
            return {"Eq8": "EQ Eight", "FilterEQ3": "EQ Three", "ChannelEq": "Channel EQ"}[d.tag]
        if d.tag in ("PluginDevice", "AuPluginDevice"):
            n = plugin_name(d)
            if re.search(r"\beq\b|pro-?q", n, re.I):
                return n
    return None


def choose(track, role):
    """(label, low_cut, high_cut) for this track, or None to leave it alone."""
    for source in ([org.display_name(track)], org.sample_names(track)):
        toks = set()
        for text in source:
            toks.update(org.tokens(text))
        for label, words, lc, hc in SOUND_RULES:
            if toks.intersection(words):
                return label, lc, hc
    if role in ROLE_RULES:
        lc, hc = ROLE_RULES[role]
        return role.lower(), lc, hc
    return None


def eq_template(root, live_set):
    for d in live_set.iter("Eq8"):
        return copy.deepcopy(d)
    if root.get("MinorVersion", "").startswith("11."):
        return ET.fromstring(zlib.decompress(base64.b64decode(EQ8_TEMPLATE_LIVE11)))
    return None


def set_band(band, on, mode=None, freq=None):
    for side in ("ParameterA", "ParameterB"):
        p = band.find(side)
        if p is None:
            continue
        org.setv(p, "IsOn/Manual", "true" if on else "false")
        if mode is not None:
            org.setv(p, "Mode/Manual", mode)
            org.setv(p, "Freq/Manual", freq)
            org.setv(p, "Gain/Manual", 0)
            org.setv(p, "Q/Manual", Q)


def make_eq(template, ids, device_id, low_cut, high_cut):
    eq = copy.deepcopy(template)
    eq.set("Id", str(device_id))
    for e in eq.iter():
        if org.is_pointee(e):
            e.set("Id", ids.pointee())
    org.setv(eq, "UserName", DEVICE_NAME)
    org.setv(eq, "Annotation", "")
    org.setv(eq, "On/Manual", "true")
    org.setv(eq, "IsExpanded", "true")
    org.setv(eq, "Mode", "0")                # stereo
    org.setv(eq, "GlobalGain/Manual", "0")
    org.setv(eq, "Scale/Manual", "1")
    for i in range(8):
        band = eq.find("Bands.%d" % i)
        if band is not None:
            set_band(band, False)
    set_band(eq.find("Bands.0"), True, LOW_CUT_MODE, low_cut)
    if high_cut:
        set_band(eq.find("Bands.7"), True, HIGH_CUT_MODE, high_cut)
    return eq


def apply_eq(root, role=None):
    """Add cleanup EQs in place. Returns a list of report lines."""
    live_set = root.find("LiveSet")
    tracks = [t for t in live_set.find("Tracks") if t.tag in org.TRACK_TAGS]
    if role is None:
        role = org.classify(tracks)[0]
    template = eq_template(root, live_set)
    if template is None:
        return ["skipped: this set has no EQ Eight to copy. Drop an EQ Eight on any track in Live, "
                "save, and run again."]
    ids = org.Ids(live_set)
    notes = []
    for t in tracks:
        name = org.display_name(t)
        if t.tag == "GroupTrack":
            continue
        devices = t.find("DeviceChain/DeviceChain/Devices")
        if devices is None:
            continue
        if not org.clips(t) and not len(devices):
            notes.append("-  %-24s skipped (empty track)" % name[:24])
            continue
        has = existing_eq(devices)
        if has:
            notes.append("-  %-24s skipped (already has %s)" % (name[:24], has))
            continue
        pick = choose(t, role.get(t.get("Id")))
        if pick is None:
            notes.append("-  %-24s skipped (couldn't tell what it is)" % name[:24])
            continue
        label, lc, hc = pick
        pos = 0
        if t.tag == "MidiTrack":
            inst = [i for i, d in enumerate(devices) if is_instrument(d)]
            if not inst:
                notes.append("-  %-24s skipped (MIDI track with no instrument)" % name[:24])
                continue
            pos = inst[0] + 1
        dev_ids = [int(d.get("Id")) for d in devices if (d.get("Id") or "").isdigit()]
        eq = make_eq(template, ids, max(dev_ids + [-1]) + 1, lc, hc)
        devices.insert(pos, eq)
        cut = "low cut %d Hz" % lc + (", high cut %d kHz" % (hc // 1000) if hc else "")
        notes.append("+  %-24s %-13s %s" % (name[:24], label, cut))
    ids.save()
    return notes


def main():
    ap = argparse.ArgumentParser(description="Add a basic cleanup EQ Eight to each track of a Live Set.")
    ap.add_argument("files", nargs="+", help=".als file(s)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, don't save")
    args = ap.parse_args()
    ok = True
    for path in args.files:
        if not path.lower().endswith(".als") or path.endswith("(eq).als"):
            print("skip: %s" % path)
            continue
        try:
            with gzip.open(path, "rb") as f:
                root = ET.fromstring(f.read())
            print("\n%s  (%s)" % (os.path.basename(path), root.get("Creator", "")))
            print("-" * 78)
            for note in apply_eq(root):
                print(note)
            if args.dry_run:
                print("\n(dry run — nothing saved)")
                continue
            out = os.path.splitext(path)[0] + " (eq).als"
            data = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                    + ET.tostring(root, encoding="unicode") + "\n").encode("utf-8")
            with gzip.open(out, "wb") as f:
                f.write(data)
            problems = org.check(out)
            if problems:
                os.remove(out)
                raise RuntimeError("output failed safety checks, nothing saved:\n  " + "\n  ".join(problems))
            print("\nSaved: %s" % out)
        except Exception as e:
            ok = False
            print("\nFAILED on %s: %s" % (path, e))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
