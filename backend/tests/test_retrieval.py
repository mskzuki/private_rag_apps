"""Unit tests for RRF score calculation logic and rerank fallback."""

import uuid
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
            {
                "chunk_id": str(i),
                "content": f"content {i}",
                "metadata": {},
                "title": f"doc{i}",
                "path": f"doc{i}.md",
            }
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
            {
                "chunk_id": str(i),
                "content": f"content {i}",
                "metadata": {},
                "title": f"doc{i}",
                "path": f"doc{i}.md",
            }
            for i in range(5)
        ]

        mock_result = MagicMock()
        # Rerank reverses the order
        mock_result.results = [
            MagicMock(index=4),
            MagicMock(index=2),
            MagicMock(index=0),
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
            {
                "chunk_id": "0",
                "content": "only one",
                "metadata": {},
                "title": "doc0",
                "path": "doc0.md",
            }
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

    def test_rerank_success_attaches_rerank_score(self):
        """T4: 成功時、各チャンクに Voyage の relevance_score が rerank_score として
        付与される（ADR 0001 / m7-score-distribution.md §1 と意味論を一致させる:
        0〜1、Voyageのrelevance_scoreそのもの）。grade の前提（スペック rev.3 §4.3）"""
        from private_rag_apps.retrieval.searcher import _rerank

        chunks = [
            {
                "chunk_id": str(i),
                "content": f"content {i}",
                "metadata": {},
                "title": f"doc{i}",
                "path": f"doc{i}.md",
            }
            for i in range(3)
        ]

        mock_result = MagicMock()
        # index=2が最高スコア、次いでindex=0、index=1は返らない(top_k=2相当)
        r0 = MagicMock(index=2)
        r0.relevance_score = 0.91
        r1 = MagicMock(index=0)
        r1.relevance_score = 0.42
        mock_result.results = [r0, r1]
        mock_result.total_tokens = 50

        with patch("private_rag_apps.retrieval.searcher.voyageai") as mock_voyage:
            mock_voyage.Client.return_value.rerank.return_value = mock_result
            with patch("private_rag_apps.retrieval.searcher.get_client") as mock_lf:
                mock_lf.return_value.update_current_span = MagicMock()
                result = _rerank("test query", chunks, top_k=2)

        assert [c["chunk_id"] for c in result] == ["2", "0"]
        assert result[0]["rerank_score"] == 0.91
        assert result[1]["rerank_score"] == 0.42
        # 元のchunks dictは変更されない(diagnostic_modeのfused_rankingへの副作用防止)
        assert "rerank_score" not in chunks[2]
        assert "rerank_score" not in chunks[0]

    def test_rerank_fallback_chunks_have_no_rerank_score(self):
        """フォールバック時(Voyage呼び出し失敗)はrerank_scoreを付与しない。
        grade側はrerank_score欠落を「kept扱い」にする安全側デフォルトを持つ
        （スペック §3.1「迷ったらgroundedに倒す」）。ここでは _rerank 側が
        フォールバック時に偽のスコアを捏造しないことのみを確認する"""
        from private_rag_apps.retrieval.searcher import _rerank

        chunks = [
            {
                "chunk_id": str(i),
                "content": f"content {i}",
                "metadata": {},
                "title": f"doc{i}",
                "path": f"doc{i}.md",
            }
            for i in range(3)
        ]

        with patch("private_rag_apps.retrieval.searcher.voyageai") as mock_voyage:
            mock_voyage.Client.return_value.rerank.side_effect = RuntimeError("API down")
            with patch("private_rag_apps.retrieval.searcher.get_client") as mock_lf:
                mock_lf.return_value.update_current_span = MagicMock()
                result = _rerank("test query", chunks, top_k=3)

        assert all("rerank_score" not in c for c in result)


class TestFormatChunks:
    """M9 T6: _format_chunks が Source から source_type/external_id/source_url を
    チャンクdictに転記することを検証する（citation chain の入口。スペック §4.7）。
    DBを介さずChunk/Sourceの属性アクセスのみを見るため、実オブジェクトの代わりに
    SimpleNamespaceでフェイクする（このファイルの他テストと同様、DB不要な純粋ロジックとして扱う）"""

    def test_local_source_maps_default_source_type_and_null_external_fields(self):
        from types import SimpleNamespace

        from private_rag_apps.retrieval.searcher import _format_chunks

        chunk_id = uuid.uuid4()
        chunk = SimpleNamespace(id=chunk_id, content="local content", metadata_={"heading": "h1"})
        source = SimpleNamespace(
            title="Local Doc",
            path="local/doc.md",
            source_type="local_fs",
            external_id=None,
            source_url=None,
        )

        result = _format_chunks([(chunk, source)])

        assert len(result) == 1
        assert result[0]["chunk_id"] == str(chunk_id)
        assert result[0]["title"] == "Local Doc"
        assert result[0]["path"] == "local/doc.md"
        assert result[0]["source_type"] == "local_fs"
        assert result[0]["external_id"] is None
        assert result[0]["source_url"] is None

    def test_drive_source_maps_source_type_external_id_and_source_url(self):
        from types import SimpleNamespace

        from private_rag_apps.retrieval.searcher import _format_chunks

        chunk_id = uuid.uuid4()
        chunk = SimpleNamespace(id=chunk_id, content="drive content", metadata_={"heading": "h2"})
        source = SimpleNamespace(
            title="Drive Doc",
            path="Notes/drive-doc.md",
            source_type="google_drive",
            external_id="drv-abc123",
            source_url="https://drive.google.com/file/d/drv-abc123/view",
        )

        result = _format_chunks([(chunk, source)])

        assert len(result) == 1
        assert result[0]["source_type"] == "google_drive"
        assert result[0]["external_id"] == "drv-abc123"
        assert result[0]["source_url"] == "https://drive.google.com/file/d/drv-abc123/view"
