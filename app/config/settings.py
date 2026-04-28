from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "dev"
    frontend_origin: str = "http://localhost:3000"
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_places_index: str = "travel_places"
    elasticsearch_place_chunks_index: str = "travel_place_chunks"
    elasticsearch_sync_enabled: bool = False
    elasticsearch_verify_certs: bool = False
    elasticsearch_request_timeout_s: int = 15
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
    openrouter_request_timeout_s: int = 25
    openweather_api_key: str = ""
    openweather_base_url: str = "https://api.openweathermap.org"
    trackasia_api_key: str = ""
    trackasia_enabled: bool = True
    trackasia_geocode_enabled: bool = True
    trackasia_routing_enabled: bool = True
    trackasia_directions_base_url: str = "https://maps.track-asia.com/route/v2/directions"
    trackasia_request_timeout_s: int = 8
    trackasia_new_admin: bool = True
    trackasia_cache_ttl_s: int = 900
    trackasia_rate_limit_window_s: int = 60
    trackasia_rate_limit_max_calls: int = 60
    trackasia_route_modes: str = "car"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/travel"
    session_cookie_name: str = "app_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_cookie_max_age_seconds: int = 60 * 60 * 24 * 30

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
