from pathlib import Path
from xml.etree import ElementTree as ET

from notatior.models import RawNote
from notatior.notation import write_midi, write_musicxml
from notatior.rhythm import normalize


def _score():
    score = normalize(
        [
            RawNote("bass", 48, 0.0, 2.0, hand="left"),
            RawNote("one", 60, 0.0, 0.5, hand="right"),
            RawNote("two", 64, 0.5, 1.0, hand="right"),
            RawNote("three", 67, 1.0, 1.5, hand="right"),
        ],
        120,
        "4/4",
    )
    score["dynamics"] = [{"measure": 0, "staff": 1, "mark": "mf", "velocity": 76}]
    return score


def test_writes_parseable_musicxml_and_midi(tmp_path: Path):
    xml_path = write_musicxml(_score(), tmp_path / "score.musicxml", "Fixture")
    midi_path = write_midi(_score(), tmp_path / "score.mid")
    root = ET.parse(xml_path).getroot()

    assert root.tag == "score-partwise"
    assert root.findtext("work/work-title") == "Fixture"
    assert len(root.findall(".//note")) >= 4
    assert root.find(".//dynamics/mf") is not None
    assert midi_path.read_bytes().startswith(b"MThd")
    assert b"MTrk" in midi_path.read_bytes()


def test_long_note_has_start_and_stop_tie_in_middle_measure(tmp_path: Path):
    score = normalize([RawNote("long", 60, 0, 5, hand="right")], 120, "4/4")
    root = ET.parse(write_musicxml(score, tmp_path / "long.musicxml")).getroot()
    tied_both_ways = [
        note
        for note in root.findall(".//note")
        if {tie.attrib["type"] for tie in note.findall("tie")} == {"start", "stop"}
    ]
    assert tied_both_ways
