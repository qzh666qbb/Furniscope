import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.services.customer_memory import extract_memory_candidates


def test_extracts_only_explicit_customer_constraints():
    rows = extract_memory_candidates("我们的目标市场是美国，成本上限为 40 美元，更看重可拆洗")
    assert {row["memory_key"] for row in rows} == {"target_market", "unit_cost_limit", "customer_preference"}
    assert any(row["memory_value"]["value"] == "美国" for row in rows)


def test_does_not_turn_normal_question_into_memory():
    assert extract_memory_candidates("美国市场的休闲椅怎么样？") == []
