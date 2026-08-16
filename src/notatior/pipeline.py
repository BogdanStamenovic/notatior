from __future__ import annotations

import shutil
import threading
import traceback
import zipfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from .audio import render_score, validate_audio
from .detection import detect_notes
from .media import acquire, extract_audio
from .models import STAGE_ORDER, RawNote, StageName, StageStatus
from .notation import write_midi, write_musicxml
from .rhythm import exact_transcription
from .store import ProjectStore
from .vision import find_keyboard

REVIEW_STAGES = {StageName.CALIBRATION, StageName.SCORE, StageName.VALIDATION}


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(self, store: ProjectStore, on_update: Callable[[str, dict], None] | None = None):
        self.store = store
        self.on_update = on_update or (lambda _project_id, _state: None)
        self._locks: dict[str, threading.Lock] = {}

    def _notify(self, project_id: str) -> None:
        self.on_update(project_id, self.store.get(project_id).to_dict())

    def _set(self, project_id: str, stage: StageName, status: StageStatus, **values) -> None:
        project = self.store.get(project_id)
        self.store.set_stage(project, stage, status, **values)
        self._notify(project_id)

    def _progress(self, project_id: str, stage: StageName, value: float, message: str = "") -> None:
        project = self.store.get(project_id)
        state = project.stages[stage.value]
        state.update(progress=round(value, 4), message=message)
        self.store.save(project)
        self._notify(project_id)

    def run(
        self, project_id: str, start: StageName | str | None = None, stop_at_review: bool = True
    ) -> None:
        lock = self._locks.setdefault(project_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise PipelineError("This project is already being processed")
        try:
            project = self.store.get(project_id)
            start_index = STAGE_ORDER.index(StageName(start)) if start else 0
            for stage in STAGE_ORDER[start_index:]:
                project = self.store.get(project_id)
                state = project.stages[stage.value]
                if state["status"] == StageStatus.COMPLETE and state.get("approved", False):
                    continue
                if (
                    state["status"] == StageStatus.REVIEW
                    and not state.get("approved", False)
                    and stop_at_review
                ):
                    return
                self.run_stage(project_id, stage)
                state = self.store.get(project_id).stages[stage.value]
                if state["status"] == StageStatus.REVIEW and stop_at_review:
                    return
        finally:
            lock.release()

    def run_stage(self, project_id: str, stage: StageName | str) -> None:
        stage = StageName(stage)
        self._set(
            project_id, stage, StageStatus.RUNNING, progress=0.0, error=None, message="Starting"
        )
        try:
            getattr(self, f"_run_{stage.value}")(project_id)
            status = StageStatus.REVIEW if stage in REVIEW_STAGES else StageStatus.COMPLETE
            self._set(
                project_id,
                stage,
                status,
                progress=1.0,
                approved=stage not in REVIEW_STAGES,
                message="Ready for review" if status == StageStatus.REVIEW else "Complete",
            )
        except Exception as exc:
            self._set(
                project_id,
                stage,
                StageStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                message="Failed",
            )
            error_file = self.store.artifact(project_id, f"analysis/{stage.value}-error.txt")
            error_file.write_text(traceback.format_exc(), encoding="utf-8")
            raise

    def approve(self, project_id: str, stage: StageName | str) -> None:
        stage = StageName(stage)
        project = self.store.get(project_id)
        state = project.stages[stage.value]
        if state["status"] not in {StageStatus.REVIEW, StageStatus.COMPLETE}:
            raise PipelineError(f"Stage {stage.value} is not ready for approval")
        state.update(status=StageStatus.COMPLETE, approved=True, message="Approved")
        self.store.save(project)
        self._notify(project_id)

    def _video(self, project_id: str) -> Path:
        files = [
            path
            for path in self.store.artifact(project_id, "source").glob("video.*")
            if path.is_file()
        ]
        if not files:
            raise PipelineError("Project has no ingested video")
        return files[0]

    def _run_ingest(self, project_id: str) -> None:
        project = self.store.get(project_id)
        video, metadata = acquire(project.source, self.store.artifact(project_id, "source"))
        self._progress(project_id, StageName.INGEST, 0.75, "Extracting analysis audio")
        extract_audio(video, self.store.artifact(project_id, "analysis/source.wav"))
        self.store.write_json(project_id, "analysis/media.json", metadata)
        if metadata.get("title"):
            project = self.store.get(project_id)
            project.title = metadata["title"]
            self.store.save(project)

    def _run_calibration(self, project_id: str) -> None:
        calibration = find_keyboard(
            self._video(project_id), self.store.artifact(project_id, "analysis")
        )
        self.store.write_json(project_id, "analysis/calibration.json", calibration)

    def _run_detection(self, project_id: str) -> None:
        calibration = self.store.read_json(project_id, "analysis/calibration.json")
        if not calibration:
            raise PipelineError("Calibration is missing")
        notes = detect_notes(
            self._video(project_id),
            calibration,
            lambda value: self._progress(project_id, StageName.DETECTION, value, "Tracking keys"),
        )
        self.store.write_json(
            project_id, "analysis/raw-notes.json", [asdict(note) for note in notes]
        )

    def _run_rhythm(self, project_id: str) -> None:
        project = self.store.get(project_id)
        raw = [
            RawNote(**item)
            for item in self.store.read_json(project_id, "analysis/raw-notes.json", [])
        ]
        score = exact_transcription(raw, project.settings.get("bpm"), project.settings.get("meter"))
        self.store.write_json(project_id, "score/score.json", score)

    def _run_score(self, project_id: str) -> None:
        project = self.store.get(project_id)
        score = self.store.read_json(project_id, "score/score.json")
        if score is None:
            raise PipelineError("Normalized score is missing")
        self._write_score_files(project_id, project.title, score)
        self.store.write_json(project_id, "score/score.json", score)

    def _write_score_files(self, project_id: str, title: str, score: dict) -> None:
        musicxml = self.store.artifact(project_id, "score/score.musicxml")
        midi = self.store.artifact(project_id, "score/score.mid")
        preview = self.store.artifact(project_id, "score/preview.pdf")
        write_musicxml(score, musicxml, title)
        write_midi(score, midi)
        try:
            render_score(musicxml, preview)
            score["musicxml_source"] = "direct"
        except RuntimeError:
            canonical = self.store.artifact(project_id, "score/canonical.musicxml")
            canonical.unlink(missing_ok=True)
            render_score(midi, canonical)
            canonical.replace(musicxml)
            render_score(musicxml, preview)
            score["musicxml_source"] = "musescore-midi-canonicalization"

    def _run_validation(self, project_id: str) -> None:
        xml = self.store.artifact(project_id, "score/score.musicxml")
        rendered = self.store.artifact(project_id, "analysis/rendered.wav")
        render_score(xml, rendered)
        score = self.store.read_json(project_id, "score/score.json")
        report = validate_audio(
            score, self.store.artifact(project_id, "analysis/source.wav"), rendered
        )
        self.store.write_json(project_id, "analysis/validation.json", report)

    def _run_dynamics(self, project_id: str) -> None:
        score = self.store.read_json(project_id, "score/score.json")
        if score is None:
            raise PipelineError("Score is missing")
        # Deliberately disabled on the testing branch. Keep the stage as a no-op so
        # existing projects and clients remain schema-compatible.
        score["dynamics"] = []
        score["hairpins"] = []
        self.store.write_json(project_id, "score/score.json", score)

    def _run_export(self, project_id: str) -> None:
        project = self.store.get(project_id)
        export_dir = self.store.artifact(project_id, "exports")
        safe_name = (
            "".join(
                character if character.isalnum() or character in "-_" else "_"
                for character in project.title
            ).strip("_")
            or "score"
        )
        xml_target = export_dir / f"{safe_name}.musicxml"
        midi_target = export_dir / f"{safe_name}.mid"
        shutil.copy2(self.store.artifact(project_id, "score/score.musicxml"), xml_target)
        shutil.copy2(self.store.artifact(project_id, "score/score.mid"), midi_target)
        pdf_target = export_dir / f"{safe_name}.pdf"
        render_score(xml_target, pdf_target)
        archive = export_dir / f"{safe_name}.notatior.zip"
        with zipfile.ZipFile(archive.with_suffix(".zip.tmp"), "w", zipfile.ZIP_DEFLATED) as package:
            for relative in (
                "project.json",
                "score/score.json",
                "score/score.musicxml",
                "score/score.mid",
                "analysis/validation.json",
            ):
                path = self.store.artifact(project_id, relative)
                if path.exists():
                    package.write(path, relative)
        archive.with_suffix(".zip.tmp").replace(archive)
