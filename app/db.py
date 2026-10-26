import sqlite3
from pathlib import Path

_DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"


def init_db(
    db_path: str | Path = "bot.db",
    schema_path: str | Path = _DEFAULT_SCHEMA,
) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
    return conn


connect = init_db
