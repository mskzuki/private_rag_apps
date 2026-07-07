import yaml
from pathlib import Path
from private_rag_apps.core.db import SessionLocal
from private_rag_apps.retrieval.searcher import retrieve_context

def run_eval():
    yaml_path = Path("evals/golden/m0.yaml")
    if not yaml_path.exists():
        print(f"Golden dataset not found at {yaml_path}")
        return

    with open(yaml_path, "r", encoding="utf-8") as f:
        dataset = yaml.safe_load(f)

    db = SessionLocal()
    correct = 0
    total = len(dataset)

    print(f"Running Eval on {total} questions...")

    for item in dataset:
        q_id = item["id"]
        question = item["question"]
        expected_sources = item["expected_sources"]

        # Retrieve top 5
        context_chunks = retrieve_context(db, query=question, top_k=5)
        
        # Check if any expected source path is in the retrieved context
        retrieved_paths = {chunk["path"] for chunk in context_chunks}
        
        # Recall@5 logic
        is_hit = any(expected in retrieved_paths for expected in expected_sources)
        if is_hit:
            correct += 1
            print(f"[{q_id}] PASS")
        else:
            print(f"[{q_id}] FAIL - Expected one of {expected_sources}, but got {retrieved_paths}")

    recall_at_5 = correct / total
    print(f"\n--- Results ---")
    print(f"Recall@5: {recall_at_5:.2f} ({correct}/{total})")

if __name__ == "__main__":
    run_eval()
