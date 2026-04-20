from __future__ import annotations

import json
import sys
from pathlib import Path

from elasticsearch import Elasticsearch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings

INDEX_NAME = "danang_places"
UNIFIED_JSON = ROOT / "data" / "processed" / "unified_places.json"


def main() -> None:
    if not UNIFIED_JSON.exists():
        raise FileNotFoundError(
            f"Missing {UNIFIED_JSON}. Run scripts/preprocess.py before ingesting."
        )
    es = Elasticsearch(get_settings().elasticsearch_url)
    create_index(es)
    ingest_file(es, UNIFIED_JSON)


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


def ingest_file(es: Elasticsearch, filepath: Path) -> None:
    data = json.loads(filepath.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a list of documents in {filepath}")

    for i, doc in enumerate(data):
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("place_id") or i)
        es.index(index=INDEX_NAME, id=doc_id, document=doc)

    print(f"Ingested {len(data)} docs from {filepath}")


if __name__ == "__main__":
    main()
