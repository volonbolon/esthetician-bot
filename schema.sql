CREATE TABLE IF NOT EXISTS clients (
    phone TEXT PRIMARY KEY,
    name TEXT,
    vip INTEGER NOT NULL DEFAULT 0,
    muted_until TEXT,
    awaiting_name INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    event_id TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    start_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    event_id TEXT PRIMARY KEY,
    send_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS processed_messages (
    wamid TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vip_alerts (
    phone TEXT PRIMARY KEY,
    last_alert_at TEXT NOT NULL
);
