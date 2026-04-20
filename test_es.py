from elasticsearch import Elasticsearch
from app.config.settings import get_settings
from app.tools.elasticsearch_tool import search_places

es = Elasticsearch(get_settings().elasticsearch_url)

res = es.search(
    index="danang_places",
    query={
        "match": {
            "name": "biển"
        }
    }
)

print(f"Raw hits: {len(res['hits']['hits'])}")

# test search restaurant
print("=== RESTAURANT ===")
print(search_places("hải sản", category="restaurant"))

# test search hotel
print("\n=== HOTEL ===")
print(search_places("biển", category="accommodation"))