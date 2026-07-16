"""private_rag_apps.evals.routing の純粋ロジック部分の単体テスト
（make eval-routing。docs/specs/m7_adaptive_routing.md rev.3 §7.2）。

routing.py 自体は evals/__main__.py と同様、DB・Voyage・LLM呼び出しを伴う
オーケストレーションスクリプトのため、既存の評価ハーネス群（__main__.py）と同じ規約に
従い、外部I/Oを伴う本体はユニットテストの対象外とする。ここでは純粋関数
(summarize_route_predictions, summarize_followup_direct_expected, percentile)
のみを対象にする。"""

from private_rag_apps.evals.routing import (
    percentile,
    summarize_followup_direct_expected,
    summarize_route_predictions,
)


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


class TestSummarizeFollowupDirectExpected:
    """T5 完了条件: 「followup の direct 期待ケース（3-5件）が rewrite 後も正しく direct
    になる」を検証するための集計関数（スペック §7.1 のfollowupカテゴリ direct期待分）"""

    def test_counts_only_followup_category_with_direct_expected(self) -> None:
        records = [
            {"id": "f1", "category": "followup", "expected_route": "direct", "predicted_route": "direct"},
            {"id": "f2", "category": "followup", "expected_route": "direct", "predicted_route": "grounded"},
            {"id": "f3", "category": "followup", "expected_route": "grounded", "predicted_route": "grounded"},
            {"id": "g1", "category": "corpus", "expected_route": "grounded", "predicted_route": "grounded"},
        ]

        result = summarize_followup_direct_expected(records)

        assert result["total"] == 2
        assert result["correct"] == 1
        assert result["wrong_ids"] == ["f2"]

    def test_empty_records(self) -> None:
        result = summarize_followup_direct_expected([])
        assert result["total"] == 0
        assert result["correct"] == 0
        assert result["wrong_ids"] == []


class TestPercentile:
    """rewrite(condense呼び出し)のレイテンシ p50/p95 算出用（T5完了条件「レイテンシp95の記録」）。
    サンプル数が小さいため線形補間ではなく単純な最近傍法を使う"""

    def test_p50_of_odd_length_list(self) -> None:
        assert percentile([1.0, 2.0, 3.0], 50) == 2.0

    def test_p95_picks_high_value(self) -> None:
        values = [float(i) for i in range(1, 21)]  # 1..20
        assert percentile(values, 95) >= 19.0

    def test_empty_list_returns_zero(self) -> None:
        assert percentile([], 95) == 0.0

    def test_single_value(self) -> None:
        assert percentile([42.0], 50) == 42.0
        assert percentile([42.0], 95) == 42.0
