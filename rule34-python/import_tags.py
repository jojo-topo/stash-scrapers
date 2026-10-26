#!/usr/bin/env python3
"""
One-time import of the rule34 tag types into rule34-tags.db (sqlite).

Downloads the tags 1000 per page through the API (request budget shared with the
scraper: 50 requests / minute) and stores them in the database read by
rule34-python.py. Resumable: continues from the last finished page.

Usage (run it from the scraper folder, after filling api_key / user_id in
rule34-config.json):
    python import_tags.py
Ctrl+C to interrupt, run it again later to resume. Takes about 30-40 minutes.
"""

import importlib.util
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from html import unescape

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 1000
MAX_PID = 5000  # safety limit (rule34 has roughly 1 to 2 million tags)


def p(msg):
    print(msg, flush=True)


def load_scraper():
    spec = importlib.util.spec_from_file_location("r34", os.path.join(HERE, "rule34-python.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.config_load()
    return mod


def parse_page(raw):
    text = raw.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
        return [(t.get("name"), int(t.get("type", 0))) for t in root.iter("tag") if t.get("name")]
    except Exception:
        # invalid XML (control characters...): regex fallback
        return [(unescape(n), int(t)) for t, n in re.findall(r'<tag [^>]*?type="(\d+)"[^>]*?name="([^"]*)"', text)]


def main():
    r34 = load_scraper()
    auth = r34.api_auth()
    if not auth:
        p(f"api_key / user_id are missing: fill both fields in {r34.CONFIG_FILE} and run this again.")
        return 1
    db = r34.tag_cache_load()  # TagDB (creates the database, migrates the old JSON cache)
    conn, lock = db._db, db._lock

    with lock:
        row = conn.execute("SELECT value FROM meta WHERE key='import_next_pid'").fetchone()
    pid = int(row[0]) if row else 0
    with lock:
        total = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    p(f"Resuming at page {pid} ({total} tags already in the database)")

    started = time.time()
    pages_done = 0
    while pid < MAX_PID:
        url = f"{r34.API_BASE}&s=tag&limit={PAGE_SIZE}&pid={pid}{auth}"
        raw = None
        for attempt in range(5):
            raw = r34._fast_get(url)
            if raw:
                break
            p(f"Page {pid}: failed (attempt {attempt + 1}/5), retrying in 30 s")
            time.sleep(30)
        if not raw:
            p(f"Page {pid}: giving up after 5 attempts. Run the script again to resume here.")
            return 2

        tags = parse_page(raw)
        if not tags:
            p(f"Page {pid} is empty: import finished.")
            with lock:
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('import_complete', '1')")
                conn.commit()
            break

        with lock:
            conn.executemany("INSERT OR REPLACE INTO tags VALUES (?, ?)", tags)
            pid += 1
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('import_next_pid', ?)", (str(pid),))
            conn.commit()
        pages_done += 1
        if pages_done % 10 == 0:
            with lock:
                total = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
            elapsed = time.time() - started
            p(f"Page {pid} - {total} tags in the database - {elapsed / 60:.1f} min elapsed")

    with lock:
        total = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    p(f"Done: {total} tags in the database.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        p("Interrupted. Run the script again to resume.")
        sys.exit(130)
