from typing import List, Optional
from pathlib import Path
import json

from pydantic import BaseModel, Field


class RelevantDoc(BaseModel):
    path: str
    heading: Optional[str] = None
    grade: int = 1


class DatasetItem(BaseModel):
    id: str
    question: str
    relevant: List[RelevantDoc] = Field(default_factory=list)
    reference_answer: str
    tags: List[str] = Field(default_factory=list)
    expect_no_answer: bool = False
    turns: Optional[List[str]] = None


def load_dataset(file_path: str | Path) -> List[DatasetItem]:
    """Load and validate dataset from JSONL file."""
    items = []
    with open(file_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                items.append(DatasetItem.model_validate(data))
            except Exception as e:
                raise ValueError(f"Invalid JSON/Schema at line {i + 1}: {e}")
    return items


def validate_paths(dataset: List[DatasetItem], seed_dir: str | Path) -> None:
    """Validate that all paths in the dataset exist in the seed directory."""
    seed_path = Path(seed_dir)
    missing = []
    for item in dataset:
        for doc in item.relevant:
            full_path = seed_path / doc.path
            if not full_path.exists():
                missing.append((item.id, doc.path))
    
    if missing:
        errors = [f"Item {item_id}: path '{path}' not found" for item_id, path in missing]
        raise FileNotFoundError(f"Missing files in seed directory:\n" + "\n".join(errors))
