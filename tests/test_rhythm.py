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


def test_clef_change_requires_sustained_register():
    normalized = normalize(
        [RawNote(str(index), 45, index * 2, index * 2 + 1, hand="right") for index in range(6)],
        120,
        "4/4",
    )
    assert {item["clef"] for item in normalized["clefs"]} == {"bass"}


def test_dense_overlaps_are_limited_to_four_notation_voices():
    dense = [
        RawNote(str(index), 60 + index, index * 0.05, 3.0, hand="right") for index in range(12)
    ]
    result = normalize(dense, 120, "4/4")
    assert max(note["voice"] for note in result["notes"]) <= 4
    assert all(note["duration_quarters"] > 0 for note in result["notes"])


def test_cross_bar_tuplet_is_nudged_to_renderable_fragments():
    result = normalize([RawNote("cross", 60, 6.833333, 7.375, hand="right")], 120, "2/4")
    note = result["notes"][0]
    start = round(note["onset_quarters"] * 24)
    duration = round(note["duration_quarters"] * 24)
    first_fragment = 48 - start % 48
    assert first_fragment in {3, 6, 9, 12, 18, 24, 36, 48}
    assert duration - first_fragment in {3, 6, 9, 12, 18, 24, 36, 48}
