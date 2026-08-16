from __future__ import annotations

import uuid
from pathlib import Path

from .models import RawNote
from .vision import VisionError, polygon_sample


def detect_notes(video: Path, calibration: dict, progress=None) -> list[RawNote]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VisionError("Note detection requires numpy and opencv-python-headless") from exc
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise VisionError(f"Cannot open {video}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    keys = calibration["keys"]
    baseline_samples: list[list[object]] = [[] for _ in keys]
    capture.set(cv2.CAP_PROP_POS_MSEC, float(calibration.get("frame_time", 0)) * 1000)
    for _ in range(max(12, round(fps * 1.2))):
        ok, frame = capture.read()
        if not ok:
            break
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        for index, key in enumerate(keys):
            baseline_samples[index].append(polygon_sample(lab, key["polygon"], key["kind"]))
    baselines = [
        np.median(np.stack(samples), axis=0) if samples else np.zeros(3, dtype=np.float32)
        for samples in baseline_samples
    ]
    noise = [
        float(np.median([np.linalg.norm(sample - base) for sample in samples])) if samples else 0.0
        for samples, base in zip(baseline_samples, baselines)
    ]
    thresholds = [max(10.0, value * 5.0 + 4.0) for value in noise]
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    active_count = [0] * len(keys)
    inactive_count = [0] * len(keys)
    active_since: list[float | None] = [None] * len(keys)
    peak_distance = [0.0] * len(keys)
    events: list[RawNote] = []
    frame_index = 0
    attack_frames = 2
    release_frames = 2
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        timestamp = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
        if timestamp <= 0:
            timestamp = frame_index / fps
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        for index, key in enumerate(keys):
            sample = polygon_sample(lab, key["polygon"], key["kind"])
            distance = float(np.linalg.norm(sample - baselines[index]))
            is_active = distance >= thresholds[index]
            if is_active:
                active_count[index] += 1
                inactive_count[index] = 0
                peak_distance[index] = max(peak_distance[index], distance)
                if active_since[index] is None and active_count[index] >= attack_frames:
                    active_since[index] = max(0.0, timestamp - (attack_frames - 1) / fps)
            else:
                inactive_count[index] += 1
                active_count[index] = 0
                if active_since[index] is not None and inactive_count[index] >= release_frames:
                    offset = max(
                        active_since[index] + 1 / fps, timestamp - (release_frames - 1) / fps
                    )
                    confidence = min(1.0, peak_distance[index] / max(thresholds[index] * 2.0, 1.0))
                    events.append(
                        RawNote(
                            id=uuid.uuid4().hex[:12],
                            midi=int(key["midi"]),
                            onset=active_since[index],
                            offset=offset,
                            confidence=confidence,
                            hand=key.get("hand"),
                            evidence={"peak_color_distance": round(peak_distance[index], 3)},
                        )
                    )
                    active_since[index] = None
                    peak_distance[index] = 0.0

        frame_index += 1
        if progress and frame_index % max(1, round(fps)) == 0:
            progress(min(0.99, frame_index / max(frame_count, 1)))
    duration = frame_index / fps
    for index, onset in enumerate(active_since):
        if onset is not None:
            events.append(
                RawNote(
                    id=uuid.uuid4().hex[:12],
                    midi=int(keys[index]["midi"]),
                    onset=onset,
                    offset=duration,
                    confidence=0.5,
                    hand=keys[index].get("hand"),
                )
            )
    capture.release()
    events.sort(key=lambda note: (note.onset, note.midi))
    return events
