"""Offline tests (no API keys needed):  LLM_PROVIDER=mock python -m tests.test_offline"""
import copy
import os
import tempfile

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("LLM_FALLBACK", "mock")

from app import checker, research  # noqa: E402
from app.mock_data import CLOUD  # noqa: E402
from app.render_carousel import render  # noqa: E402


def test_rules_catch_problems():
    bad = copy.deepcopy(CLOUD)
    bad["slides"][0]["body"] = "word " * 60
    bad["slides"][1]["code"] = "docker run --rm -it --name a-very-long-container-name nginx"
    bad["hashtags"] = ["#ok", "bad tag"]
    bad["caption"] += " This is a game-changer."
    err, _ = checker.rules(bad)
    text = " | ".join(err)
    assert "over 45 words" in text and "code line" in text and "hashtag" in text and "hype" in text, text
    news = dict(copy.deepcopy(CLOUD), pillar="ai_news", sources=[])
    assert any("without a source" in e for e in checker.rules(news)[0])
    assert checker.rules(CLOUD)[0] == []
    print("rules: ok")


def test_render_shrinks_and_flags_overflow():
    dense = copy.deepcopy(CLOUD)
    dense["slides"][0]["body"] = " ".join(["Containers share the host kernel so they start fast."] * 7)
    dense["slides"][0]["bullets"] = ["First point here", "Second point here", "Third point here", "Fourth one"]
    broken = copy.deepcopy(CLOUD)
    broken["slides"][1]["code"] = "x" * 80
    with tempfile.TemporaryDirectory() as d:
        _, rep = render(dense, d)
        assert rep["overflow"] == [], rep["overflow"]
        assert rep["layout"][1]["scale"] < 1, rep["layout"]  # slide 2 was shrunk to fit
        _, rep2 = render(broken, d)
        assert any("code line too wide" in o for o in rep2["overflow"]), rep2["overflow"]
    print("render fit/overflow: ok")


def test_news_picker():
    items = [
        {"source": "OpenAI", "weight": 3, "title": "OpenAI releases a new open model for coding", "link": "https://a", "summary": "", "when": None},
        {"source": "The Verge AI", "weight": 1, "title": "OpenAI releases new open coding model", "link": "https://b", "summary": "", "when": None},
        {"source": "Hacker News AI", "weight": 1, "title": "Show HN: my weekend project", "link": "https://c", "summary": "", "when": None},
        {"source": "Blog", "weight": 3, "title": "Insecure link story", "link": "http://d", "summary": "", "when": None},
    ]
    assert research.pick_top(items)["link"] == "https://a"
    assert research.pick_top(items, avoid_titles=["OpenAI releases a new open model for coding"])["link"] != "https://a"
    assert research._strip("<p>Hi <script>x()</script><b>there</b></p>") == "Hi there"
    print("news picker: ok")


def test_reel():
    """Rules, frames, Kokoro voice and ffmpeg video, all offline."""
    import json
    import subprocess
    from app import render_reel
    from app.mock_data import REEL
    assert checker.reel_rules(REEL)[0] == [], checker.reel_rules(REEL)[0]
    bad = copy.deepcopy(REEL)
    bad["scenes"][0]["say"] = "word " * 50
    bad["hook"]["say"] = "see https://example.com"
    text = " | ".join(checker.reel_rules(bad)[0])
    assert "narration over 40 words" in text and "link, hashtag" in text, text
    with tempfile.TemporaryDirectory() as d:
        frames, layout = render_reel.render_frames(REEL, d)
        assert len(frames) == len(REEL["scenes"]) + 2 and layout["overflow"] == [], layout
        vid = render_reel.make_video(REEL, frames, d)
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
             "-of", "json", vid["video"]], capture_output=True, text=True).stdout)
        streams = {s["codec_type"]: s for s in probe["streams"]}
        assert streams["video"]["codec_name"] == "h264" and streams["video"]["width"] == 1080
        assert streams["video"]["height"] == 1920 and streams["audio"]["codec_name"] == "aac"
        length = float(probe["format"]["duration"])
        assert abs(length - vid["seconds"]) < 0.5 and 15 < length < 60, (length, vid)
    print(f"reel: ok ({vid['seconds']}s, {vid['mb']} MB)")


if __name__ == "__main__":
    test_rules_catch_problems()
    test_render_shrinks_and_flags_overflow()
    test_news_picker()
    test_reel()
    print("ALL OFFLINE TESTS PASSED")
