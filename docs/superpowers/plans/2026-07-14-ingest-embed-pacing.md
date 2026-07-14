# Ingest Embed Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Voyage AI 429 rate-limit failures during `make ingest`/`make demo` by pacing embed calls at least `INGEST_EMBED_MIN_INTERVAL_SEC` apart.

**Architecture:** Add a new `ingest_embed_min_interval_sec` setting to `core/config.py`. In `ingestion/indexer.py`, track the monotonic timestamp of the last Voyage `embed()` call in a module-level variable and, before every subsequent call, sleep for whatever remainder of the configured interval hasn't elapsed. This covers both inter-file pacing (each source calls `_embed_documents` once) and inter-batch pacing within a single large file (the existing `for start in range(0, len(texts), batch_size)` loop already calls Voyage multiple times per file).

**Tech Stack:** Python 3.13, `time` stdlib module (no new dependency), pytest + `unittest.mock.patch` (existing test patterns in `backend/tests/test_ingestion_indexer.py`).

## Global Constraints

- Settings must come from `core/config.py` (pydantic-settings), never hardcoded (AGENTS.md §6).
- Public functions require type annotations; avoid `Any` (AGENTS.md §6).
- Tests must not call real paid APIs — mock the Voyage client the same way existing tests do (AGENTS.md §8).
- New feature needs a corresponding test (AGENTS.md §10 Definition of Done).
- Spec already updated: `docs/specs/m4_ingestion_and_demo.md` §4.2/§10 (v0.4) and `docs/specs/m4_tasklist.md` Phase 2 (v0.6) describe this behavior — implementation must match: default `21.0`, setting name `INGEST_EMBED_MIN_INTERVAL_SEC` / `ingest_embed_min_interval_sec`.
- Scope is ingestion only — do not touch `retrieval/searcher.py`'s query-time embed/rerank calls.

---

### Task 1: Add embed pacing to the ingestion indexer

**Files:**
- Modify: `backend/src/private_rag_apps/core/config.py:51` (add setting after `ingest_embed_batch_size`)
- Modify: `backend/.env.example:35` (add commented example after `INGEST_EMBED_BATCH_SIZE`)
- Modify: `backend/src/private_rag_apps/ingestion/indexer.py` (add `import time`, module-level last-call timestamp, `_pace_embed_call()` helper, call site in `_embed_documents`)
- Test: `backend/tests/test_ingestion_indexer.py` (new `TestEmbedPacing` class)

**Interfaces:**
- Produces: `private_rag_apps.core.config.Settings.ingest_embed_min_interval_sec: float` (default `21.0`)
- Produces: `private_rag_apps.ingestion.indexer._pace_embed_call() -> None` (module-private; called once per Voyage `embed()` invocation, before the call)

- [ ] **Step 1: Add the config setting**

In `backend/src/private_rag_apps/core/config.py`, in the `# Ingestion Settings` block, change:

```python
    ingest_embed_batch_size: int = 64  # Voyage embed呼び出し1回あたりのチャンク数上限
    ingest_trigger: Literal["cli", "demo"] = "cli"  # CLI --trigger 省略時の既定値（INGEST_TRIGGER）
```

to:

```python
    ingest_embed_batch_size: int = 64  # Voyage embed呼び出し1回あたりのチャンク数上限
    ingest_embed_min_interval_sec: float = 21.0  # Voyage embed呼び出し間の最低待機秒数（レート制限予防のペーシング。無支払い枠3RPM=20秒間隔が理論上限）
    ingest_trigger: Literal["cli", "demo"] = "cli"  # CLI --trigger 省略時の既定値（INGEST_TRIGGER）
```

- [ ] **Step 2: Add the `.env.example` entry**

In `backend/.env.example`, change:

```
# INGEST_EMBED_BATCH_SIZE=64
# INGEST_TRIGGER=cli
```

to:

```
# INGEST_EMBED_BATCH_SIZE=64
# INGEST_EMBED_MIN_INTERVAL_SEC=21.0
# INGEST_TRIGGER=cli
```

- [ ] **Step 3: Write the failing test**

In `backend/tests/test_ingestion_indexer.py`, add a new test class after `class TestInsertSkipReplace:`'s existing tests (append at end of file, keeping the file's existing `class Test...` grouping convention):

```python
class TestEmbedPacing:
    def test_embed_calls_are_paced_at_least_min_interval_apart(self, corpus_dir, db, monkeypatch):
        path_a = f"{uuid.uuid4()}.md"
        path_b = f"{uuid.uuid4()}.md"
        _write_corpus(corpus_dir, {path_a: "# A\n\ncontent a", path_b: "# B\n\ncontent b"})

        import private_rag_apps.ingestion.indexer as indexer_module

        monkeypatch.setattr(indexer_module, "_last_embed_call_at", None)

        fake_now = [1000.0]
        sleep_calls: list[float] = []

        def fake_monotonic() -> float:
            return fake_now[0]

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            fake_now[0] += seconds

        monkeypatch.setattr(indexer_module.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(indexer_module.time, "sleep", fake_sleep)

        def embed_side_effect(texts, **kwargs):
            fake_now[0] += 0.01  # simulate API call latency
            return type("R", (), {"embeddings": [FAKE_EMBEDDING for _ in texts]})()

        run_ids = []
        try:
            from unittest.mock import patch

            with patch("private_rag_apps.ingestion.indexer.voyageai") as mock_voyage:
                mock_voyage.Client.return_value.embed.side_effect = embed_side_effect
                run_ids.append(run_ingestion(db, trigger="cli").id)

            assert len(sleep_calls) == 1
            expected_wait = settings.ingest_embed_min_interval_sec - 0.01
            assert sleep_calls[0] == pytest.approx(expected_wait, rel=0.01)
        finally:
            _cleanup(db, [path_a, path_b], run_ids)
```

This test writes two brand-new source files (both take the `Action.INSERT` path, so both call `_embed_documents` exactly once each — see `indexer.py:114-126`). It fakes `time.monotonic`/`time.sleep` so the test runs instantly instead of waiting 21 real seconds, and resets the module's pacing state so the test doesn't depend on execution order relative to other tests in the file.

- [ ] **Step 4: Run the test and verify it fails**

Run: `cd backend && uv run pytest tests/test_ingestion_indexer.py::TestEmbedPacing -v`

Expected: FAIL — `assert len(sleep_calls) == 1` fails because `sleep_calls == []` (no pacing exists yet, so `time.sleep` is never called).

- [ ] **Step 5: Implement the pacing helper**

In `backend/src/private_rag_apps/ingestion/indexer.py`, add `time` to the imports at the top and add `Optional` to the existing `typing` import (matching the `Optional[...]` style already used in `ingestion/concurrency.py`, rather than `X | None`):

```python
import time
from typing import List, Optional, TypedDict, cast
```

Add the module-level state and helper function right after the imports (before `class Stats`):

```python
_last_embed_call_at: Optional[float] = None


def _pace_embed_call() -> None:
    """Voyage embed呼び出し間隔がINGEST_EMBED_MIN_INTERVAL_SEC未満にならないよう待機する
    （レート制限予防。m4_ingestion_and_demo.md §4.2）"""
    global _last_embed_call_at
    now = time.monotonic()
    if _last_embed_call_at is not None:
        wait = settings.ingest_embed_min_interval_sec - (now - _last_embed_call_at)
        if wait > 0:
            time.sleep(wait)
    _last_embed_call_at = time.monotonic()
```

Then update `_embed_documents` to call it before each Voyage call:

```python
@observe(name="embed_documents")
def _embed_documents(texts: List[str]) -> List[List[float]]:
    """チャンク化されたテキストのリストをVoyage AIを用いてバッチ単位でベクトル化（埋め込み）する"""
    if not texts:
        return []
    voyage_client = voyageai.Client(api_key=settings.voyage_api_key, max_retries=settings.voyage_max_retries)
    batch_size = settings.ingest_embed_batch_size
    embeddings: List[List[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        _pace_embed_call()
        result = voyage_client.embed(batch, model=settings.embed_model, input_type="document")
        embeddings.extend(cast(List[List[float]], result.embeddings))
    return embeddings
```

- [ ] **Step 6: Run the test and verify it passes**

Run: `cd backend && uv run pytest tests/test_ingestion_indexer.py::TestEmbedPacing -v`

Expected: PASS

- [ ] **Step 7: Run the full ingestion indexer test module to check for regressions**

Run: `cd backend && uv run pytest tests/test_ingestion_indexer.py -v`

Expected: all tests PASS (existing tests mock `voyageai` per-call and don't span real multi-second gaps, so real `time.sleep`/`time.monotonic` — not monkeypatched outside `TestEmbedPacing` — will only ever wait a few milliseconds between two files' embed calls at most, since the previous test in the same process may have left `_last_embed_call_at` set close to "now"; confirm no test in the file takes noticeably longer than before. If any test other than `TestEmbedPacing` starts taking ~21s, add `monkeypatch.setattr(indexer_module, "_last_embed_call_at", None)` at the top of that test too — see note in Step 8).

- [ ] **Step 8: Confirm no other test in the suite is slowed by real pacing**

Run: `cd backend && uv run pytest tests/ -k "ingest" -v --durations=10`

Expected: no test takes anywhere near 21s. Because `_last_embed_call_at` is a real module-level global (not reset between tests unless a test explicitly resets it, as `TestEmbedPacing` does), a test that runs soon after another test's real (unmocked) Voyage-mock call could in theory observe a `_last_embed_call_at` from moments ago and sleep briefly — this is expected to be sub-second in practice since pytest test bodies run much faster than 21s apart. If the `--durations=10` output shows any ingestion test taking multiple seconds, that test needs the same `monkeypatch.setattr(indexer_module, "_last_embed_call_at", None)` reset added at its start.

- [ ] **Step 9: Lint and type-check**

Run: `cd backend && uv run ruff check src/private_rag_apps/ingestion/indexer.py src/private_rag_apps/core/config.py tests/test_ingestion_indexer.py && uv run mypy src/private_rag_apps/ingestion/indexer.py src/private_rag_apps/core/config.py`

Expected: no errors.

- [ ] **Step 10: Commit**

```bash
cd /Users/mskzuk/src/private_rag_apps
git add backend/src/private_rag_apps/core/config.py backend/.env.example backend/src/private_rag_apps/ingestion/indexer.py backend/tests/test_ingestion_indexer.py docs/specs/m4_ingestion_and_demo.md docs/specs/m4_tasklist.md
git commit -m "$(cat <<'EOF'
fix(ingestion): Voyage embed呼び出しをペーシングしレート制限429を予防する

無支払い枠のVoyage RPM上限(3RPM)により、make ingestで4ファイル中2ファイルが
連続して埋め込み失敗する事象を確認。INGEST_EMBED_MIN_INTERVAL_SEC(既定21秒)で
embed呼び出し間隔を空けるペーシングを追加する。

EOF
)"
```

---

## Post-implementation note (not a task — informational)

The live DB currently already has all 4 seed corpus sources ingested (confirmed via direct psql query during diagnosis: `sources` = 4 rows, `chunks` = 113 rows), because the two previously-failing files were re-processed in isolation outside the rate-limit window while diagnosing this issue. No `make ingest`/`make demo` re-run is required to fix the current data — this plan only prevents the failure from recurring on future ingests.
