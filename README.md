# als-organizer

Tidies any Ableton Live Set (`.als`) in about a second. It groups, names, colours and orders your tracks by role. It never touches the original: it saves `My Track (organized).als` next to it.

- Loose drum tracks (kick, snare, hats, percs, cymbals, toms…) go into one **DRUMS BUS** group, and loose synth tracks into one **SYNTH BUS**. If the set already has a drum or synth group, they join that instead.
- Each group, every track in it, and every clip (arrangement and session view) all get the same colour.

```
DRUMS   red      kick, hat, clap, perc, 909, drum rack ...
BASS    orange   bass, sub, 303, reese ...
CHORDS  yellow   chord, stab, keys, rhodes, organ ...
SYNTH   green    synth, lead, arp, pluck, seq, Operator, Wavetable ...
ATMOS   blue     pad, drone, texture, field, vinyl, tape, dub ...
VOX     purple   vox, vocal, voice, spoken ...
FX      magenta  fx, riser, sweep, impact, reverse ...
MISC    grey     no clues found
```

Works with Live 10, 11 and 12 sets. It only needs the Python 3 that ships with macOS (Xcode Command Line Tools), with no installs.

## Use

```
python3 organize_als.py "My Track.als"             # organize
python3 organize_als.py "My Track.als" --dry-run   # preview only
python3 organize_als.py "My Track.als" -v          # show why each track got its role
python3 organize_als.py ~/Music/Projects/*/*.als   # a whole folder of sets
```

Tip: type `python3 organize_als.py ` and then drag the .als from Finder into Terminal to fill in the path.

| Option | What it does |
|---|---|
| `--dry-run` | Show the plan, save nothing |
| `--no-group` | Don't make new groups |
| `--no-color-clips` | Colour tracks but leave clip colours alone |
| `--no-rename` / `--no-color` / `--no-reorder` | Skip that step |
| `--prefix` | Also prefix names you typed yourself: `kick` → `DRUMS kick` |
| `--listen` | Listen to the samples to tell what each track is (see below) |
| `--samples DIR` | With `--listen`: another folder to look in for samples that moved |
| `--track-colors` | Colour each track by its own role, even inside a group of another role |
| `-v` | Show the clues behind each decision |

## How it decides

If a track's own name says what it is (`SUB`, `OPEN HATS`), that decides. Otherwise it scores keywords in clip names, sample file names and devices/plugins. A track with no clues inherits its group's role. For example, unnamed recordings inside a group called `MAIN SYNTH` become `SYNTH 1`, `SYNTH 2`. Anything left over is `MISC`.

- **Renaming:** auto-named tracks (`3-Audio`, `Audio 2 [2026-08-19 110949]`) become `SYNTH 1` and so on. Names you typed are kept unless you use `--prefix`. Auto-named groups become e.g. `DRUMS BUS`.
- **Grouping:** roles listed in `AUTO_GROUP_ROLES` (drums and synth by default) get one group each. It takes at least 2 loose tracks to make a new group (`MIN_TRACKS_TO_GROUP`), but a single track still joins an existing group. Grouped tracks are routed into the group. The new group track is copied from a group already in your set. Live 11 sets without one use a built-in template. Live 10/12 sets without any group: group two tracks in Live (Cmd+G), save, and run again.
- **Colour:** a group's colour wins. Everything inside it, clips included, matches the group.
- **Order:** drums → bass → chords → synth → atmos → vox → fx → misc. Inside a group: kick → snare → clap → hats → perc → cymbals → toms. Groups move as a block. Returns and master stay where they are.
- **Safety check:** after saving, the script re-reads the file and checks track ids, automation ids, group membership and sends. If anything is off, it deletes the output and tells you.
- **Empty tracks** (no clips, no devices) are listed so you can delete them. Nothing is deleted automatically.

## Listen to the audio (`--listen`)

Names lie: a track called `CLOSED HAT` can be playing an open-hat loop, and `20-Audio` says nothing at all. With `--listen` the organizer opens the actual sample files and works out what each track sounds like.

```
python3 organize_als.py "My Track.als" --listen                     # organize by sound
python3 organize_als.py "My Track.als" --listen --track-colors -v   # colour every track by its own role, show what it heard
python3 listen_als.py "My Track.als"                                # just the listening report, nothing saved
python3 listen_als.py kick.wav "pad loop.aif" -v                    # single samples, with the measurements
```

It measures where the energy sits (sub, bass, mids, highs), how noisy or tonal the sound is, how many notes ring at once, how often it hits, how fast each hit dies away, whether the pitch drops inside a hit (kick) or holds (bass), and whether it swells up (riser). That gives one of these labels:

| Role | Labels |
|---|---|
| DRUMS | kick, closed hats, open hats, crash, clap/snare, perc, drum loop, drum kit |
| BASS | sub, bass |
| CHORDS | chords, stabs |
| SYNTH | lead, arp |
| ATMOS | pad, drone, noise |
| FX | riser, reverse cymbal |

- **Your typed name still wins.** When the audio disagrees with it, you get a "Check these" list instead (e.g. `'CLOSED HAT' sounds like open hats`, `'PRE CRASH' ... sounds like reverse cymbal (FX)`).
- For tracks without a typed name, the sound is the strongest clue. Auto-named tracks are renamed after what they sound like: `20-Audio` → `RISER`.
- **Plugin synths** (Diva, Serum, Pigments…) make their sound inside Live, so there's no file to hear. Those are judged from their MIDI notes (how low, how many at once, how long, how busy), marked "notes only" and counted for less.
- Samples are found at their saved path, then relative to the set, then by file name anywhere in the project folder. If you moved your sample library, point at it with `--samples ~/Splice` (repeatable).
- Reads WAV (16/24/32-bit, float) and AIFF/AIFC. Long recordings: only the first 90 s are analysed. Still standard library only — no installs.
- All thresholds (`ONE_SHOT_SECONDS`, `OPEN_HAT_DECAY_MS`, `KICK_GLIDE`, `MIDI_BASS_KEY` …) are at the top of `listen_als.py` if your sounds land in the wrong bucket.

`--track-colors` works with or without `--listen`: by default a group's colour wins, so a sub inside a DRUMS group turns red. With `--track-colors` every track (and its clips) wears its own role's colour, and the groups keep theirs.

Tests: `python3 -m unittest discover tests` (synthesizes its own kicks, hats, pads, risers …).

## Cleanup EQ (`eq_als.py`)

A separate script that adds one **EQ Eight named "AUTO EQ"** to each track. It's a starting point that cuts the low end each sound doesn't need so the kick and bass sit cleanly. It isn't a mix.

```
python3 eq_als.py "My Track.als"                 # saves "My Track (eq).als"
python3 eq_als.py "My Track.als" --dry-run       # preview
python3 organize_als.py "My Track.als" --eq      # organize + EQ in one file
```

| Sound | Low cut |
|---|---|
| Kick, bass, sub | 30 Hz (rumble only) |
| Hats, cymbals, crash, shaker | 350 Hz |
| Snare, clap, rim | 150 Hz |
| Perc, toms | 150 Hz |
| Chords / stabs | 150 Hz |
| Synths | 120 Hz |
| Pads / atmos | 200 Hz + high cut 12 kHz |
| Vox | 100 Hz |
| FX | 200 Hz |

- Gentle 12 dB/octave slopes. Every value is a normal EQ Eight band you can change or switch off.
- **Your own EQ always wins:** tracks that already have EQ Eight, EQ Three, Channel EQ, Pro-Q or any plugin with "EQ" in its name are skipped.
- Groups, returns, master, empty tracks and tracks it can't identify (MISC) are skipped.
- Audio tracks get the EQ first in the chain. MIDI tracks get it right after the instrument.
- The EQ device is copied from one already in your set. Live 11 sets without one use a built-in copy. Live 10/12 sets without one: drop an EQ Eight on any track, save, and run again.
- Edit `SOUND_RULES` / `ROLE_RULES` at the top of `eq_als.py` to change the frequencies.

## Make it yours

Everything is in the `ROLES` list at the top of `organize_als.py`: keywords, colours, and the order. Add your own sample-naming habits or synths there. Colour numbers are Live's palette slots (0–69). Row 2 of Live's colour picker is 14–27.

## Right-click in Finder (optional)

1. Open **Automator** → New → **Quick Action**.
2. Set "Workflow receives current" to **files or folders** in **Finder**.
3. Add **Run Shell Script**, set "Pass input" to **as arguments**, and paste:
   ```
   python3 "$HOME/Documents/als-organizer/organize_als.py" "$@"
   ```
4. Save it as **Organize Ableton Set**.

Then right-click any `.als` → Quick Actions → **Organize Ableton Set**.

## Notes

- Live can't open a set saved by a newer version (a Live 11 set won't open in Live 10). The script keeps whatever version the set already was.
- Open the organized set from the same project folder so sample paths still resolve.
- Close the set in Live before organizing it, or Live will overwrite your changes the next time it saves.