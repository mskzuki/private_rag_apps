"""永続化した検索結果を再生するための、Eval 専用キャッシュ。"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


CACHE_VERSION = 1


class RetrievalCacheError(ValueError):
    """検索結果キャッシュが利用できないことを示す例外。"""


def file_sha256(path: Path) -> str:
    """ファイル内容の SHA-256 を返す。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_retrieval_cache(path: Path, expected_provenance: Dict[str, Any]) -> Dict[str, Any]:
    """provenance が一致する検索結果キャッシュを読み込む。"""
    if not path.exists():
        raise RetrievalCacheError(
            f"Retrieval cache not found: {path}. Run `make eval ARGS=\"--no-cache\"` first."
        )

    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetrievalCacheError(f"Retrieval cache is unreadable: {path}") from exc

    if not isinstance(cache, dict) or cache.get("cache_version") != CACHE_VERSION:
        raise RetrievalCacheError(f"Retrieval cache has an unsupported format: {path}")
    if cache.get("provenance") != expected_provenance:
        raise RetrievalCacheError(
            "Retrieval cache provenance does not match the current evaluation. "
            'Run `make eval ARGS="--no-cache"` to refresh it.'
        )

    entries = cache.get("entries")
    if not isinstance(entries, dict):
        raise RetrievalCacheError(f"Retrieval cache entries are invalid: {path}")
    return entries


def read_cached_rankings(entries: Dict[str, Any], item_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """設問IDに対応する fused / reranked ランキングを検証して返す。"""
    entry = entries.get(item_id)
    if not isinstance(entry, dict):
        raise RetrievalCacheError(
            f"Retrieval cache has no entry for {item_id}. "
            'Run `make eval ARGS="--no-cache"` to refresh it.'
        )

    fused = entry.get("fused_ranking")
    reranked = entry.get("reranked_ranking")
    if not isinstance(fused, list) or not isinstance(reranked, list):
        raise RetrievalCacheError(f"Retrieval cache entry is invalid for {item_id}")
    return {"fused_ranking": fused, "reranked_ranking": reranked}


def write_retrieval_cache(
    path: Path, provenance: Dict[str, Any], entries: Dict[str, Dict[str, Any]]
) -> None:
    """検索結果キャッシュを原子的に書き換える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_version": CACHE_VERSION,
        "provenance": provenance,
        "entries": entries,
    }
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary_path.replace(path)
