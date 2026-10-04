# als-organizer

Tidies any Ableton Live Set (`.als`) in about a second. It names, colours and orders your tracks by role. It never touches the original: it saves `My Track (organized).als` next to it.

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
| `--no-rename` / `--no-color` / `--no-reorder` | Skip that step |
| `--prefix` | Also prefix names you typed yourself: `kick` → `DRUMS kick` |
| `--color-clips` | Recolour clips to match their track |
| `-v` | Show the clues behind each decision |

## How it decides

For each track it scores keywords found in the track name, clip names, sample file names and devices/plugins. Names count most and devices count least. A track with no clues inherits its group's role. For example, unnamed recordings inside a group called `MAIN SYNTH` become `SYNTH 1`, `SYNTH 2`. Anything left over is `MISC`.

- **Renaming:** auto-named tracks (`3-Audio`, `Audio 2 [2026-08-19 110949]`) become `SYNTH 1` and so on. Names you typed are kept unless you use `--prefix`. Auto-named groups become e.g. `DRUMS BUS`.
- **Order:** drums → bass → chords → synth → atmos → vox → fx → misc. Groups move as a block and their tracks are sorted inside them. Returns and master stay where they are.
- **Empty tracks** (no clips, no devices) are listed so you can delete them. Nothing is deleted automatically.

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
