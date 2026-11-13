"""One function, many providers: ask_json(system, user, images=None) -> dict.

LLM_PROVIDER is tried first, then LLM_FALLBACK. Images (paths to JPEG/PNG)
are only sent to providers that can see them (gemini, claude, ollama-vision).
"""
import base64
import json
import logging
import re
import time
from pathlib import Path

import requests

from .config import S

log = logging.getLogger("llm")
VISION = {"gemini", "claude", "mock"}


class LLMError(RuntimeError):
    pass


def _b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def _mime(path):
    return "image/png" if str(path).lower().endswith(".png") else "image/jpeg"


def _post(url, **kw):
    for attempt in range(3):
        try:
            r = requests.post(url, timeout=120, **kw)
        except requests.RequestException as e:
            if attempt == 2:
                raise LLMError(f"network: {e}") from e
            time.sleep(4 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(8 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:400]}")
        return r.json()
    raise LLMError("retries exhausted")


def _gemini(system, user, images):
    if not S.gemini_key:
        raise LLMError("GEMINI_API_KEY missing")
    parts = [{"text": user}]
    for p in images or []:
        parts.append({"inline_data": {"mime_type": _mime(p), "data": _b64(p)}})
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{S.gemini_model}:generateContent"
    data = _post(url, params={"key": S.gemini_key}, json=body)
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as e:
        raise LLMError(f"gemini: unexpected reply {str(data)[:300]}") from e


def _groq(system, user, images):
    if images:
        raise LLMError("groq cannot read images")
    if not S.groq_key:
        raise LLMError("GROQ_API_KEY missing")
    body = {
        "model": S.groq_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.6,
    }
    data = _post("https://api.groq.com/openai/v1/chat/completions",
                 headers={"Authorization": f"Bearer {S.groq_key}"}, json=body)
    return data["choices"][0]["message"]["content"]


def _claude(system, user, images):
    if not S.anthropic_key:
        raise LLMError("ANTHROPIC_API_KEY missing")
    content = [{"type": "image", "source": {"type": "base64", "media_type": _mime(p), "data": _b64(p)}}
               for p in images or []]
    content.append({"type": "text", "text": user + "\n\nReply with JSON only."})
    body = {"model": S.claude_model, "max_tokens": 4000, "system": system,
            "messages": [{"role": "user", "content": content}]}
    data = _post("https://api.anthropic.com/v1/messages",
                 headers={"x-api-key": S.anthropic_key, "anthropic-version": "2023-06-01"}, json=body)
    return "".join(b.get("text", "") for b in data.get("content", []))


def _ollama(system, user, images):
    msg = {"role": "user", "content": user}
    if images:
        msg["images"] = [_b64(p) for p in images]
    body = {"model": S.ollama_model, "stream": False, "format": "json",
            "messages": [{"role": "system", "content": system}, msg]}
    data = _post(f"{S.ollama_url}/api/chat", json=body)
    return data["message"]["content"]


def _mock(system, user, images):
    from . import mock_data
    return json.dumps(mock_data.reply(system, user, images))


PROVIDERS = {"gemini": _gemini, "groq": _groq, "claude": _claude, "ollama": _ollama, "mock": _mock}


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise LLMError(f"not JSON: {text[:200]}")


def can_see_images():
    return S.llm_provider in VISION or S.llm_fallback in VISION


def ask_json(system, user, images=None):
    order = [S.llm_provider] + ([S.llm_fallback] if S.llm_fallback and S.llm_fallback != S.llm_provider else [])
    if images:
        order = [p for p in order if p in VISION or p == "ollama"]
        if not order:
            raise LLMError("no configured provider can read images")
    errors = []
    for name in order:
        fn = PROVIDERS.get(name)
        if not fn:
            errors.append(f"{name}: unknown provider")
            continue
        try:
            out = parse_json(fn(system, user, images))
            log.info("llm ok via %s", name)
            return out
        except (LLMError, ValueError, KeyError) as e:
            log.warning("llm %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    raise LLMError("; ".join(errors))
