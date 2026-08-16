# Portable third-party tools

Notatior's Ownbox bootstrap downloads these tools; they are not committed to this repository.

- **FFmpeg / FFprobe**, GPL build from the yt-dlp FFmpeg-Builds release channel. FFmpeg source and
  license information: <https://ffmpeg.org/legal.html>
- **MuseScore Studio 4.7.2**, official x86-64 AppImage, GPL-3.0. Source and releases:
  <https://github.com/musescore/MuseScore>

Download locations and SHA-256 checksums are pinned in `src/notatior/bootstrap.py`. The MuseScore
AppImage is extracted during setup so it works without FUSE. These programs remain separate
executables invoked by Notatior for media processing and score rendering.
