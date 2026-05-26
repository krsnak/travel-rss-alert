import logging
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

import feedparser
import requests
import yaml


def setup_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


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


def matches_filters(entry: dict, filters: list[str]) -> tuple[bool, str]:
    if not filters:
        return True, "passed"
    haystack = normalize_text(" ".join([entry.get("title", ""), entry.get("summary", ""), entry.get("description", "")]))
    for keyword in filters:
        if keyword_matches_text(keyword, haystack):
            return True, f"matched_include_keyword:{keyword}"
    return False, "failed_include_filter"


def normalize_text(text: str) -> str:
    lowered = text.lower()
    no_diacritics = "".join(ch for ch in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(ch))
    return " ".join(no_diacritics.split())


def keyword_matches_text(keyword: str, normalized_text: str) -> bool:
    normalized_keyword = normalize_text(keyword)
    if not normalized_keyword:
        return False
    if normalized_keyword.endswith("o") and len(normalized_keyword) > 1:
        normalized_keyword = normalized_keyword[:-1]
    pattern = rf"\b{re.escape(normalized_keyword)}\w*\b"
    return re.search(pattern, normalized_text) is not None


def passes_filters(entry: dict, include_keywords: list[str], exclude_keywords: list[str]) -> tuple[bool, str]:
    include_passed, include_reason = matches_filters(entry, include_keywords)
    if not include_passed:
        return False, include_reason

    if not exclude_keywords:
        return True, "passed"

    haystack = normalize_text(" ".join([entry.get("title", ""), entry.get("summary", ""), entry.get("description", "")]))
    for keyword in exclude_keywords:
        if keyword_matches_text(keyword, haystack):
            return False, f"matched_exclude_keyword:{keyword}"
    return True, "passed"


def has_any_seen_entries(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM seen_entries LIMIT 1").fetchone()
    return row is not None


def feed_filters(feed_cfg: dict, global_filters: dict) -> tuple[list[str], list[str]]:
    global_include = global_filters.get("include_keywords", [])
    global_exclude = global_filters.get("exclude_keywords", [])
    include = feed_cfg.get("include_keywords")
    if include is None:
        include = feed_cfg.get("keywords")
    if include is None:
        include = global_include
    exclude = feed_cfg.get("exclude_keywords", global_exclude)
    return include or [], exclude or []


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
            if not entry_id or is_seen(conn, feed_url, entry_id):
                continue
            mark_seen(conn, feed_url, entry_id)
            seeded_count += 1
    conn.commit()
    return seeded_count


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
    resp.raise_for_status()


def process_feeds(config: dict, conn: sqlite3.Connection, token: str, chat_id: str, dry_run: bool = False, ignore_seen: bool = False) -> dict:
    stats = {
        "feeds": 0,
        "entries": 0,
        "new": 0,
        "seen": 0,
        "filtered": 0,
        "sent": 0,
        "marked_seen": 0,
        "errors": 0,
        "empty": 0,
    }
    feeds = config.get("feeds", [])
    global_filters = config.get("filters", {})
    logging.info("Loaded feeds=%d global_filters=%s dry_run=%s ignore_seen=%s", len(feeds), bool(global_filters), dry_run, ignore_seen)
    for feed_cfg in feeds:
        feed_url = feed_cfg.get("url")
        if not feed_url:
            continue
        stats["feeds"] += 1
        try:
            parsed = feedparser.parse(feed_url)
            include_keywords, exclude_keywords = feed_filters(feed_cfg, global_filters)
            max_items = int(feed_cfg.get("max_items", 20))
            entries = parsed.entries[:max_items]
            stats["entries"] += len(entries)
            if len(entries) == 0:
                stats["empty"] += 1
            logging.info("Feed url=%s entries_returned=%d include=%s exclude=%s", feed_url, len(entries), include_keywords, exclude_keywords)

            feed_seen = feed_new = feed_filtered = feed_sent = 0
            for entry in entries:
                entry_id = get_entry_id(entry)
                if not entry_id:
                    continue
                if (not ignore_seen) and is_seen(conn, feed_url, entry_id):
                    stats["seen"] += 1
                    feed_seen += 1
                    continue

                stats["new"] += 1
                feed_new += 1
                title = entry.get("title", "(no title)")
                passed_filters, filter_reason = passes_filters(entry, include_keywords, exclude_keywords)
                logging.info("Item title=%r passed_filters=%s reason=%s", title, passed_filters, filter_reason)

                if not passed_filters:
                    stats["filtered"] += 1
                    feed_filtered += 1
                    if not dry_run and not ignore_seen:
                        mark_seen(conn, feed_url, entry_id)
                        stats["marked_seen"] += 1
                    continue

                link = entry.get("link", "")
                message = f"{title}\n{link}".strip()
                if dry_run:
                    logging.info("DRY_RUN would send message title=%r", title)
                else:
                    try:
                        send_telegram_message(token, chat_id, message)
                        stats["sent"] += 1
                        feed_sent += 1
                    except Exception:
                        stats["errors"] += 1
                        logging.exception("Telegram send failed feed=%s title=%r", feed_url, title)
                        continue

                if not dry_run and not ignore_seen:
                    mark_seen(conn, feed_url, entry_id)
                    stats["marked_seen"] += 1

            logging.info("Feed result url=%s seen=%d new=%d filtered=%d sent=%d", feed_url, feed_seen, feed_new, feed_filtered, feed_sent)
        except Exception:
            stats["errors"] += 1
            logging.exception("Feed processing failed url=%s", feed_url)

    if not dry_run and not ignore_seen:
        conn.commit()
    return stats


def main() -> None:
    setup_logging()

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    config_path = os.environ.get("CONFIG_PATH", "config.yml")
    db_path = os.environ.get("DB_PATH", "seen.db")

    run_once = env_bool("RUN_ONCE", True)
    test_telegram = env_bool("TEST_TELEGRAM", False)
    seed_on_first_run = env_bool("SEED_ON_FIRST_RUN", False)
    dry_run = env_bool("DRY_RUN", False)
    ignore_seen = env_bool("IGNORE_SEEN", False)
    poll_seconds = int(os.environ.get("POLL_SECONDS", "600"))

    config = load_config(config_path)
    conn = ensure_db(db_path)
    feeds_count = len(config.get("feeds", []))
    logging.info("Startup config RUN_ONCE=%s TEST_TELEGRAM=%s DRY_RUN=%s IGNORE_SEEN=%s SEED_ON_FIRST_RUN=%s feeds=%d DB_PATH=%s", run_once, test_telegram, dry_run, ignore_seen, seed_on_first_run, feeds_count, db_path)

    if seed_on_first_run and not has_any_seen_entries(conn):
        seeded_count = seed_existing_entries(config, conn)
        logging.info("SEED_ON_FIRST_RUN enabled on empty DB, seeded_items=%d", seeded_count)
        return

    if test_telegram:
        send_telegram_message(token, chat_id, "✅ travel-rss-alert test OK")
        logging.info("Test Telegram message sent")
        return

    if run_once:
        stats = process_feeds(config, conn, token, chat_id, dry_run=dry_run, ignore_seen=ignore_seen)
        logging.info("Summary: feeds=%d entries=%d new=%d seen=%d filtered=%d sent=%d marked_seen=%d empty=%d errors=%d", stats["feeds"], stats["entries"], stats["new"], stats["seen"], stats["filtered"], stats["sent"], stats["marked_seen"], stats["empty"], stats["errors"])
        return

    while True:
        stats = process_feeds(config, conn, token, chat_id, dry_run=dry_run, ignore_seen=ignore_seen)
        logging.info("Summary: feeds=%d entries=%d new=%d seen=%d filtered=%d sent=%d marked_seen=%d empty=%d errors=%d", stats["feeds"], stats["entries"], stats["new"], stats["seen"], stats["filtered"], stats["sent"], stats["marked_seen"], stats["empty"], stats["errors"])
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
