from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.place_metadata import enrich_place_record, fold_text
from app.services.vector_rag import build_chunk_documents_with_config, retrieve_place_candidates

UNIFIED_JSON = ROOT / "data" / "processed" / "unified_places.json"
BENCHMARK_CONFIGS = [
    {"label": "80/16", "size": 80, "overlap": 16},
    {"label": "96/20", "size": 96, "overlap": 20},
    {"label": "120/24", "size": 120, "overlap": 24},
]


@dataclass(frozen=True)
class BenchmarkCase:
    query: str
    expected_title: str
    source_kind: str = "destinations"


BENCHMARK_CASES = [
    BenchmarkCase(
        query="bao tang luu giu bo suu tap dieu khac cua van hoa Cham tai Da Nang",
        expected_title="Bảo tàng điêu khắc Chămpa",
    ),
    BenchmarkCase(
        query="noi trung bay chu quyen bien dao Hoang Sa o Da Nang",
        expected_title="Nhà trưng bày Hoàng Sa",
    ),
    BenchmarkCase(
        query="bao tang ve cuoc doi va su nghiep cua Chu tich Ho Chi Minh tai Da Nang",
        expected_title="Bảo tàng Hồ Chí Minh",
    ),
    BenchmarkCase(
        query="bao tang my thuat trung bay tac pham nghe thuat cua Da Nang",
        expected_title="Bảo tàng Mỹ Thuật Đà Nẵng",
    ),
    BenchmarkCase(
        query="bao tang phat giao trung bay co vat va van hoa Phat giao",
        expected_title="Bảo tàng văn hóa Phật giáo",
    ),
    BenchmarkCase(
        query="cum danh thang nui da voi hang dong va chua noi tieng o Da Nang",
        expected_title="Ngũ Hành Sơn",
    ),
    BenchmarkCase(
        query="chua co tuong Quan Am lon tren ban dao Son Tra",
        expected_title="Chùa Linh Ứng Sơn Trà",
    ),
    BenchmarkCase(
        query="khu bao ton thien nhien voi rung va dong vat o Son Tra",
        expected_title="Khu bảo tồn thiên nhiên Sơn Trà",
    ),
    BenchmarkCase(
        query="pho co voi den long va kien truc co o Quang Nam",
        expected_title="Khu phố cổ Hội An",
    ),
    BenchmarkCase(
        query="di san den thap Champa duoc unesco cong nhan o Quang Nam",
        expected_title="Di sản văn hóa Mỹ Sơn",
    ),
    BenchmarkCase(
        query="dao gan Hoi An noi co bien dep va lan ngam san ho",
        expected_title="Cù lao Chàm",
    ),
]


def main() -> None:
    places = _load_places()
    place_lookup = {
        str(place.get("place_id") or "").strip(): place
        for place in places
        if str(place.get("place_id") or "").strip()
    }
    print(f"Loaded {len(places)} places for chunk benchmark")
    print(f"Benchmark cases: {len(BENCHMARK_CASES)}")
    print("")

    summaries: list[dict] = []
    for config in BENCHMARK_CONFIGS:
        label = str(config["label"])
        size = int(config["size"])
        overlap = int(config["overlap"])
        print(f"=== Config {label} ===")
        docs = build_chunk_documents_with_config(
            places,
            max_words=size,
            overlap_words=overlap,
        )
        print(f"Built {len(docs)} chunks")
        details = _run_benchmark(docs=docs, place_lookup=place_lookup)
        summary = _summarize(details, chunk_count=len(docs), place_count=len(places))
        summaries.append({"label": label, **summary})
        print(
            f"MRR={summary['mrr']:.3f} | Hit@3={summary['hit_at_3']:.3f} | "
            f"Hit@5={summary['hit_at_5']:.3f} | Hit@10={summary['hit_at_10']:.3f} | "
            f"MeanChunksPerPlace={summary['chunks_per_place']:.2f}"
        )
        misses = [item for item in details if item["rank"] is None]
        if misses:
            print("Misses:")
            for miss in misses[:5]:
                print(f"- {miss['expected_title']} | query={miss['query']}")
        print("")

    ranked = sorted(
        summaries,
        key=lambda item: (
            float(item["mrr"]),
            float(item["hit_at_5"]),
            float(item["hit_at_10"]),
        ),
        reverse=True,
    )
    best = ranked[0]
    print("=== Recommendation ===")
    print(
        f"Best config: {best['label']} "
        f"(MRR={best['mrr']:.3f}, Hit@5={best['hit_at_5']:.3f}, Hit@10={best['hit_at_10']:.3f})"
    )


def _run_benchmark(docs: list[dict], place_lookup: dict[str, dict]) -> list[dict]:
    results: list[dict] = []
    for case in BENCHMARK_CASES:
        rows = retrieve_place_candidates(
            query=case.query,
            source_kind=case.source_kind,
            top_k=10,
            docs=docs,
            place_lookup=place_lookup,
        )
        normalized_expected = fold_text(case.expected_title)
        rank = None
        top_names = []
        for index, row in enumerate(rows, start=1):
            name = str(row.get("name") or "").strip()
            if name:
                top_names.append(name)
            if fold_text(name) == normalized_expected:
                rank = index
                break
        results.append(
            {
                "query": case.query,
                "expected_title": case.expected_title,
                "rank": rank,
                "top_names": top_names[:5],
            }
        )
    return results


def _summarize(results: list[dict], *, chunk_count: int, place_count: int) -> dict:
    reciprocal_ranks = [1.0 / item["rank"] for item in results if item["rank"]]
    hit_at_3 = sum(1 for item in results if item["rank"] and item["rank"] <= 3) / max(len(results), 1)
    hit_at_5 = sum(1 for item in results if item["rank"] and item["rank"] <= 5) / max(len(results), 1)
    hit_at_10 = sum(1 for item in results if item["rank"] and item["rank"] <= 10) / max(len(results), 1)
    return {
        "mrr": sum(reciprocal_ranks) / max(len(results), 1),
        "hit_at_3": hit_at_3,
        "hit_at_5": hit_at_5,
        "hit_at_10": hit_at_10,
        "chunks_per_place": chunk_count / max(place_count, 1),
    }


def _load_places() -> list[dict]:
    payload = json.loads(UNIFIED_JSON.read_text(encoding="utf-8"))
    return [enrich_place_record(item) for item in payload if isinstance(item, dict)]


if __name__ == "__main__":
    main()
