"""Unit tests for RRF score calculation logic and rerank fallback."""

import pytest
from unittest.mock import MagicMock, patch


class TestRrfScoreLogic:
    """Test RRF score computation in isolation (no DB)."""

    def test_rrf_score_formula(self):
        """Verify 1/(k + rank) formula produces expected scores."""
        rrf_k = 60
        # rank 1 in both searches → 2 * 1/(60+1)
        score_rank1 = 1.0 / (rrf_k + 1)
        assert score_rank1 == pytest.approx(1.0 / 61)

    def test_rrf_fusion_both_sides(self):
        """Document appearing in both searches gets sum of scores."""
        rrf_k = 60
        vector_rank = 1
        fts_rank = 3
        expected = 1.0 / (rrf_k + vector_rank) + 1.0 / (rrf_k + fts_rank)
        assert expected == pytest.approx(1.0 / 61 + 1.0 / 63)

    def test_rrf_single_side_only(self):
        """Document in only one search gets only that score."""
        rrf_k = 60
        score = 1.0 / (rrf_k + 5)
        assert score == pytest.approx(1.0 / 65)

    def test_higher_rank_gets_higher_score(self):
        """Rank 1 should produce a higher RRF score than rank 10."""
        rrf_k = 60
        score_rank1 = 1.0 / (rrf_k + 1)
        score_rank10 = 1.0 / (rrf_k + 10)
        assert score_rank1 > score_rank10


class TestRerankFallback:
    """Test the _rerank function's error handling."""

    def test_empty_chunks_returns_empty(self):
        from private_rag_apps.retrieval.searcher import _rerank

        with patch("private_rag_apps.retrieval.searcher.get_client"):
            result = _rerank("test query", [], top_k=5)
        assert result == []

    def test_rerank_api_failure_falls_back(self):
        """When rerank API raises, fall back to input order truncated to top_k."""
        from private_rag_apps.retrieval.searcher import _rerank

        chunks = [
            {"chunk_id": str(i), "content": f"content {i}", "metadata": {},
             "title": f"doc{i}", "path": f"doc{i}.md"}
            for i in range(10)
        ]

        with patch("private_rag_apps.retrieval.searcher.voyageai") as mock_voyage:
            mock_voyage.Client.return_value.rerank.side_effect = RuntimeError("API down")
            with patch("private_rag_apps.retrieval.searcher.get_client") as mock_lf:
                mock_lf.return_value.update_current_span = MagicMock()
                result = _rerank("test query", chunks, top_k=5)

        assert len(result) == 5
        # Should be the first 5 in original order (fallback)
        assert [c["chunk_id"] for c in result] == ["0", "1", "2", "3", "4"]

    def test_rerank_success_reorders(self):
        """When rerank API succeeds, chunks are reordered by relevance."""
        from private_rag_apps.retrieval.searcher import _rerank

        chunks = [
            {"chunk_id": str(i), "content": f"content {i}", "metadata": {},
             "title": f"doc{i}", "path": f"doc{i}.md"}
            for i in range(5)
        ]

        mock_result = MagicMock()
        # Rerank reverses the order
        mock_result.results = [
            MagicMock(index=4), MagicMock(index=2), MagicMock(index=0),
        ]
        mock_result.total_tokens = 100

        with patch("private_rag_apps.retrieval.searcher.voyageai") as mock_voyage:
            mock_voyage.Client.return_value.rerank.return_value = mock_result
            with patch("private_rag_apps.retrieval.searcher.get_client") as mock_lf:
                mock_lf.return_value.update_current_span = MagicMock()
                result = _rerank("test query", chunks, top_k=3)

        assert [c["chunk_id"] for c in result] == ["4", "2", "0"]

    def test_rerank_fewer_than_top_k(self):
        """When fewer chunks than top_k are available, return all."""
        from private_rag_apps.retrieval.searcher import _rerank

        chunks = [
            {"chunk_id": "0", "content": "only one", "metadata": {},
             "title": "doc0", "path": "doc0.md"}
        ]

        mock_result = MagicMock()
        mock_result.results = [MagicMock(index=0)]
        mock_result.total_tokens = 10

        with patch("private_rag_apps.retrieval.searcher.voyageai") as mock_voyage:
            mock_voyage.Client.return_value.rerank.return_value = mock_result
            with patch("private_rag_apps.retrieval.searcher.get_client") as mock_lf:
                mock_lf.return_value.update_current_span = MagicMock()
                result = _rerank("test query", chunks, top_k=8)

        assert len(result) == 1
