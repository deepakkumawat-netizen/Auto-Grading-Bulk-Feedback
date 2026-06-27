"""Local NCERT RAG using Chroma + sentence-transformers.

On first call, ingests every chapter from cbse_kb into a persistent local
Chroma collection. All subsequent calls reuse the index — no network, no API.

`rag_retrieve(query, grade_level, subject)` returns a formatted context block
similar to the legacy `retrieve_context` from cbse_kb, but uses embeddings so
"Photosynthesis" matches "Life Processes" via semantic similarity.

`fetch_ncert_chapter_text(grade, subject, chapter)` fetches live NCERT book
content from ncert.nic.in (open source, free) and returns extracted text.
"""
from __future__ import annotations

import os
import re
from typing import Any

# ─── NCERT open-source book URL patterns ────────────────────────────────────
# NCERT hosts all textbooks openly at ncert.nic.in
_NCERT_BASE = "https://ncert.nic.in/textbook/pdf"

# Subject code map for NCERT PDF URLs
_SUBJECT_CODE = {
    "English":          {"10": "jela1", "9": "iela1", "8": "hela1", "7": "gela1",
                         "6": "fela1", "11": "kela1", "12": "lela1"},
    "Mathematics":      {"10": "jemh1", "9": "iemh1", "8": "hemh1", "7": "gemh1",
                         "6": "femh1", "11": "kemh1", "12": "lemh1"},
    "Maths":            {"10": "jemh1", "9": "iemh1", "8": "hemh1", "7": "gemh1",
                         "6": "femh1", "11": "kemh1", "12": "lemh1"},
    "Science":          {"10": "jesc1", "9": "iesc1", "8": "hesc1", "7": "gesc1",
                         "6": "fesc1"},
    "Social Science":   {"10": "jess4", "9": "iess4", "8": "hess4", "7": "gess4",
                         "6": "fess4"},
    "Hindi":            {"10": "jehl1", "9": "iehl1", "8": "hehl1", "7": "gehl1",
                         "6": "fehl1"},
    "Physics":          {"11": "keph1", "12": "leph1"},
    "Chemistry":        {"11": "kech1", "12": "lech1"},
    "Biology":          {"11": "kebo1", "12": "lebo1"},
}

_ncert_cache: dict[str, str] = {}  # cache fetched text by key


def fetch_ncert_chapter_text(grade: int, subject: str, chapter_num: int,
                              max_chars: int = 3000) -> str:
    """Fetch NCERT chapter text from ncert.nic.in (open source).
    Returns extracted text or '' if unavailable."""
    import urllib.request
    from pypdf import PdfReader
    import io

    grade_str = str(grade)
    subj_codes = _SUBJECT_CODE.get(subject, {})
    code = subj_codes.get(grade_str, "")
    if not code:
        return ""

    cache_key = f"{grade}|{subject}|{chapter_num}"
    if cache_key in _ncert_cache:
        return _ncert_cache[cache_key]

    # NCERT PDF URL pattern: e.g. https://ncert.nic.in/textbook/pdf/jesc101.pdf (Chapter 1)
    ch_str = str(chapter_num).zfill(2)
    url = f"{_NCERT_BASE}/{code}{ch_str}.pdf"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
        reader = PdfReader(io.BytesIO(raw))
        pages_text = []
        for page in reader.pages[:6]:  # first 6 pages of the chapter
            t = page.extract_text() or ""
            if t.strip():
                pages_text.append(t.strip())
        text = "\n".join(pages_text)[:max_chars]
        _ncert_cache[cache_key] = text
        print(f"[ncert_rag] fetched NCERT G{grade} {subject} ch{chapter_num} ({len(text)} chars)")
        return text
    except Exception as e:
        print(f"[ncert_rag] could not fetch {url}: {e}")
        _ncert_cache[cache_key] = ""
        return ""


def get_ncert_context_for_grading(grade: int, subject: str, chapter: str,
                                   query: str = "") -> str:
    """Get NCERT book content for grading context.
    First tries live fetch from ncert.nic.in, falls back to local RAG."""
    # Try to extract chapter number from chapter string
    ch_match = re.search(r"\d+", chapter or "")
    if ch_match:
        ch_num = int(ch_match.group())
        live_text = fetch_ncert_chapter_text(grade, subject, ch_num)
        if live_text:
            return (f"NCERT Grade {grade} {subject} Chapter {ch_num} "
                    f"(official textbook content):\n{live_text}")
    # Fallback to local RAG
    return rag_retrieve(query or chapter or subject, grade, subject, top_k=3)

_HERE = os.path.dirname(os.path.abspath(__file__))
_DB_PATH = os.path.join(_HERE, "data", "ncert_chroma")
_COLLECTION_NAME = "ncert_chapters_v1"

_client = None
_collection = None


def _get_collection():
    """Return (and lazily build) the persistent Chroma collection."""
    global _client, _collection
    if _collection is not None:
        return _collection

    import chromadb
    from chromadb.config import Settings
    os.makedirs(_DB_PATH, exist_ok=True)
    _client = chromadb.PersistentClient(
        path=_DB_PATH,
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    try:
        _collection = _client.get_collection(_COLLECTION_NAME)
        print(f"[ncert_rag] loaded existing collection ({_collection.count()} chapters)")
    except Exception:
        print(f"[ncert_rag] building collection from cbse_kb…")
        _collection = _client.create_collection(_COLLECTION_NAME)
        _ingest_all_chapters(_collection)
        print(f"[ncert_rag] ingested {_collection.count()} chapters into Chroma")
    return _collection


def _ingest_all_chapters(coll) -> None:
    """Walk cbse_kb.CBSE_KB and add every chapter to the collection."""
    from cbse_kb import CBSE_KB
    ids, docs, metas = [], [], []
    for grade_key, subjects in CBSE_KB.items():
        for subj, chapters in subjects.items():
            for i, ch in enumerate(chapters):
                ch_id = ch.get("ch", f"ch{i}")
                title = ch.get("title", "")
                concepts = ch.get("concepts", "")
                unit = ch.get("unit", "")
                stream = ch.get("stream", "")
                # Build a richer doc string for embedding
                doc = f"{title}. {concepts}"
                if unit: doc += f" [unit: {unit}]"
                if stream: doc += f" [stream: {stream}]"
                uid = f"{grade_key}|{subj}|{ch_id}|{i}"
                ids.append(uid)
                docs.append(doc)
                metas.append({
                    "grade": grade_key, "subject": subj, "ch": ch_id,
                    "title": title, "concepts": concepts,
                    "unit": unit or "", "stream": stream or "",
                })
    # Chroma's default embedder will be used (sentence-transformers/all-MiniLM-L6-v2)
    BATCH = 500
    for i in range(0, len(ids), BATCH):
        coll.add(ids=ids[i:i+BATCH], documents=docs[i:i+BATCH], metadatas=metas[i:i+BATCH])


def rag_retrieve(query: str, grade_level: int, subject: str = "",
                  top_k: int = 3) -> str:
    """Semantic search over all NCERT chapters. Filters by grade (and subject
    if given). Returns a formatted context block, or "" if nothing matches."""
    if not query or not query.strip():
        return ""
    try:
        coll = _get_collection()
    except Exception as e:
        print(f"[ncert_rag] collection init failed: {e}")
        return ""

    grade_key = f"Grade {grade_level}"
    where: dict[str, Any] = {"grade": grade_key}
    if subject:
        where = {"$and": [{"grade": grade_key}, {"subject": subject}]}

    try:
        res = coll.query(query_texts=[query], n_results=top_k, where=where)
    except Exception as e:
        print(f"[ncert_rag] query failed: {e}")
        return ""

    metas = (res.get("metadatas") or [[]])[0]
    if not metas:
        # Fallback: query without subject filter
        try:
            res = coll.query(query_texts=[query], n_results=top_k,
                            where={"grade": grade_key})
            metas = (res.get("metadatas") or [[]])[0]
        except Exception:
            metas = []
    if not metas:
        return ""

    lines = [f"OFFICIAL CBSE {grade_key} CURRICULUM CONTEXT (align grading to this):"]
    for m in metas:
        unit = m.get("unit") or m.get("stream") or ""
        unit_str = f" [{unit}]" if unit else ""
        concepts = m.get("concepts", "")
        lines.append(
            f"- {m.get('subject','')} · {m.get('ch','')}: {m.get('title','')}{unit_str}"
            + (f" — {concepts}" if concepts else "")
        )
    return "\n".join(lines)
