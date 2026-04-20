from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings
from app.graph.build_graph import run_travel_graph
from app.services.external_place_store import load_external_places
from app.services.place_metadata import enrich_place_record
from app.services.vector_rag import (
    RAG_JSON,
    UNIFIED_JSON,
    attach_embeddings,
    build_chunk_documents,
    retrieve_place_candidates,
)

RAG_JSONL = ROOT / "data" / "rag" / "rag_documents.jsonl"
DEFAULT_QUERY = (
    "Lich trinh 2 ngay o Quang Nam, tham quan Di san van hoa My Son, "
    "can khach san gan diem tham quan va quan an toi gan noi o."
)
_SOURCE_KIND_BY_CATEGORY = {
    "destination": "destinations",
    "entertainment": "entertainment",
    "restaurant": "restaurants",
    "accommodation": "accommodations",
}


def main() -> None:
    args = _parse_args()

    before_cache = load_external_places()
    before_ids = _place_ids(before_cache)
    print(f"External cache before: {len(before_cache)} places")

    print(f"Running travel graph query: {args.query}")
    response = run_travel_graph(args.query, top_k=args.top_k, with_plan=True)
    print(f"Graph trace: {', '.join(response.trace) if response.trace else '(empty)'}")
    _print_external_sources(response.sources)

    after_cache = load_external_places()
    after_ids = _place_ids(after_cache)
    new_rows = [row for row in after_cache if str(row.get("place_id") or "").strip() and str(row.get("place_id")) not in before_ids]

    print(f"External cache after: {len(after_cache)} places")
    print(f"New external places cached this run: {len(after_ids - before_ids)}")
    if new_rows:
        _print_places("New cached external places", new_rows, limit=args.verify_limit)
    else:
        print("No new external places were cached. Try a query that is more likely to trigger fallback.")

    rebuilt_docs, embedded_count = rebuild_rag_documents()
    print(f"RAG rebuild completed with {len(rebuilt_docs)} chunk documents")
    print(f"Chunks carrying embeddings: {embedded_count}/{len(rebuilt_docs)}")

    if not new_rows:
        return

    chunk_counts = _chunk_counts_for_places(rebuilt_docs, {str(row.get('place_id')) for row in new_rows})
    print("RAG corpus check for new external places:")
    for row in new_rows[: args.verify_limit]:
        place_id = str(row.get("place_id") or "").strip()
        name = str(row.get("name") or "").strip() or place_id
        print(f"- {name}: {chunk_counts.get(place_id, 0)} chunks in corpus")

    print("Vector retrieval spot-check:")
    for row in new_rows[: args.verify_limit]:
        category = str(row.get("category") or "").strip().lower()
        source_kind = _SOURCE_KIND_BY_CATEGORY.get(category)
        place_id = str(row.get("place_id") or "").strip()
        name = str(row.get("name") or "").strip()
        if not source_kind or not place_id or not name:
            continue
        hits = retrieve_place_candidates(name, source_kind=source_kind, top_k=3, docs=rebuilt_docs)
        found = any(str(hit.get("place_id") or "").strip() == place_id for hit in hits)
        labels = [str(hit.get("name") or "").strip() for hit in hits[:3]]
        status = "FOUND" if found else "MISS"
        print(f"- {name} [{category}]: {status} | top hits: {labels}")


def rebuild_rag_documents() -> tuple[list[dict[str, Any]], int]:
    places = _load_places()
    documents = build_chunk_documents(places)
    documents, _ = attach_embeddings(documents)
    RAG_JSON.parent.mkdir(parents=True, exist_ok=True)
    RAG_JSON.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    RAG_JSONL.write_text(
        "\n".join(json.dumps(doc, ensure_ascii=False) for doc in documents),
        encoding="utf-8",
    )
    try:
        from app.services.vector_rag import _load_rag_documents

        _load_rag_documents.cache_clear()
    except Exception:
        pass
    embedded_count = sum(
        1 for doc in documents if isinstance(doc.get("embedding"), list) and doc.get("embedding")
    )
    return documents, embedded_count


def _load_places() -> list[dict[str, Any]]:
    if not UNIFIED_JSON.exists():
        raise FileNotFoundError(
            f"Missing unified catalog at {UNIFIED_JSON}. Run scripts/preprocess.py first."
        )
    payload = json.loads(UNIFIED_JSON.read_text(encoding="utf-8"))
    places = [enrich_place_record(item) for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
    seen = {str(place.get("place_id") or "").strip() for place in places if str(place.get("place_id") or "").strip()}
    for item in load_external_places():
        enriched = enrich_place_record(item)
        place_id = str(enriched.get("place_id") or "").strip()
        if not place_id or place_id in seen:
            continue
        seen.add(place_id)
        places.append(enriched)
    return places


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trigger an external fallback query, rebuild RAG, and verify cached external places."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Natural-language travel query to run through the graph.")
    parser.add_argument("--top-k", type=int, default=10, help="Top-k candidates used by the graph retrieval stage.")
    parser.add_argument(
        "--verify-limit",
        type=int,
        default=5,
        help="Maximum number of newly cached external places to print and verify.",
    )
    return parser.parse_args()


def _place_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("place_id") or "").strip()
        for row in rows
        if str(row.get("place_id") or "").strip()
    }


def _chunk_counts_for_places(
    docs: list[dict[str, Any]],
    place_ids: set[str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for doc in docs:
        place_id = str(doc.get("place_id") or "").strip()
        if place_id not in place_ids:
            continue
        counts[place_id] = counts.get(place_id, 0) + 1
    return counts


def _print_places(title: str, rows: list[dict[str, Any]], *, limit: int) -> None:
    print(f"{title}:")
    for row in rows[:limit]:
        print(
            "- "
            + " | ".join(
                part
                for part in [
                    str(row.get("name") or "").strip(),
                    str(row.get("category") or "").strip(),
                    str(row.get("city") or "").strip(),
                    str(row.get("address") or "").strip(),
                ]
                if part
            )
        )


def _print_external_sources(sources: list[dict[str, Any]]) -> None:
    external = [
        source
        for source in sources
        if "nominatim-fallback" in str(source.get("source") or "")
    ]
    print(f"External sources used in response: {len(external)}")
    for source in external[:5]:
        label = " | ".join(
            part
            for part in [
                str(source.get("name") or "").strip(),
                str(source.get("category") or "").strip(),
                str(source.get("retrieval_tier") or "").strip(),
                str(source.get("source") or "").strip(),
            ]
            if part
        )
        print(f"- {label}")


if __name__ == "__main__":
    settings = get_settings()
    print(
        "Embedding config: "
        f"{settings.embedding_provider}/{settings.embedding_model}"
    )
    main()
