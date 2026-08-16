from pathlib import Path

import cv2
import numpy as np

from notatior.detection import detect_notes


def test_color_change_becomes_note(tmp_path: Path):
    video = tmp_path / "keys.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 30, (240, 100))
    assert writer.isOpened()
    for frame_index in range(100):
        frame = np.full((100, 240, 3), 20, dtype=np.uint8)
        cv2.rectangle(frame, (50, 20), (90, 95), (235, 235, 235), -1)
        if 45 <= frame_index < 75:
            cv2.rectangle(frame, (50, 20), (90, 95), (30, 30, 230), -1)
        writer.write(frame)
    writer.release()
    calibration = {
        "frame_time": 0,
        "keys": [
            {
                "index": 0,
                "midi": 60,
                "kind": "white",
                "polygon": [[50, 20], [90, 20], [90, 95], [50, 95]],
            }
        ],
    }
    detected = detect_notes(video, calibration)
    assert len(detected) == 1
    assert detected[0].midi == 60
    assert abs(detected[0].onset - 1.5) < 0.1
    assert abs(detected[0].offset - 2.5) < 0.1
