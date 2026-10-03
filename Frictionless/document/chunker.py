def chunk_text(text, chunk_size=900, overlap=150):
    text = " ".join(text.replace("\x00", " ").split())

    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Prefer ending at a sentence or word boundary.
        if end < len(text):
            boundary = max(
                text.rfind(". ", start, end),
                text.rfind("? ", start, end),
                text.rfind("! ", start, end),
                text.rfind(" ", start, end)
            )
            if boundary > start + chunk_size // 2:
                end = boundary + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = max(end - overlap, start + 1)

    return chunks
