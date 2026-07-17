import os
import hashlib
import datetime
from pathlib import Path
from typing import List, Optional

class Document:
    """取り込み対象1件分の正規化済み内部表現。

    `source_type`/`external_id`/`source_url` は M9（Google Drive取り込み）で追加した項目
    （既定値付きのため、既存のローカル取り込み呼び出し `Document(path, title, content, updated_at)`
    はそのまま動作する）。ローカルソースは `source_type="local_fs"` のまま `external_id`/`source_url`
    を使わない。Driveソースは `ingestion/gdrive_loader.py` が `source_type="google_drive"` /
    `external_id`（Drive file ID） / `source_url`（`webViewLink`）を設定して構築する。
    """

    def __init__(
        self,
        path: str,
        title: str,
        content: str,
        updated_at: datetime.datetime,
        source_type: str = "local_fs",
        external_id: Optional[str] = None,
        source_url: Optional[str] = None,
    ):
        self.path = path
        self.title = title
        self.content = content
        self.updated_at = updated_at
        self.source_type = source_type
        self.external_id = external_id
        self.source_url = source_url
        self.content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

def extract_title(content: str, fallback: str) -> str:
    """本文先頭のH1（`# `始まりの行）があればそれをタイトルとして採用し、無ければfallbackを使う。

    ローカル取り込み（`load_directory`）とDrive取り込み（`ingestion/gdrive_loader.py`）で
    共通のタイトル抽出ロジック（重複実装を避けるためここに切り出した）。
    """
    for line in content.split('\n'):
        if line.startswith('# '):
            return line[2:].strip()
    return fallback

def load_directory(directory: str) -> List[Document]:
    """指定されたディレクトリから再帰的にマークダウンおよびテキストファイルを読み込み、Documentオブジェクトのリストを返す"""
    docs: List[Document] = []
    base_path = Path(directory)
    if not base_path.exists() or not base_path.is_dir():
        return docs

    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.md') or file.endswith('.txt'):
                full_path = Path(root) / file
                rel_path = str(full_path.relative_to(base_path))
                
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    title = extract_title(content, file)

                    mtime = datetime.datetime.fromtimestamp(full_path.stat().st_mtime, tz=datetime.timezone.utc)
                    docs.append(Document(rel_path, title, content, mtime))
                except Exception as e:
                    print(f"Skipping {full_path}: {e}")
                    
    return docs
