"""Eval 検索結果キャッシュの振る舞いを外部 API なしで検証する。"""

import json
from pathlib import Path

import pytest

import private_rag_apps.evals.__main__ as main_module
from private_rag_apps.evals.retrieval_cache import RetrievalCacheError, load_retrieval_cache


class _FakeSession:
    def close(self) -> None:
        pass


def test_run_eval_replays_cache_and_no_cache_refreshes_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "doc.md").write_text("source", encoding="utf-8")
    dataset_path = tmp_path / "golden.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "id": "q1",
                "question": "question",
                "relevant": [{"path": "doc.md"}],
                "reference_answer": "answer",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "cache" / "retrieval.json"
    calls = 0
    rankings = {
        "fused_ranking": [{"path": "doc.md", "content": "source"}],
        "reranked_ranking": [{"path": "doc.md", "content": "source"}],
    }

    def fake_retrieve(*_args: object, **_kwargs: object) -> dict[str, list[dict[str, str]]]:
        nonlocal calls
        calls += 1
        return rankings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module.settings, "corpus_dir", str(corpus_dir))
    monkeypatch.setattr(main_module.settings, "eval_dataset_path", str(dataset_path))
    monkeypatch.setattr(main_module.settings, "eval_retrieval_cache_path", str(cache_path))
    monkeypatch.setattr(main_module, "SessionLocal", _FakeSession)
    monkeypatch.setattr(main_module, "retrieve_context", fake_retrieve)
    monkeypatch.setattr(main_module, "get_answer", lambda *_args: "answer")
    monkeypatch.setattr(main_module, "evaluate_faithfulness", lambda *_args: {"score": 1})
    monkeypatch.setattr(main_module, "evaluate_answer_relevance", lambda *_args: {"score": 1})
    monkeypatch.setattr(main_module, "_last_voyage_call_at", None)

    main_module.run_eval(no_cache=True)

    assert calls == 1
    assert cache_path.exists()

    def unexpected_retrieve(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cache replay must not call retrieval")

    monkeypatch.setattr(main_module, "retrieve_context", unexpected_retrieve)
    main_module.run_eval()


def test_load_retrieval_cache_rejects_mismatched_provenance(tmp_path: Path) -> None:
    cache_path = tmp_path / "retrieval.json"
    cache_path.write_text(
        json.dumps({"cache_version": 1, "provenance": {"corpus_hash": "old"}, "entries": {}}),
        encoding="utf-8",
    )

    with pytest.raises(RetrievalCacheError, match="provenance"):
        load_retrieval_cache(cache_path, {"corpus_hash": "new"})
