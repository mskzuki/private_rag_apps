# コーパス取り込み用CLI。ingestion層を呼ぶ薄い層で、ロジックは持たない。
# `make ingest` / `make demo` から実行される。
import argparse
import sys
from private_rag_apps.core.config import settings
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.ingestion.indexer import run_ingestion

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

    args = parser.parse_args()

    if args.command == "ingest":
        ingest(trigger=args.trigger, force_delete=args.force_delete)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
