"""Unit tests for Eval metric helpers (calc_ndcg, calc_mrr, calc_recall)."""

import math
import pytest

from private_rag_apps.evals.__main__ import calc_ndcg, calc_mrr, calc_recall


class TestCalcRecall:
    def test_hit_in_top_k(self):
        assert calc_recall([0, 0, 1, 0, 0], k=5) == 1.0

    def test_no_hit_in_top_k(self):
        assert calc_recall([0, 0, 0, 0, 0, 1], k=5) == 0.0

    def test_hit_at_boundary(self):
        assert calc_recall([0, 0, 0, 0, 1], k=5) == 1.0

    def test_empty_rels(self):
        assert calc_recall([], k=5) == 0.0


class TestCalcMrr:
    def test_first_position(self):
        assert calc_mrr([1, 0, 0]) == 1.0

    def test_second_position(self):
        assert calc_mrr([0, 1, 0]) == 0.5

    def test_third_position(self):
        assert calc_mrr([0, 0, 1]) == pytest.approx(1.0 / 3.0)

    def test_no_relevant(self):
        assert calc_mrr([0, 0, 0]) == 0.0

    def test_empty(self):
        assert calc_mrr([]) == 0.0


class TestCalcNdcg:
    def test_perfect_ranking(self):
        """Single relevant doc at position 1 → nDCG = 1.0."""
        assert calc_ndcg([1, 0, 0, 0, 0], k=5) == pytest.approx(1.0)

    def test_relevant_at_position_2(self):
        """Single relevant doc at position 2."""
        rels = [0, 1, 0, 0, 0]
        dcg = 1.0 / math.log2(3)  # position index 1 → log2(1+2)
        idcg = 1.0 / math.log2(2)  # ideal: position 0 → log2(0+2)
        assert calc_ndcg(rels, k=5) == pytest.approx(dcg / idcg)

    def test_two_relevant(self):
        """Two relevant docs at positions 1 and 3."""
        rels = [1, 0, 1, 0, 0]
        dcg = 1.0 / math.log2(2) + 1.0 / math.log2(4)
        idcg = 1.0 / math.log2(2) + 1.0 / math.log2(3)
        assert calc_ndcg(rels, k=5) == pytest.approx(dcg / idcg)

    def test_no_relevant(self):
        assert calc_ndcg([0, 0, 0], k=3) == 0.0

    def test_empty(self):
        assert calc_ndcg([], k=5) == 0.0
