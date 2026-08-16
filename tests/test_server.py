from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from notatior.server import create_app
from notatior.store import ProjectStore


def test_dashboard_and_project_api(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("/tmp/video.mp4", "API fixture")
    with TestClient(create_app(store)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Notatior" in response.text
        response = client.get("/api/v1/projects")
        assert response.status_code == 200
        assert response.json()[0]["id"] == project.id
        response = client.patch(
            f"/api/v1/projects/{project.id}/settings", json={"bpm": 120, "meter": "4/4"}
        )
        assert response.status_code == 200
        assert response.json()["settings"] == {"bpm": 120, "meter": "4/4"}


def test_manual_key_regions_are_validated_and_store_baseline(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("/tmp/video.mp4", "manual regions")
    frame = np.full((80, 120, 3), 220, dtype=np.uint8)
    frame_path = store.artifact(project.id, "analysis/calibration.jpg")
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(frame_path), frame)
    store.write_json(project.id, "analysis/calibration.json", {"frame_time": 0, "keys": []})
    with TestClient(create_app(store)) as client:
        response = client.put(
            f"/api/v1/projects/{project.id}/calibration/regions",
            json={"regions": [{
                "midi": 60, "left": 20, "top": 10, "right": 50, "bottom": 70,
                "threshold": 12, "hand": None,
            }]},
        )
        assert response.status_code == 200
        key = response.json()["keys"][0]
        assert response.json()["mode"] == "manual-regions"
        assert key["midi"] == 60
        assert key["threshold"] == 12
        assert len(key["baseline_lab"]) == 3


def test_exports_before_export_stage_is_empty(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("/tmp/video.mp4", "no exports")
    with TestClient(create_app(store)) as client:
        response = client.get(f"/api/v1/projects/{project.id}/exports")
        assert response.status_code == 200
        assert response.json() == []
