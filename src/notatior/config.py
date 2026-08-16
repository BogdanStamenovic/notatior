from __future__ import annotations

import os
from pathlib import Path


def data_root() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return Path(os.environ.get("NOTATIOR_DATA_DIR", base / "notatior"))


def project_root() -> Path:
    return data_root() / "projects"


def tool_root() -> Path:
    configured = os.environ.get("NOTATIOR_TOOL_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / ".tools"


def ffmpeg_path() -> str:
    configured = os.environ.get("NOTATIOR_FFMPEG")
    if configured:
        return configured
    candidate = tool_root() / "ffmpeg" / "ffmpeg"
    return str(candidate if candidate.exists() else "ffmpeg")


def ffprobe_path() -> str:
    configured = os.environ.get("NOTATIOR_FFPROBE")
    if configured:
        return configured
    candidate = tool_root() / "ffmpeg" / "ffprobe"
    return str(candidate if candidate.exists() else "ffprobe")


def musescore_path() -> str:
    configured = os.environ.get("NOTATIOR_MUSESCORE")
    if configured:
        return configured
    candidates = [
        tool_root() / "musescore" / "AppRun",
        Path("/usr/bin/mscore"),
        Path("/usr/bin/musescore"),
        Path("/usr/bin/mscore4portable"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "musescore"
