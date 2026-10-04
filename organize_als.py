#!/usr/bin/env python3
"""
organize_als.py — tidy any Ableton Live Set (.als).

What it does (never touches the original file):
  * works out each track's role (drums, bass, chords, synth, atmos, vox, fx, misc)
    from its name, clip names, sample file names, devices and plugins
  * puts loose drum tracks into one DRUMS group and loose synth tracks into one SYNTH group
    (joins an existing group of that kind if the set already has one)
  * renames auto-named tracks ("3-Audio", "Audio 2 [2026-08-19 110949]") to "SYNTH 1", "DRUMS 2" ...
  * colours each group, every track inside it, and every clip (arrangement + session) the same colour
  * reorders: drums -> bass -> chords -> synth -> atmos -> vox -> fx -> misc
  * flags empty tracks
  * saves "<name> (organized).als" next to the original

Works with Live 10, 11 and 12 sets. Standard library only (Python 3.8+).

Usage:
  python3 organize_als.py "My Track.als"            # organize
  python3 organize_als.py "My Track.als" --dry-run  # just show what it would do
  python3 organize_als.py *.als                     # several sets at once
  python3 organize_als.py "My Track.als" --listen   # also listen to the audio (listen_als.py)
  python3 organize_als.py --help                    # all options
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
from collections import Counter

# ---------------------------------------------------------------------------
# YOUR RULES — edit freely.
# Order matters: this is the order tracks end up in, top to bottom.
# colour = Live palette index (0-69). Row 2 of the colour picker is 14-27.
# keywords match whole words in track/clip/sample/device names (case-insensitive).
# ---------------------------------------------------------------------------
ROLES = [
    ("DRUMS", 14, ["kick", "kik", "bd", "snare", "sd", "clap", "clp", "hat", "hats", "hh", "hihat",
                   "oh", "ch", "perc", "percs", "percussion", "rim", "rimshot", "shaker", "shk", "ride",
                   "cymbal", "cymbals", "cym", "tom", "toms", "conga", "bongo", "808", "909", "707",
                   "drum", "drums", "beat", "break", "top", "tops", "groove", "tamb", "tambourine",
                   "cowbell", "snap", "crash", "drumgroupdevice", "drumrack"]),
    ("BASS",  15, ["bass", "sub", "subbass", "303", "reese", "low", "lows", "bassline"]),
    ("CHORDS", 17, ["chord", "chords", "stab", "stabs", "keys", "key", "piano", "rhodes", "organ",
                    "epiano", "dubchord", "harmony"]),
    ("SYNTH", 19, ["synth", "synths", "lead", "arp", "pluck", "seq", "sequence", "bleep", "melody",
                   "mel", "hook", "operator", "wavetable", "instrumentvector", "ultraanalog", "analog",
                   "drift", "meld", "diva", "serum", "vital", "pigments", "juno", "moog", "minimoog"]),
    ("ATMOS", 23, ["pad", "pads", "atmos", "atmosphere", "drone", "texture", "textures", "ambient",
                   "ambience", "field", "fieldrec", "noise", "rain", "vinyl", "crackle", "tape",
                   "foley", "room", "hiss", "wash", "space", "dub", "string", "strings"]),
    ("VOX",   25, ["vox", "vocal", "vocals", "voice", "spoken", "acapella", "acappella", "chant",
                   "speech"]),
    ("FX",    26, ["fx", "sfx", "riser", "sweep", "impact", "downlifter", "uplifter",
                   "reverse", "rev", "whoosh", "transition", "fill", "boom", "hit", "zap"]),
]
MISC = ("MISC", 27)        # anything with no clues

# Loose tracks of these roles get put into one group per role.
AUTO_GROUP_ROLES = ["DRUMS", "SYNTH"]
MIN_TRACKS_TO_GROUP = 2   # don't make a group for a single track
GROUP_SUFFIX = " BUS"     # new / auto-named groups become e.g. "DRUMS BUS"

# Names Live gives tracks automatically — these get replaced.
GENERIC = re.compile(r"^(audio|midi|group|track|\d+)( \d+)*$", re.I)

# Where clues come from, and how much each counts.
WEIGHT_NAME = 4      # track name
WEIGHT_CLIP = 3      # clip names
WEIGHT_SAMPLE = 2    # sample file names
WEIGHT_DEVICE = 1    # instruments / effects / plugins
WEIGHT_AUDIO = 6     # --listen: what the audio sounds like (beats everything but a typed name)
WEIGHT_NOTES = 2     # --listen: plugin synths, judged from their MIDI notes only

ROLE_ORDER = [r[0] for r in ROLES] + [MISC[0]]
ROLE_COLOR = dict([(r[0], r[1]) for r in ROLES] + [MISC])
KEYWORDS = {}
for _role, _col, _words in ROLES:
    for _w in _words:
        KEYWORDS.setdefault(_w.lower(), _role)

TRACK_TAGS = ("AudioTrack", "MidiTrack", "GroupTrack")

# An empty Live 11 group track, used only when a Live 11 set has no group of its own to copy.
GROUP_TEMPLATE_LIVE11 = (
    "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"
    "CMI="
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def val(el, path, default=""):
    x = el.find(path)
    return x.get("Value", default) if x is not None else default


def setv(el, path, value):
    x = el.find(path)
    if x is not None:
        x.set("Value", str(value))


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


def is_pointee(el):
    """Elements whose Id comes from the set-wide NextPointeeId counter."""
    t = el.tag
    return el.get("Id") is not None and (t.endswith("Target") or t == "Pointee"
                                         or t.startswith("ControllerTargets"))


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------
def score(track, heard=None):
    """heard: what listen_als.py made of this track's audio (or its MIDI notes), if anything."""
    s = Counter()
    clues = []

    def add(text, weight, where):
        for t in tokens(text):
            role = KEYWORDS.get(t)
            if role:
                s[role] += weight
                clues.append("%s '%s'" % (where, t))

    user = val(track, "Name/UserName")
    add(user or clean_auto_name(val(track, "Name/EffectiveName")), WEIGHT_NAME, "name")
    if s:                       # the track's own name says what it is — that wins
        return s, clues
    if heard:
        audio = heard["source"] == "audio"
        s[heard["role"]] += WEIGHT_AUDIO if audio else WEIGHT_NOTES
        clues.append("%s '%s'" % ("audio" if audio else "notes", heard["label"]))
    for n in sorted(set(val(c, "Name") for c in clips(track))):   # each clip name once
        add(n, WEIGHT_CLIP, "clip")
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


def classify(all_tracks, heard=None):
    """Work out each track's role. Returns (role, why, empty) keyed by track Id.
    heard: optional {track Id: verdict} from listen_als.py."""
    heard = heard or {}
    by_id = dict((t.get("Id"), t) for t in all_tracks)
    parent = dict((t.get("Id"), val(t, "TrackGroupId", "-1")) for t in all_tracks)

    def kids(pid):
        return [t for t in all_tracks if parent[t.get("Id")] == pid]

    role, why, empty = {}, {}, []

    # 1. classify non-group tracks from their own clues
    for t in all_tracks:
        if t.tag == "GroupTrack":
            continue
        s, clues = score(t, heard.get(t.get("Id")))
        role[t.get("Id")] = pick(s)
        why[t.get("Id")] = clues
        if not clips(t) and not device_names(t):
            empty.append(t)

    # 2. groups: own name first, else majority of members
    def group_role(g):
        gid = g.get("Id")
        if gid in role:
            return role[gid]
        own = Counter()
        for tk in tokens(val(g, "Name/UserName")):
            if tk in KEYWORDS:
                own[KEYWORDS[tk]] += 1
        r = pick(own)
        if r is None:
            votes = Counter()
            for c in kids(gid):
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
    for t in all_tracks:
        if role.get(t.get("Id")) is None:
            role[t.get("Id")] = MISC[0]
            why.setdefault(t.get("Id"), [])

    return role, why, empty


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
# creating a group track
# ---------------------------------------------------------------------------
class Ids(object):
    """Hands out fresh track Ids and pointee Ids so nothing in the set collides."""

    def __init__(self, live_set):
        self.live_set = live_set
        track_ids = [int(t.get("Id")) for t in live_set.find("Tracks") if t.get("Id")]
        self.next_track = max(track_ids + [0]) + 1
        pointees = [int(e.get("Id")) for e in live_set.iter() if is_pointee(e)]
        self.next_pointee = max([int(val(live_set, "NextPointeeId", "0"))] + [p + 1 for p in pointees])

    def track(self):
        self.next_track += 1
        return str(self.next_track - 1)

    def pointee(self):
        self.next_pointee += 1
        return str(self.next_pointee - 1)

    def save(self):
        setv(self.live_set, "NextPointeeId", self.next_pointee)


def group_template(root, live_set):
    """Copy of an existing group in this set, or the built-in Live 11 one."""
    for t in live_set.find("Tracks"):
        if t.tag == "GroupTrack":
            return copy.deepcopy(t), "copied from a group in this set"
    if root.get("MinorVersion", "").startswith("11."):
        xml = zlib.decompress(base64.b64decode(GROUP_TEMPLATE_LIVE11))
        return ET.fromstring(xml), "built-in Live 11 group"
    return None, None


def make_group(template, name, color, ids, n_returns):
    g = copy.deepcopy(template)
    g.set("Id", ids.track())
    for e in g.iter():                                  # fresh automation/modulation ids
        if is_pointee(e):
            e.set("Id", ids.pointee())
    setv(g, "Name/UserName", name)
    setv(g, "Name/EffectiveName", name)
    setv(g, "Name/Annotation", "")
    set_color(g, color)
    setv(g, "TrackGroupId", "-1")
    setv(g, "TrackUnfolded", "true")
    setv(g, "LinkedTrackGroupId", "-1")
    for path in ("AutomationEnvelopes/Envelopes", "DeviceChain/DeviceChain/Devices"):
        holder = g.find(path)
        if holder is not None:
            for child in list(holder):
                holder.remove(child)
    out = g.find("DeviceChain/AudioOutputRouting")
    if out is not None:
        setv(out, "Target", "AudioOut/Master")
        setv(out, "UpperDisplayString", "Master")
        setv(out, "LowerDisplayString", "")
    mixer = g.find("DeviceChain/Mixer")
    if mixer is not None:
        setv(mixer, "Volume/Manual", "1")
        setv(mixer, "Pan/Manual", "0")
        setv(mixer, "Speaker/Manual", "true")
        setv(mixer, "On/Manual", "true")
        setv(mixer, "SoloSink", "false")
        sends = mixer.find("Sends")
        if sends is not None:
            proto = list(sends)[0] if len(sends) else None
            for child in list(sends):
                sends.remove(child)
            for i in range(n_returns if proto is not None else 0):
                h = copy.deepcopy(proto)
                h.set("Id", str(i))
                for e in h.iter():
                    if is_pointee(e):
                        e.set("Id", ids.pointee())
                setv(h, "Send/Manual", val(h, "Send/MidiControllerRange/Min", "0.0003162277571"))
                sends.append(h)
    return g


def route_to_group(track):
    out = track.find("DeviceChain/AudioOutputRouting")
    if out is not None and val(out, "Target") == "AudioOut/Master":
        setv(out, "Target", "AudioOut/GroupTrack")
        setv(out, "UpperDisplayString", "Group")
        setv(out, "LowerDisplayString", "")


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
    n_returns = len([t for t in others if t.tag == "ReturnTrack"])
    by_id = dict((t.get("Id"), t) for t in all_tracks)
    parent = dict((t.get("Id"), val(t, "TrackGroupId", "-1")) for t in all_tracks)

    def kids(pid):
        return [t for t in all_tracks if parent[t.get("Id")] == pid]

    heard, listened = {}, {}
    if args.listen:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import listen_als
        print("\nListening to %s ..." % os.path.basename(path))
        listened = listen_als.listen(root, path, args.samples,
                                     progress=lambda n: print("   " + n[:70]))
        heard = dict((tid, info["verdict"]) for tid, info in listened.items() if info["verdict"])

    role, why, empty = classify(all_tracks, heard)

    # where the audio disagrees with a name you typed (your name still wins — you decide)
    checks = []
    for t in all_tracks:
        h = heard.get(t.get("Id"))
        if not h or h["source"] != "audio" or h["conf"] == "low":
            continue
        name = display_name(t)
        toks = tokens(name)
        if role[t.get("Id")] != h["role"]:
            checks.append("'%s' is %s by its name, but sounds like %s (%s)"
                          % (name, role[t.get("Id")], h["label"], h["role"]))
        elif ("closed" in toks and h["label"] == "open hats") or \
                ("open" in toks and h["label"] == "closed hats"):
            checks.append("'%s' sounds like %s" % (name, h["label"]))

    # 4. put loose drum / synth tracks into one group per role
    ids = Ids(live_set)
    grouped_note = []
    template, template_src = None, None
    if not args.no_group:
        for r in AUTO_GROUP_ROLES:
            loose = [t for t in all_tracks if t.tag != "GroupTrack"
                     and parent[t.get("Id")] == "-1" and role[t.get("Id")] == r]
            existing = [g for g in all_tracks if g.tag == "GroupTrack"
                        and parent[g.get("Id")] == "-1" and role[g.get("Id")] == r]
            if not loose or (not existing and len(loose) < MIN_TRACKS_TO_GROUP):
                continue
            if existing:
                g = existing[0]
                grouped_note.append("%d loose %s track(s) moved into existing group '%s'"
                                    % (len(loose), r.lower(), display_name(g)))
            else:
                if template is None:
                    template, template_src = group_template(root, live_set)
                if template is None:
                    grouped_note.append("couldn't make a %s group: this set has no group to copy. "
                                        "Group any two tracks in Live (Cmd+G), save, and run again." % r)
                    continue
                g = make_group(template, r + GROUP_SUFFIX, ROLE_COLOR[r], ids, n_returns)
                all_tracks.insert(all_tracks.index(loose[0]), g)
                gid = g.get("Id")
                by_id[gid] = g
                parent[gid] = "-1"
                role[gid] = r
                grouped_note.append("new group '%s' made for %d %s track(s) (%s)"
                                    % (r + GROUP_SUFFIX, len(loose), r.lower(), template_src))
            for t in loose:
                setv(t, "TrackGroupId", g.get("Id"))
                parent[t.get("Id")] = g.get("Id")
                route_to_group(t)
    ids.save()

    def top_group(tid):
        top = None
        p = parent[tid]
        while p != "-1":
            top = p
            p = parent.get(p, "-1")
        return top

    # 5. rename + colour (everything in a group takes the group's colour, clips included)
    counters = Counter()
    changes = {}
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
                h = heard.get(tid)
                if (not base or GENERIC.match(base)) and h and h["role"] == r:
                    new = h["label"].upper()            # named after what it sounds like
                elif not base or GENERIC.match(base):
                    counters[r] += 1
                    new = "%s %d" % (r, counters[r])
                else:
                    new = "%s %s" % (r, base) if args.prefix else base
            elif args.prefix and not user.upper().startswith(r):
                new = "%s %s" % (r, user)
            candidate, n = new, 2
            while candidate.upper() in used_names and candidate != old:
                candidate = "%s %d" % (new, n)
                n += 1
            new = candidate
            used_names.add(new.upper())
            if new != old:
                setv(t, "Name/UserName", new)
                setv(t, "Name/EffectiveName", new)
        old_col = get_color(t)
        top = top_group(tid)
        if top and not args.track_colors:
            colour = ROLE_COLOR[role[top]]      # everything in a group wears the group's colour
        else:
            colour = ROLE_COLOR[r]              # each track wears its own role's colour
        if not args.no_color:
            set_color(t, colour)
            if not args.no_color_clips:
                for c in clips(t):
                    set_color(c, colour)
        changes[tid] = (r, old, new, old_col, get_color(t), len(clips(t)))

    # 6. reorder: sort siblings by role (and kick -> snare -> hats ... inside a role),
    #    keeping groups' members right after them
    order = []
    role_words = dict((r[0], r[2]) for r in ROLES)

    def sort_key(t):
        r = role[t.get("Id")]
        words = role_words.get(r, [])
        sub = len(words)
        if t.tag != "GroupTrack":
            for tk in tokens(display_name(t)):
                if tk in words:
                    sub = min(sub, words.index(tk))
        return (ROLE_ORDER.index(r), sub)

    def emit(pid):
        sibs = kids(pid)
        if not args.no_reorder:
            sibs = sorted(sibs, key=sort_key)  # stable
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

    eq_notes = []
    if args.eq:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import eq_als
        eq_notes = eq_als.apply_eq(root, role)

    # report
    print("\n%s  (%s)" % (os.path.basename(path), creator))
    print("-" * 78)
    depth = {}
    n_clips = 0
    for t in order:
        tid = t.get("Id")
        p = parent[tid]
        depth[tid] = 0 if p == "-1" else depth.get(p, 0) + 1
        r, old, new, _, _, nc = changes[tid]
        n_clips += nc
        indent = "   " * depth[tid]
        kind = "[group] " if t.tag == "GroupTrack" else ""
        arrow = ("%-26s -> %s" % (old[:26], new)) if old != new else new
        print("%s%s%-7s %s" % (indent, kind, r, arrow))
        if args.verbose and why.get(tid):
            print("%s        clues: %s" % (indent, ", ".join(why[tid][:6])))
        h = heard.get(tid)
        if args.verbose and h:
            print("%s        heard: %s — %s" % (indent, h["label"], "; ".join(h["why"])))
    for note in grouped_note:
        print("\n* " + note)
    if not args.no_color and not args.no_color_clips:
        print("* %d clip(s) recoloured to match their track/group" % n_clips)
    if args.listen:
        n_audio = len([h for h in heard.values() if h["source"] == "audio"])
        n_notes = len(heard) - n_audio
        print("* listened to %d track(s); %d plugin-synth track(s) judged from MIDI notes only"
              % (n_audio, n_notes))
        missing = sorted(set(m for info in listened.values() for m in info["missing"]))
        broken = sorted(set("%s (%s)" % (f, e) for info in listened.values()
                            for f, _, e, _ in info["files"] if e))
        if checks:
            print("\nCheck these — the audio disagrees with the name (the name was kept):")
            for c in checks:
                print("   - " + c)
        if missing:
            print("\nSamples not found, so not heard (try --samples <folder>):")
            for m in missing:
                print("   - " + m)
        if broken:
            print("\nCouldn't read:")
            for b in broken:
                print("   - " + b)
    if eq_notes:
        print("\nCleanup EQ:")
        for note in eq_notes:
            print("   " + note)
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
    problems = check(out)
    if problems:
        os.remove(out)
        raise RuntimeError("output failed safety checks, nothing saved:\n  " + "\n  ".join(problems))
    print("\nSaved: %s" % out)
    return out


def check(path):
    """Re-read the saved set and make sure its structure is consistent."""
    with gzip.open(path, "rb") as f:
        ls = ET.fromstring(f.read()).find("LiveSet")
    problems = []
    tracks = list(ls.find("Tracks"))
    tids = [t.get("Id") for t in tracks]
    if len(tids) != len(set(tids)):
        problems.append("duplicate track ids")
    pids = [e.get("Id") for e in ls.iter() if is_pointee(e)]
    if len(pids) != len(set(pids)):
        problems.append("duplicate automation ids")
    if pids and max(int(p) for p in pids) >= int(val(ls, "NextPointeeId", "0")):
        problems.append("NextPointeeId too low")
    groups = set(t.get("Id") for t in tracks if t.tag == "GroupTrack")
    n_returns = len([t for t in tracks if t.tag == "ReturnTrack"])
    open_groups = []                       # groups whose members we're currently inside
    for t in tracks:
        if t.tag == "ReturnTrack":
            continue
        g = val(t, "TrackGroupId", "-1")
        if g != "-1" and g not in groups:
            problems.append("'%s' points at a missing group" % display_name(t))
        while open_groups and open_groups[-1] != g:
            open_groups.pop()
        if g != "-1" and not open_groups:
            problems.append("'%s' is not directly under its group" % display_name(t))
        if t.tag == "GroupTrack":
            open_groups.append(t.get("Id"))
        sends = t.find("DeviceChain/Mixer/Sends")
        if sends is not None and len(sends) != n_returns:
            problems.append("'%s' has %d sends for %d returns" % (display_name(t), len(sends), n_returns))
    return problems


def main():
    ap = argparse.ArgumentParser(description="Organize Ableton Live Sets: group, name, colour and order tracks by role.")
    ap.add_argument("files", nargs="+", help=".als file(s)")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, don't save")
    ap.add_argument("--no-group", action="store_true", help="don't make new groups")
    ap.add_argument("--no-rename", action="store_true", help="leave track names alone")
    ap.add_argument("--no-color", action="store_true", help="leave all colours alone")
    ap.add_argument("--no-color-clips", action="store_true", help="colour tracks but leave clip colours alone")
    ap.add_argument("--no-reorder", action="store_true", help="leave track order alone")
    ap.add_argument("--prefix", action="store_true",
                    help="also prefix names you typed yourself, e.g. 'kick' -> 'DRUMS kick'")
    ap.add_argument("--listen", action="store_true",
                    help="listen to the samples (and read plugin synths' MIDI) to tell what each track is "
                         "— see listen_als.py")
    ap.add_argument("--samples", action="append", default=[], metavar="DIR",
                    help="with --listen: extra folder to look in for samples that moved (can repeat)")
    ap.add_argument("--track-colors", action="store_true",
                    help="colour each track by its own role, even inside a group of another role")
    ap.add_argument("--eq", action="store_true",
                    help="also add a cleanup EQ Eight (low cuts by role) — see eq_als.py")
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