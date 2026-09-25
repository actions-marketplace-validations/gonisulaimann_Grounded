"""Render demo/firewall.gif deterministically (stdlib + Pillow + ffmpeg).

Runs the firewall demo's exact steps in a temp git repo against the
current source tree, captures verbatim output, and renders cumulative
terminal frames. Aborts unless the lie is caught (a GIF of a passing
demo, never theater). Regenerate: python3 demo/render_gif.py
Requires: Pillow, ffmpeg, git, and `grounded` importable from src/.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

BG = (13, 17, 23)
FG = (230, 237, 243)
GREEN = (126, 231, 135)
RED = (255, 123, 114)
DIM = (139, 148, 158)
FONT_PATH = "/System/Library/Fonts/Monaco.ttf"
FONT_SIZE = 16
PAD = 14
LINE_H = 22


def sh(cmd: list[str], cwd: Path, env: dict | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    return proc.stdout + proc.stderr


def main() -> int:
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415 (build-time only)

    if not shutil.which("git") or not shutil.which("ffmpeg"):
        print("need git and ffmpeg on PATH")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="firewall-gif"))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    binpy = [sys.executable, "-m", "grounded.cli"]
    git = ["git", "-c", "user.email=d@d", "-c", "user.name=d"]

    def grounded(*args: str) -> str:
        return sh([*binpy, *args, "--no-color"], cwd=tmp, env=env)

    (tmp / "pkg").mkdir()
    sh([*git, "init", "-q", "."], cwd=tmp)
    (tmp / "pkg" / "core.py").write_text("def get_user(uid):\n    return uid\n")
    (tmp / "pkg" / "views.py").write_text(
        "from .core import get_user\n\n\ndef show(uid):\n    return get_user(uid)\n")
    (tmp / "pkg" / "__init__.py").write_text("")
    sh([*git, "add", "-A"], cwd=tmp)
    sh([*git, "commit", "-qm", "baseline"], cwd=tmp)

    clean = grounded("scan", ".", "--quiet")
    (tmp / "pkg" / "core.py").write_text("def get_account(uid):\n    return uid\n")
    caught = grounded("scan", ".", "--changed")
    impact = grounded("impact", "get_user", ".")
    if "[stale-import]" not in caught or "get_user" not in caught:
        print("refusing to render: lie was NOT caught")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1

    frames: list[tuple[list[str], float]] = [
        (["$ grounded scan . --quiet", clean.rstrip()], 2.5),
        (["# agent renames get_user -> get_account in core.py only"], 1.5),
        (["$ grounded scan . --changed", caught.rstrip()], 5.0),
        (["$ grounded impact get_user .", impact.rstrip()], 5.0),
    ]
    # cumulative terminal: each frame keeps everything above it
    cumulative: list[list[str]] = []
    seen: list[str] = []
    for lines, dur in frames:
        seen = seen + [ln for ln in lines if ln.strip()]
        cumulative.append((seen, dur))

    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    width = max(
        int(font.getlength(ln)) for lines, _ in cumulative for ln in lines for ln in ln.splitlines()
    ) + PAD * 2
    width = min(max(width, 400), 1200)
    height = 480
    pngs = []
    for i, (lines, _dur) in enumerate(cumulative):
        flat: list[str] = []
        for block in lines:
            flat.extend(block.splitlines())
        flat = [ln for ln in flat if ln.strip()][:20]
        img = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(img)
        for j, ln in enumerate(flat):
            color = DIM if ln.startswith(("$", "#")) else FG
            if ln.startswith("LIE"):
                color = RED
            elif ln.startswith("grounded:"):
                color = GREEN
            draw.text((PAD, PAD + j * LINE_H), ln, font=font, fill=color)
        path = tmp / f"frame{i:02d}.png"
        img.save(path)
        pngs.append((path, _dur))

    # durations -> per-frame repeats at 2 fps
    gif_frames = []
    for path, dur in pngs:
        gif_frames.extend([path] * max(1, round(dur * 2)))
    lst = tmp / "frames.txt"
    lst.write_text("".join(f"file '{p}'\nduration 1.0\n" for p in gif_frames))
    out = REPO / "demo" / "firewall.gif"
    pal = tmp / "palette.png"
    common = ["-hide_banner", "-loglevel", "error", "-y"]
    proc = subprocess.run(
        ["ffmpeg", *common, "-f", "concat", "-safe", "0", "-i", str(lst),
         "-vf", "scale=900:-1:flags=lanczos,palettegen", str(pal)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:])
        return 1
    proc = subprocess.run(
        ["ffmpeg", *common, "-f", "concat", "-safe", "0", "-i", str(lst),
         "-i", str(pal), "-lavfi",
         "scale=900:-1:flags=lanczos [s]; [s][1:v] paletteuse",
         "-gifflags", "+transdiff", str(out)],
        capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        print(proc.stderr[-2000:])
        return 1
    print(f"wrote {out} ({out.stat().st_size // 1024} KiB)")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
