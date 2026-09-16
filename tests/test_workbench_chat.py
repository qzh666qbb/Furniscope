from pathlib import Path
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.services.chat_sse import stream_chat_events
from furniscope_api.services.workbench_chat import (
    build_workbench_thinking,
    local_workbench_chat_answer,
    parse_model_chat_text,
    requested_product_label,
)


LOUNGE = [
    {"product_id": 1, "sku": "HF-A0689", "name": "休闲椅 HF-A0689", "category_code": "chair"},
    {"product_id": 2, "sku": "HF-A0590", "name": "休闲椅 HF-A0590", "category_code": "chair"},
]
US_DATASET = [{
    "dataset_id": 9, "name": "美国授权市场数据", "market_country": "US",
    "status": "ready", "listing_count": 12, "valid_review_count": 80,
}]


def test_question_label_is_not_the_whole_sentence():
    assert requested_product_label("说说当前休闲椅在美国市场怎么样") == "休闲椅"


def test_missing_us_market_prompts_insights():
    answer = local_workbench_chat_answer(
        "说说当前休闲椅在美国市场怎么样",
        products=LOUNGE,
        datasets=[],
    )
    assert "美国" in answer["answer"]
    assert "没有" in answer["answer"] or "授权" in answer["answer"]
    assert answer["suggested_action"] == "insights"
    assert any("市场洞察" in item for item in answer["suggested_prompts"])
    assert "说说当前休闲椅在 怎么样" not in answer["answer"]
    assert any(item["sku"] == "HF-A0689" for item in answer["product_candidates"])


def test_ready_us_market_lists_skus():
    answer = local_workbench_chat_answer(
        "说说当前休闲椅在美国市场怎么样",
        products=LOUNGE,
        datasets=US_DATASET,
    )
    assert "美国授权市场数据" in answer["answer"]
    assert answer.get("suggested_action") in {"", None}
    assert any("HF-A0689" in item for item in answer["suggested_prompts"])


def test_fetch_followup_goes_to_insights():
    answer = local_workbench_chat_answer(
        "帮我获取一下美国市场数据",
        products=LOUNGE,
        datasets=[],
    )
    assert answer["suggested_action"] == "insights"
    assert "市场洞察" in answer["answer"]
    assert "爬取公开" in answer["answer"] or "不会在对话里直接爬取" in answer["answer"]


def test_thinking_mentions_catalog_and_missing_us_market():
    thinking = build_workbench_thinking(
        "说说当前休闲椅在美国市场怎么样",
        products=LOUNGE,
        datasets=[],
    )
    assert "休闲椅" in thinking
    assert "美国" in thinking
    assert "HF-A0689" in thinking
    assert "没有" in thinking or "授权" in thinking


def test_parse_model_chat_text_extracts_json_answer():
    parsed = parse_model_chat_text('```json\n{"answer":"目录已命中休闲椅","title":"休闲椅"}\n```')
    assert parsed["answer"] == "目录已命中休闲椅"


def test_stream_events_emit_thinking_then_fallback_answer():
    class FakeClient:
        closed = False

        async def stream_chat(self, messages):
            raise RuntimeError("MODEL_ROUTER_FAILED")
            yield {}

        async def close(self):
            self.closed = True

    async def run():
        client = FakeClient()
        chunks = []
        async for item in stream_chat_events(
            thinking="1. 核对目录",
            messages=[],
            client=client,
            fallback={"answer": "请先选产品"},
            finalize=lambda text: {"answer": text},
        ):
            chunks.append(item)
        body = "".join(chunks)
        assert "event: thinking" in body
        assert "核对目录" in body
        assert "event: thinking_done" in body
        assert "请先选产品" in body
        assert "event: done" in body
        assert client.closed is True

    asyncio.run(run())
