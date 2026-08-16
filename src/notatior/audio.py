from __future__ import annotations

import os
import subprocess
import wave
from itertools import pairwise
from pathlib import Path

from .config import ffmpeg_path, musescore_path


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
    hairpins = []
    for left, right in pairwise(dynamics):
        distance = right["measure"] - left["measure"]
        delta = right["velocity"] - left["velocity"]
        if distance >= 2 and abs(delta) >= 12:
            hairpins.append(
                {
                    "start_measure": left["measure"],
                    "end_measure": right["measure"],
                    "staff": 1,
                    "type": "crescendo" if delta > 0 else "diminuendo",
                }
            )
    score["hairpins"] = hairpins
    return score


def render_score(musicxml: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    musescore_output = output
    if output.suffix.lower() == ".wav":
        musescore_output = output.with_suffix(".render.mp3")
    command = [musescore_path(), "--export-to", str(musescore_output), str(musicxml)]
    result = subprocess.run(
        command, capture_output=True, text=True, env=environment, timeout=300, check=False
    )
    if result.returncode or not musescore_output.exists():
        raise RuntimeError(f"MuseScore export failed: {result.stderr[-1200:]}")
    if musescore_output != output:
        conversion = subprocess.run(
            [
                ffmpeg_path(),
                "-y",
                "-i",
                str(musescore_output),
                "-ac",
                "1",
                "-ar",
                "22050",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        musescore_output.unlink(missing_ok=True)
        if conversion.returncode or not output.exists():
            raise RuntimeError(f"Rendered-audio conversion failed: {conversion.stderr[-1200:]}")
    return output


def validate_audio(score: dict, source_audio: Path, rendered_audio: Path) -> dict:
    import numpy as np
    from scipy.signal import correlate, correlation_lags

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
    if len(source_env) and len(render_env) and source_env.std() and render_env.std():
        cross = correlate(
            source_env - np.mean(source_env),
            render_env - np.mean(render_env),
            mode="full",
            method="fft",
        )
        lags = correlation_lags(len(source_env), len(render_env), mode="full")
        lag = int(lags[int(np.argmax(cross))])
    else:
        lag = 0
    source_start = max(0, lag)
    render_start = max(0, -lag)
    size = min(len(source_env) - source_start, len(render_env) - render_start)
    if size < 2:
        correlation = 0.0
    else:
        a = source_env[source_start : source_start + size]
        b = render_env[render_start : render_start + size]
        correlation = float(np.corrcoef(a, b)[0, 1]) if a.std() and b.std() else 0.0

    def chroma(signal):
        window_size = 4096
        window = np.hanning(window_size)
        frequencies = np.fft.rfftfreq(window_size, 1 / source_rate)
        valid = frequencies >= 27.5
        pitch_classes = np.zeros(len(frequencies), dtype=int)
        pitch_classes[valid] = np.mod(
            np.rint(69 + 12 * np.log2(frequencies[valid] / 440.0)).astype(int), 12
        )
        frames = []
        for index in range(0, len(signal) - window_size, hop):
            spectrum = np.abs(np.fft.rfft(signal[index : index + window_size] * window))
            vector = np.asarray(
                [spectrum[valid & (pitch_classes == pc)].sum() for pc in range(12)],
                dtype=float,
            )
            norm = np.linalg.norm(vector)
            frames.append(vector / norm if norm else vector)
        return np.asarray(frames)

    source_chroma = chroma(source)
    render_chroma = chroma(rendered)
    chroma_size = min(len(source_chroma) - source_start, len(render_chroma) - render_start)
    if chroma_size > 0:
        source_slice = source_chroma[source_start : source_start + chroma_size]
        render_slice = render_chroma[render_start : render_start + chroma_size]
        frame_similarity = np.sum(source_slice * render_slice, axis=1)
        chroma_similarity = float(np.mean(frame_similarity))
        stride = max(1, len(frame_similarity) // 300)
        heatmap = [round(float(value), 3) for value in frame_similarity[::stride]]
    else:
        chroma_similarity = 0.0
        heatmap = []
    findings = []
    for note in score["notes"]:
        if float(note.get("confidence", 1)) < 0.55:
            findings.append(
                {"note_id": note["id"], "kind": "low_visual_confidence", "severity": "warning"}
            )
    return {
        "status": "review" if findings or correlation < 0.55 or chroma_similarity < 0.5 else "pass",
        "envelope_correlation": round(correlation, 4),
        "chroma_similarity": round(chroma_similarity, 4),
        "alignment_seconds": round(lag * hop / source_rate, 4),
        "similarity_timeline": heatmap,
        "source_duration": round(len(source) / source_rate, 3),
        "rendered_duration": round(len(rendered) / source_rate, 3),
        "findings": findings,
        "method": "FFT-aligned RMS envelope and 12-bin chroma similarity",
    }
