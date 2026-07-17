"""Google Drive フォルダの再帰探索・ローダー実装（M9・スペック §4.4, §4.5）。

`gdrive_client.GoogleDriveClient` を消費し、フォルダを再帰的に走査して
`loader.py::Document` 相当の内部表現（`source_type`/`external_id`/`source_url` 付き）へ
正規化する。責務はここまでで、以下は明示的にこのモジュールの責務外（T4）:

- `ingestion/diff.py::classify()` の呼び出し・INSERT/REPLACE/SKIP/REVIVE_ONLY の判定と適用
- `Source`/`Chunk`/`IngestRun` へのDB書き込み
- チャンキング・埋め込み
- 削除検知（走査結果とDB生存ソースの突き合わせ）そのもの

このモジュールが持つのは「Drive固有の変更検知の第1段（`modifiedTime`による事前フィルタで
ダウンロードを省略する）」までであり、`content_hash` に基づく最終判定（第2段）は
既存の `classify()` にそのまま委譲する（新規の変更検知ロジックを classify() の外に
持ち込まない）。
"""

import datetime
from typing import Dict, List, NamedTuple, Optional, Set

from langfuse import get_client, observe
from sqlalchemy.orm import Session

from private_rag_apps.core.time import utcnow
from private_rag_apps.models.rag import Source

from .gdrive_client import GOOGLE_DOC_MIME_TYPE, DriveFile, GoogleDriveClient
from .loader import Document, extract_title

# フォルダ・ショートカットのmimeType（スペック §4.4 手順2）
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"

# 対応mimeType（スペック §4.4 手順3: "text/plain・text/markdown 等のテキスト系、および
# application/vnd.google-apps.document"）。text/x-markdownはDriveアップロード経路によって
# 付与されうる、text/markdownに準ずる別表記への保険。
_SUPPORTED_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    GOOGLE_DOC_MIME_TYPE,
}

# 曖昧なmimeType（例: application/octet-stream）の救済判定に使うファイル名拡張子
_RESCUE_EXTENSIONS = (".md", ".txt")


class SkippedItem(NamedTuple):
    """ショートカット・非対応mimeTypeによりスキップされたDrive上のアイテム1件を表す。

    T4がこれを `ingest_runs.stats` 相当へ畳み込む（スペック §4.4 手順2/3、完了条件
    「スキップがログ・統計に残ることを確認」）。
    """

    name: str
    path: str
    reason: str


class DriveLoadResult(NamedTuple):
    """`load_drive()` の戻り値。

    - documents: 新規、または `modifiedTime` に変化がありDriveから本文を再取得して
      `content_hash` を計算し直した候補（`source_type="google_drive"`）。ここに含まれる
      `Document` を既存の `ingestion/diff.py::classify()` にそのまま渡して最終判定するのが
      T4の責務（本モジュールは classify() を呼ばない・変更検知を独自に再実装しない）。
    - skipped: ショートカット・非対応mimeTypeによりスキップされたアイテム（構造化情報）。
      「取り込まないと選択した」ものであり、失敗ではない。
    - failed: ダウンロード（`GoogleDriveClient.download_content`）またはデコードに失敗した
      アイテム（構造化情報。`SkippedItem`と同じ形を再利用する）。skippedとは異なり
      「取り込むつもりだったが失敗した」ものであり、スペック §4.9「個別ファイルのダウンロード
      失敗」行に対応する。T4がこれを `ingest_runs.stats.failed_files` へ畳み込む
      （T7・m9_tasklist.md T7 作業項目3）。1件の失敗が走査全体を中断させないことがこの
      フィールドの存在意義。
    - found_external_ids: 対応mimeType判定を通過し、フォルダ配下に現存する全ファイルの
      `external_id` 集合。`documents` には「`modifiedTime` 無変化のためダウンロードを
      省略した」ファイルは含まれないため、`documents` の `external_id` だけを「走査で
      見つかったファイル」の集合として扱うと、無変化ファイルを誤って「消えた」と
      判定しかねない（スペック §4.4 手順5の削除検知はT4の責務だが、判定の材料として
      正しい「現存する全ファイル」の情報が必要になる）。本モジュールはこの集合を使った
      削除判定自体は行わない。
    """

    documents: List[Document]
    skipped: List[SkippedItem]
    failed: List[SkippedItem]
    found_external_ids: Set[str]


class _ExistingSourceInfo(NamedTuple):
    """`modifiedTime` 事前フィルタの判定に必要な、既存Driveソース側の情報のみを抜き出したもの。"""

    source_updated_at: Optional[datetime.datetime]
    deleted_at: Optional[datetime.datetime]


def _is_ingestible(file: DriveFile) -> bool:
    """対応mimeType、または曖昧なmimeType（例: application/octet-stream）でも
    ファイル名拡張子が `.md`/`.txt` なら救済して対応形式とみなす（スペック §4.4 手順3）。
    """
    if file.mime_type in _SUPPORTED_MIME_TYPES:
        return True
    return file.name.lower().endswith(_RESCUE_EXTENSIONS)


def _should_skip_download(file: DriveFile, existing: Optional[_ExistingSourceInfo]) -> bool:
    """`modifiedTime` 事前フィルタ（スペック §4.4 手順4・変更検知の2段構えの第1段）。

    既存の生存Driveソース（`deleted_at is None`）があり、かつDriveの`modifiedTime`が
    前回取り込み時の`source_updated_at`から変化していない場合のみダウンロードを省略する。
    新規ファイル・ソフトデリート済みソース（リバイブ判定にはclassify()がcontent_hashを
    必要とするため）・modifiedTime変化ありの場合は常にダウンロードし直す。
    """
    if existing is None:
        return False
    if existing.deleted_at is not None:
        return False
    if file.modified_time is None or existing.source_updated_at is None:
        return False
    return file.modified_time == existing.source_updated_at


def _load_existing_drive_sources(db: Session) -> Dict[str, _ExistingSourceInfo]:
    """既存のDriveソース（`source_type='google_drive'`）の `external_id -> (source_updated_at,
    deleted_at)` を一括取得する。大量ファイルの走査中にファイル単位でクエリを発行するN+1を
    避けるため、走査開始前に一度だけ実行する。
    """
    rows = (
        db.query(Source.external_id, Source.source_updated_at, Source.deleted_at)
        .filter(Source.source_type == "google_drive", Source.external_id.isnot(None))
        .all()
    )
    return {
        external_id: _ExistingSourceInfo(source_updated_at=source_updated_at, deleted_at=deleted_at)
        for external_id, source_updated_at, deleted_at in rows
    }


def _walk(
    client: GoogleDriveClient,
    folder_id: str,
    breadcrumb: str,
    existing_by_external_id: Dict[str, _ExistingSourceInfo],
    documents: List[Document],
    skipped: List[SkippedItem],
    failed: List[SkippedItem],
    found_external_ids: Set[str],
    api_call_counts: Dict[str, int],
) -> None:
    """`folder_id` 配下を再帰的に走査し、`documents`/`skipped`/`failed`/`found_external_ids` を
    その場で追記していく（`gdrive_client.list_children` がページネーションを内部で処理済みの
    ため、ここでは子フォルダへの再帰のみを扱えばよい）。`api_call_counts` は Drive API 呼び出し
    回数を`load_drive()`側の`gdrive_scan` spanメタデータへ渡すためのカウンタ（スペック §6）。
    """
    api_call_counts["list_calls"] += 1
    for child in client.list_children(folder_id):
        child_path = f"{breadcrumb}/{child.name}" if breadcrumb else child.name

        if child.mime_type == FOLDER_MIME_TYPE:
            _walk(
                client,
                child.id,
                child_path,
                existing_by_external_id,
                documents,
                skipped,
                failed,
                found_external_ids,
                api_call_counts,
            )
            continue

        if child.mime_type == SHORTCUT_MIME_TYPE:
            reason = "Google Driveショートカットは非対応のためスキップしました（リンク先の解決は行わない）"
            skipped.append(SkippedItem(name=child.name, path=child_path, reason=reason))
            print(f"Skipping {child_path}: {reason}")
            continue

        if not _is_ingestible(child):
            reason = f"非対応のmimeTypeのためスキップしました: {child.mime_type}"
            skipped.append(SkippedItem(name=child.name, path=child_path, reason=reason))
            print(f"Skipping {child_path}: {reason}")
            continue

        found_external_ids.add(child.id)

        existing = existing_by_external_id.get(child.id)
        if _should_skip_download(child, existing):
            continue

        api_call_counts["download_calls"] += 1
        try:
            content = client.download_content(child)
            text = content.decode("utf-8")
        except Exception as e:
            # スペック §4.9「個別ファイルのダウンロード失敗」: 1件の失敗（ネットワーク瞬断・
            # 取得直後にアクセス不能になった等の`GoogleDriveAccessError`、非UTF-8コンテンツによる
            # デコード失敗を含む）で走査全体を中断させず、スキップして続行する。
            # ingest_runs.stats.failed_files への畳み込みは呼び出し元（indexer.py）の責務
            reason = f"ダウンロードに失敗したためスキップしました: {e}"
            failed.append(SkippedItem(name=child.name, path=child_path, reason=reason))
            print(f"Failed to download {child_path}: {e}")
            continue

        documents.append(
            Document(
                path=child_path,
                title=extract_title(text, child.name),
                content=text,
                updated_at=child.modified_time or utcnow(),
                source_type="google_drive",
                external_id=child.id,
                source_url=child.web_view_link,
            )
        )


@observe(name="gdrive_scan")
def load_drive(db: Session, folder_id: str) -> DriveLoadResult:
    """`folder_id` を起点にDriveフォルダを再帰的に走査し、`Document` 相当の内部表現へ
    正規化する（スペック §4.4/§4.5）。

    - フォルダ再帰・ページネーションは `GoogleDriveClient.list_children` に委譲する
      （ページネーション自体は `list_children` 内で処理済み）
    - mimeType判定 + ファイル名拡張子による救済判定でショートカット・非対応形式をスキップする
    - `modifiedTime` 事前フィルタで無変化ファイルはダウンロードを省略する
    - ダウンロードした本文から `Document`（`content_hash` はDocument.__init__が自動計算）を
      構築する。`content_hash` に基づく最終判定（`classify()`）はT4が行う
    - 走査全体を `@observe(name="gdrive_scan")` でLangfuseにtrace化し、走査件数・API呼び出し
      回数をspanメタデータへ記録する（スペック §6・Drive APIのレート制限診断用。T7）
    """
    client = GoogleDriveClient()
    existing_by_external_id = _load_existing_drive_sources(db)

    documents: List[Document] = []
    skipped: List[SkippedItem] = []
    failed: List[SkippedItem] = []
    found_external_ids: Set[str] = set()
    api_call_counts: Dict[str, int] = {"list_calls": 0, "download_calls": 0}

    _walk(
        client,
        folder_id,
        "",
        existing_by_external_id,
        documents,
        skipped,
        failed,
        found_external_ids,
        api_call_counts,
    )

    get_client().update_current_span(
        metadata={
            # found_external_idsには、ダウンロードに成功したファイルと失敗したファイルの両方が
            # 含まれる（185行目の found_external_ids.add(child.id) はダウンロード試行より前に
            # 実行されるため）。よって failed を別途足すと二重カウントになる。
            # skipped（ショートカット・非対応mimeType）はfound_external_idsに一切含まれない
            # （185行目に到達する前にcontinueする）ため、これだけ加算すればよい
            "files_scanned": len(found_external_ids) + len(skipped),
            "documents_found": len(documents),
            "skipped": len(skipped),
            "failed": len(failed),
            "list_calls": api_call_counts["list_calls"],
            "download_calls": api_call_counts["download_calls"],
        },
    )

    return DriveLoadResult(
        documents=documents, skipped=skipped, failed=failed, found_external_ids=found_external_ids
    )
