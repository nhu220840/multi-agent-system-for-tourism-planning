from __future__ import annotations

from app.services.place_fit_scoring import add_fit_scores
from app.tools.elasticsearch_tool import search_places_scored

# So voi top_k: lay nhieu hon de xep hang truoc khi cat.
LOCAL_FETCH_MULTIPLIER = 2
# Neu top_k muc deu >= nguong nay thi khong goi OSM/Overpass.
MIN_LOCAL_FIT_TO_SKIP_EXTERNAL = 40.0


def _enrich_coords(place: dict) -> None:
    # DB-only mode: do not call external geocoding services.
    return


def fetch_ranked_local_places(query: str, category: str | None, fetch_k: int) -> list[dict]:
    raw = search_places_scored(query=query, category=category, top_k=fetch_k)
    scored = add_fit_scores(raw, query)
    scored.sort(key=lambda p: float(p.get("customer_fit_score") or 0), reverse=True)
    for p in scored[:fetch_k]:
        _enrich_coords(p)
    for p in scored:
        p.setdefault("retrieval_tier", "local_elasticsearch")
    return scored
