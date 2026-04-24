from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings
from app.core.database import init_db
from app.services.elasticsearch_sync import (
    elasticsearch_available,
    elasticsearch_configured,
    elasticsearch_status,
    sync_travel_indices,
)


def main() -> None:
    init_db()
    settings = get_settings()
    if not elasticsearch_configured():
        raise RuntimeError(
            "Missing Elasticsearch configuration. Set ELASTICSEARCH_URL before running sync."
        )
    if not elasticsearch_available():
        raise RuntimeError(
            "Elasticsearch client unavailable. Install dependencies from requirements.txt first."
        )
    status = elasticsearch_status()
    if not status.ok:
        raise RuntimeError(status.message)

    counts = sync_travel_indices(recreate=True)
    print(
        "Synced PostgreSQL travel catalog to Elasticsearch "
        f"at {settings.elasticsearch_url}: "
        f"{counts['places_indexed']} places, "
        f"{counts['place_chunks_indexed']} place chunks."
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"Elasticsearch sync failed: {exc}")
        raise SystemExit(1)
