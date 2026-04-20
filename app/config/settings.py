from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "dev"
    rag_chunk_size_words: int = 120
    rag_chunk_overlap_words: int = 24
    rag_context_chunks: int = 6
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_batch_size: int = 32
    places_resolver_enabled: bool = False
    places_resolver_override_coordinates: bool = False
    places_resolver_base_url: str = "https://nominatim.openstreetmap.org"
    places_resolver_request_timeout_s: int = 10
    places_resolver_country_codes: str = "vn"
    places_resolver_user_agent: str = "multi-agent-travel/0.1 (free-places-resolver)"
    google_maps_api_key: str = ""
    google_places_base_url: str = "https://places.googleapis.com"
    google_places_enrich_enabled: bool = False
    google_places_override_coordinates: bool = False
    google_places_language_code: str = "vi"
    google_places_region_code: str = "VN"
    google_places_request_timeout_s: int = 10
    google_places_follow_moved_limit: int = 2
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-oss-120b:free"
    openrouter_reasoning_enabled: bool = True
    openweather_api_key: str = ""
    openweather_base_url: str = "https://api.openweathermap.org"
    mytomtom_api_key: str = ""
    mytomtom_base_url: str = "https://api.tomtom.com"
    elasticsearch_url: str = "http://localhost:9200"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/travel"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
