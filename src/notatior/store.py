from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import project_root
from .models import Project, STAGE_ORDER, StageName, StageState, StageStatus


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or project_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root.parent / "notatior.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state_json TEXT NOT NULL
                )"""
            )

    def create(self, source: str, title: str | None = None) -> Project:
        project_id = uuid.uuid4().hex[:12]
        now = _now()
        inferred = Path(source).stem if not source.startswith(("http://", "https://")) else source
        safe_title = title or inferred or "Untitled transcription"
        stages = {stage.value: asdict(StageState(stage.value)) for stage in STAGE_ORDER}
        project = Project(project_id, safe_title, source, now, now, {}, stages)
        path = self.path(project_id)
        for folder in ("source", "analysis", "score", "exports", "tmp"):
            (path / folder).mkdir(parents=True, exist_ok=True)
        self.save(project)
        return project

    def save(self, project: Project) -> None:
        project.updated_at = _now()
        payload = json.dumps(project.to_dict(), separators=(",", ":"), sort_keys=True)
        with self._connect() as db:
            db.execute(
                """INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title, source=excluded.source,
                updated_at=excluded.updated_at, state_json=excluded.state_json""",
                (project.id, project.title, project.source, project.created_at, project.updated_at, payload),
            )
        target = self.path(project.id) / "project.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")
        temporary.replace(target)

    def get(self, project_id: str) -> Project:
        if not re.fullmatch(r"[a-f0-9]{12}", project_id):
            raise KeyError(project_id)
        with self._connect() as db:
            row = db.execute("SELECT state_json FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return Project(**json.loads(row["state_json"]))

    def list(self) -> list[Project]:
        with self._connect() as db:
            rows = db.execute("SELECT state_json FROM projects ORDER BY updated_at DESC").fetchall()
        return [Project(**json.loads(row["state_json"])) for row in rows]

    def path(self, project_id: str) -> Path:
        return self.root / project_id

    def artifact(self, project_id: str, relative: str) -> Path:
        base = self.path(project_id).resolve()
        target = (base / relative).resolve()
        if base not in target.parents and target != base:
            raise ValueError("artifact path escapes project")
        return target

    def write_json(self, project_id: str, relative: str, value: Any) -> Path:
        target = self.artifact(project_id, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target

    def read_json(self, project_id: str, relative: str, default: Any = None) -> Any:
        target = self.artifact(project_id, relative)
        return json.loads(target.read_text(encoding="utf-8")) if target.exists() else default

    def set_stage(
        self, project: Project, stage: StageName | str, status: StageStatus | str, **values: Any
    ) -> None:
        name = StageName(stage).value
        state = project.stages.setdefault(name, asdict(StageState(name)))
        state.update(status=StageStatus(status).value, **values)
        self.save(project)

    def invalidate_after(self, project: Project, stage: StageName | str) -> None:
        position = STAGE_ORDER.index(StageName(stage))
        for downstream in STAGE_ORDER[position + 1 :]:
            state = project.stages[downstream.value]
            if state["status"] != StageStatus.PENDING:
                state.update(status=StageStatus.STALE, approved=False, progress=0.0)
        self.save(project)

    def delete_temporary(self, project_id: str) -> None:
        target = self.artifact(project_id, "tmp")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
