from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

from .bootstrap import bootstrap, doctor
from .models import StageName, StageStatus
from .pipeline import Pipeline
from .store import ProjectStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notatior", description="Turn piano videos into notation")
    subcommands = parser.add_subparsers(dest="command")
    serve = subcommands.add_parser("serve", help="Run the local web application")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true")
    transcribe = subcommands.add_parser("transcribe", help="Create and process a project")
    transcribe.add_argument("source")
    transcribe.add_argument("--output", type=Path)
    transcribe.add_argument("--bpm", type=float)
    transcribe.add_argument("--meter")
    transcribe.add_argument("--accept-draft", action="store_true")
    subcommands.add_parser("doctor", help="Inspect media/rendering dependencies")
    setup = subcommands.add_parser("bootstrap", help="Install portable media/rendering tools")
    setup.add_argument("--skip-large-tools", action="store_true", help=argparse.SUPPRESS)
    return parser


def _serve(host: str, port: int, open_browser: bool) -> int:
    try:
        import uvicorn
    except ImportError:
        print("Install Notatior dependencies before starting the server", file=sys.stderr)
        return 2
    from .server import create_app

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0


def _transcribe(args) -> int:
    store = ProjectStore()
    project = store.create(args.source)
    project.settings.update(bpm=args.bpm, meter=args.meter)
    store.save(project)
    pipeline = Pipeline(store)
    try:
        while True:
            pipeline.run(project.id, stop_at_review=not args.accept_draft)
            project = store.get(project.id)
            review = next(
                (name for name, state in project.stages.items() if state["status"] == StageStatus.REVIEW),
                None,
            )
            if review and args.accept_draft:
                pipeline.approve(project.id, review)
                continue
            break
    except Exception as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1
    project = store.get(project.id)
    if args.output and project.stages[StageName.EXPORT.value]["status"] == StageStatus.COMPLETE:
        args.output.mkdir(parents=True, exist_ok=True)
        for item in store.artifact(project.id, "exports").iterdir():
            if item.is_file():
                (args.output / item.name).write_bytes(item.read_bytes())
    print(json.dumps({"project_id": project.id, "stages": project.stages}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"
    if command == "serve":
        return _serve(getattr(args, "host", "127.0.0.1"), getattr(args, "port", 8765), not getattr(args, "no_browser", False))
    if command == "transcribe":
        return _transcribe(args)
    if command == "doctor":
        report = doctor()
        print(json.dumps(report, indent=2))
        return 0 if report["ffmpeg"]["ok"] and report["musescore"]["ok"] else 1
    if command == "bootstrap":
        report = bootstrap(skip_large_tools=args.skip_large_tools)
        print(json.dumps(report, indent=2))
        return 0
    parser.error(f"Unknown command {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

