from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .models import KeyRegion

BLACK_PCS = {1, 3, 6, 8, 10}
WHITE_MIDI = [midi for midi in range(21, 109) if midi % 12 not in BLACK_PCS]


class VisionError(RuntimeError):
    pass


def _imports():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VisionError("Video analysis requires numpy and opencv-python-headless") from exc
    return cv2, np


def _runs(mask) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for index, enabled in enumerate(mask.tolist() + [False]):
        if enabled and start is None:
            start = index
        elif not enabled and start is not None:
            runs.append((start, index))
            start = None
    return runs


def find_keyboard(video: Path, analysis_dir: Path) -> dict:
    cv2, np = _imports()
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise VisionError(f"Cannot open {video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps if frame_count else 60.0
    candidates: list[tuple[float, float, tuple[int, int, int, int], object]] = []
    for timestamp in np.linspace(0, min(duration, 45.0), num=min(24, max(6, int(duration / 2)))):
        capture.set(cv2.CAP_PROP_POS_MSEC, float(timestamp) * 1000)
        ok, frame = capture.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        bright = (gray > 125).mean(axis=1)
        textured = np.abs(np.diff(gray.astype(np.float32), axis=1)).mean(axis=1) / 255.0
        row_score = bright * 0.7 + textured * 0.3
        mask = row_score > max(0.20, float(np.percentile(row_score, 72)))
        for top, bottom in _runs(mask):
            band_h = bottom - top
            if band_h < height * 0.055 or band_h > height * 0.35:
                continue
            if bottom < height * 0.45:
                continue
            roi = gray[top:bottom]
            coverage = float((roi.mean(axis=0) > 80).mean())
            vertical = float(
                np.percentile(np.abs(np.diff(roi.astype(float), axis=1)).mean(axis=0), 80)
            )
            score = coverage + vertical / 80.0 + bottom / height * 0.15
            candidates.append((score, float(timestamp), (0, top, width, bottom), frame.copy()))
    capture.release()
    if not candidates:
        raise VisionError(
            "No stable keyboard-like bright band was found; manual calibration is required"
        )
    score, timestamp, bounds, frame = max(candidates, key=lambda item: item[0])
    left, top, right, bottom = bounds
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    row_coverage = (gray > 80).mean(axis=1)
    expanded_top = top
    misses = 0
    minimum_row = max(0, bottom - round(frame.shape[0] * 0.34))
    for row in range(top - 1, minimum_row - 1, -1):
        if row_coverage[row] >= 0.28:
            expanded_top = row
            misses = 0
        else:
            misses += 1
            if misses >= 3:
                break
    bounds = (left, expanded_top, right, bottom)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    frame_path = analysis_dir / "calibration.jpg"
    cv2.imwrite(str(frame_path), frame)
    keys = segment_keys(frame, bounds)
    return {
        "frame_time": timestamp,
        "frame": str(frame_path.name),
        "bounds": list(bounds),
        "confidence": min(1.0, score / 2.0),
        "first_midi": keys[0].midi if keys else None,
        "keys": [asdict(key) for key in keys],
        "approved": False,
    }


def _peak_positions(values, minimum_distance: int, threshold: float):
    peaks: list[int] = []
    order = values.argsort()[::-1]
    for raw in order:
        position = int(raw)
        if values[position] < threshold:
            break
        if all(abs(position - old) >= minimum_distance for old in peaks):
            peaks.append(position)
    return sorted(peaks)


def segment_keys(
    frame,
    bounds: tuple[int, int, int, int],
    first_midi: int | None = None,
    white_key_count: int | None = None,
) -> list[KeyRegion]:
    cv2, np = _imports()
    left, top, right, bottom = bounds
    roi = frame[top:bottom, left:right]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    lower = gray[int(height * 0.55) :]
    edge = np.abs(np.diff(lower.astype(np.float32), axis=1)).mean(axis=0)
    expected = max(8, int(width / 52))
    peaks = _peak_positions(edge, max(3, expected // 2), float(np.percentile(edge, 82)))
    boundaries = [0] + [p for p in peaks if expected * 0.45 < p < width - expected * 0.45] + [width]
    widths = np.diff(boundaries)
    if white_key_count is not None:
        if not 7 <= white_key_count <= 75:
            raise VisionError("White-key count must be between 7 and 75")
        boundaries = [round(i * width / white_key_count) for i in range(white_key_count + 1)]
    elif len(boundaries) < 22 or len(boundaries) > 62 or np.median(widths) <= 0:
        white_count = (
            52 if width / max(height, 1) > 4.5 else max(14, round(width / max(height * 0.32, 1)))
        )
        boundaries = [round(i * width / white_count) for i in range(white_count + 1)]
    else:
        median = float(np.median(widths))
        filtered = [boundaries[0]]
        for boundary in boundaries[1:]:
            if boundary - filtered[-1] >= median * 0.55:
                filtered.append(boundary)
        boundaries = filtered
    white_count = len(boundaries) - 1
    if first_midi is None:
        first_midi = 21 if white_count == 52 else 60 - (white_count // 2 * 2)
        while first_midi % 12 in BLACK_PCS:
            first_midi += 1
    white_midis: list[int] = []
    midi = first_midi
    while len(white_midis) < white_count:
        if midi % 12 not in BLACK_PCS:
            white_midis.append(midi)
        midi += 1
    keys: list[KeyRegion] = []
    for index, (x0, x1, midi) in enumerate(zip(boundaries, boundaries[1:], white_midis)):
        polygon = [[left + x0, top], [left + x1, top], [left + x1, bottom], [left + x0, bottom]]
        keys.append(KeyRegion(index=index, midi=midi, kind="white", polygon=polygon))
        next_midi = midi + 1
        if next_midi % 12 in BLACK_PCS and index < white_count - 1:
            center = left + x1
            black_width = max(3, round((x1 - x0) * 0.58))
            black_polygon = [
                [center - black_width // 2, top],
                [center + black_width // 2, top],
                [center + black_width // 2, top + round(height * 0.62)],
                [center - black_width // 2, top + round(height * 0.62)],
            ]
            keys.append(KeyRegion(index=0, midi=next_midi, kind="black", polygon=black_polygon))
    keys.sort(key=lambda key: key.midi)
    for index, key in enumerate(keys):
        key.index = index
    return keys


def update_pitch_anchor(calibration: dict, key_index: int, midi: int) -> dict:
    delta = midi - int(calibration["keys"][key_index]["midi"])
    for key in calibration["keys"]:
        key["midi"] = int(key["midi"]) + delta
    calibration["first_midi"] = calibration["keys"][0]["midi"]
    return calibration


def polygon_sample(frame, polygon: list[list[float]], kind: str):
    _, np = _imports()
    points = np.asarray(polygon, dtype=np.int32)
    x0, y0 = points.min(axis=0)
    x1, y1 = points.max(axis=0)
    width, height = x1 - x0, y1 - y0
    if kind == "white":
        y0 = y0 + int(height * 0.72)
        x0 = x0 + max(1, int(width * 0.20))
        x1 = x1 - max(1, int(width * 0.20))
    else:
        x0 = x0 + max(1, int(width * 0.20))
        x1 = x1 - max(1, int(width * 0.20))
        y0 = y0 + max(1, int(height * 0.18))
        y1 = y1 - max(1, int(height * 0.18))
    crop = frame[max(0, y0) : max(y0 + 1, y1), max(0, x0) : max(x0 + 1, x1)]
    if crop.size == 0:
        return np.zeros(3, dtype=np.float32)
    return np.median(crop.reshape(-1, 3), axis=0).astype(np.float32)
