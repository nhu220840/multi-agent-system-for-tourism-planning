from __future__ import annotations

import sys
from pathlib import Path

from elasticsearch import Elasticsearch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings
from app.core.database import init_db
from app.services.place_repository import list_places

INDEX_NAME = "danang_places"


def main() -> None:
    init_db()
    documents = list_places()
    if not documents:
        raise RuntimeError(
            "No places found in PostgreSQL. Run scripts/preprocess.py before syncing Elasticsearch."
        )
    es = Elasticsearch(get_settings().elasticsearch_url)
    create_index(es)
    ingest_documents(es, documents)


def create_index(es: Elasticsearch) -> None:
    if es.indices.exists(index=INDEX_NAME):
        return
    es.indices.create(
        index=INDEX_NAME,
        mappings={
            "properties": {
                "name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "category": {"type": "keyword"},
                "description": {"type": "text"},
                "detail_content": {"type": "text"},
                "list_snippet": {"type": "text"},
                "address": {"type": "text"},
                "city": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "city_key": {"type": "keyword"},
                "district": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "primary_area_key": {"type": "keyword"},
                "admin_area_keys": {"type": "keyword"},
                "intent_tags": {"type": "keyword"},
                "planner_role": {"type": "keyword"},
                "density_bucket": {"type": "keyword"},
                "verification_status": {"type": "keyword"},
                "source": {"type": "keyword"},
                "source_category_code": {"type": "keyword"},
                "destination_type": {"type": "text"},
                "item_id": {"type": "keyword"},
                "detail_url": {"type": "keyword"},
                "website": {"type": "keyword"},
                "phone": {"type": "keyword"},
                "lat": {"type": "float"},
                "lon": {"type": "float"},
            }
        },
    )
    print("Created index:", INDEX_NAME)


def ingest_documents(es: Elasticsearch, data: list[dict]) -> None:
    for i, doc in enumerate(data):
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("place_id") or i)
        es.index(index=INDEX_NAME, id=doc_id, document=doc)

    print(f"Synced {len(data)} PostgreSQL places into Elasticsearch index {INDEX_NAME}")


if __name__ == "__main__":
    main()
