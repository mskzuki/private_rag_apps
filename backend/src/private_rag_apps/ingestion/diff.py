import datetime
from enum import Enum, auto
from typing import Optional


class Action(Enum):
    SKIP = auto()
    INSERT = auto()
    REPLACE = auto()
    REVIVE_ONLY = auto()


def classify(
    existing_hash: Optional[str],
    existing_deleted_at: Optional[datetime.datetime],
    new_hash: str,
) -> Action:
    """既存sourceの状態と新しいcontent_hashから、適用すべきActionを判定する"""
    if existing_hash is None:
        return Action.INSERT
    unchanged = existing_hash == new_hash
    if existing_deleted_at is not None:
        return Action.REVIVE_ONLY if unchanged else Action.REPLACE
    return Action.SKIP if unchanged else Action.REPLACE


def should_apply_deletions(
    alive_count: int, found_count: int, ratio: float, force: bool
) -> tuple[bool, Optional[str]]:
    """削除フェーズを適用してよいか判定する（安全弁）。許可時は(True, None)、拒否時は(False, 理由)を返す"""
    if force:
        return True, None
    if found_count == 0:
        return False, "scan found 0 files"
    if alive_count > 0 and (found_count / alive_count) < ratio:
        return False, f"found/alive ratio {found_count}/{alive_count} < guard {ratio}"
    return True, None
