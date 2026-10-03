from pathlib import Path
import json
import re
import math
from collections import Counter


STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "what",
    "when", "where", "which", "who", "how", "why", "are", "was",
    "were", "is", "in", "of", "to", "a", "an", "on", "by", "or",
    "as", "it", "be", "does", "do", "can", "could", "would", "should"
}


def tokenize(text):
    words = re.findall(r"[a-zA-Z0-9']+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


class Retriever:
    def __init__(self, index_path):
        self.index_path = Path(index_path)
        self.documents = []
        self._load()

    def _load(self):
        if self.index_path.exists():
            try:
                self.documents = json.loads(
                    self.index_path.read_text(encoding="utf-8")
                )
            except Exception:
                self.documents = []

    def _save(self):
        self.index_path.parent.mkdir(exist_ok=True)
        self.index_path.write_text(
            json.dumps(self.documents, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    def add_document(self, source_id, filename, chunks):
        self.documents = [
            d for d in self.documents
            if d["source_id"] != source_id
        ]

        for number, text in enumerate(chunks):
            self.documents.append({
                "source_id": source_id,
                "filename": filename,
                "chunk": number,
                "text": text
            })

        self._save()

    def remove_document(self, source_id):
        self.documents = [
            d for d in self.documents
            if d["source_id"] != source_id
        ]
        self._save()

    def get_chunks_for_sources(self, source_ids):
        return [
            d for d in self.documents
            if d["source_id"] in source_ids
        ]

    def search(self, query, top_k=5):
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        q_counts = Counter(q_tokens)
        scored = []

        for doc in self.documents:
            tokens = tokenize(doc["text"])
            if not tokens:
                continue

            counts = Counter(tokens)
            score = 0.0

            for word, q_count in q_counts.items():
                if word in counts:
                    # Simple TF weighting.
                    score += (1 + math.log(counts[word])) * q_count

            # Small phrase bonus.
            lowered_query = query.lower()
            lowered_text = doc["text"].lower()
            if len(lowered_query) > 8 and lowered_query in lowered_text:
                score += 5

            if score > 0:
                item = dict(doc)
                item["score"] = score
                scored.append(item)

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]
