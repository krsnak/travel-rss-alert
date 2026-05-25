import os
import sqlite3
import time
from pathlib import Path

import feedparser
import requests
import yaml


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def ensure_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_entries (
            feed_url TEXT NOT NULL,
            entry_id TEXT NOT NULL,
            seen_at INTEGER NOT NULL,
            PRIMARY KEY (feed_url, entry_id)
        )
        """
    )
    conn.commit()
    return conn


def get_entry_id(entry: dict) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def is_seen(conn: sqlite3.Connection, feed_url: str, entry_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_entries WHERE feed_url = ? AND entry_id = ?",
        (feed_url, entry_id),
    ).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, feed_url: str, entry_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen_entries(feed_url, entry_id, seen_at) VALUES(?, ?, strftime('%s','now'))",
        (feed_url, entry_id),
    )
    conn.commit()


def matches_filters(entry: dict, filters: list[str]) -> bool:
    if not filters:
        return True
    haystack = " ".join(
        [entry.get("title", ""), entry.get("summary", ""), entry.get("description", "")]
    ).lower()
    return any(keyword.lower() in haystack for keyword in filters)


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
    resp.raise_for_status()


def process_feeds(config: dict, conn: sqlite3.Connection, token: str, chat_id: str) -> None:
    feeds = config.get("feeds", [])
    for feed_cfg in feeds:
        feed_url = feed_cfg.get("url")
        if not feed_url:
            continue

        parsed = feedparser.parse(feed_url)
        filters = feed_cfg.get("keywords", [])
        max_items = int(feed_cfg.get("max_items", 20))

        for entry in parsed.entries[:max_items]:
            entry_id = get_entry_id(entry)
            if not entry_id:
                continue
            if is_seen(conn, feed_url, entry_id):
                continue
            if not matches_filters(entry, filters):
                mark_seen(conn, feed_url, entry_id)
                continue

            title = entry.get("title", "(no title)")
            link = entry.get("link", "")
            message = f"{title}\n{link}".strip()
            send_telegram_message(token, chat_id, message)
            mark_seen(conn, feed_url, entry_id)


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    config_path = os.environ.get("CONFIG_PATH", "config.yml")
    db_path = os.environ.get("DB_PATH", "seen.db")

    run_once = os.environ.get("RUN_ONCE", "").lower() == "true"
    poll_seconds = int(os.environ.get("POLL_SECONDS", "600"))

    config = load_config(config_path)
    conn = ensure_db(db_path)

    if run_once:
        process_feeds(config, conn, token, chat_id)
        return

    while True:
        process_feeds(config, conn, token, chat_id)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
