import os
import sqlite3
import time
import logging
from pathlib import Path

import feedparser
import requests
import yaml


def setup_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
    )


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


def matches_filters(entry: dict, filters: list[str]) -> tuple[bool, str]:
    if not filters:
        return True, "passed"
    haystack = " ".join(
        [entry.get("title", ""), entry.get("summary", ""), entry.get("description", "")]
    ).lower()
    for keyword in filters:
        if keyword.lower() in haystack:
            return True, "passed"
    return False, "failed_include_filter"


def passes_filters(entry: dict, include_keywords: list[str], exclude_keywords: list[str]) -> tuple[bool, str]:
    include_passed, include_reason = matches_filters(entry, include_keywords)
    if not include_passed:
        return False, include_reason

    if not exclude_keywords:
        return True, "passed"

    haystack = " ".join(
        [entry.get("title", ""), entry.get("summary", ""), entry.get("description", "")]
    ).lower()
    for keyword in exclude_keywords:
        if keyword.lower() in haystack:
            return False, f"matched_exclude_keyword:{keyword}"
    return True, "passed"


def has_any_seen_entries(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM seen_entries LIMIT 1").fetchone()
    return row is not None


def seed_existing_entries(config: dict, conn: sqlite3.Connection) -> int:
    seeded_count = 0
    feeds = config.get("feeds", [])
    for feed_cfg in feeds:
        feed_url = feed_cfg.get("url")
        if not feed_url:
            continue
        parsed = feedparser.parse(feed_url)
        max_items = int(feed_cfg.get("max_items", 20))
        entries = parsed.entries[:max_items]
        for entry in entries:
            entry_id = get_entry_id(entry)
            if not entry_id:
                continue
            if is_seen(conn, feed_url, entry_id):
                continue
            mark_seen(conn, feed_url, entry_id)
            seeded_count += 1
    return seeded_count


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
        include_keywords = feed_cfg.get("keywords", [])
        exclude_keywords = feed_cfg.get("exclude_keywords", [])
        max_items = int(feed_cfg.get("max_items", 20))
        entries = parsed.entries[:max_items]
        logging.info("Processing feed url=%s entries_returned=%d", feed_url, len(entries))

        for entry in entries:
            entry_id = get_entry_id(entry)
            if not entry_id:
                continue
            if is_seen(conn, feed_url, entry_id):
                continue
            title = entry.get("title", "(no title)")
            passed_filters, filter_reason = passes_filters(entry, include_keywords, exclude_keywords)
            logging.info(
                "New item title=%r passed_filters=%s reason=%s",
                title,
                passed_filters,
                filter_reason,
            )

            if not passed_filters:
                mark_seen(conn, feed_url, entry_id)
                continue

            link = entry.get("link", "")
            message = f"{title}\n{link}".strip()
            send_telegram_message(token, chat_id, message)
            mark_seen(conn, feed_url, entry_id)


def main() -> None:
    setup_logging()

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    config_path = os.environ.get("CONFIG_PATH", "config.yml")
    db_path = os.environ.get("DB_PATH", "seen.db")

    run_once = os.environ.get("RUN_ONCE", "").lower() == "true"
    test_telegram = os.environ.get("TEST_TELEGRAM", "").lower() == "true"
    seed_on_first_run = os.environ.get("SEED_ON_FIRST_RUN", "false").lower() == "true"
    poll_seconds = int(os.environ.get("POLL_SECONDS", "600"))

    config = load_config(config_path)
    conn = ensure_db(db_path)
    feeds_count = len(config.get("feeds", []))
    logging.info(
        "Startup config RUN_ONCE=%s TEST_TELEGRAM=%s feeds=%d DB_PATH=%s",
        run_once,
        test_telegram,
        feeds_count,
        db_path,
    )

    if seed_on_first_run and not has_any_seen_entries(conn):
        seeded_count = seed_existing_entries(config, conn)
        logging.info("SEED_ON_FIRST_RUN enabled on empty DB, seeded_items=%d", seeded_count)
        return

    if test_telegram:
        send_telegram_message(token, chat_id, "✅ travel-rss-alert test OK")
        logging.info("Test Telegram message sent")
        return

    if run_once:
        process_feeds(config, conn, token, chat_id)
        return

    while True:
        process_feeds(config, conn, token, chat_id)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
