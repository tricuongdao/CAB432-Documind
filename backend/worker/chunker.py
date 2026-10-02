"""Sentence-aware text chunking with token overlap.

Chunks are sized by characters (a 4-chars-per-token approximation that is
accurate enough for Titan's 8192-token input limit) and use a suffix window
of the previous chunk as overlap, so retrieval does not lose context that
falls on a chunk boundary.
"""

import re

from config import CHUNK_TARGET_CHARS, CHUNK_OVERLAP_CHARS, MAX_CHUNKS

_PARAGRAPH_SPLIT = re.compile(r"\n{2,}")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list:
    """Split into sentence-ish units, one paragraph at a time.

    The split is deliberately approximate: abbreviations ("e.g.") may split
    early. That is harmless for retrieval quality and much cheaper than a
    full NLP sentence segmenter.
    """
    sentences = []
    for paragraph in _PARAGRAPH_SPLIT.split(text):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        sentences.extend(s for s in _SENTENCE_SPLIT.split(paragraph) if s.strip())
    return sentences


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def chunk_text(text: str, target_chars: int = None, overlap_chars: int = None,
               max_chunks: int = None) -> list:
    """Pack sentences into overlapping chunks.

    Returns a list of dicts:
        {"idx", "text", "chars", "tokensEst", "overlapChars"}
    where overlapChars records the measured verbatim overlap with the chunk
    before it.
    """
    target_chars = target_chars or CHUNK_TARGET_CHARS
    overlap_chars = overlap_chars or CHUNK_OVERLAP_CHARS
    max_chunks = max_chunks or MAX_CHUNKS

    sentences = split_sentences(text)
    if not sentences:
        return []

    # Greedy sentence packing with a carry-over tail.
    raw_chunks = []
    current, current_len = [], 0
    for sentence in sentences:
        if current and current_len + len(sentence) + 1 > target_chars:
            raw_chunks.append(" ".join(current))
            tail, tail_len = [], 0
            for prev in reversed(current):
                if tail and tail_len + len(prev) > overlap_chars:
                    break
                tail.insert(0, prev)
                tail_len += len(prev) + 1
            current = list(tail)
            current_len = tail_len
        current.append(sentence)
        current_len += len(sentence) + 1
    if current:
        raw_chunks.append(" ".join(current))

    # Normalise: strip, merge tiny fragments forward, index, cap.
    out = []
    for c in raw_chunks[:max_chunks]:
        t = c.strip()
        if not t:
            continue
        if len(t) < 40:
            if out:  # merge a tiny tail into the previous chunk
                merged = out[-1]["text"] + " " + t
                out[-1] = {
                    "idx": out[-1]["idx"],
                    "text": merged,
                    "chars": len(merged),
                    "tokensEst": estimate_tokens(merged),
                    "overlapChars": out[-1]["overlapChars"],
                }
            continue
        out.append({
            "idx": len(out),
            "text": t,
            "chars": len(t),
            "tokensEst": estimate_tokens(t),
            "overlapChars": 0,
        })

    # Measure the true verbatim overlap (suffix of previous == prefix of next).
    for i in range(1, len(out)):
        prev, cur = out[i - 1]["text"], out[i]["text"]
        limit = min(len(prev), len(cur), 400)
        overlap = 0
        for k in range(limit, 0, -1):
            if prev.endswith(cur[:k]):
                overlap = k
                break
        out[i]["overlapChars"] = overlap
    return out
