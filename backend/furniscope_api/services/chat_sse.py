"""Server-sent events helpers for streaming workbench and task chat."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from .model_router_client import ServiceModelRouterClient
from .workbench_chat import parse_model_chat_text

FinalizeFn = Callable[[str], dict[str, Any]]


def sse(event: str, data: dict[str, Any], *, event_id: str | None = None) -> str:
    identifier = f"id: {event_id}\n" if event_id else ""
    return (
        f"{identifier}event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


def text_chunks(text: str, size: int = 16) -> list[str]:
    raw = text or ""
    if not raw:
        return []
    return [raw[index:index + size] for index in range(0, len(raw), size)]


def _looks_like_json(text: str) -> bool:
    stripped = (text or "").lstrip()
    return stripped.startswith("{") or stripped.startswith("```")


async def stream_chat_events(
    *,
    thinking: str,
    messages: list[dict[str, Any]],
    client: ServiceModelRouterClient,
    fallback: dict[str, Any],
    finalize: FinalizeFn,
) -> AsyncIterator[str]:
    del thinking
    try:
        yield ": connected\n\n"
        yield sse("progress", {
            "stage": "context_ready",
            "message": "上下文已核对，正在生成回答",
        })
        answer = ""
        buffered = ""
        json_mode: bool | None = None

        try:
            async for delta in client.stream_chat(messages=messages):
                content = str(delta.get("content") or "")
                if not content:
                    continue
                if json_mode is None:
                    json_mode = _looks_like_json(content if not buffered else buffered + content)
                if json_mode:
                    buffered += content
                    continue
                answer += content
                yield sse("answer_delta", {"delta": content})
        except Exception:
            answer = ""
            buffered = ""
            json_mode = False

        parsed = parse_model_chat_text(buffered) if json_mode and buffered else None
        if parsed and str(parsed.get("answer") or "").strip():
            answer = str(parsed.get("answer") or "").strip()
            for part in text_chunks(answer):
                yield sse("answer_delta", {"delta": part})
                await asyncio.sleep(0.01)
            payload = finalize(json.dumps(parsed, ensure_ascii=False))
        elif str(answer).strip():
            payload = finalize(answer)
        else:
            answer = str(fallback.get("answer") or "")
            for part in text_chunks(answer):
                yield sse("answer_delta", {"delta": part})
                await asyncio.sleep(0.01)
            payload = dict(fallback)
            payload["answer"] = answer
        yield sse("done", payload)
    except Exception:
        yield sse("error", {"message": "暂时无法完成这次问询。"})
    finally:
        await client.close()
