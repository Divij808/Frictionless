from pathlib import Path
from pypdf import PdfReader


def extract_text(path):
    path = Path(path)

    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")

    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        pages = []

        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)

        return "\n\n".join(pages)

    raise ValueError("Unsupported file type.")
