from app.core.rag import build_sources_block


def test_build_sources_block_returns_formatted_lines() -> None:
    items = [
        {"source": "policy.pdf", "chunk_id": "policy.pdf:0", "score": 0.81},
        {"source": "policy.pdf", "chunk_id": "policy.pdf:0", "score": 0.79},
        {"source": "runbook.docx", "chunk_id": "runbook.docx:2", "score": 0.77},
    ]

    block = build_sources_block(items, max_items=3)

    assert "Источники:" in block
    assert "policy.pdf (policy.pdf:0" in block
    assert "runbook.docx (runbook.docx:2" in block
    assert block.count("- policy.pdf") == 1


def test_build_sources_block_empty() -> None:
    assert build_sources_block([], max_items=3) == ""
