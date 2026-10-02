"""PDF text extraction for the DocuMind worker.

Distinguishes between *poison* inputs (corrupt, password-protected or
text-free PDFs that retrying can never fix) and transient failures
(raised normally so SQS redelivery can succeed on a later attempt).
"""

import pdfplumber

from config import MIN_TEXT_CHARS


class PoisonError(Exception):
    """A message that can never be processed successfully, whatever we retry.

    Such messages are forwarded to the dead-letter queue immediately and the
    job is marked FAILED, instead of being retried three times first.
    """


def _describe_chain(exc: Exception) -> str:
    """Lowercased text of the full exception cause chain.

    pdfplumber/pdfminer wrap the useful detail (e.g. PDFPasswordIncorrect)
    inside the __cause__/__context__ chain, so scan all of it.
    """
    parts, seen, e = [], set(), exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        parts.append(f"{type(e).__name__} {e}")
        e = e.__cause__ or e.__context__
    return " | ".join(parts).lower()


def extract_text(path: str) -> dict:
    """Extract text and metadata from a PDF file on disk.

    Returns {"text": str, "pages": int, "title": str}.
    Raises PoisonError for corrupt/encrypted/empty documents.
    """
    try:
        with pdfplumber.open(path) as pdf:
            pages = [(page.extract_text() or "") for page in pdf.pages]
            page_count = len(pdf.pages)
            title = str((pdf.metadata or {}).get("Title") or "").strip()
    except Exception as exc:
        name = type(exc).__name__
        blob = _describe_chain(exc)
        if "password" in blob or "encrypted" in blob or "decrypt" in blob:
            raise PoisonError(f"password-protected PDF ({name})") from exc
        raise PoisonError(f"corrupt or unreadable PDF ({name}: {str(exc)[:160]})") from exc

    text = "\n\n".join(pages).strip()
    if len(text) < MIN_TEXT_CHARS:
        # Image-only scans land here: with no OCR in the pipeline there is
        # nothing we can embed, so the job is failed fast and visibly.
        raise PoisonError(
            f"no extractable text ({len(text)} chars, {page_count} pages; scanned image PDFs are not supported)"
        )

    return {"text": text, "pages": page_count, "title": title}
