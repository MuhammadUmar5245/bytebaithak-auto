"""Turns a reel script (JSON) into a 1080x1920 MP4: Playwright frames + Kokoro voiceover + ffmpeg.

Scene 1 = hook, then one scene per idea, then the outro. Each scene plays a short entrance animation
(see seek() in reel.html.j2), holds until its narration (plus a short pause) ends, then cuts to the next,
so picture and voice stay in sync.
Everything runs offline in the container; no paid service is used.
"""
import logging
import math
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from playwright.sync_api import sync_playwright

from .config import PILLARS, S
from .render_carousel import _env

log = logging.getLogger("reel")
FPS = 30
LEAD = 0.15         # silence before each line
TAIL = 0.45         # silence after each line
_kokoro = None


def render_frames(post, out_dir):
    """Same contract as render_carousel.render(): returns (frame paths, layout report)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    label, accent = PILLARS.get(post.get("pillar"), ("TECH", "#22D3EE"))
    total = len(post["scenes"]) + 2
    page_html = _env.get_template("reel.html.j2").render(
        post=post, total=total, accent=accent, handle=S.handle,
        kicker=((post.get("hook") or {}).get("kicker") or label)[:24],
        fonts=(S.assets_dir / "fonts").resolve().as_uri())
    src = out_dir / "reel.html"
    src.write_text(page_html, encoding="utf-8")

    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        page.goto(src.resolve().as_uri())
        page.evaluate("document.fonts.ready")
        report = page.evaluate("window.fitAll()")
        for i in range(1, total + 1):
            path = out_dir / f"slide-{i:02d}.jpg"
            page.locator(f"#s{i}").screenshot(path=str(path), type="jpeg", quality=93)
            paths.append(path)
        browser.close()

    issues = [f"scene {r['slide']}: {pr}" for r in report for pr in r["problems"]]
    dense = [r["slide"] for r in report if r["scale"] < 0.8]
    return paths, {"layout": report, "overflow": issues, "dense_slides": dense}


def narration_lines(post):
    lines = [(post.get("hook") or {}).get("say", "")]
    lines += [s.get("say", "") for s in post.get("scenes", [])]
    lines.append((post.get("outro") or {}).get("say", ""))
    return [_speakable(x) for x in lines]


def _speakable(text):
    text = re.sub(r"\*\*|`", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _tts():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(S.models_dir / "kokoro-v1.0.onnx"), str(S.models_dir / "voices-v1.0.bin"))
    return _kokoro


def voiceover(lines, out_dir):
    """One WAV for the whole reel; returns (wav path, seconds each scene lasts)."""
    k = _tts()
    chunks, durations, sr = [], [], 24000
    for line in lines:
        if line:
            samples, sr = k.create(line, voice=S.reel_voice, speed=S.reel_speed, lang="en-us")
        else:
            samples = np.zeros(int(sr * 1.5), dtype=np.float32)
        chunk = np.concatenate([np.zeros(int(sr * LEAD), np.float32), samples.astype(np.float32),
                                np.zeros(int(sr * TAIL), np.float32)])
        chunks.append(chunk)
        durations.append(len(chunk) / sr)
    wav = Path(out_dir) / "voice.wav"
    sf.write(str(wav), np.concatenate(chunks), sr)
    return wav, durations


def motion_frames(out_dir, durs):
    """Screenshot each scene's entrance animation, frame by frame, from the reel.html render_frames wrote.
    Returns one (folder, frame count) per scene. The intro never runs longer than the scene's narration."""
    out_dir = Path(out_dir)
    anim = out_dir / "anim"
    shutil.rmtree(anim, ignore_errors=True)
    seqs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        page.goto((out_dir / "reel.html").resolve().as_uri())
        page.evaluate("document.fonts.ready")
        page.evaluate("window.fitAll()")
        for i, d in enumerate(durs, 1):
            folder = anim / f"s{i:02d}"
            folder.mkdir(parents=True)
            count = max(1, min(math.ceil(page.evaluate(f"window.introLength({i})") * FPS) + 1, int(d * FPS)))
            scene = page.locator(f"#s{i}")
            for f in range(count):
                t = f / FPS if f < count - 1 else 999      # the last frame is always the finished scene
                page.evaluate(f"window.seek({i}, {t})")
                scene.screenshot(path=str(folder / f"{f:03d}.jpg"), type="jpeg", quality=90)
            seqs.append((folder, count))
        browser.close()
    return seqs


def make_video(post, frames, out_dir):
    """Frames + narration -> reel.mp4 (H.264 + AAC, 1080x1920, 30 fps, as Instagram wants).
    Each scene plays its entrance animation, then holds its last frame until its narration ends;
    scenes cut straight into each other, so picture and voice stay in sync."""
    out_dir = Path(out_dir)
    lines = narration_lines(post)
    if len(lines) != len(frames):
        raise ValueError(f"{len(frames)} frames but {len(lines)} narration lines")
    wav, durs = voiceover(lines, out_dir)
    seqs = motion_frames(out_dir, durs)

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for folder, _ in seqs:
        cmd += ["-framerate", str(FPS), "-i", str(folder / "%03d.jpg")]
    cmd += ["-i", str(wav)]
    n = len(seqs)
    parts = [f"[{i}:v]format=yuv420p,setsar=1,"
             f"tpad=stop_mode=clone:stop_duration={max(d - count / FPS, 0):.3f}[v{i}]"
             for i, ((_, count), d) in enumerate(zip(seqs, durs))]
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", f"{n}:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-shortest", "-movflags", "+faststart", str(out_dir / "reel.mp4")]
    res = subprocess.run(cmd, capture_output=True, text=True)
    shutil.rmtree(out_dir / "anim", ignore_errors=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr[-600:]}")
    video = out_dir / "reel.mp4"
    total = round(sum(durs), 1)
    log.info("reel %s: %.1fs, %d scenes, %.1f MB", out_dir.name, total, n, video.stat().st_size / 1e6)
    return {"video": str(video), "seconds": total, "scene_seconds": [round(d, 2) for d in durs],
            "mb": round(video.stat().st_size / 1e6, 2)}
