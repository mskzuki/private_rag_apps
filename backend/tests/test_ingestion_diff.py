import datetime

from private_rag_apps.ingestion.diff import Action, classify, should_apply_deletions


def test_classify_new_path_is_insert():
    assert classify(existing_hash=None, existing_deleted_at=None, new_hash="abc") == Action.INSERT


def test_classify_alive_unchanged_is_skip():
    assert classify(existing_hash="abc", existing_deleted_at=None, new_hash="abc") == Action.SKIP


def test_classify_alive_changed_is_replace():
    assert classify(existing_hash="abc", existing_deleted_at=None, new_hash="xyz") == Action.REPLACE


def test_classify_soft_deleted_unchanged_is_revive_only():
    deleted_at = datetime.datetime.now(datetime.timezone.utc)
    assert classify(existing_hash="abc", existing_deleted_at=deleted_at, new_hash="abc") == Action.REVIVE_ONLY


def test_classify_soft_deleted_changed_is_replace():
    deleted_at = datetime.datetime.now(datetime.timezone.utc)
    assert classify(existing_hash="abc", existing_deleted_at=deleted_at, new_hash="xyz") == Action.REPLACE


def test_guard_allows_when_ratio_met():
    allowed, reason = should_apply_deletions(alive_count=10, found_count=8, ratio=0.5, force=False)
    assert allowed is True
    assert reason is None


def test_guard_blocks_when_ratio_not_met():
    allowed, reason = should_apply_deletions(alive_count=10, found_count=4, ratio=0.5, force=False)
    assert allowed is False
    assert reason is not None


def test_guard_blocks_when_scan_found_zero():
    allowed, reason = should_apply_deletions(alive_count=10, found_count=0, ratio=0.5, force=False)
    assert allowed is False
    assert reason is not None


def test_guard_force_bypasses_block():
    allowed, reason = should_apply_deletions(alive_count=10, found_count=0, ratio=0.5, force=True)
    assert allowed is True
    assert reason is None


def test_guard_allows_when_no_alive_sources_and_files_found():
    allowed, reason = should_apply_deletions(alive_count=0, found_count=3, ratio=0.5, force=False)
    assert allowed is True
    assert reason is None
