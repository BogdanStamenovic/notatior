from __future__ import annotations

import math
import struct
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

from .rhythm import TICKS_PER_QUARTER


SHARP_NAMES = ("C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B")
SHARP_ALTERS = (0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0)
FLAT_NAMES = ("C", "D", "D", "E", "E", "F", "G", "G", "A", "A", "B", "B")
FLAT_ALTERS = (0, -1, 0, -1, 0, 0, -1, 0, -1, 0, -1, 0)
MAJOR_PROFILES = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MAJOR_FIFTHS = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: 7, 5: -1, 10: -2, 3: -3, 8: -4}


def infer_key(notes: list[dict]) -> dict:
    histogram = [0.0] * 12
    for note in notes:
        histogram[int(note["midi"]) % 12] += float(note["duration_quarters"])
    scores = []
    for tonic in range(12):
        score = sum(histogram[pc] * MAJOR_PROFILES[(pc - tonic) % 12] for pc in range(12))
        scores.append(score)
    tonic = max(range(12), key=lambda index: scores[index]) if notes else 0
    return {"tonic_pc": tonic, "fifths": MAJOR_FIFTHS.get(tonic, 0), "mode": "major"}


def _pitch(midi: int, prefer_flats: bool) -> tuple[str, int, int]:
    pc = midi % 12
    names, alters = (FLAT_NAMES, FLAT_ALTERS) if prefer_flats else (SHARP_NAMES, SHARP_ALTERS)
    return names[pc], alters[pc], midi // 12 - 1


def _duration_type(ticks: int) -> tuple[str, int, tuple[int, int] | None]:
    exact = {
        96: ("whole", 0, None), 72: ("half", 1, None), 48: ("half", 0, None),
        36: ("quarter", 1, None), 24: ("quarter", 0, None),
        18: ("eighth", 1, None), 12: ("eighth", 0, None),
        9: ("16th", 1, None), 6: ("16th", 0, None), 3: ("32nd", 0, None),
        16: ("quarter", 0, (3, 2)), 8: ("eighth", 0, (3, 2)), 4: ("16th", 0, (3, 2)),
    }
    return exact.get(ticks, ("eighth", 0, None))


def _sub(parent, tag: str, text=None, **attributes):
    node = ET.SubElement(parent, tag, attributes)
    if text is not None:
        node.text = str(text)
    return node


def _append_note(parent, note: dict, duration: int, staff: int, chord: bool, tie: str | None, fifths: int):
    element = _sub(parent, "note")
    if chord:
        _sub(element, "chord")
    pitch = _sub(element, "pitch")
    step, alter, octave = _pitch(int(note["midi"]), fifths < 0)
    _sub(pitch, "step", step)
    if alter:
        _sub(pitch, "alter", alter)
    _sub(pitch, "octave", octave)
    _sub(element, "duration", duration)
    _sub(element, "voice", int(note.get("voice", 1)))
    kind, dots, tuplet = _duration_type(duration)
    _sub(element, "type", kind)
    for _ in range(dots):
        _sub(element, "dot")
    if tuplet:
        timing = _sub(element, "time-modification")
        _sub(timing, "actual-notes", tuplet[0])
        _sub(timing, "normal-notes", tuplet[1])
    if tie:
        _sub(element, "tie", type=tie)
        notations = _sub(element, "notations")
        _sub(notations, "tied", type=tie)
    _sub(element, "staff", staff)


def _segments(notes: list[dict], measure_len: int) -> dict[tuple[int, int, int], list[dict]]:
    result: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for note in notes:
        start = round(float(note["onset_quarters"]) * TICKS_PER_QUARTER)
        remaining = max(1, round(float(note["duration_quarters"]) * TICKS_PER_QUARTER))
        first = True
        while remaining:
            measure = start // measure_len
            inside = start % measure_len
            duration = min(remaining, measure_len - inside)
            segment = dict(note, start_tick=inside, duration_tick=duration)
            if not first:
                segment["tie"] = "stop"
            if remaining > duration:
                segment["tie"] = "start" if first else "continue"
            result[(measure, 1 if note["hand"] == "right" else 2, int(note.get("voice", 1)))].append(segment)
            start += duration
            remaining -= duration
            first = False
    return result


def write_musicxml(score: dict, output: Path, title: str = "Notatior transcription") -> Path:
    notes = score["notes"]
    key = score.setdefault("key", infer_key(notes))
    numerator = int(score["meter"]["numerator"])
    denominator = int(score["meter"]["denominator"])
    measure_len = round(numerator * 4 / denominator * TICKS_PER_QUARTER)
    groups = _segments(notes, measure_len)
    max_tick = max((round((n["onset_quarters"] + n["duration_quarters"]) * TICKS_PER_QUARTER) for n in notes), default=measure_len)
    measures = max(1, math.ceil(max_tick / measure_len))
    root = ET.Element("score-partwise", version="4.0")
    work = _sub(root, "work")
    _sub(work, "work-title", title)
    part_list = _sub(root, "part-list")
    score_part = _sub(part_list, "score-part", id="P1")
    _sub(score_part, "part-name", "Piano")
    part = _sub(root, "part", id="P1")
    dynamics_by_measure = defaultdict(list)
    for dynamic in score.get("dynamics", []):
        dynamics_by_measure[int(dynamic.get("measure", 0))].append(dynamic)
    for measure_index in range(measures):
        measure = _sub(part, "measure", number=measure_index + 1)
        if measure_index == 0:
            attributes = _sub(measure, "attributes")
            _sub(attributes, "divisions", TICKS_PER_QUARTER)
            key_node = _sub(attributes, "key")
            _sub(key_node, "fifths", key["fifths"])
            time_node = _sub(attributes, "time")
            _sub(time_node, "beats", numerator)
            _sub(time_node, "beat-type", denominator)
            _sub(attributes, "staves", 2)
            clef_1 = _sub(attributes, "clef", number="1")
            _sub(clef_1, "sign", "G")
            _sub(clef_1, "line", 2)
            clef_2 = _sub(attributes, "clef", number="2")
            _sub(clef_2, "sign", "F")
            _sub(clef_2, "line", 4)
            direction = _sub(measure, "direction", placement="above")
            direction_type = _sub(direction, "direction-type")
            metronome = _sub(direction_type, "metronome")
            _sub(metronome, "beat-unit", "quarter")
            _sub(metronome, "per-minute", score["bpm"])
            _sub(direction, "sound", tempo=score["bpm"])
        for dynamic in dynamics_by_measure.get(measure_index, []):
            direction = _sub(measure, "direction", placement="below")
            dtype = _sub(direction, "direction-type")
            dynamics = _sub(dtype, "dynamics")
            _sub(dynamics, dynamic["mark"])
            _sub(direction, "staff", dynamic.get("staff", 1))
        streams = sorted(key for key in groups if key[0] == measure_index)
        first_stream = True
        for _, staff, voice in streams:
            if not first_stream:
                backup = _sub(measure, "backup")
                _sub(backup, "duration", measure_len)
            first_stream = False
            cursor = 0
            chord_start = None
            for note in sorted(groups[(measure_index, staff, voice)], key=lambda n: (n["start_tick"], n["midi"])):
                start = int(note["start_tick"])
                if start > cursor:
                    forward = _sub(measure, "forward")
                    _sub(forward, "duration", start - cursor)
                    _sub(forward, "voice", voice)
                    _sub(forward, "staff", staff)
                    cursor = start
                chord = chord_start == start
                tie = note.get("tie")
                if tie == "continue":
                    _append_note(measure, note, int(note["duration_tick"]), staff, chord, "stop", key["fifths"])
                    # MusicXML cannot express start+stop with this helper; the next segment remains audible.
                else:
                    _append_note(measure, note, int(note["duration_tick"]), staff, chord, tie, key["fifths"])
                chord_start = start
                if not chord:
                    cursor += int(note["duration_tick"])
    ET.indent(root, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return output


def _vlq(value: int) -> bytes:
    buffer = value & 0x7F
    result = bytearray([buffer])
    while value >> 7:
        value >>= 7
        buffer = (value & 0x7F) | 0x80
        result.insert(0, buffer)
    return bytes(result)


def _track(events: list[tuple[int, bytes]]) -> bytes:
    payload = bytearray()
    last = 0
    for tick, event in sorted(events, key=lambda item: (item[0], item[1][0] & 0x10)):
        payload.extend(_vlq(max(0, tick - last)))
        payload.extend(event)
        last = tick
    payload.extend(b"\x00\xff\x2f\x00")
    return b"MTrk" + struct.pack(">I", len(payload)) + payload


def write_midi(score: dict, output: Path) -> Path:
    bpm = float(score["bpm"])
    numerator = int(score["meter"]["numerator"])
    denominator = int(score["meter"]["denominator"])
    microseconds = round(60_000_000 / bpm)
    tempo_events = [
        (0, b"\xff\x51\x03" + microseconds.to_bytes(3, "big")),
        (0, bytes([0xFF, 0x58, 0x04, numerator, int(math.log2(denominator)), 24, 8])),
    ]
    tracks = [_track(tempo_events)]
    for channel, hand in enumerate(("right", "left")):
        events = []
        for note in score["notes"]:
            if note["hand"] != hand:
                continue
            start = round(float(note["onset_quarters"]) * 480)
            end = start + max(1, round(float(note["duration_quarters"]) * 480))
            pitch = max(0, min(127, int(note["midi"])))
            velocity = max(1, min(127, int(note.get("velocity", 72))))
            events.append((start, bytes([0x90 | channel, pitch, velocity])))
            events.append((end, bytes([0x80 | channel, pitch, 0])))
        tracks.append(_track(events))
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), 480)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + b"".join(tracks))
    return output

