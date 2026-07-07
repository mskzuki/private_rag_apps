import re
from typing import List, Dict, Any

class ChunkResult:
    def __init__(self, content: str, metadata: Dict[str, Any]):
        self.content = content
        self.metadata = metadata

def chunk_markdown(content: str, max_chars: int = 512) -> List[ChunkResult]:
    # Very basic chunking by heading for M0.
    lines = content.split('\n')
    chunks = []
    current_chunk = []
    current_length = 0
    current_heading = ""

    for line in lines:
        match = re.match(r'^(#+)\s+(.*)', line)
        if match:
            # new heading, maybe split
            if current_chunk and current_length > 0:
                chunks.append(ChunkResult("\n".join(current_chunk), {"heading": current_heading}))
                current_chunk = []
                current_length = 0
            current_heading = match.group(2)
        
        current_chunk.append(line)
        current_length += len(line)

        # Force split if it gets too large, even without heading
        if current_length > max_chars:
            chunks.append(ChunkResult("\n".join(current_chunk), {"heading": current_heading}))
            current_chunk = []
            current_length = 0

    if current_chunk:
        chunks.append(ChunkResult("\n".join(current_chunk), {"heading": current_heading}))

    return chunks
