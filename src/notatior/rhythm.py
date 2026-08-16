from __future__ import annotations

import math
from dataclasses import asdict
from itertools import pairwise
from statistics import median

from .models import RawNote, ScoreNote

TICKS_PER_QUARTER = 24
METERS = ((2, 4), (3, 4), (4, 4), (6, 8))


def _snap(value: float, grid: int = TICKS_PER_QUARTER) -> float:
    return round(value * grid) / grid


def _candidate_score(notes: list[RawNote], bpm: float, phase_seconds: float) -> float:
    quarter = 60.0 / bpm
    errors = []
    complexity = 0.0
    for note in notes:
        onset = (note.onset - phase_seconds) / quarter
        duration = (note.offset - note.onset) / quarter
        snapped_onset = _snap(onset)
        snapped_duration = max(3 / TICKS_PER_QUARTER, _snap(duration))
        errors.append(abs(onset - snapped_onset) + abs(duration - snapped_duration) * 0.45)
        ticks = round(snapped_duration * TICKS_PER_QUARTER)
        if ticks % 6:
            complexity += 0.025
        if ticks % 3:
            complexity += 0.05
    if not errors:
        return math.inf
    return sum(errors) / len(errors) + complexity / len(errors)


def rank_tempos(notes: list[RawNote], requested_bpm: float | None = None) -> list[dict]:
    if not notes:
        return [{"bpm": requested_bpm or 120.0, "phase_seconds": 0.0, "score": 0.0}]
    bpms = [float(requested_bpm)] if requested_bpm else [float(value) for value in range(40, 201)]
    ranked: list[dict] = []
    for bpm in bpms:
        quarter = 60.0 / bpm
        best = None
        for phase_tick in range(24):
            phase = phase_tick / 24 * quarter
            score = _candidate_score(notes, bpm, phase)
            candidate = {"bpm": bpm, "phase_seconds": phase, "score": score}
            if best is None or score < best["score"]:
                best = candidate
        ranked.append(best)
    ranked.sort(key=lambda item: (item["score"], abs(item["bpm"] - 100)))
    # Half/double candidates are useful to review, but near-identical neighbors are not.
    selected: list[dict] = []
    for candidate in ranked:
        if all(abs(candidate["bpm"] - old["bpm"]) >= 3 for old in selected):
            selected.append(candidate)
        if len(selected) == 5:
            break
    return selected


def choose_meter(
    notes: list[RawNote], bpm: float, phase: float, requested: str | None = None
) -> dict:
    candidates = []
    meters = [tuple(map(int, requested.split("/")))] if requested else METERS
    onsets = [(note.onset - phase) * bpm / 60 for note in notes]
    for numerator, denominator in meters:
        measure_quarters = numerator * 4 / denominator
        strong = 0.0
        boundary = 0.0
        for onset in onsets:
            position = onset % measure_quarters
            boundary += min(position, measure_quarters - position)
            if min(position, measure_quarters - position) < 0.12:
                strong += 1
        score = boundary / max(len(onsets), 1) - strong * 0.02
        if (numerator, denominator) == (4, 4):
            score -= 0.03
        candidates.append({"numerator": numerator, "denominator": denominator, "score": score})
    return min(candidates, key=lambda item: item["score"])


def assign_hands(notes: list[RawNote]) -> None:
    groups: list[list[RawNote]] = []
    for note in sorted(notes, key=lambda item: (item.onset, item.midi)):
        if not groups or note.onset - groups[-1][0].onset > 0.055:
            groups.append([note])
        else:
            groups[-1].append(note)
    previous = {"left": 48.0, "right": 67.0}
    for group in groups:
        unknown = [note for note in group if note.hand not in {"left", "right"}]
        pitches = sorted(note.midi for note in unknown)
        split = 60
        if len(pitches) >= 2:
            gaps = [(b - a, (a + b) / 2) for a, b in pairwise(pitches)]
            largest, midpoint = max(gaps)
            if largest >= 5:
                split = round(midpoint)
        for note in unknown:
            left_cost = abs(note.midi - previous["left"]) + max(0, note.midi - 64) * 2
            right_cost = abs(note.midi - previous["right"]) + max(0, 55 - note.midi) * 2
            if len(pitches) > 1:
                left_cost += max(0, note.midi - split) * 1.5
                right_cost += max(0, split - note.midi) * 1.5
            note.hand = "left" if left_cost <= right_cost else "right"
        for hand in ("left", "right"):
            assigned = [note.midi for note in group if note.hand == hand]
            if assigned:
                previous[hand] = median(assigned)


def allocate_voices(notes: list[ScoreNote]) -> None:
    for hand in ("left", "right"):
        ends: list[float] = []
        hand_notes = sorted(
            (note for note in notes if note.hand == hand),
            key=lambda n: (n.onset_quarters, -n.duration_quarters),
        )
        chord_voice: dict[float, int] = {}
        for note in hand_notes:
            rounded_onset = round(note.onset_quarters, 6)
            if rounded_onset in chord_voice:
                note.voice = chord_voice[rounded_onset]
                continue
            for index, end in enumerate(ends):
                if end <= note.onset_quarters + 1e-6:
                    note.voice = index + 1
                    ends[index] = note.onset_quarters + note.duration_quarters
                    break
            else:
                ends.append(note.onset_quarters + note.duration_quarters)
                note.voice = len(ends)
            chord_voice[rounded_onset] = note.voice


def infer_clefs(notes: list[ScoreNote], measure_quarters: float) -> list[dict]:
    """Choose conservative treble/bass changes, requiring two measures of evidence."""
    result: list[dict] = []
    max_measure = int(
        max((note.onset_quarters + note.duration_quarters for note in notes), default=0)
        / measure_quarters
    )
    for hand, staff, default, threshold, direction in (
        ("right", 1, "treble", 55, "below"),
        ("left", 2, "bass", 60, "above"),
    ):
        desired = []
        for measure in range(max_measure + 1):
            pitches = [
                note.midi
                for note in notes
                if note.hand == hand
                and measure <= note.onset_quarters / measure_quarters < measure + 1
            ]
            center = median(pitches) if pitches else None
            alternate = center is not None and (
                (direction == "below" and center < threshold)
                or (direction == "above" and center > threshold)
            )
            desired.append(
                "bass"
                if default == "treble" and alternate
                else "treble"
                if default == "bass" and alternate
                else default
            )
        current = default
        for measure, clef in enumerate(desired):
            sustained = measure + 1 < len(desired) and desired[measure + 1] == clef
            if clef != current and sustained:
                result.append({"measure": measure, "staff": staff, "clef": clef})
                current = clef
    return result


def normalize(
    raw: list[RawNote], requested_bpm: float | None = None, requested_meter: str | None = None
) -> dict:
    ranked = rank_tempos(raw, requested_bpm)
    choice = ranked[0]
    meter = choose_meter(raw, choice["bpm"], choice["phase_seconds"], requested_meter)
    assign_hands(raw)
    quarter = 60.0 / choice["bpm"]
    score_notes: list[ScoreNote] = []
    for note in raw:
        onset = max(0.0, _snap((note.onset - choice["phase_seconds"]) / quarter))
        duration = max(3 / TICKS_PER_QUARTER, _snap((note.offset - note.onset) / quarter))
        score_notes.append(
            ScoreNote(
                id=note.id,
                midi=note.midi,
                onset_quarters=onset,
                duration_quarters=duration,
                hand=note.hand or ("left" if note.midi < 60 else "right"),
                confidence=note.confidence,
            )
        )
    allocate_voices(score_notes)
    measure_quarters = meter["numerator"] * 4 / meter["denominator"]
    return {
        "bpm": choice["bpm"],
        "phase_seconds": choice["phase_seconds"],
        "meter": meter,
        "tempo_candidates": ranked,
        "ticks_per_quarter": TICKS_PER_QUARTER,
        "notes": [asdict(note) for note in score_notes],
        "clefs": infer_clefs(score_notes, measure_quarters),
    }
