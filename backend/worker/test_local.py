#!/usr/bin/env python3
"""Local sanity tests for the DocuMind worker pipeline (no AWS required).

Covers: sentence splitting, chunk packing + measured overlap, PDF extraction
(happy path, password-protected, corrupt, blank), the Bedrock adapter's
request/response shapes per model family, and TTL helpers.

Run: python backend/worker/test_local.py
Exit code 0 = all checks passed.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"PASS  {name}")
    except Exception as exc:
        FAIL.append((name, exc))
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_pdf(path, pages=2, text="DocuMind test document content marker"):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(path, pagesize=A4)
    for i in range(pages):
        y = 780
        for line in range(30):
            c.drawString(60, y, f"{text} page {i + 1} line {line}")
            y -= 22
        c.showPage()
    c.save()


def make_blank_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(path, pagesize=A4)
    c.showPage()
    c.save()


def make_encrypted_pdf(path):
    from pypdf import PdfWriter
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas
    base = path + ".plain.pdf"
    c = Canvas(base, pagesize=A4)
    c.drawString(60, 760, "secret content " * 40)
    c.showPage()
    c.save()
    w = PdfWriter(clone_from=base)
    w.encrypt("n12136514")
    with open(path, "wb") as fh:
        w.write(fh)
    os.unlink(base)


TMP = os.path.join(os.environ.get("TMPDIR", "."), "documind_test")
os.makedirs(TMP, exist_ok=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def t_sentences():
    from chunker import split_sentences
    s = split_sentences("One sentence. Two sentence! Three sentence? Four.")
    assert len(s) == 4, s


def t_chunks_overlap():
    from chunker import chunk_text
    text = " ".join(f"Sentence number {i} discusses cloud architecture topic {i}." for i in range(120))
    chunks = chunk_text(text, target_chars=600, overlap_chars=120)
    assert len(chunks) > 3, f"expected several chunks, got {len(chunks)}"
    assert chunks[0]["idx"] == 0
    for c in chunks:
        assert c["chars"] <= 600 + 200, f"chunk too big: {c['chars']}"
    # overlap measured between consecutive chunks
    carr = [c["overlapChars"] for c in chunks[1:]]
    assert all(v >= 0 for v in carr)
    assert sum(1 for v in carr if v > 20) >= len(carr) - 1, f"weak overlap: {carr}"
    # no text loss: first and last sentences present somewhere
    joined = " ".join(c["text"] for c in chunks)
    assert "Sentence number 0 " in joined
    assert "Sentence number 119 " in joined


def t_extract_happy():
    from extract import extract_text
    p = os.path.join(TMP, "ok.pdf")
    make_pdf(p)
    out = extract_text(p)
    assert out["pages"] == 2, out["pages"]
    assert "marker" in out["text"]


def t_extract_password():
    from extract import extract_text
    from extract import PoisonError
    p = os.path.join(TMP, "enc.pdf")
    make_encrypted_pdf(p)
    try:
        extract_text(p)
    except PoisonError as exc:
        assert "password" in str(exc).lower(), str(exc)
        return
    raise AssertionError("encrypted PDF was not rejected")


def t_extract_corrupt():
    from extract import extract_text
    from extract import PoisonError
    p = os.path.join(TMP, "corrupt.pdf")
    with open(p, "wb") as fh:
        fh.write(b"%PDF-1.4 this is not really a pdf" + os.urandom(300))
    try:
        extract_text(p)
    except PoisonError:
        return
    raise AssertionError("corrupt PDF was not rejected")


def t_extract_blank():
    from extract import extract_text
    from extract import PoisonError
    p = os.path.join(TMP, "blank.pdf")
    make_blank_pdf(p)
    try:
        extract_text(p)
    except PoisonError:
        return
    raise AssertionError("blank PDF was not rejected")


def t_llm_shapes():
    from llm import _gen_body, _parse_gen
    b = _gen_body("anthropic.claude-3-haiku-20240307-v1:0", "hi", 10, 0.2)
    assert "messages" in b and b["max_tokens"] == 10
    b = _gen_body("google.gemma-3-12b-it", "hi", 10, 0.2)
    assert b["messages"][0]["content"] == "hi" and b["max_tokens"] == 10
    b = _gen_body("amazon.nova-lite-v1:0", "hi", 10, 0.2)
    assert "inferenceConfig" in b
    b = _gen_body("amazon.titan-text-premier-v1:0", "hi", 10, 0.2)
    assert "textGenerationConfig" in b
    assert _parse_gen("google.gemma-3-12b-it",
                      {"choices": [{"message": {"content": "OK"}}]}) == "OK"
    assert _parse_gen("anthropic.x", {"content": [{"text": "A"}]}) == "A"
    assert _parse_gen("amazon.nova-lite-v1:0",
                      {"output": {"message": {"content": [{"text": "N"}]}}}) == "N"


def t_ttl():
    from config import ttl_epoch
    t = ttl_epoch(24)
    assert abs(t - (time.time() + 86400)) < 60


if __name__ == "__main__":
    check("split_sentences", t_sentences)
    check("chunk overlap + bounds", t_chunks_overlap)
    check("extract happy path", t_extract_happy)
    check("extract password-protected -> PoisonError", t_extract_password)
    check("extract corrupt -> PoisonError", t_extract_corrupt)
    check("extract blank -> PoisonError", t_extract_blank)
    check("llm adapter shapes", t_llm_shapes)
    check("ttl helper", t_ttl)
    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
