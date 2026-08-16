from itertools import pairwise

from notatior.models import RawNote
from notatior.rhythm import normalize, rank_tempos


def notes():
    return [
        RawNote("a", 48, 0.0, 0.5),
        RawNote("b", 60, 0.0, 1.0),
        RawNote("c", 64, 0.5, 1.0),
        RawNote("d", 67, 1.0, 1.5),
        RawNote("e", 52, 1.5, 2.0),
    ]


def test_requested_tempo_and_meter_quantize_cleanly():
    result = normalize(notes(), 120, "4/4")
    assert result["bpm"] == 120
    assert result["meter"]["numerator"] == 4
    assert [note["onset_quarters"] for note in result["notes"]] == [0, 0, 1, 2, 3]
    assert all(note["duration_quarters"] >= 1 for note in result["notes"])
    assert {note["hand"] for note in result["notes"]} == {"left", "right"}


def test_tempo_ranking_returns_distinct_review_candidates():
    candidates = rank_tempos(notes())
    assert len(candidates) == 5
    assert candidates == sorted(
        candidates, key=lambda item: (item["score"], abs(item["bpm"] - 100))
    )
    assert all(abs(a["bpm"] - b["bpm"]) >= 3 for a, b in pairwise(candidates))
