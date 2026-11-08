import json
import os
import re
import urllib.error
import urllib.request

from services.common.env import load_env


DEFAULT_BASE_URL = "http://10.169.145.229:1234/v1"
DEFAULT_MODEL = "google/gemma-4-26b-a4b"
DEFAULT_TIMEOUT = 180


class ModelError(RuntimeError):
    pass


def base_url() -> str:
    load_env()
    return os.environ.get("EXTRACT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def model_name() -> str:
    load_env()
    return os.environ.get("EXTRACT_MODEL", DEFAULT_MODEL)


def timeout_seconds() -> float:
    load_env()
    raw = os.environ.get("EXTRACT_TIMEOUT", str(DEFAULT_TIMEOUT))
    try:
        return float(raw)
    except ValueError:
        return float(DEFAULT_TIMEOUT)


def parse_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model did not return JSON")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("model did not return JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("model JSON was not an object")
    return value


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return ""


def _post(payload: dict, timeout: float) -> str:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        base_url() + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise ModelError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ModelError(str(exc.reason)) from exc
    choices = data.get("choices") or []
    if not choices:
        raise ModelError("model returned no choices")
    text = _message_text(choices[0].get("message") or {})
    if not text.strip():
        raise ModelError("model returned empty content")
    return text


def chat(messages: list, timeout: float | None = None) -> str:
    limit = timeout_seconds() if timeout is None else timeout
    payload = {
        "model": model_name(),
        "temperature": 0,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    try:
        return _post(payload, limit)
    except ModelError as exc:
        if not str(exc).startswith("HTTP 400"):
            raise
        payload.pop("response_format", None)
        return _post(payload, limit)


def complete_json(messages: list, timeout: float | None = None) -> dict:
    content = chat(messages, timeout)
    try:
        return parse_json_object(content)
    except ValueError:
        retried = list(messages) + [
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": "That was not a single JSON object. Reply with only the JSON object.",
            },
        ]
        return parse_json_object(chat(retried, timeout))
