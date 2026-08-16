from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .config import ffmpeg_path, ffprobe_path


class MediaError(RuntimeError):
    pass


def is_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def run_checked(
    command: list[str], *, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        raise MediaError(f"Command failed: {command[0]}: {stderr[-1000:]}") from exc


def probe(path: Path) -> dict:
    result = run_checked(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    return json.loads(result.stdout)


def acquire(source: str, destination: Path) -> tuple[Path, dict]:
    destination.mkdir(parents=True, exist_ok=True)
    if is_url(source):
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise MediaError("yt-dlp is required for URL sources") from exc
        template = str(destination / "video.%(ext)s")
        options = {
            "format": "bv*[height<=1080]+ba/b[height<=1080]",
            "outtmpl": template,
            "merge_output_format": "mp4",
            "ffmpeg_location": str(Path(ffmpeg_path()).parent),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with YoutubeDL(options) as downloader:
                info = downloader.extract_info(source, download=True)
        except Exception as exc:
            raise MediaError(f"Could not download source: {exc}") from exc
        files = sorted(destination.glob("video.*"))
        if not files:
            raise MediaError("Downloader finished without producing a video")
        video = files[0]
        metadata = {
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "webpage_url": info.get("webpage_url", source),
            "duration": info.get("duration"),
        }
    else:
        original = Path(source).expanduser().resolve()
        if not original.is_file():
            raise MediaError(f"Video does not exist: {original}")
        video = destination / f"video{original.suffix.lower()}"
        if original != video:
            shutil.copy2(original, video)
        metadata = {"title": original.stem, "original_path": str(original)}
    metadata["probe"] = probe(video)
    return video, metadata


def extract_audio(video: Path, output: Path, sample_rate: int = 22050) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            ffmpeg_path(),
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    return output
