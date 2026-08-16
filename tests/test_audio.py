import math
import struct
import wave
from pathlib import Path

from notatior.audio import apply_dynamics, validate_audio


def _wav(path: Path, amplitude: float, seconds: float = 2.0, rate: int = 22050):
    samples = [
        int(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / rate))
        for i in range(round(seconds * rate))
    ]
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def test_dynamics_and_validation(tmp_path: Path):
    source = tmp_path / "source.wav"
    rendered = tmp_path / "rendered.wav"
    _wav(source, 0.7)
    _wav(rendered, 0.5)
    score = {
        "bpm": 120,
        "phase_seconds": 0,
        "meter": {"numerator": 4, "denominator": 4},
        "notes": [
            {"id": "a", "midi": 69, "onset_quarters": 0, "duration_quarters": 1, "confidence": 1},
            {"id": "b", "midi": 69, "onset_quarters": 2, "duration_quarters": 1, "confidence": 0.4},
        ],
    }
    apply_dynamics(score, source)
    report = validate_audio(score, source, rendered)
    assert all(1 <= note["velocity"] <= 127 for note in score["notes"])
    assert score["dynamics"]
    assert report["envelope_correlation"] > 0.99
    assert report["findings"][0]["note_id"] == "b"
