from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "processed" / "unified_places.json"


def main() -> None:
    if not CATALOG.exists():
        print(f"Missing catalog: {CATALOG}")
        return

    items = json.loads(CATALOG.read_text(encoding="utf-8"))
    total = len(items)
    with_coords = [item for item in items if isinstance(item.get("lat"), (int, float)) and isinstance(item.get("lon"), (int, float))]
    unresolved = [item for item in items if not (isinstance(item.get("lat"), (int, float)) and isinstance(item.get("lon"), (int, float)))]

    print(f"catalog: {CATALOG}")
    print(f"total_places: {total}")
    print(f"with_coords: {len(with_coords)}")
    print(f"unresolved: {len(unresolved)}")

    source_counter = Counter(str(item.get("coordinate_source") or "missing") for item in items)
    confidence_counter = Counter(str(item.get("coordinate_confidence") or "missing") for item in items)

    print("\ncoordinate_source:")
    for key, count in source_counter.most_common():
        print(f"  - {key}: {count}")

    print("\ncoordinate_confidence:")
    for key, count in confidence_counter.most_common():
        print(f"  - {key}: {count}")

    if unresolved:
        print("\nunresolved_examples:")
        for item in unresolved[:30]:
            print(
                {
                    "name": item.get("name"),
                    "category": item.get("category"),
                    "city": item.get("city"),
                    "address": item.get("address"),
                    "source": item.get("source"),
                    "primary_area_key": item.get("primary_area_key"),
                }
            )


if __name__ == "__main__":
    main()
