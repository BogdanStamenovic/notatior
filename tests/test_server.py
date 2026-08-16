from pathlib import Path

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
