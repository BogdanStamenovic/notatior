from __future__ import annotations

import hashlib
import platform
import shutil
import stat
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from .config import tool_root

MUSESCORE_VERSION = "4.7.2.260525085"
MUSESCORE_URL = (
    "https://github.com/musescore/MuseScore/releases/download/v4.7.2/"
    f"MuseScore-Studio-{MUSESCORE_VERSION}-x86_64.AppImage"
)
MUSESCORE_SHA256 = "9c1c2c2db1a7dc830b1bccf530f392de9bff47022826596b3b2117fc19cd73f5"
FFMPEG_URL = (
    "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-linux64-gpl.tar.xz"
)
FFMPEG_SHA256 = "74e284aa52dcadc2d6aea59cf646aa78cc57a7ef446d7d1b1d6ce5d294f74e48"


class BootstrapError(RuntimeError):
    pass


def _download(url: str, destination: Path, sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as target:
            while block := response.read(1024 * 1024):
                digest.update(block)
                target.write(block)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    actual = digest.hexdigest()
    if actual != sha256:
        partial.unlink(missing_ok=True)
        raise BootstrapError(f"Checksum mismatch for {url}: expected {sha256}, got {actual}")
    partial.replace(destination)


def _install_ffmpeg(root: Path) -> None:
    destination = root / "ffmpeg"
    if (destination / "ffmpeg").exists() and (destination / "ffprobe").exists():
        return
    archive = root / "downloads" / "ffmpeg.tar.xz"
    if not archive.exists():
        _download(FFMPEG_URL, archive, FFMPEG_SHA256)
    extract = root / "ffmpeg-extract"
    shutil.rmtree(extract, ignore_errors=True)
    extract.mkdir(parents=True)
    with tarfile.open(archive) as package:
        package.extractall(extract, filter="data")
    binaries = list(extract.glob("**/bin/ffmpeg"))
    probes = list(extract.glob("**/bin/ffprobe"))
    if not binaries or not probes:
        raise BootstrapError("FFmpeg archive did not contain expected binaries")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binaries[0], destination / "ffmpeg")
    shutil.copy2(probes[0], destination / "ffprobe")
    shutil.rmtree(extract)


def _install_musescore(root: Path) -> None:
    destination = root / "musescore"
    if (destination / "AppRun").exists():
        return
    image = root / "downloads" / "musescore.AppImage"
    if not image.exists():
        _download(MUSESCORE_URL, image, MUSESCORE_SHA256)
    image.chmod(image.stat().st_mode | stat.S_IXUSR)
    extract_parent = root / "musescore-extract"
    shutil.rmtree(extract_parent, ignore_errors=True)
    extract_parent.mkdir(parents=True)
    result = subprocess.run(
        [str(image), "--appimage-extract"],
        cwd=extract_parent,
        capture_output=True,
        text=True,
        check=False,
    )
    extracted = extract_parent / "squashfs-root"
    if result.returncode or not (extracted / "AppRun").exists():
        raise BootstrapError(f"MuseScore extraction failed: {result.stderr[-1000:]}")
    shutil.rmtree(destination, ignore_errors=True)
    extracted.replace(destination)
    shutil.rmtree(extract_parent, ignore_errors=True)


def bootstrap(*, skip_large_tools: bool = False) -> dict:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise BootstrapError("The bundled toolchain currently supports x86-64 Linux only")
    root = tool_root()
    root.mkdir(parents=True, exist_ok=True)
    if not skip_large_tools:
        _install_ffmpeg(root)
        _install_musescore(root)
    return doctor()


def _version(command: list[str]) -> dict:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        output = (result.stdout or result.stderr).splitlines()
        return {"ok": result.returncode == 0, "version": output[0] if output else "unknown"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}


def doctor() -> dict:
    from .config import ffmpeg_path, ffprobe_path, musescore_path

    return {
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "ffmpeg": _version([ffmpeg_path(), "-version"]),
        "ffprobe": _version([ffprobe_path(), "-version"]),
        "musescore": _version([musescore_path(), "--version"]),
        "tools": str(tool_root()),
    }
