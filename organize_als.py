#!/usr/bin/env python3
"""
organize_als.py — tidy any Ableton Live Set (.als).

What it does (never touches the original file):
  * works out each track's role (drums, bass, chords, synth, pad/atmos, vox, fx, misc)
    from its name, clip names, sample file names, devices and plugins
  * renames auto-named tracks ("3-Audio", "Audio 2 [2026-08-19 110949]") to "SYNTH 1", "BASS 2" ...
  * colours every track by role
  * reorders tracks/groups: drums -> bass -> chords -> synth -> pad/atmos -> vox -> fx -> misc
  * flags empty tracks
  * saves "<name> (organized).als" next to the original

Works with Live 10, 11 and 12 sets. Standard library only (Python 3.8+).

Usage:
  python3 organize_als.py "My Track.als"            # organize
  python3 organize_als.py "My Track.als" --dry-run  # just show what it would do
  python3 organize_als.py *.als                     # several sets at once
  python3 organize_als.py --help                    # all options
"""

import argparse
import gzip
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

# ---------------------------------------------------------------------------
# YOUR RULES — edit freely.
# Order matters: this is the order tracks end up in, top to bottom.
# colour = Live palette index (0-69). Row 2 of the colour picker is 14-27.
# keywords match whole words in track/clip/sample/device names (case-insensitive).
# ---------------------------------------------------------------------------
ROLES = [
    ("DRUMS", 14, ["kick", "kik", "bd", "snare", "sd", "clap", "clp", "hat", "hats", "hh", "hihat",
                   "oh", "ch", "perc", "percussion", "rim", "rimshot", "shaker", "shk", "ride",
                   "cymbal", "tom", "toms", "conga", "bongo", "808", "909", "707", "drum", "drums",
                   "beat", "break", "top", "tops", "groove", "tamb", "tambourine", "cowbell",
                   "snap", "drumgroupdevice", "drumrack"]),
    ("BASS",  15, ["bass", "sub", "subbass", "303", "reese", "low", "lows", "bassline"]),
    ("CHORDS", 17, ["chord", "chords", "stab", "stabs", "keys", "key", "piano", "rhodes", "organ",
                    "epiano", "dubchord", "harmony"]),
    ("SYNTH", 19, ["synth", "lead", "arp", "pluck", "seq", "sequence", "bleep", "melody", "mel",
                   "hook", "operator", "wavetable", "instrumentvector", "ultraanalog", "analog",
                   "drift", "meld", "diva", "serum", "vital", "pigments", "juno", "moog", "minimoog"]),
    ("ATMOS", 23, ["pad", "pads", "atmos", "atmosphere", "drone", "texture", "textures", "ambient",
                   "ambience", "field", "fieldrec", "noise", "rain", "vinyl", "crackle", "tape",
                   "foley", "room", "hiss", "wash", "space", "dub", "string", "strings"]),
    ("VOX",   25, ["vox", "vocal", "vocals", "voice", "spoken", "acapella", "acappella", "chant",
                   "speech", "sample_vox"]),
    ("FX",    26, ["fx", "sfx", "riser", "sweep", "impact", "downlifter", "uplifter", "crash",
                   "reverse", "rev", "whoosh", "transition", "fill", "boom", "hit", "zap"]),
]
MISC = ("MISC", 27)        # anything with no clues
GROUP_SUFFIX = " BUS"     # auto-named groups become e.g. "DRUMS BUS"

# Names Live gives tracks automatically — these get replaced.
GENERIC = re.compile(r"^(audio|midi|group|track|\d+)( \d+)*$", re.I)

# Where clues come from, and how much each counts.
WEIGHT_NAME = 4      # track name
WEIGHT_CLIP = 3      # clip names
WEIGHT_SAMPLE = 2    # sample file names
WEIGHT_DEVICE = 1    # instruments / effects / plugins

ROLE_ORDER = [r[0] for r in ROLES] + [MISC[0]]
ROLE_COLOR = dict([(r[0], r[1]) for r in ROLES] + [MISC])
KEYWORDS = {}
for _role, _col, _words in ROLES:
    for _w in _words:
        KEYWORDS.setdefault(_w.lower(), _role)

TRACK_TAGS = ("AudioTrack", "MidiTrack", "GroupTrack")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def val(el, path, default=""):
    x = el.find(path)
    return x.get("Value", default) if x is not None else default


def tokens(text):
    """'Kick_909-Loop 2.wav' -> ['kick', '909', 'loop', '2'] ; 'DubChord' -> ['dub','chord','dubchord']"""
    text = re.sub(r"\.(wav|aif|aiff|mp3|flac|ogg|adv|adg|asd)$", "", text, flags=re.I)
    text = re.sub(r"\[\d{4}-\d{2}-\d{2}[^\]]*\]", " ", text)          # Live recording timestamps
    out = []
    for word in re.split(r"[^A-Za-z0-9]+", text):
        if not word:
            continue
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", word) or [word]
        out.extend(p.lower() for p in parts)
        if len(parts) > 1:
            out.append(word.lower())
    return out


def display_name(track):
    user = val(track, "Name/UserName")
    return user if user else val(track, "Name/EffectiveName")


def clean_auto_name(name):
    """'5-Audio 2 [2026-08-19 110949]' -> 'Audio 2' ; '2-Full Immersion_' -> 'Full Immersion'"""
    name = re.sub(r"^\d+-", "", name)
    name = re.sub(r"\[\d{4}-\d{2}-\d{2}[^\]]*\]", "", name)
    name = re.sub(r"[_\s]+$", "", name).strip(" _-")
    return name


def device_names(track):
    names = []
    devices = track.find("DeviceChain/DeviceChain/Devices")
    if devices is None:
        return names
    for dev in devices.iter():
        if dev.tag in ("Vst3PluginInfo", "AuPluginInfo"):
            names.append(val(dev, "Name"))
        elif dev.tag == "VstPluginInfo":
            names.append(val(dev, "PlugName"))
    for dev in devices:
        names.append(dev.tag)                 # e.g. Operator, DrumGroupDevice, Reverb
        user = val(dev, "UserName")
        if user:
            names.append(user)
    return [n for n in names if n]


def clips(track):
    return [c for c in track.iter() if c.tag in ("AudioClip", "MidiClip")]


def sample_names(track):
    names = set()
    for ref in track.iter("SampleRef"):
        fr = ref.find("FileRef")
        if fr is None:
            continue
        n = val(fr, "Name") or val(fr, "RelativePath") or val(fr, "Path")
        if n:
            names.add(re.split(r"[\\/]", n)[-1])
    return sorted(names)


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------
def score(track, include_name=True):
    s = Counter()
    clues = []

    def add(text, weight, where):
        for t in tokens(text):
            role = KEYWORDS.get(t)
            if role:
                s[role] += weight
                clues.append("%s '%s'" % (where, t))

    if include_name:
        user = val(track, "Name/UserName")
        add(user or clean_auto_name(val(track, "Name/EffectiveName")), WEIGHT_NAME, "name")
    for c in clips(track):
        add(val(c, "Name"), WEIGHT_CLIP, "clip")
    for n in sample_names(track):
        add(n, WEIGHT_SAMPLE, "sample")
    for d in device_names(track):
        add(d, WEIGHT_DEVICE, "device")
    return s, clues


def pick(counter):
    if not counter:
        return None
    best = max(counter.values())
    for role in ROLE_ORDER:          # tie-break by role order
        if counter.get(role) == best:
            return role


# ---------------------------------------------------------------------------
# colour (Live 11/12 use <Color>, Live 10 uses <ColorIndex>)
# ---------------------------------------------------------------------------
def set_color(el, index):
    for tag in ("Color", "ColorIndex"):
        c = el.find(tag)
        if c is not None:
            c.set("Value", str(index))
            return True
    return False


def get_color(el):
    return val(el, "Color") or val(el, "ColorIndex")


# ---------------------------------------------------------------------------
# main work
# ---------------------------------------------------------------------------
def organize(path, args):
    with gzip.open(path, "rb") as f:
        raw = f.read()
    root = ET.fromstring(raw)
    live_set = root.find("LiveSet")
    tracks_el = live_set.find("Tracks")
    creator = root.get("Creator", "unknown Live version")

    all_tracks = [t for t in tracks_el if t.tag in TRACK_TAGS]
    others = [t for t in tracks_el if t.tag not in TRACK_TAGS]      # Return tracks etc.
    by_id = dict((t.get("Id"), t) for t in all_tracks)
    parent = dict((t.get("Id"), val(t, "TrackGroupId", "-1")) for t in all_tracks)
    children = dict((t.get("Id"), []) for t in all_tracks)
    children["-1"] = []
    for t in all_tracks:                                             # keeps original order
        children.setdefault(parent[t.get("Id")], []).append(t)

    role, why, empty = {}, {}, []

    # 1. classify non-group tracks from their own clues
    for t in all_tracks:
        if t.tag == "GroupTrack":
            continue
        s, clues = score(t)
        r = pick(s)
        role[t.get("Id")] = r
        why[t.get("Id")] = clues
        if not clips(t) and not device_names(t):
            empty.append(t)

    # 2. groups: own name first, else majority of members
    def group_role(g):
        gid = g.get("Id")
        if gid in role:
            return role[gid]
        own = Counter()
        user = val(g, "Name/UserName")
        for tk in tokens(user):
            if tk in KEYWORDS:
                own[KEYWORDS[tk]] += 1
        r = pick(own)
        if r is None:
            votes = Counter()
            for c in children.get(gid, []):
                cr = group_role(c) if c.tag == "GroupTrack" else role.get(c.get("Id"))
                if cr:
                    votes[cr] += 1
            r = pick(votes)
        role[gid] = r
        return r

    for t in all_tracks:
        if t.tag == "GroupTrack":
            group_role(t)

    # 3. tracks with no clues inherit their group's role
    for t in all_tracks:
        tid = t.get("Id")
        if role.get(tid) is None:
            p = parent[tid]
            while p != "-1" and role.get(p) in (None, MISC[0]):
                p = parent.get(p, "-1")
            if p != "-1":
                role[tid] = role[p]
                why[tid] = ["inherited from group '%s'" % display_name(by_id[p])]
        if role.get(tid) is None:
            role[tid] = MISC[0]
            why.setdefault(tid, [])
    for t in all_tracks:   # groups whose members were all clueless
        if role[t.get("Id")] is None:
            role[t.get("Id")] = MISC[0]

    # 4. rename + colour
    counters = Counter()
    changes = []
    used_names = set()
    for t in all_tracks:
        tid = t.get("Id")
        r = role[tid]
        old = display_name(t)
        new = old
        user = val(t, "Name/UserName")
        base = clean_auto_name(val(t, "Name/EffectiveName"))
        if not args.no_rename:
            if t.tag == "GroupTrack":
                if not user and GENERIC.match(base or "group"):
                    new = r + GROUP_SUFFIX
            elif not user:
                if not base or GENERIC.match(base):
                    counters[r] += 1
                    new = "%s %d" % (r, counters[r])
                else:
                    new = "%s %s" % (r, base) if args.prefix else base
            elif args.prefix and not user.upper().startswith(r):
                new = "%s %s" % (r, user)
            # avoid duplicates like two "SYNTH BUS"
            candidate, n = new, 2
            while candidate.upper() in used_names and candidate != old:
                candidate = "%s %d" % (new, n)
                n += 1
            new = candidate
            used_names.add(new.upper())
            if new != old:
                t.find("Name/UserName").set("Value", new)
                t.find("Name/EffectiveName").set("Value", new)
        old_col = get_color(t)
        if not args.no_color:
            set_color(t, ROLE_COLOR[r])
            if args.color_clips:
                for c in clips(t):
                    set_color(c, ROLE_COLOR[r])
        changes.append((t, r, old, new, old_col, get_color(t)))

    # 5. reorder: sort siblings by role, keep groups' members right after them
    order = []

    def emit(pid):
        sibs = children.get(pid, [])
        if not args.no_reorder:
            sibs = sorted(sibs, key=lambda x: ROLE_ORDER.index(role[x.get("Id")]))  # stable
        for s in sibs:
            order.append(s)
            if s.tag == "GroupTrack":
                emit(s.get("Id"))

    emit("-1")
    assert len(order) == len(all_tracks), "track tree mismatch — refusing to write"
    for t in list(tracks_el):
        tracks_el.remove(t)
    for t in order + others:
        tracks_el.append(t)

    # report
    print("\n%s  (%s)" % (os.path.basename(path), creator))
    print("-" * 78)
    depth = {}
    for t in order:
        p = parent[t.get("Id")]
        depth[t.get("Id")] = 0 if p == "-1" else depth.get(p, 0) + 1
        c = [x for x in changes if x[0] is t][0]
        indent = "   " * depth[t.get("Id")]
        kind = "[group] " if t.tag == "GroupTrack" else ""
        arrow = ("%-26s -> %s" % (c[2][:26], c[3])) if c[2] != c[3] else c[3]
        print("%s%s%-7s %s" % (indent, kind, c[1], arrow))
        if args.verbose and why.get(t.get("Id")):
            print("%s        clues: %s" % (indent, ", ".join(why[t.get("Id")][:6])))
    if empty:
        print("\nEmpty tracks (no clips, no devices) — maybe delete in Live:")
        for t in empty:
            print("   - %s" % display_name(t))
    if others:
        print("\nReturn tracks left as they are: %s" %
              ", ".join(display_name(t) for t in others if t.find("Name") is not None))

    if args.dry_run:
        print("\n(dry run — nothing saved)")
        return None

    base, _ = os.path.splitext(path)
    out = base + " (organized).als"
    body = ET.tostring(root, encoding="unicode")
    data = ('<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n").encode("utf-8")
    with gzip.open(out, "wb") as f:
        f.write(data)
    # sanity check: re-read what we wrote
    with gzip.open(out, "rb") as f:
        check = ET.fromstring(f.read())
    n_after = len([t for t in check.find("LiveSet/Tracks") if t.tag in TRACK_TAGS])
    assert n_after == len(all_tracks), "track count changed — output is suspect"
    print("\nSaved: %s" % out)
    return out


def main():
    ap = argparse.ArgumentParser(description="Organize Ableton Live Sets: name, colour and order tracks by role.")
    ap.add_argument("files", nargs="+", help=".als file(s)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, don't save")
    ap.add_argument("--no-rename", action="store_true", help="leave track names alone")
    ap.add_argument("--no-color", action="store_true", help="leave colours alone")
    ap.add_argument("--no-reorder", action="store_true", help="leave track order alone")
    ap.add_argument("--prefix", action="store_true",
                    help="also prefix names you typed yourself, e.g. 'kick' -> 'DRUMS kick'")
    ap.add_argument("--color-clips", action="store_true", help="recolour clips to match their track")
    ap.add_argument("-v", "--verbose", action="store_true", help="show why each track got its role")
    args = ap.parse_args()

    ok = True
    for f in args.files:
        if not f.lower().endswith(".als"):
            print("skip (not .als): %s" % f)
            continue
        if f.endswith("(organized).als"):
            print("skip (already organized): %s" % f)
            continue
        try:
            organize(f, args)
        except Exception as e:      # keep going with other files
            ok = False
            print("\nFAILED on %s: %s" % (f, e))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
