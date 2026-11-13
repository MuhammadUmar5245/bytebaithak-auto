"""Renders a post JSON into 1080x1350 JPEG slides (Instagram's API accepts JPEG only)."""
import html
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from playwright.sync_api import sync_playwright

from .config import PILLARS, S

TEMPLATES = Path(__file__).parent / "templates"


def _hl(text):
    """Escape, then turn **word** into an accent-coloured span."""
    safe = html.escape(str(text or ""))
    return Markup(re.sub(r"\*\*(.+?)\*\*", r'<span class="hl">\1</span>', safe))


def _codefmt(code):
    """Escape code and colour a leading shell prompt."""
    lines = []
    for line in str(code or "").splitlines():
        e = html.escape(line)
        if e.startswith("$ "):
            e = "<b>$</b> " + e[2:]
        lines.append(e)
    return Markup("\n".join(lines))


_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
_env.filters["hl"] = _hl
_env.filters["codefmt"] = _codefmt


def render(post, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    label, accent = PILLARS.get(post.get("pillar"), ("TECH", "#22D3EE"))
    total = len(post["slides"]) + 2
    page_html = _env.get_template("carousel.html.j2").render(
        post=post, total=total, accent=accent, handle=S.handle,
        kicker=(post.get("cover", {}).get("kicker") or label)[:24],
        fonts=(S.assets_dir / "fonts").resolve().as_uri())
    src = out_dir / "slides.html"
    src.write_text(page_html, encoding="utf-8")

    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        page.goto(src.resolve().as_uri())
        page.evaluate("document.fonts.ready")
        report = page.evaluate("window.fitAll()")
        for i in range(1, total + 1):
            path = out_dir / f"slide-{i:02d}.jpg"
            page.locator(f"#s{i}").screenshot(path=str(path), type="jpeg", quality=93)
            paths.append(path)
        browser.close()

    issues = [f"slide {r['slide']}: {pr}" for r in report for pr in r["problems"]]
    dense = [r["slide"] for r in report if r["scale"] < 0.8]
    return paths, {"layout": report, "overflow": issues, "dense_slides": dense}
