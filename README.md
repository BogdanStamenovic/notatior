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

This project is under active development. See `notatior --help` for the currently implemented
commands and options.
