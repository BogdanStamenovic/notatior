from __future__ import annotations

import os
import subprocess
import wave
from pathlib import Path

from .config import musescore_path


def _read_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as stream:
        rate = stream.getframerate()
        channels = stream.getnchannels()
        width = stream.getsampwidth()
        frames = stream.readframes(stream.getnframes())
    if width != 2:
        raise ValueError("Only 16-bit PCM validation audio is supported")
    values = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        values = values.reshape(-1, channels).mean(axis=1)
    return rate, values


def apply_dynamics(score: dict, source_audio: Path) -> dict:
    import numpy as np

    rate, audio = _read_wav(source_audio)
    bpm = float(score["bpm"])
    energies = []
    for note in score["notes"]:
        center = (float(note["onset_quarters"]) * 60 / bpm) + float(score.get("phase_seconds", 0))
        start = max(0, round((center - 0.01) * rate))
        end = min(len(audio), start + round(0.12 * rate))
        rms = float(np.sqrt(np.mean(audio[start:end] ** 2))) if end > start else 0.0
        energies.append(rms)
    positive = np.asarray([value for value in energies if value > 0], dtype=float)
    low, high = (
        (float(np.percentile(positive, 10)), float(np.percentile(positive, 90)))
        if positive.size
        else (0.0, 1.0)
    )
    span = max(high - low, 1e-6)
    for note, energy in zip(score["notes"], energies):
        normalized = max(0.0, min(1.0, (energy - low) / span))
        note["velocity"] = round(42 + normalized * 58)
    marks = ((48, "p"), (61, "mp"), (76, "mf"), (90, "f"))
    measure_quarters = score["meter"]["numerator"] * 4 / score["meter"]["denominator"]
    dynamics = []
    previous = None
    measure_count = max(
        1, int(max((n["onset_quarters"] for n in score["notes"]), default=0) / measure_quarters) + 1
    )
    for measure in range(measure_count):
        velocities = [
            n["velocity"]
            for n in score["notes"]
            if measure <= n["onset_quarters"] / measure_quarters < measure + 1
        ]
        if not velocities:
            continue
        value = round(float(np.median(velocities)))
        mark = min(marks, key=lambda candidate: abs(candidate[0] - value))[1]
        if mark != previous:
            dynamics.append({"measure": measure, "staff": 1, "mark": mark, "velocity": value})
            previous = mark
    score["dynamics"] = dynamics
    return score


def render_score(musicxml: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    command = [musescore_path(), "--no-webview", "--export-to", str(output), str(musicxml)]
    result = subprocess.run(
        command, capture_output=True, text=True, env=environment, timeout=300, check=False
    )
    if result.returncode or not output.exists():
        raise RuntimeError(f"MuseScore export failed: {result.stderr[-1200:]}")
    return output


def validate_audio(score: dict, source_audio: Path, rendered_audio: Path) -> dict:
    import numpy as np

    source_rate, source = _read_wav(source_audio)
    render_rate, rendered = _read_wav(rendered_audio)
    if source_rate != render_rate:
        length = round(len(rendered) * source_rate / render_rate)
        rendered = np.interp(
            np.linspace(0, len(rendered) - 1, length), np.arange(len(rendered)), rendered
        )
    hop = 2048
    source_env = np.asarray(
        [np.sqrt(np.mean(source[i : i + hop] ** 2)) for i in range(0, len(source) - hop, hop)]
    )
    render_env = np.asarray(
        [np.sqrt(np.mean(rendered[i : i + hop] ** 2)) for i in range(0, len(rendered) - hop, hop)]
    )
    size = min(len(source_env), len(render_env))
    if size < 2:
        correlation = 0.0
    else:
        a, b = source_env[:size], render_env[:size]
        correlation = float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else 0.0
    findings = []
    for note in score["notes"]:
        if float(note.get("confidence", 1)) < 0.55:
            findings.append(
                {"note_id": note["id"], "kind": "low_visual_confidence", "severity": "warning"}
            )
    return {
        "status": "review" if findings or correlation < 0.55 else "pass",
        "envelope_correlation": round(correlation, 4),
        "source_duration": round(len(source) / source_rate, 3),
        "rendered_duration": round(len(rendered) / source_rate, 3),
        "findings": findings,
        "method": "aligned RMS envelope; pitch/timing findings retain visual confidence evidence",
    }
