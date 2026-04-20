from app.services.place_metadata import enrich_place_record
from app.services.vector_rag import build_chunk_documents, chunk_text, place_document_text


def test_chunk_text_splits_long_documents_with_overlap() -> None:
    text = " ".join(f"token{i}" for i in range(180))
    chunks = chunk_text(text, max_words=60, overlap_words=10)

    assert len(chunks) >= 3
    assert all(len(chunk.split()) <= 60 for chunk in chunks)
    assert chunks[0].split()[-10:] == chunks[1].split()[:10]


def test_build_chunk_documents_preserves_place_metadata() -> None:
    place = enrich_place_record(
        {
            "name": "Bao tang Cham",
            "category": "destination",
            "city": "Đà Nẵng",
            "district": "Hải Châu",
            "address": "2 2 Thang 9, Hai Chau, Da Nang",
            "description": "Không gian trưng bày nghệ thuật Chăm với nhiều hiện vật lịch sử. " * 20,
            "source": "local-processed:dest_danang.json",
        }
    )

    docs = build_chunk_documents([place])

    assert docs
    assert all(doc["place_id"] == place["place_id"] for doc in docs)
    assert docs[0]["title"] == "Bao tang Cham"
    assert all("metadata" in doc for doc in docs)


def test_place_document_text_contains_planning_fields() -> None:
    place = enrich_place_record(
        {
            "name": "My Khe Beach",
            "category": "destination",
            "city": "Đà Nẵng",
            "district": "Sơn Trà",
            "address": "Vo Nguyen Giap, Da Nang",
            "description": "Bai bien noi tieng phu hop tam bien va ngắm bình minh.",
            "source": "local-processed:dest_danang.json",
        }
    )

    text = place_document_text(place)

    assert "My Khe Beach" in text
    assert "Vai tro lap lich" in text
    assert "Dia chi" in text
