import argparse
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.ingestion.indexer import run_ingestion

def ingest():
    db = SessionLocal()
    try:
        print("Starting ingestion...")
        run_ingestion(db, trigger="cli")
        print("Ingestion complete.")
    finally:
        db.close()

def main():
    parser = argparse.ArgumentParser(description="Private RAG Apps CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Ingest command
    subparsers.add_parser("ingest", help="Ingest documents from corpus")

    args = parser.parse_args()

    if args.command == "ingest":
        ingest()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
