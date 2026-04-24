from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config.settings import get_settings
from app.services.place_repository import list_place_chunks, list_places

_PLACES_CATEGORY_MAP = {
    "destinations": ["destination"],
    "entertainment": ["entertainment"],
    "restaurants": ["restaurant"],
    "accommodations": ["accommodation"],
}


@dataclass(frozen=True)
class ElasticsearchStatus:
    ok: bool
    message: str = ""


def elasticsearch_configured() -> bool:
    settings = get_settings()
    return bool(str(settings.elasticsearch_url or "").strip())


def elasticsearch_available() -> bool:
    if not elasticsearch_configured():
        return False
    try:
        from elasticsearch import Elasticsearch  # noqa: F401
    except Exception:
        return False
    return True


def elasticsearch_status() -> ElasticsearchStatus:
    settings = get_settings()
    url = str(settings.elasticsearch_url or "").strip() or "http://localhost:9200"
    if not elasticsearch_configured():
        return ElasticsearchStatus(ok=False, message="ELASTICSEARCH_URL is empty.")
    if not elasticsearch_available():
        return ElasticsearchStatus(
            ok=False,
            message="Python Elasticsearch client is not installed.",
        )
    client = get_elasticsearch_client()
    if client is None:
        return ElasticsearchStatus(ok=False, message="Elasticsearch client could not be created.")
    try:
        client.ping()
    except Exception as exc:
        return ElasticsearchStatus(
            ok=False,
            message=f"Cannot connect to Elasticsearch at {url}: {exc}",
        )
    return ElasticsearchStatus(ok=True, message=f"Connected to Elasticsearch at {url}.")


def get_elasticsearch_client():
    if not elasticsearch_available():
        return None
    settings = get_settings()
    from elasticsearch import Elasticsearch

    return Elasticsearch(
        settings.elasticsearch_url,
        verify_certs=bool(settings.elasticsearch_verify_certs),
        request_timeout=max(1, int(settings.elasticsearch_request_timeout_s or 15)),
    )


def sync_travel_indices(*, recreate: bool = True) -> dict[str, int]:
    _require_reachable_elasticsearch()
    places_count = sync_places_index(recreate=recreate)
    chunks_count = sync_place_chunks_index(recreate=recreate)
    return {
        "places_indexed": places_count,
        "place_chunks_indexed": chunks_count,
    }


def sync_places_index(*, recreate: bool = True, places: list[dict[str, Any]] | None = None) -> int:
    client = get_elasticsearch_client()
    if client is None:
        return 0
    _require_reachable_elasticsearch(client=client)

    settings = get_settings()
    index_name = str(settings.elasticsearch_places_index or "travel_places").strip() or "travel_places"
    rows = places if places is not None else list_places()
    _ensure_index(client=client, index_name=index_name, mapping=_places_index_mapping(), recreate=recreate)

    actions = []
    for place in rows:
        doc = _serialize_place_document(place)
        place_id = str(doc.get("place_id") or "").strip()
        if not place_id:
            continue
        actions.append(
            {
                "_op_type": "index",
                "_index": index_name,
                "_id": place_id,
                "_source": doc,
            }
        )

    _bulk_index(client=client, actions=actions)
    client.indices.refresh(index=index_name)
    return len(actions)


def sync_place_chunks_index(*, recreate: bool = True, documents: list[dict[str, Any]] | None = None) -> int:
    client = get_elasticsearch_client()
    if client is None:
        return 0
    _require_reachable_elasticsearch(client=client)

    settings = get_settings()
    index_name = str(settings.elasticsearch_place_chunks_index or "travel_place_chunks").strip() or "travel_place_chunks"
    rows = documents if documents is not None else list_place_chunks()
    _ensure_index(client=client, index_name=index_name, mapping=_place_chunks_index_mapping(), recreate=recreate)

    actions = []
    for row in rows:
        doc = _serialize_place_chunk_document(row)
        doc_id = str(doc.get("doc_id") or "").strip()
        if not doc_id:
            continue
        actions.append(
            {
                "_op_type": "index",
                "_index": index_name,
                "_id": doc_id,
                "_source": doc,
            }
        )

    _bulk_index(client=client, actions=actions)
    client.indices.refresh(index=index_name)
    return len(actions)


def search_places_index(
    *,
    query: str,
    source_kind: str,
    top_k: int,
) -> list[dict[str, Any]]:
    client = get_elasticsearch_client()
    if client is None:
        return []

    settings = get_settings()
    index_name = str(settings.elasticsearch_places_index or "travel_places").strip() or "travel_places"
    try:
        index_exists = client.indices.exists(index=index_name)
    except Exception:
        return []
    if not index_exists:
        return []

    categories = _PLACES_CATEGORY_MAP.get(source_kind)
    if not categories:
        return []

    body = {
        "size": max(1, int(top_k)),
        "query": {
            "bool": {
                "filter": [
                    {
                        "terms": {
                            "category": categories,
                        }
                    }
                ],
                "must": [
                    {
                        "multi_match": {
                            "query": query,
                            "fields": [
                                "name^6",
                                "list_snippet^4",
                                "description^3",
                                "detail_content^2",
                                "address^2",
                                "district^1.5",
                                "city^1.5",
                                "planner_role^1.5",
                                "destination_type^1.5",
                                "source^0.5",
                                "intent_tags_text^2",
                                "admin_area_keys_text",
                                "map_primary_type",
                                "map_types_text",
                                "google_primary_type",
                                "google_types_text",
                            ],
                            "type": "best_fields",
                            "fuzziness": "AUTO",
                            "operator": "or",
                        }
                    }
                ],
            }
        },
    }

    try:
        response = client.search(index=index_name, body=body)
    except Exception:
        return []
    hits = response.get("hits", {}).get("hits", [])
    rows: list[dict[str, Any]] = []
    for hit in hits:
        source = hit.get("_source") or {}
        payload = source.get("payload")
        row = dict(payload) if isinstance(payload, dict) else {}
        if not row:
            continue
        score = float(hit.get("_score") or 0.0)
        row["elasticsearch_score"] = round(score, 4)
        row["retrieval_relevance"] = round(min(1.0, score / 20.0), 4)
        row["retrieval_origin"] = "elasticsearch"
        rows.append(row)
    return rows


def _ensure_index(*, client, index_name: str, mapping: dict[str, Any], recreate: bool) -> None:
    try:
        if client.indices.exists(index=index_name):
            if recreate:
                client.indices.delete(index=index_name)
            else:
                return
        client.indices.create(index=index_name, body=mapping)
    except Exception as exc:
        raise RuntimeError(f"Failed to ensure Elasticsearch index '{index_name}': {exc}") from exc


def _bulk_index(*, client, actions: list[dict[str, Any]]) -> None:
    if not actions:
        return
    from elasticsearch.helpers import bulk

    try:
        bulk(client, actions, refresh=False)
    except Exception as exc:
        raise RuntimeError(f"Failed to bulk index documents into Elasticsearch: {exc}") from exc


def _require_reachable_elasticsearch(*, client=None) -> None:
    settings = get_settings()
    url = str(settings.elasticsearch_url or "").strip() or "http://localhost:9200"
    client = client or get_elasticsearch_client()
    if client is None:
        raise RuntimeError("Elasticsearch client is unavailable.")
    try:
        reachable = bool(client.ping())
    except Exception as exc:
        raise RuntimeError(f"Cannot connect to Elasticsearch at {url}: {exc}") from exc
    if not reachable:
        raise RuntimeError(f"Cannot connect to Elasticsearch at {url}.")


def _serialize_place_document(place: dict[str, Any]) -> dict[str, Any]:
    payload = dict(place)
    return {
        "place_id": str(payload.get("place_id") or "").strip(),
        "name": str(payload.get("name") or "").strip(),
        "category": str(payload.get("category") or "").strip().lower(),
        "city": str(payload.get("city") or "").strip(),
        "city_key": str(payload.get("city_key") or "").strip().lower(),
        "district": str(payload.get("district") or "").strip(),
        "ward": str(payload.get("ward") or "").strip(),
        "address": str(payload.get("address") or "").strip(),
        "description": str(payload.get("description") or "").strip(),
        "detail_content": str(payload.get("detail_content") or "").strip(),
        "list_snippet": str(payload.get("list_snippet") or "").strip(),
        "source": str(payload.get("source") or "").strip(),
        "source_category_code": str(payload.get("source_category_code") or "").strip(),
        "destination_type": str(payload.get("destination_type") or "").strip(),
        "planner_role": str(payload.get("planner_role") or "").strip(),
        "primary_area_key": str(payload.get("primary_area_key") or "").strip().lower(),
        "admin_area_keys": [str(item).strip().lower() for item in (payload.get("admin_area_keys") or []) if str(item).strip()],
        "admin_area_keys_text": " ".join(str(item).strip().lower() for item in (payload.get("admin_area_keys") or []) if str(item).strip()),
        "intent_tags": [str(item).strip().lower() for item in (payload.get("intent_tags") or []) if str(item).strip()],
        "intent_tags_text": " ".join(str(item).strip().lower() for item in (payload.get("intent_tags") or []) if str(item).strip()),
        "density_bucket": str(payload.get("density_bucket") or "").strip(),
        "verification_status": str(payload.get("verification_status") or "").strip(),
        "map_primary_type": str(payload.get("map_primary_type") or "").strip(),
        "map_types_text": " ".join(str(item).strip() for item in (payload.get("map_types") or []) if str(item).strip()),
        "google_primary_type": str(payload.get("google_primary_type") or "").strip(),
        "google_types_text": " ".join(str(item).strip() for item in (payload.get("google_types") or []) if str(item).strip()),
        "lat": payload.get("lat"),
        "lon": payload.get("lon"),
        "payload": payload,
    }


def _serialize_place_chunk_document(document: dict[str, Any]) -> dict[str, Any]:
    row = dict(document)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "doc_id": str(row.get("doc_id") or "").strip(),
        "place_id": str(row.get("place_id") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "city": str(row.get("city") or "").strip(),
        "category": str(row.get("category") or "").strip().lower(),
        "chunk_index": int(row.get("chunk_index") or 0),
        "document_text": str(row.get("document_text") or "").strip(),
        "embedding_model": str(row.get("embedding_model") or "").strip(),
        "metadata": metadata,
    }


def _places_index_mapping() -> dict[str, Any]:
    return {
        "settings": {
            "analysis": {
                "normalizer": {
                    "lowercase_normalizer": {
                        "type": "custom",
                        "filter": ["lowercase", "asciifolding"],
                    }
                }
            }
        },
        "mappings": {
            "properties": {
                "place_id": {"type": "keyword"},
                "name": {"type": "text"},
                "category": {"type": "keyword"},
                "city": {"type": "text"},
                "city_key": {"type": "keyword"},
                "district": {"type": "text"},
                "ward": {"type": "text"},
                "address": {"type": "text"},
                "description": {"type": "text"},
                "detail_content": {"type": "text"},
                "list_snippet": {"type": "text"},
                "source": {"type": "keyword"},
                "source_category_code": {"type": "keyword"},
                "destination_type": {"type": "text"},
                "planner_role": {"type": "text"},
                "primary_area_key": {"type": "keyword"},
                "admin_area_keys": {"type": "keyword"},
                "admin_area_keys_text": {"type": "text"},
                "intent_tags": {"type": "keyword"},
                "intent_tags_text": {"type": "text"},
                "density_bucket": {"type": "keyword"},
                "verification_status": {"type": "keyword"},
                "map_primary_type": {"type": "text"},
                "map_types_text": {"type": "text"},
                "google_primary_type": {"type": "text"},
                "google_types_text": {"type": "text"},
                "lat": {"type": "float"},
                "lon": {"type": "float"},
                "payload": {"type": "object", "enabled": False},
            }
        },
    }


def _place_chunks_index_mapping() -> dict[str, Any]:
    return {
        "mappings": {
            "properties": {
                "doc_id": {"type": "keyword"},
                "place_id": {"type": "keyword"},
                "title": {"type": "text"},
                "city": {"type": "text"},
                "category": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "document_text": {"type": "text"},
                "embedding_model": {"type": "keyword"},
                "metadata": {"type": "object", "enabled": False},
            }
        }
    }
