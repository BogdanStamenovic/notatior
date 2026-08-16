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


def exact_transcription(
    raw: list[RawNote], requested_bpm: float | None = None, requested_meter: str | None = None
) -> dict:
    """Build a score without tempo search or rhythmic snapping.

    Raw video timestamps remain authoritative. BPM only controls how those seconds are
    represented in notation/MIDI; it never moves an onset or changes a duration.
    """
    bpm = float(requested_bpm or 60.0)
    try:
        numerator, denominator = map(int, (requested_meter or "4/4").split("/"))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Meter must look like 4/4") from exc
    assign_hands(raw)
    quarter = 60.0 / bpm
    score_notes = [
        ScoreNote(
            id=note.id,
            midi=note.midi,
            onset_quarters=max(0.0, note.onset / quarter),
            duration_quarters=max(1 / TICKS_PER_QUARTER, (note.offset - note.onset) / quarter),
            hand=note.hand or ("left" if note.midi < 60 else "right"),
            confidence=note.confidence,
        )
        for note in raw
    ]
    allocate_voices(score_notes)
    meter = {"numerator": numerator, "denominator": denominator, "score": 0.0}
    measure_quarters = numerator * 4 / denominator
    return {
        "bpm": bpm,
        "phase_seconds": 0.0,
        "meter": meter,
        "tempo_candidates": [],
        "ticks_per_quarter": TICKS_PER_QUARTER,
        "timing_mode": "exact-video-timestamps",
        "notes": [asdict(note) for note in score_notes],
        "clefs": infer_clefs(score_notes, measure_quarters),
        "dynamics": [],
        "hairpins": [],
    }


def allocate_voices(notes: list[ScoreNote]) -> None:
    for hand in ("left", "right"):
        ends: list[float] = []
        active_groups: list[list[ScoreNote]] = []
        hand_notes = sorted(
            (note for note in notes if note.hand == hand),
            key=lambda n: (n.onset_quarters, -n.duration_quarters),
        )
        groups: list[list[ScoreNote]] = []
        for note in hand_notes:
            if not groups or abs(note.onset_quarters - groups[-1][0].onset_quarters) > 1e-6:
                groups.append([note])
            else:
                groups[-1].append(note)
        for group in groups:
            onset = group[0].onset_quarters
            group_end = max(note.onset_quarters + note.duration_quarters for note in group)
            for index, end in enumerate(ends):
                if end <= onset + 1e-6:
                    voice = index + 1
                    ends[index] = group_end
                    active_groups[index] = group
                    break
            else:
                if len(ends) < 4:
                    ends.append(group_end)
                    active_groups.append(group)
                    voice = len(ends)
                else:
                    index = min(range(4), key=lambda item: ends[item])
                    for previous in active_groups[index]:
                        available = onset - previous.onset_quarters
                        previous.duration_quarters = max(
                            3 / TICKS_PER_QUARTER,
                            min(previous.duration_quarters, available),
                        )
                    ends[index] = group_end
                    active_groups[index] = group
                    voice = index + 1
            for note in group:
                note.voice = voice


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


def regularize_barlines(notes: list[ScoreNote], measure_quarters: float) -> None:
    """Nudge cross-bar events so each tied fragment has a renderable written duration."""
    measure_ticks = round(measure_quarters * TICKS_PER_QUARTER)
    # Tuplet fragments (4/8/16 ticks) need their complete rhythmic group and cannot safely be
    # split in isolation at a barline. Prefer nearby binary/dotted fragments there.
    written_ticks = {3, 6, 9, 12, 18, 24, 36, 48, 72, 96, measure_ticks}
    for note in notes:
        start = round(note.onset_quarters * TICKS_PER_QUARTER)
        duration = max(3, round(note.duration_quarters * TICKS_PER_QUARTER))
        if start // measure_ticks == (start + duration - 1) // measure_ticks:
            continue
        best = None
        for start_delta in range(-6, 7):
            candidate_start = max(0, start + start_delta)
            for duration_delta in range(-6, 7):
                candidate_duration = max(3, duration + duration_delta)
                cursor = candidate_start
                remaining = candidate_duration
                fragments = []
                while remaining:
                    fragment = min(remaining, measure_ticks - cursor % measure_ticks)
                    fragments.append(fragment)
                    cursor += fragment
                    remaining -= fragment
                if all(fragment in written_ticks for fragment in fragments):
                    cost = abs(start_delta) + abs(duration_delta) * 0.65
                    candidate = (cost, abs(start_delta), candidate_start, candidate_duration)
                    if best is None or candidate < best:
                        best = candidate
        if best:
            note.onset_quarters = best[2] / TICKS_PER_QUARTER
            note.duration_quarters = best[3] / TICKS_PER_QUARTER


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
    measure_quarters = meter["numerator"] * 4 / meter["denominator"]
    regularize_barlines(score_notes, measure_quarters)
    allocate_voices(score_notes)
    return {
        "bpm": choice["bpm"],
        "phase_seconds": choice["phase_seconds"],
        "meter": meter,
        "tempo_candidates": ranked,
        "ticks_per_quarter": TICKS_PER_QUARTER,
        "notes": [asdict(note) for note in score_notes],
        "clefs": infer_clefs(score_notes, measure_quarters),
    }
