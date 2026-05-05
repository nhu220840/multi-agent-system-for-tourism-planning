"""Wipe cached conversations + messages from the backing store.

Useful after changing the response format: since each assistant answer is
persisted verbatim in the database, reloading the chat UI keeps showing the
previously-stored format. Running this script deletes all conversations +
messages so the next chat request is rendered with the current formatter.

The script uses the same database URL as the running app (resolved via
``app.config.settings``), so it works for both the Postgres-in-Docker setup
(``DATABASE_URL=postgresql://...``) and any other backend the app is wired
to.

Usage::

    python scripts/clear_conversations.py                # delete everything
    python scripts/clear_conversations.py --dry-run      # only print counts

Inside the Docker api container you can run::

    docker-compose -f docker/docker-compose.yml exec api \\
        python scripts/clear_conversations.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `app` importable when running directly: ``python scripts/foo.py``.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _counts(cursor) -> tuple[int, int]:
    cursor.execute("SELECT COUNT(*) AS c FROM conversations")
    conv = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) AS c FROM messages")
    msg = cursor.fetchone()
    return (
        int(conv["c"] if isinstance(conv, dict) else conv[0]),
        int(msg["c"] if isinstance(msg, dict) else msg[0]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print counts without deleting.")
    args = parser.parse_args()

    from app.core.database import get_cursor

    with get_cursor(commit=not args.dry_run) as cursor:
        try:
            conv_count, msg_count = _counts(cursor)
        except Exception as exc:
            print(f"❌ Could not read counts (database likely not initialised yet): {exc}")
            return

        print(f"Found {conv_count} conversations and {msg_count} messages.")

        if args.dry_run:
            print("[dry-run] Not deleting anything.")
            return

        cursor.execute("DELETE FROM messages")
        cursor.execute("DELETE FROM conversations")
        print("✓ Cleared conversations and messages.")


if __name__ == "__main__":
    main()
