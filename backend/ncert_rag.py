"""Local NCERT RAG using Chroma + sentence-transformers.

On first call, ingests every chapter from cbse_kb into a persistent local
Chroma collection. All subsequent calls reuse the index — no network, no API.

`rag_retrieve(query, grade_level, subject)` returns a formatted context block
similar to the legacy `retrieve_context` from cbse_kb, but uses embeddings so
"Photosynthesis" matches "Life Processes" via semantic similarity.
"""
from __future__ import annotations

import os
from typing import Any

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
