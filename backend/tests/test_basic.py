import pytest
from private_rag_apps.ingestion.chunker import chunk_markdown

class TestChunkMarkdown:
    def test_splits_on_headings(self):
        content = "# Title\nSome text\n## Section A\nContent A\n## Section B\nContent B"
        chunks = chunk_markdown(content, max_chars=5000)
        assert len(chunks) >= 2
        # First chunk should contain the title
        assert "# Title" in chunks[0].content

    def test_single_chunk_when_short(self):
        content = "# Title\nShort text"
        chunks = chunk_markdown(content, max_chars=5000)
        assert len(chunks) == 1
        assert chunks[0].metadata["heading"] == "Title"

    def test_force_splits_large_content(self):
        content = "A" * 600
        chunks = chunk_markdown(content, max_chars=512)
        assert len(chunks) >= 2

    def test_empty_content(self):
        chunks = chunk_markdown("")
        # Should return at least one chunk (the empty one)
        assert len(chunks) >= 1

    def test_heading_metadata(self):
        content = "# Main\ntext\n## Sub\nmore text"
        chunks = chunk_markdown(content, max_chars=5000)
        assert chunks[0].metadata["heading"] == "Main"
        assert chunks[1].metadata["heading"] == "Sub"


class TestCitationFormatting:
    """Test that citation formatting is correct."""

    def test_build_context_text(self):
        from private_rag_apps.prompts.rag import build_context_text

        chunks = [
            {"title": "Doc A", "content": "Content of A"},
            {"title": "Doc B", "content": "Content of B"},
        ]
        result = build_context_text(chunks)
        assert "[1] Doc A" in result
        assert "[2] Doc B" in result
        assert "Content of A" in result
        assert "Content of B" in result

    def test_context_numbering_starts_at_one(self):
        from private_rag_apps.prompts.rag import build_context_text

        chunks = [{"title": f"Doc {i}", "content": f"Content {i}"} for i in range(5)]
        result = build_context_text(chunks)
        assert "[1]" in result
        assert "[5]" in result
        assert "[0]" not in result
