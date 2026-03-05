from app.core.splitter import TextSplitter


def test_splitter_creates_multiple_chunks_for_long_text():
    splitter = TextSplitter(chunk_size=100, chunk_overlap=20)
    text = " ".join(["слово"] * 300)

    chunks = splitter.split_text(text)

    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)


def test_splitter_returns_single_chunk_for_short_text():
    splitter = TextSplitter(chunk_size=1000, chunk_overlap=100)
    text = "короткий текст"

    chunks = splitter.split_text(text)

    assert chunks == ["короткий текст"]