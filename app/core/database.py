from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from app.config.settings import get_settings

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS principals (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL CHECK (type IN ('anonymous', 'user')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS anonymous_sessions (
        id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL,
        session_key TEXT NOT NULL UNIQUE,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        user_agent TEXT,
        ip_hash TEXT,
        FOREIGN KEY (principal_id) REFERENCES principals(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        principal_id TEXT NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (principal_id) REFERENCES principals(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plans (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        principal_id TEXT NOT NULL,
        city TEXT,
        days INTEGER,
        structured_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id),
        FOREIGN KEY (principal_id) REFERENCES principals(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS places (
        place_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        city TEXT,
        city_key TEXT,
        district TEXT,
        ward TEXT,
        address TEXT,
        description TEXT,
        detail_content TEXT,
        list_snippet TEXT,
        source TEXT,
        source_category_code TEXT,
        destination_type TEXT,
        item_id TEXT,
        detail_url TEXT,
        website TEXT,
        phone TEXT,
        planner_role TEXT,
        primary_area_key TEXT,
        admin_area_keys_json TEXT,
        intent_tags_json TEXT,
        density_bucket TEXT,
        verification_status TEXT,
        lat DOUBLE PRECISION,
        lon DOUBLE PRECISION,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS place_chunks (
        doc_id TEXT PRIMARY KEY,
        place_id TEXT NOT NULL,
        title TEXT,
        city TEXT,
        category TEXT,
        chunk_index INTEGER NOT NULL,
        document_text TEXT NOT NULL,
        metadata_json TEXT,
        embedding_json TEXT,
        embedding_model TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (place_id) REFERENCES places(place_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_anonymous_sessions_principal_id ON anonymous_sessions(principal_id)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_principal_id ON conversations(principal_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_plans_conversation_id ON plans(conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_plans_principal_id ON plans(principal_id)",
    "CREATE INDEX IF NOT EXISTS idx_places_category ON places(category)",
    "CREATE INDEX IF NOT EXISTS idx_places_city_key ON places(city_key)",
    "CREATE INDEX IF NOT EXISTS idx_places_primary_area_key ON places(primary_area_key)",
    "CREATE INDEX IF NOT EXISTS idx_places_source ON places(source)",
    "CREATE INDEX IF NOT EXISTS idx_place_chunks_place_id ON place_chunks(place_id)",
    "CREATE INDEX IF NOT EXISTS idx_place_chunks_category ON place_chunks(category)",
)


def _database_name_from_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    db_name = parsed.path.lstrip("/").strip()
    return db_name or "postgres"


def _replace_database_in_url(database_url: str, database_name: str) -> str:
    parsed = urlparse(database_url)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def _bootstrap_database_if_missing(database_url: str) -> None:
    target_db = _database_name_from_url(database_url)
    if target_db in {"postgres", "template1"}:
        return

    bootstrap_errors: list[Exception] = []
    for maintenance_db in ("postgres", "template1"):
        admin_url = _replace_database_in_url(database_url, maintenance_db)
        try:
            admin_connection = psycopg2.connect(admin_url)
            admin_connection.autocommit = True
            try:
                with admin_connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM pg_database WHERE datname = %s",
                        (target_db,),
                    )
                    if cursor.fetchone():
                        return
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db))
                    )
                    print(f"✅ Created PostgreSQL database '{target_db}'")
                    return
            finally:
                admin_connection.close()
        except Exception as exc:
            bootstrap_errors.append(exc)

    if bootstrap_errors:
        raise bootstrap_errors[-1]


def get_connection() -> psycopg2.extensions.connection:
    settings = get_settings()
    try:
        return psycopg2.connect(
            settings.database_url,
            cursor_factory=RealDictCursor,
        )
    except psycopg2.OperationalError as exc:
        if 'database "' not in str(exc) or "does not exist" not in str(exc):
            raise
        _bootstrap_database_if_missing(settings.database_url)
        return psycopg2.connect(
            settings.database_url,
            cursor_factory=RealDictCursor,
        )


@contextmanager
def get_cursor(*, commit: bool = False) -> Iterator[RealDictCursor]:
    connection = get_connection()
    try:
        cursor = connection.cursor()
        try:
            yield cursor
            if commit:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
    finally:
        connection.close()


def init_db() -> None:
    try:
        with get_cursor(commit=True) as cursor:
            for statement in _SCHEMA_STATEMENTS:
                cursor.execute(statement)
        print("✅ PostgreSQL database initialized successfully")
    except Exception as exc:
        print(f"❌ Database initialization failed: {exc}")
        raise
