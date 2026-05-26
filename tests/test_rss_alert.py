import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3

import rss_alert


def test_passes_filters_include_and_exclude():
    entry = {"title": "Levná letenka na Bali", "summary": "super", "description": ""}
    assert rss_alert.passes_filters(entry, ["Bali"], []) == (True, "passed")
    assert rss_alert.passes_filters(entry, ["Maledivy"], []) == (False, "failed_include_filter")
    assert rss_alert.passes_filters(entry, [], ["Bali"])[0] is False


def test_feed_filters_global_and_overrides():
    global_filters = {"include_keywords": ["A"], "exclude_keywords": ["X"]}
    include, exclude = rss_alert.feed_filters({"url": "u"}, global_filters)
    assert include == ["A"] and exclude == ["X"]

    include, exclude = rss_alert.feed_filters({"url": "u", "keywords": ["B"]}, global_filters)
    assert include == ["B"] and exclude == ["X"]

    include, exclude = rss_alert.feed_filters({"url": "u", "include_keywords": ["C"], "exclude_keywords": ["Y"]}, global_filters)
    assert include == ["C"] and exclude == ["Y"]


def test_process_feeds_dry_run_and_ignore_seen(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE seen_entries(feed_url TEXT, entry_id TEXT, seen_at INTEGER, PRIMARY KEY(feed_url, entry_id))")
    feed_url = "https://example.com/feed"
    entries = [{"id": "1", "title": "Ahoj Bali", "link": "https://x"}]

    class Parsed:
        def __init__(self, entries):
            self.entries = entries

    monkeypatch.setattr(rss_alert.feedparser, "parse", lambda _url: Parsed(entries))
    sent = []
    monkeypatch.setattr(rss_alert, "send_telegram_message", lambda *args, **kwargs: sent.append(1))

    config = {"feeds": [{"url": feed_url}], "filters": {"include_keywords": [], "exclude_keywords": []}}
    stats = rss_alert.process_feeds(config, conn, "t", "c", dry_run=True, ignore_seen=False)
    assert stats["sent"] == 0
    assert conn.execute("SELECT COUNT(*) FROM seen_entries").fetchone()[0] == 0

    stats = rss_alert.process_feeds(config, conn, "t", "c", dry_run=False, ignore_seen=True)
    assert stats["sent"] == 1
    assert conn.execute("SELECT COUNT(*) FROM seen_entries").fetchone()[0] == 0
