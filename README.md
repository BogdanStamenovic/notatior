# Notatior

Notatior turns videos with a visible, color-changing piano keyboard into editable piano notation.
It runs locally, keeps an auditable result for every analysis stage, and exports MusicXML, MIDI,
PDF, and validation diagnostics.

The initial release targets fixed-camera or rendered solo-piano videos on modern x86-64 Linux.
It supports YouTube URLs and local video files. Users are responsible for ensuring they have the
right to download and transcribe source material.

## Development quick start

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/notatior --help
.venv/bin/notatior serve
```

The browser UI binds to `127.0.0.1` by default. Project data is stored under
`$XDG_DATA_HOME/notatior`, normally `~/.local/share/notatior`.

## Ownbox

After this repository is visible to Ownbox's configured GitHub account:

```bash
ownbox sync
ownbox install notatior
notatior
```

Ownbox installs the Python environment and invokes the idempotent dependency bootstrap. Run
`notatior doctor` to inspect the installation.

## Pipeline

The UI guides each project through ingest, keyboard calibration, raw note detection, rhythm
normalization, score construction, audio validation, dynamics, and export. Calibration, score,
and validation are review gates; changing upstream data marks dependent results as stale.

### Headless transcription

```bash
# Stop at each review gate (the project can be continued in the browser)
notatior transcribe ./performance.mp4

# Accept automatically generated drafts and copy finished exports
notatior transcribe 'https://youtu.be/VIDEO_ID' --accept-draft --output ./score

# Lock the global timing model instead of searching for it
notatior transcribe ./performance.mp4 --bpm 120 --meter 4/4 --accept-draft
```

## Review workflow

1. **Ingest** acquires the video, probes it, and extracts mono analysis audio.
2. **Calibration** finds the first keyboard frame. Correct the keyboard bounds, inspect every
   overlay, and set a known MIDI anchor (middle C is `60`) before approving it.
3. **Detection** uses color distance and temporal hysteresis to create editable note events.
4. **Rhythm** searches a fixed BPM, meter, and bar phase, then quantizes to standard/dotted/triplet
   values down to a 32nd note.
5. **Score** assigns hands and voices, infers spelling/key signature, and introduces conservative
   treble/bass clef changes. Edit notes and approve the draft.
6. **Validation** renders the MusicXML through MuseScore and compares aligned energy and chroma
   against the source. Review low-confidence findings and listen to both versions.
7. **Dynamics** maps relative source energy to MIDI velocity, dynamics, and longer hairpins.
8. **Export** produces MusicXML, MIDI, PDF, validation audio/report, and a project archive.

An edit marks only downstream stages stale. Projects are atomic and resumable after interruption.

## Portable toolchain

`notatior bootstrap` downloads checksum-verified x86-64 Linux builds into the checkout's `.tools`
directory. It installs FFmpeg/FFprobe and extracts the official MuseScore AppImage so FUSE and
system packages are not required. The download is intentionally large. Override binaries with
`NOTATIOR_FFMPEG`, `NOTATIOR_FFPROBE`, and `NOTATIOR_MUSESCORE` when necessary.

The first release supports modern x86-64 glibc Linux. It is designed for solo-piano videos with a
fixed visible keybed and clear key-color changes. Scene cuts can be recalibrated in separate
projects; continuously moving/occluded keyboards, pedal, fingering, and exact physical MIDI
velocity are not inferred.

## Tests

```bash
.venv/bin/ruff check src tests
.venv/bin/pytest -q
.venv/bin/python -m pip wheel . --no-deps -w dist
```

The suite includes persistent-state, path-safety, synthetic video/color detection, quantization,
MusicXML/MIDI, audio validation, dynamics, and API smoke tests.
