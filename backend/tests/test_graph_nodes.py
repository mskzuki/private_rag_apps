from unittest.mock import MagicMock, patch

import pytest

from private_rag_apps.core.config import settings
from private_rag_apps.graph.nodes.generate import generate
from private_rag_apps.graph.nodes.grade import grade
from private_rag_apps.graph.nodes.retrieve import make_retrieve_node
from private_rag_apps.graph.state import GraphState


class TestGrade:
    """grade ノード: THETA によるgrounded/direct分岐(スペック rev.3 §4.3 grade)。
    LLMを使わない純関数。カバレッジ判定(contextでどこまで答えられるか)は
    generateプロンプト側の責務であり、ここではテストしない(スペック §2 grounded)。"""

    def test_keeps_chunks_at_or_above_theta_and_routes_grounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "routing_theta", 0.5)
        state: GraphState = {
            "retrieved": [
                {"chunk_id": "a", "rerank_score": 0.9},
                {"chunk_id": "b", "rerank_score": 0.4},
                {"chunk_id": "c", "rerank_score": 0.6},
            ]
        }

        result = grade(state)

        assert result["kept"] == [
            {"chunk_id": "a", "rerank_score": 0.9},
            {"chunk_id": "c", "rerank_score": 0.6},
        ]
        assert result["route"] == "grounded"

    def test_boundary_score_equal_to_theta_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """THETA境界: rerank_score == THETA は kept に含める(>=、> ではない。スペック §4.3)"""
        monkeypatch.setattr(settings, "routing_theta", 0.56)
        state: GraphState = {"retrieved": [{"chunk_id": "a", "rerank_score": 0.56}]}

        result = grade(state)

        assert result["kept"] == [{"chunk_id": "a", "rerank_score": 0.56}]
        assert result["route"] == "grounded"

    def test_all_below_theta_routes_direct_with_empty_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "routing_theta", 0.5)
        state: GraphState = {
            "retrieved": [
                {"chunk_id": "a", "rerank_score": 0.3},
                {"chunk_id": "b", "rerank_score": 0.1},
            ]
        }

        result = grade(state)

        assert result["kept"] == []
        assert result["route"] == "direct"

    def test_empty_retrieved_routes_direct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "routing_theta", 0.5)
        state: GraphState = {"retrieved": []}

        result = grade(state)

        assert result["kept"] == []
        assert result["route"] == "direct"

    def test_missing_rerank_score_defaults_to_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """rerank_score欠落(Voyageリランク失敗時のRRFフォールバック。
        retrieval/searcher.py::_rerank()参照)は、誤判定コストの非対称性
        (スペック §3.1「迷ったらgroundedに倒す」)に基づき kept として扱う。
        既存テスト(test_api.py等)がrerank_score無しのmock chunkでgrounded経路の
        アサーションをしていることとも整合させる必要がある"""
        monkeypatch.setattr(settings, "routing_theta", 0.5)
        state: GraphState = {"retrieved": [{"chunk_id": "a", "content": "no score"}]}

        result = grade(state)

        assert result["kept"] == [{"chunk_id": "a", "content": "no score"}]
        assert result["route"] == "grounded"

    def test_uses_default_theta_from_settings(self) -> None:
        """settings.routing_theta のデフォルト値(ADR 0001の0.56)がmonkeypatch無しでも
        実際に使われることを確認する(configが正しく配線されていることの検証)"""
        assert settings.routing_theta == 0.56
        state: GraphState = {
            "retrieved": [
                {"chunk_id": "a", "rerank_score": 0.561},
                {"chunk_id": "b", "rerank_score": 0.559},
            ]
        }

        result = grade(state)

        assert [c["chunk_id"] for c in result["kept"]] == ["a"]
        assert result["route"] == "grounded"


@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_retrieve_node_uses_user_query_as_search_query(mock_retrieve_context: MagicMock) -> None:
    """T3 時点では rewrite ノードが無いため、search_query には user_query をそのまま使う
    （タスク T3 補足コンテキスト#1、スペック rev.3 §4.3 retrieve）"""
    mock_retrieve_context.return_value = [{"chunk_id": "c1"}]
    mock_db = MagicMock()

    node = make_retrieve_node(mock_db)
    result = node({"user_query": "raw question", "conversation_id": "c", "history": []})

    mock_retrieve_context.assert_called_once_with(mock_db, query="raw question")
    assert result == {"search_query": "raw question", "retrieved": [{"chunk_id": "c1"}]}


@patch("private_rag_apps.graph.nodes.retrieve.retrieve_context")
def test_retrieve_node_binds_distinct_db_sessions_per_call(
    mock_retrieve_context: MagicMock,
) -> None:
    """db セッションはノード関数のクロージャで保持し、State には含めない（スペック §3.4）。
    リクエストごとに異なる db を束縛できることを確認する"""
    mock_retrieve_context.return_value = []
    db_a = MagicMock(name="db_a")
    db_b = MagicMock(name="db_b")

    make_retrieve_node(db_a)({"user_query": "q"})
    make_retrieve_node(db_b)({"user_query": "q"})

    assert mock_retrieve_context.call_args_list[0].args[0] is db_a
    assert mock_retrieve_context.call_args_list[1].args[0] is db_b


class TestGenerateNode:
    """generate ノード: state["route"] によって grounded/direct のプロンプト・呼び出し先関数を
    切り替える（1ノード内で分岐。スペック rev.3 §4.1: 「generate は1ノードとし、プロンプト
    以外のロジックが共通のため2ノードに分割しない」）"""

    @patch("private_rag_apps.graph.nodes.generate.get_stream_writer")
    @patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
    def test_grounded_route_calls_generate_answer_stream_with_kept_chunks(
        self, mock_generate_stream: MagicMock, mock_get_writer: MagicMock
    ) -> None:
        """grounded経路: context_chunksにはretrieved全件ではなくkept(THETA足切り後)を渡す"""
        written: list[dict[str, object]] = []
        mock_get_writer.return_value = written.append

        mock_generate_stream.return_value = iter(
            [
                {"event": "citations", "data": [{"n": 1, "title": "T1"}]},
                {"event": "token", "data": "Hello"},
                {"event": "token", "data": " World"},
            ]
        )

        state: GraphState = {
            "search_query": "q",
            "route": "grounded",
            "retrieved": [{"chunk_id": "c1"}, {"chunk_id": "c2", "rerank_score": 0.1}],
            "kept": [{"chunk_id": "c1"}],
        }
        result = generate(state)

        mock_generate_stream.assert_called_once_with("q", [{"chunk_id": "c1"}])
        assert written == [
            {"event": "citations", "data": [{"n": 1, "title": "T1"}]},
            {"event": "token", "data": "Hello"},
            {"event": "token", "data": " World"},
        ]
        assert result == {"citations": [{"n": 1, "title": "T1"}]}

    @patch("private_rag_apps.graph.nodes.generate.get_stream_writer")
    @patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
    def test_grounded_route_no_citations_event_keeps_citations_empty(
        self, mock_generate_stream: MagicMock, mock_get_writer: MagicMock
    ) -> None:
        """citations イベントが来ない場合（例: error のみ）でも citations は空リストのまま
        返す（KeyError を起こさないことの確認）"""
        written: list[dict[str, object]] = []
        mock_get_writer.return_value = written.append
        mock_generate_stream.return_value = iter([{"event": "error", "data": "boom"}])

        state: GraphState = {"search_query": "q", "route": "grounded", "kept": []}
        result = generate(state)

        assert result == {"citations": []}

    @patch("private_rag_apps.graph.nodes.generate.get_stream_writer")
    @patch("private_rag_apps.graph.nodes.generate.generate_direct_answer_stream")
    def test_direct_route_calls_generate_direct_answer_stream_with_query_only(
        self, mock_generate_direct_stream: MagicMock, mock_get_writer: MagicMock
    ) -> None:
        """direct経路: contextを渡さず、search_queryのみでgenerate_direct_answer_streamを呼ぶ"""
        written: list[dict[str, object]] = []
        mock_get_writer.return_value = written.append

        mock_generate_direct_stream.return_value = iter(
            [
                {"event": "citations", "data": []},
                {"event": "token", "data": "Hi"},
            ]
        )

        state: GraphState = {
            "search_query": "q",
            "route": "direct",
            "kept": [],
            "retrieved": [{"chunk_id": "c1", "rerank_score": 0.1}],
        }
        result = generate(state)

        mock_generate_direct_stream.assert_called_once_with("q")
        assert written == [
            {"event": "citations", "data": []},
            {"event": "token", "data": "Hi"},
        ]
        assert result == {"citations": []}

    @patch("private_rag_apps.graph.nodes.generate.get_stream_writer")
    @patch("private_rag_apps.graph.nodes.generate.generate_answer_stream")
    def test_missing_route_defaults_to_grounded(
        self, mock_generate_stream: MagicMock, mock_get_writer: MagicMock
    ) -> None:
        """route未設定時はgrounded扱い(誤判定コストの非対称性。スペック §3.1「迷ったら
        groundedに倒す」)。実際のグラフではgradeが必ず先行するため通常は発生しないが、
        防御的デフォルトとする"""
        mock_get_writer.return_value = MagicMock()
        mock_generate_stream.return_value = iter([])

        state: GraphState = {"search_query": "q", "kept": [{"chunk_id": "c1"}]}
        generate(state)

        mock_generate_stream.assert_called_once_with("q", [{"chunk_id": "c1"}])
