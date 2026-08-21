from __future__ import annotations

import re

import requests

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
CHUNK_SIZE = 450


def is_mostly_chinese(text: str) -> bool:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z]", text))
    return chinese >= letters and chinese > 0


def _split_chunks(text: str) -> list[str]:
    text = text.strip()
    if len(text) <= CHUNK_SIZE:
        return [text] if text else []

    chunks: list[str] = []
    buf = ""
    for part in re.split(r"(\n+)", text):
        if len(buf) + len(part) <= CHUNK_SIZE:
            buf += part
            continue
        if buf.strip():
            chunks.append(buf)
        if len(part) <= CHUNK_SIZE:
            buf = part
        else:
            for i in range(0, len(part), CHUNK_SIZE):
                chunks.append(part[i : i + CHUNK_SIZE])
            buf = ""
    if buf.strip():
        chunks.append(buf)
    return chunks


def translate(text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    langpair = "zh-CN|en" if is_mostly_chinese(text) else "en|zh-CN"
    parts: list[str] = []
    for chunk in _split_chunks(text):
        try:
            resp = requests.get(
                MYMEMORY_URL,
                params={"q": chunk, "langpair": langpair},
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"翻译接口请求失败：{exc}") from exc

        status = data.get("responseStatus")
        translated = (data.get("responseData") or {}).get("translatedText") or ""
        if status != 200 or not translated:
            detail = data.get("responseDetails") or data.get("quotaFinished") or "未知错误"
            raise RuntimeError(f"翻译失败：{detail}")
        if "MYMEMORY WARNING" in translated.upper():
            raise RuntimeError("免费翻译额度已用尽，请稍后再试。")
        parts.append(translated)
    return "\n".join(parts).strip()
