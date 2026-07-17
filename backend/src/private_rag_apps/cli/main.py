# コーパス取り込み用CLI。ingestion層を呼ぶ薄い層で、ロジックは持たない。
# `make ingest` / `make demo` / `make ingest-gdrive` から実行される。
import argparse
import sys
from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.ingestion.indexer import run_gdrive_ingestion, run_ingestion

def ingest(trigger: str, force_delete: bool) -> None:
    db = SessionLocal()
    try:
        print("Starting ingestion...")
        run = run_ingestion(db, trigger=trigger, force_delete=force_delete)
        if run.status == "error":
            print(f"Ingestion finished with error: {run.error}", file=sys.stderr)
            sys.exit(1)
        print("Ingestion complete.")
    finally:
        db.close()

def ingest_gdrive(trigger: str, force_delete: bool, folder_id: str) -> None:
    if not folder_id:
        print(
            "DRIVE_FOLDER_ID が設定されていません。backend/.env に取り込み対象フォルダの "
            "Drive ID を設定するか、--folder-id で指定してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    db = SessionLocal()
    try:
        print("Starting Google Drive ingestion...")
        run = run_gdrive_ingestion(db, folder_id=folder_id, trigger=trigger, force_delete=force_delete)
        if run.status == "error":
            print(f"Ingestion finished with error: {run.error}", file=sys.stderr)
            sys.exit(1)
        print("Ingestion complete.")
    finally:
        db.close()

def main() -> None:
    parser = argparse.ArgumentParser(description="Private RAG Apps CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Ingest command
    p_ingest = subparsers.add_parser("ingest", help="Ingest documents from corpus")
    p_ingest.add_argument(
        "--trigger",
        default=settings.ingest_trigger,
        choices=["cli", "demo"],
        help="ingest_runs.trigger に記録する値（既定値は INGEST_TRIGGER / core.config.Settings 経由）",
    )
    p_ingest.add_argument(
        "--force-delete",
        action="store_true",
        default=settings.force_delete,
        help="削除安全弁をバイパスする（FORCE_DELETE=1 でも指定可）",
    )

    # Ingest from Google Drive command（M9。ARQ/API経由トリガはT5のスコープでありCLIはここに含めない）
    p_ingest_gdrive = subparsers.add_parser(
        "ingest-gdrive", help="Ingest documents from a Google Drive folder"
    )
    p_ingest_gdrive.add_argument(
        "--trigger",
        default="cli",
        choices=["cli"],
        help="ingest_runs.trigger に記録する値（Drive取り込みのCLI経路は常にcli）",
    )
    p_ingest_gdrive.add_argument(
        "--force-delete",
        action="store_true",
        default=settings.force_delete,
        help="削除安全弁をバイパスする（FORCE_DELETE=1 でも指定可）",
    )
    p_ingest_gdrive.add_argument(
        "--folder-id",
        default=settings.drive_folder_id,
        help="取り込み対象のDriveフォルダID（既定値は DRIVE_FOLDER_ID / core.config.Settings 経由）",
    )

    args = parser.parse_args()

    if args.command == "ingest":
        ingest(trigger=args.trigger, force_delete=args.force_delete)
    elif args.command == "ingest-gdrive":
        ingest_gdrive(trigger=args.trigger, force_delete=args.force_delete, folder_id=args.folder_id)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
