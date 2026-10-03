import os
import re
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer


class LocalAI:

  def __init__(self):
    self.model_name = os.getenv("EPOCH_MODEL", "google/flan-t5-base")

    print(f"Loading local AI model ({self.model_name})...")
    self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
    self.model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)

  def _generate(self, prompt, max_new_tokens=350):
    inputs = self.tokenizer(
        prompt, return_tensors="pt", max_length=512, truncation=True
    )
    outputs = self.model.generate(
        **inputs, max_new_tokens=max_new_tokens, do_sample=False
    )
    return self.tokenizer.decode(outputs[0], skip_special_tokens=True).strip()

  def answer_question(self, question, context):
    """Answers queries by hunting for accurate explanations and definitions."""
    prompt = f"""
Read the text below and answer the question accurately. If the text defines the term or concept, provide that definition clearly.

Context:
{context}

Question:
{question}

Answer:
"""
    return self._generate(prompt, 250)

  def summary(self, context):
    """Creates a concise study summary using headings and bullet points."""
    prompt = f"""
Create a clear study summary from the material below.
Use headings and concise bullet points.
Include important definitions, processes, facts and relationships.
Do not invent information.

Material:
{context}

Study summary:
"""
    return self._generate(prompt, 450)

  def study_notes(self, context):
    """Transforms raw document text into structured revision notes."""
    prompt = f"""
Turn the following material into useful revision notes.
Use:
- Topic headings
- Key definitions
- Important facts
- Explanations
- Examples where present
- Common relationships or cause/effect links

Do not invent facts.

Material:
{context}

Revision notes:
"""
    return self._generate(prompt, 550)

  def flashcards(self, context):
    """Generates multiple flashcards reliably by chunking document text."""
    chunk_size = 1000
    chunks = [
        context[i : i + chunk_size] for i in range(0, len(context), chunk_size)
    ]

    all_cards = []
    # Process up to 6 chunks to build a rich card deck safely
    for chunk in chunks[:6]:
      prompt = f"""
Create a study flashcard from this text. 
Format it like this:
QUESTION: [Your question here]
ANSWER: [Your answer here]

Text:
{chunk}
"""
      raw = self._generate(prompt, 150)
      parsed = self._parse_flashcards(raw, chunk)
      all_cards.extend(parsed)

    # Deduplicate based on question text
    seen = set()
    unique_cards = []
    for card in all_cards:
      if card["question"] not in seen:
        seen.add(card["question"])
        unique_cards.append(card)

    return unique_cards[:12]

  def _parse_flashcards(self, raw, fallback_chunk=""):
    """Flexible line-by-line parser for local model outputs."""
    cards = []
    raw_lines = raw.split("\n")

    current_q = None
    current_a = None

    for line in raw_lines:
      line_clean = line.strip()
      if line_clean.lower().startswith("question:"):
        if current_q and current_a:
          cards.append({
              "type": "WRITTEN",
              "question": current_q,
              "answer": current_a,
          })
        current_q = re.sub(r"^question:\s*", "", line_clean, flags=re.IGNORECASE)
        current_a = None
      elif line_clean.lower().startswith("answer:"):
        current_a = re.sub(r"^answer:\s*", "", line_clean, flags=re.IGNORECASE)

    if current_q and current_a:
      cards.append(
          {"type": "WRITTEN", "question": current_q, "answer": current_a}
      )

    # Fallback wrapper if the model output is unstructured text
    if not cards and len(raw.strip()) > 10:
      cards.append({
          "type": "WRITTEN",
          "question": f"Explain key concept from: {fallback_chunk[:60]}...",
          "answer": raw.strip(),
      })

    return cards

  def practice_questions(self, context):
    """Generates numbered revision questions without answers."""
    prompt = f"""
Create 10 revision questions from the material.
Mix short-answer, explanation and application questions.
Number them 1 to 10.
Do not provide answers.
Do not invent information.

Material:
{context}

Questions:
"""
    return self._generate(prompt, 500)