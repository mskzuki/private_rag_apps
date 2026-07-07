import os
import hashlib
import datetime
from pathlib import Path
from typing import List

class Document:
    def __init__(self, path: str, title: str, content: str, updated_at: datetime.datetime):
        self.path = path
        self.title = title
        self.content = content
        self.updated_at = updated_at
        self.content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

def load_directory(directory: str) -> List[Document]:
    docs = []
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
                    
                    # Extract title from first H1 or use filename
                    title = file
                    for line in content.split('\n'):
                        if line.startswith('# '):
                            title = line[2:].strip()
                            break
                    
                    mtime = datetime.datetime.fromtimestamp(full_path.stat().st_mtime, tz=datetime.timezone.utc)
                    docs.append(Document(rel_path, title, content, mtime))
                except Exception as e:
                    print(f"Skipping {full_path}: {e}")
                    
    return docs
