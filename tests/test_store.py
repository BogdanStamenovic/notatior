from pathlib import Path

from notatior.models import StageName, StageStatus
from notatior.store import ProjectStore


def test_project_store_round_trip_and_invalidation(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("/tmp/example.mp4", "Example")
    assert project.title == "Example"
    assert len(project.stages) == 8

    store.set_stage(project, StageName.INGEST, StageStatus.COMPLETE, approved=True)
    store.set_stage(project, StageName.CALIBRATION, StageStatus.COMPLETE, approved=True)
    store.set_stage(project, StageName.DETECTION, StageStatus.COMPLETE, approved=True)
    store.invalidate_after(project, StageName.CALIBRATION)

    loaded = store.get(project.id)
    assert loaded.stages["calibration"]["status"] == "complete"
    assert loaded.stages["detection"]["status"] == "stale"
    assert store.list()[0].id == project.id


def test_artifact_cannot_escape_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("source.mp4")
    try:
        store.artifact(project.id, "../../outside")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal was accepted")
