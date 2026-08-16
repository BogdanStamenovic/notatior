from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel

from .models import StageName, StageStatus
from .pipeline import Pipeline, PipelineError
from .store import ProjectStore
from .vision import polygon_sample, segment_keys, update_pitch_anchor

LOGGER = logging.getLogger(__name__)


class ProjectCreate(BaseModel):
    source: str
    title: str | None = None


class SettingsUpdate(BaseModel):
    bpm: float | None = None
    meter: str | None = None


class AnchorUpdate(BaseModel):
    key_index: int
    midi: int


class BoundsUpdate(BaseModel):
    left: int
    top: int
    right: int
    bottom: int
    first_midi: int | None = None
    white_key_count: int | None = None


class ManualRegion(BaseModel):
    midi: int
    left: float
    top: float
    right: float
    bottom: float
    threshold: float = 14.0
    hand: str | None = None


class ManualRegionsUpdate(BaseModel):
    regions: list[ManualRegion]


ARTIFACTS = {
    "calibration-frame": "analysis/calibration.jpg",
    "calibration": "analysis/calibration.json",
    "raw-notes": "analysis/raw-notes.json",
    "score-data": "score/score.json",
    "musicxml": "score/score.musicxml",
    "midi": "score/score.mid",
    "source-audio": "analysis/source.wav",
    "rendered-audio": "analysis/rendered.wav",
    "validation": "analysis/validation.json",
    "score-preview": "score/preview.pdf",
}


def create_app(store: ProjectStore | None = None) -> FastAPI:
    store = store or ProjectStore()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notatior")
    pipeline = Pipeline(store)
    app = FastAPI(title="Notatior", version="0.1.0")

    def submit(project_id: str, stage: str | None = None):
        future = executor.submit(pipeline.run, project_id, stage, True)

        def report_failure(done):
            try:
                done.result()
            except PipelineError as exc:
                # Pipeline errors are persisted in project state and belong in the UI,
                # not as noisy unhandled ThreadPoolExecutor tracebacks.
                LOGGER.info("Project %s stopped: %s", project_id, exc)
            except Exception:
                LOGGER.exception("Unexpected background failure for project %s", project_id)

        future.add_done_callback(report_failure)

    @app.get("/api/v1/projects")
    def list_projects():
        return [project.to_dict() for project in store.list()]

    @app.post("/api/v1/projects", status_code=201)
    def create_project(request: ProjectCreate):
        project = store.create(request.source, request.title)
        submit(project.id)
        return project.to_dict()

    @app.post("/api/v1/projects/upload", status_code=201)
    async def upload_project(file: Annotated[UploadFile, File()]):
        if not file.filename:
            raise HTTPException(400, "Upload needs a filename")
        project = store.create("pending-upload", Path(file.filename).stem)
        suffix = Path(file.filename).suffix.lower()
        uploaded = store.artifact(project.id, f"source/upload{suffix}")
        with uploaded.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                destination.write(chunk)
        project.source = str(uploaded)
        store.save(project)
        submit(project.id)
        return project.to_dict()

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str):
        try:
            return store.get(project_id).to_dict()
        except KeyError:
            raise HTTPException(404, "Project not found") from None

    @app.patch("/api/v1/projects/{project_id}/settings")
    def update_settings(project_id: str, request: SettingsUpdate):
        try:
            project = store.get(project_id)
        except KeyError:
            raise HTTPException(404, "Project not found") from None
        if request.bpm is not None and not 20 <= request.bpm <= 300:
            raise HTTPException(422, "BPM must be between 20 and 300")
        if request.meter is not None:
            try:
                beats, unit = map(int, request.meter.split("/"))
            except (ValueError, AttributeError):
                raise HTTPException(422, "Meter must look like 4/4") from None
            if beats < 1 or unit not in {2, 4, 8, 16}:
                raise HTTPException(422, "Unsupported meter")
        project.settings.update({"bpm": request.bpm, "meter": request.meter})
        store.invalidate_after(project, StageName.DETECTION)
        return store.get(project_id).to_dict()

    @app.post("/api/v1/projects/{project_id}/stages/{stage}/run", status_code=202)
    def run_stage(project_id: str, stage: str):
        try:
            StageName(stage)
            store.get(project_id)
            submit(project_id, stage)
        except (KeyError, ValueError):
            raise HTTPException(404, "Project or stage not found") from None
        return {"accepted": True}

    @app.post("/api/v1/projects/{project_id}/stages/{stage}/approve")
    def approve_stage(project_id: str, stage: str):
        try:
            pipeline.approve(project_id, stage)
            position = list(StageName).index(StageName(stage))
            if position + 1 < len(StageName):
                submit(project_id, list(StageName)[position + 1].value)
            return store.get(project_id).to_dict()
        except (KeyError, ValueError, PipelineError) as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/v1/projects/{project_id}/calibration/anchor")
    def set_anchor(project_id: str, request: AnchorUpdate):
        calibration = store.read_json(project_id, "analysis/calibration.json")
        if calibration is None:
            raise HTTPException(404, "Calibration not found")
        if not 0 <= request.key_index < len(calibration["keys"]) or not 0 <= request.midi <= 127:
            raise HTTPException(422, "Invalid key index or MIDI pitch")
        update_pitch_anchor(calibration, request.key_index, request.midi)
        store.write_json(project_id, "analysis/calibration.json", calibration)
        project = store.get(project_id)
        store.invalidate_after(project, StageName.CALIBRATION)
        return calibration

    @app.post("/api/v1/projects/{project_id}/calibration/bounds")
    def set_bounds(project_id: str, request: BoundsUpdate):
        import cv2

        calibration = store.read_json(project_id, "analysis/calibration.json")
        frame_path = store.artifact(project_id, "analysis/calibration.jpg")
        if calibration is None or not frame_path.exists():
            raise HTTPException(404, "Calibration not found")
        frame = cv2.imread(str(frame_path))
        height, width = frame.shape[:2]
        bounds = (request.left, request.top, request.right, request.bottom)
        if not (
            0 <= request.left < request.right <= width
            and 0 <= request.top < request.bottom <= height
        ):
            raise HTTPException(422, "Bounds must form a rectangle inside the frame")
        keys = segment_keys(frame, bounds, request.first_midi, request.white_key_count)
        calibration.update(
            bounds=list(bounds),
            first_midi=keys[0].midi if keys else None,
            keys=[
                {
                    "index": key.index,
                    "midi": key.midi,
                    "kind": key.kind,
                    "polygon": key.polygon,
                    "active_lab": key.active_lab,
                    "hand": key.hand,
                }
                for key in keys
            ],
        )
        store.write_json(project_id, "analysis/calibration.json", calibration)
        project = store.get(project_id)
        store.invalidate_after(project, StageName.CALIBRATION)
        return calibration

    @app.put("/api/v1/projects/{project_id}/calibration/regions")
    def set_manual_regions(project_id: str, request: ManualRegionsUpdate):
        import cv2

        calibration = store.read_json(project_id, "analysis/calibration.json")
        frame_path = store.artifact(project_id, "analysis/calibration.jpg")
        if calibration is None or not frame_path.exists():
            raise HTTPException(404, "Calibration not found")
        frame = cv2.imread(str(frame_path))
        if frame is None:
            raise HTTPException(409, "Calibration frame cannot be read")
        height, width = frame.shape[:2]
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        if not request.regions:
            raise HTTPException(422, "Draw at least one key region")
        keys = []
        for index, region in enumerate(request.regions):
            if not (
                0 <= region.midi <= 127
                and 0 <= region.left < region.right <= width
                and 0 <= region.top < region.bottom <= height
                and 2 <= region.threshold <= 100
                and region.hand in {None, "left", "right"}
            ):
                raise HTTPException(422, f"Invalid key region {index + 1}")
            polygon = [
                [region.left, region.top], [region.right, region.top],
                [region.right, region.bottom], [region.left, region.bottom],
            ]
            baseline = polygon_sample(lab, polygon, "manual")
            keys.append({
                "index": index,
                "midi": region.midi,
                "kind": "manual",
                "polygon": polygon,
                "baseline_lab": [round(float(value), 4) for value in baseline],
                "threshold": region.threshold,
                "hand": region.hand,
            })
        calibration.update(
            mode="manual-regions",
            keys=keys,
            bounds=[
                min(key["polygon"][0][0] for key in keys),
                min(key["polygon"][0][1] for key in keys),
                max(key["polygon"][2][0] for key in keys),
                max(key["polygon"][2][1] for key in keys),
            ],
        )
        store.write_json(project_id, "analysis/calibration.json", calibration)
        project = store.get(project_id)
        store.invalidate_after(project, StageName.CALIBRATION)
        return calibration

    @app.get("/api/v1/projects/{project_id}/data/{name}")
    def get_data(project_id: str, name: str):
        relative = ARTIFACTS.get(name)
        if not relative or not relative.endswith(".json"):
            raise HTTPException(404, "Data not found")
        value = store.read_json(project_id, relative)
        if value is None:
            raise HTTPException(404, "Data not ready")
        return value

    @app.put("/api/v1/projects/{project_id}/data/raw-notes")
    def put_notes(project_id: str, payload: list[dict[str, Any]]):
        for note in payload:
            if (
                not {"id", "midi", "onset", "offset"} <= note.keys()
                or note["offset"] <= note["onset"]
            ):
                raise HTTPException(
                    422, "Each note requires a valid id, MIDI pitch, onset, and offset"
                )
        store.write_json(project_id, "analysis/raw-notes.json", payload)
        project = store.get(project_id)
        store.invalidate_after(project, StageName.DETECTION)
        return payload

    @app.put("/api/v1/projects/{project_id}/data/score-data")
    def put_score(project_id: str, payload: dict[str, Any]):
        if not {"bpm", "meter", "notes"} <= payload.keys():
            raise HTTPException(422, "Score needs bpm, meter, and notes")
        store.write_json(project_id, "score/score.json", payload)
        project = store.get(project_id)
        state = project.stages[StageName.SCORE.value]
        state.update(status=StageStatus.STALE, approved=False)
        store.invalidate_after(project, StageName.SCORE)
        return payload

    @app.get("/api/v1/projects/{project_id}/artifact/{name}")
    def artifact(project_id: str, name: str):
        relative = ARTIFACTS.get(name)
        if not relative:
            raise HTTPException(404, "Artifact not found")
        path = store.artifact(project_id, relative)
        if not path.is_file():
            raise HTTPException(404, "Artifact not ready")
        return FileResponse(path)

    @app.get("/api/v1/projects/{project_id}/exports")
    def exports(project_id: str):
        export_dir = store.artifact(project_id, "exports")
        if not export_dir.exists():
            return []
        return [
            {"name": path.name, "size": path.stat().st_size}
            for path in export_dir.iterdir()
            if path.is_file()
        ]

    @app.get("/api/v1/projects/{project_id}/exports/{filename}")
    def download_export(project_id: str, filename: str):
        if Path(filename).name != filename:
            raise HTTPException(404, "Export not found")
        path = store.artifact(project_id, f"exports/{filename}")
        if not path.is_file():
            raise HTTPException(404, "Export not found")
        return FileResponse(path, filename=filename)

    @app.websocket("/api/v1/projects/{project_id}/events")
    async def events(websocket: WebSocket, project_id: str):
        await websocket.accept()
        previous = None
        try:
            while True:
                current = store.get(project_id).to_dict()
                if current != previous:
                    await websocket.send_json(current)
                    previous = current
                await asyncio.sleep(0.7)
        except (WebSocketDisconnect, KeyError, RuntimeError):
            return

    static = files("notatior").joinpath("web")
    app.mount("/", StaticFiles(directory=str(static), html=True), name="web")
    return app
