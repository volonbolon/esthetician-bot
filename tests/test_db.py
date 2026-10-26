from pathlib import Path

from app.db import init_db

EXPECTED_TABLES = {
    "clients": {"phone", "name", "vip", "muted_until", "awaiting_name", "created_at"},
    "appointments": {"event_id", "phone", "start_at"},
    "reminders": {"event_id", "send_at", "sent_at"},
    "processed_messages": {"wamid", "received_at"},
    "vip_alerts": {"phone", "last_alert_at"},
}


def test_init_db_wal_and_schema(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = init_db(db_file)
    try:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"

        for table, expected_cols in EXPECTED_TABLES.items():
            rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
            cols = {r["name"] for r in rows}
            assert cols == expected_cols
            pks = {r["name"] for r in rows if r["pk"] == 1}
            assert len(pks) == 1

        # Idempotent re-init on existing DB
        conn2 = init_db(db_file)
        conn2.close()
    finally:
        conn.close()
