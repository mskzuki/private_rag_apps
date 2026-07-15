"""private_rag_apps.evals.routing の純粋ロジック部分の単体テスト
（make eval-routing。docs/specs/m7_adaptive_routing.md rev.3 §7.2）。

routing.py 自体は evals/__main__.py と同様、DB・Voyage・LLM呼び出しを伴う
オーケストレーションスクリプトのため、既存の評価ハーネス群（__main__.py）と同じ規約に
従い、外部I/Oを伴う本体はユニットテストの対象外とする。ここでは純粋関数
(summarize_route_predictions)のみを対象にする。"""

from private_rag_apps.evals.routing import summarize_route_predictions


def test_summarize_route_predictions_counts_misses_and_wrongs() -> None:
    records = [
        {"id": "a", "expected_route": "grounded", "predicted_route": "grounded"},
        {"id": "b", "expected_route": "grounded", "predicted_route": "direct"},  # miss
        {"id": "c", "expected_route": "direct", "predicted_route": "direct"},
        {"id": "d", "expected_route": "direct", "predicted_route": "grounded"},  # wrong
    ]

    result = summarize_route_predictions(records)

    assert result["grounded_total"] == 2
    assert result["direct_total"] == 2
    assert result["grounded_miss"] == 1
    assert result["grounded_miss_ids"] == ["b"]
    assert result["direct_wrong"] == 1
    assert result["direct_wrong_ids"] == ["d"]


def test_summarize_route_predictions_empty_records() -> None:
    result = summarize_route_predictions([])

    assert result["grounded_total"] == 0
    assert result["direct_total"] == 0
    assert result["grounded_miss"] == 0
    assert result["direct_wrong"] == 0
    assert result["grounded_miss_ids"] == []
    assert result["direct_wrong_ids"] == []


def test_summarize_route_predictions_all_correct() -> None:
    records = [
        {"id": "a", "expected_route": "grounded", "predicted_route": "grounded"},
        {"id": "b", "expected_route": "direct", "predicted_route": "direct"},
    ]

    result = summarize_route_predictions(records)

    assert result["grounded_miss"] == 0
    assert result["direct_wrong"] == 0
