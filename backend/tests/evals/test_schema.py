import json
import tempfile
from pathlib import Path
import pytest

from private_rag_apps.evals.schema import load_dataset, validate_paths


def test_valid_dataset():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(json.dumps({
            "id": "q1",
            "question": "test q?",
            "relevant": [{"path": "doc1.md"}],
            "reference_answer": "test a",
            "tags": ["lookup"],
            "expect_no_answer": False
        }) + "\n")
        file_path = f.name
    
    try:
        dataset = load_dataset(file_path)
        assert len(dataset) == 1
        assert dataset[0].id == "q1"
        assert len(dataset[0].relevant) == 1
        assert dataset[0].relevant[0].path == "doc1.md"
        assert dataset[0].relevant[0].grade == 1
    finally:
        Path(file_path).unlink()


def test_invalid_dataset():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(json.dumps({
            "id": "q1",
            # missing question
            "reference_answer": "test a"
        }) + "\n")
        file_path = f.name
    
    try:
        with pytest.raises(ValueError, match="Invalid JSON/Schema"):
            load_dataset(file_path)
    finally:
        Path(file_path).unlink()


def test_validate_paths():
    with tempfile.TemporaryDirectory() as td:
        seed_dir = Path(td)
        (seed_dir / "exists.md").write_text("test")
        
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(json.dumps({
                "id": "q1",
                "question": "test q?",
                "relevant": [{"path": "exists.md"}],
                "reference_answer": "test a"
            }) + "\n")
            
            f.write(json.dumps({
                "id": "q2",
                "question": "test q2?",
                "relevant": [{"path": "missing.md"}],
                "reference_answer": "test a"
            }) + "\n")
            file_path = f.name
            
        try:
            dataset = load_dataset(file_path)
            with pytest.raises(FileNotFoundError, match="Missing files in seed directory"):
                validate_paths(dataset, seed_dir)
        finally:
            Path(file_path).unlink()
