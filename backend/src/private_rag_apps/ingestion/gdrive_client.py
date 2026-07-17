"""Google Drive API (v3) への薄いラッパー（M9・スペック §4.3, §4.5）。

`files.list` / `files.get`(alt=media) / `files.export` を呼ぶだけの薄い層であり、
フォルダの再帰探索・変更検知・mimeType判定等のロジックは一切持たない
（それらは ingestion/gdrive_loader.py の責務。T3）。

認証はサービスアカウント（OAuth不使用）。`core/config.py` の設定
（`DRIVE_FOLDER_ID` / `DRIVE_SERVICE_ACCOUNT_FILE`）を明示的に読み込み、
Google認証ライブラリに渡す。`GOOGLE_APPLICATION_CREDENTIALS` 等の暗黙的な
環境変数経由の認証には依存しない（AGENTS.md: 設定は core/config.py に一元化する方針）。
"""

import datetime
import json
from pathlib import Path
from typing import Any, List, NamedTuple, Optional

from google.auth import exceptions as google_auth_exceptions
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from private_rag_apps.core.config import settings

# 読み取り専用スコープ（スペック §4.3）。書き込み権限は要求しない。
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# Googleドキュメントのmimetype。files.exportでtext/plainへ変換して取得する（スペック §4.4）
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
_EXPORT_MIME_TYPE = "text/plain"

# files.listで要求するフィールド（薄いラッパーとして必要十分なものに絞る。スペック §4.4/§4.7 で
# 参照される id/name/mimeType/modifiedTime/webViewLink/parents のみ）
_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink, parents)"
_PAGE_SIZE = 100


class GoogleDriveConfigError(Exception):
    """DRIVE_FOLDER_ID / DRIVE_SERVICE_ACCOUNT_FILE が未設定の場合に送出する。

    Drive機能は完全にオプトインであり、このエラーは「設定されていないので機能を使えない」
    ことを示す（スペック §5）。
    """


class GoogleDriveAuthError(Exception):
    """サービスアカウントの認証情報が読み込めない、または認証自体に失敗した場合に送出する
    （スペック §4.9「サービスアカウントの認証失敗」行）。
    """


class GoogleDriveAccessError(Exception):
    """対象フォルダ・ファイルが見つからない、またはサービスアカウントに共有されていない場合に
    送出する（スペック §4.9「対象フォルダが見つからない／共有されていない」行）。
    """


class DriveFile(NamedTuple):
    """`files.list` のレスポンス1件を表す薄い内部表現。

    T3（gdrive_loader.py）がこれを消費して探索・変更検知ロジックを実装する。
    フィールドはDrive API v3の`files.list`で実際に返る生の値をそのまま素直に写したもの
    （`mime_type`/`web_view_link`のみDriveのcamelCaseからsnake_caseに変換、
    `modified_time`のみRFC3339文字列からdatetimeへパース）で、T2側で解釈・加工はしない。
    """

    id: str
    name: str
    mime_type: str
    modified_time: Optional[datetime.datetime]
    web_view_link: Optional[str]
    parents: List[str]


def _require_drive_config() -> None:
    """DRIVE_FOLDER_ID / DRIVE_SERVICE_ACCOUNT_FILE の両方が設定されていることを確認する。
    どちらか一方でも空ならGoogle Drive機能は無効であり、早期に分かりやすいエラーで失敗させる
    （スペック §5: 「空 = Drive機能無効」）。"""
    if not settings.drive_folder_id:
        raise GoogleDriveConfigError(
            "DRIVE_FOLDER_ID が設定されていません。Google Drive 取り込み機能は無効です。"
            "backend/.env に取り込み対象フォルダの Drive ID（フォルダURL末尾のID部分）を設定してください。"
            "手順は README の「Google Drive からの取り込み」セクションを参照してください。"
        )
    if not settings.drive_service_account_file:
        raise GoogleDriveConfigError(
            "DRIVE_SERVICE_ACCOUNT_FILE が設定されていません。Google Drive 取り込み機能は無効です。"
            "backend/.env にサービスアカウントJSONキーファイルへのパスを設定してください。"
            "手順は README の「Google Drive からの取り込み」セクションを参照してください。"
        )


def _load_service_account_credentials() -> service_account.Credentials:
    """DRIVE_SERVICE_ACCOUNT_FILE のJSONキーファイルからサービスアカウント認証情報を読み込む。
    ファイル不在・JSON不正・必須フィールド欠落を早期に検知し分かりやすいエラーにする
    （スペック §4.9）。"""
    key_path = Path(settings.drive_service_account_file)
    if not key_path.is_file():
        raise GoogleDriveAuthError(
            f"サービスアカウントJSONキーファイルが見つかりません: {key_path}\n"
            "DRIVE_SERVICE_ACCOUNT_FILE の値（backend/.env）を確認してください。"
        )
    try:
        return service_account.Credentials.from_service_account_file(
            str(key_path), scopes=[DRIVE_READONLY_SCOPE]
        )
    except (ValueError, json.JSONDecodeError, google_auth_exceptions.GoogleAuthError) as e:
        # ValueErrorはJSONの必須フィールド欠落等（google-authが送出）、
        # JSONDecodeErrorはJSON構文自体が不正な場合。両方とも「キーファイルが壊れている」ことを示す。
        raise GoogleDriveAuthError(
            f"サービスアカウントJSONキーファイルの読み込みに失敗しました: {key_path}\n"
            f"詳細: {e}\n"
            "GCPコンソールでダウンロードしたサービスアカウントキー（JSON形式）か確認してください。"
        ) from e


def _parse_drive_file(raw: "dict[str, Any]") -> DriveFile:
    modified_time_raw = raw.get("modifiedTime")
    modified_time = datetime.datetime.fromisoformat(modified_time_raw) if modified_time_raw else None
    return DriveFile(
        id=raw["id"],
        name=raw.get("name", ""),
        mime_type=raw.get("mimeType", ""),
        modified_time=modified_time,
        web_view_link=raw.get("webViewLink"),
        parents=list(raw.get("parents") or []),
    )


def _http_error_status(error: HttpError) -> Optional[int]:
    status = getattr(error.resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


class GoogleDriveClient:
    """サービスアカウント認証で Google Drive API (v3) に接続する薄いクライアント。

    コンストラクタで設定検証・認証情報読み込み・APIクライアント構築までを即座に行う
    （スペック §4.9「起動・実行開始時に早期検知し実行を開始しない」）。
    `list_children`/`download_content` 以外のロジック（再帰探索・変更検知・mimeType判定）
    は持たない（T3 `gdrive_loader.py` の責務）。
    """

    def __init__(self) -> None:
        _require_drive_config()
        self._credentials = _load_service_account_credentials()
        try:
            self._service = build("drive", "v3", credentials=self._credentials, cache_discovery=False)
        except Exception as e:
            raise GoogleDriveAuthError(
                f"Google Drive APIクライアントの初期化に失敗しました: {e}"
            ) from e

    @property
    def service_account_email(self) -> str:
        """エラーメッセージでの案内・README手順の裏付けに使うサービスアカウントのメールアドレス。"""
        return str(self._credentials.service_account_email)

    def _folder_access_error(self, folder_id: str) -> GoogleDriveAccessError:
        return GoogleDriveAccessError(
            f"Google Drive フォルダ (id={folder_id}) が見つからないか、"
            "サービスアカウントに共有されていません。\n"
            "Google Drive で対象フォルダを開き、「共有」からサービスアカウントのメールアドレス "
            f"'{self.service_account_email}' を「閲覧者」として追加してください。\n"
            "手順は README の「Google Drive からの取り込み」セクションを参照してください。"
        )

    def list_children(self, folder_id: str) -> List[DriveFile]:
        """folder_id直下の子要素（ファイル・サブフォルダ・ショートカット等）を全件取得する。
        `pageToken` によるページネーションはこの中で処理する。再帰探索・mimeTypeによる
        絞り込みは行わない（呼び出し元＝T3の責務）。

        Drive APIへの最初の実アクセスとなるため、フォルダ未共有・存在しない場合の
        404/403（両者を区別する情報がAPIから得られないため一つのエラーにまとめる。
        スペック §4.9「フォルダが見つからない／共有されていない」は元々1行の事象として
        まとめられている）や、認証情報自体が拒否される401もここで検知される。
        """
        files: List[DriveFile] = []
        page_token: Optional[str] = None
        query = f"'{folder_id}' in parents and trashed = false"
        while True:
            try:
                response = (
                    self._service.files()
                    .list(
                        q=query,
                        fields=_LIST_FIELDS,
                        pageToken=page_token,
                        pageSize=_PAGE_SIZE,
                    )
                    .execute()
                )
            except HttpError as e:
                status = _http_error_status(e)
                if status == 401:
                    raise GoogleDriveAuthError(
                        "Google Drive サービスアカウントの認証に失敗しました"
                        f"（folder_id={folder_id}）。DRIVE_SERVICE_ACCOUNT_FILE のJSONキーが"
                        f"有効か確認してください。詳細: {e}"
                    ) from e
                if status in (403, 404):
                    raise self._folder_access_error(folder_id) from e
                raise
            for raw in response.get("files", []):
                files.append(_parse_drive_file(raw))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return files

    def download_content(self, file: DriveFile) -> bytes:
        """ファイル本文を取得する。通常ファイルは`files.get(alt=media)`、Googleドキュメントは
        `files.export(mimeType=text/plain)`で取得する（スペック §4.4 手順4）。
        SHA256でのハッシュ計算・変更検知・classify()への受け渡しは呼び出し元（T3/T4）の責務。
        """
        try:
            if file.mime_type == GOOGLE_DOC_MIME_TYPE:
                request = self._service.files().export(fileId=file.id, mimeType=_EXPORT_MIME_TYPE)
            else:
                request = self._service.files().get_media(fileId=file.id)
            content: Any = request.execute()
        except HttpError as e:
            raise GoogleDriveAccessError(
                f"Google Driveファイル '{file.name}' (id={file.id}) の取得に失敗しました: {e}"
            ) from e
        return content if isinstance(content, bytes) else str(content).encode("utf-8")
