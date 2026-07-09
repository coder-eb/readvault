"""
Sync Goodreads shelf + book data into data/*.jsonl.

Usage:
  python scripts/ingest_goodreads.py                # incremental (skip cached book metadata)
  python scripts/ingest_goodreads.py --refresh-books # re-fetch metadata for every book seen
  python scripts/ingest_goodreads.py --shelf read    # only sync one shelf

What it does, per configured shelf (config/settings.yaml: goodreads.shelves):
  1. Pull the public RSS feed for read_at / date_added (fast, no auth).
  2. Pull the authenticated HTML shelf table for review_id + book_url per title.
  3. For each review: fetch its reading-timeline events (start/%/finish dates).
  4. For each book_id not already cached (or if --refresh-books): fetch the
     book page for page count, author, isbn, rating, genres.

Writes:
  data/books.jsonl            book_id -> metadata (cache, append/overwrite by book_id)
  data/shelf_entries.jsonl    one row per review: shelf, date_added, date_read
  data/reading_timeline.jsonl one row per (review_id, date, event)
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from playwright.sync_api import sync_playwright

from connectors import goodreads
from processors.books import load_books_cache, read_jsonl, write_jsonl

DATA_DIR = Path(__file__).parent.parent / "data"


def load_settings():
    with open(Path(__file__).parent.parent / "config" / "settings.yaml") as f:
        return yaml.safe_load(f) or {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shelf", action="append", help="Only sync this shelf (repeatable)")
    parser.add_argument(
        "--refresh-books", action="store_true", help="Re-fetch metadata for every book, not just new ones"
    )
    args = parser.parse_args()

    settings = load_settings()
    gr = settings.get("goodreads", {})
    user_id = gr.get("user_id")
    cookies_db = gr.get("cookies_db")
    shelves = args.shelf or gr.get("shelves", ["read", "currently-reading", "to-read", "did-not-finish"])

    if not user_id or not cookies_db:
        print("ERROR: set goodreads.user_id and goodreads.cookies_db in config/settings.yaml")
        sys.exit(1)

    # Public endpoints (RSS, book pages) -- plain requests, no WAF challenge.
    session = goodreads.load_session(cookies_db)

    books_path = DATA_DIR / "books.jsonl"
    entries_path = DATA_DIR / "shelf_entries.jsonl"
    timeline_path = DATA_DIR / "reading_timeline.jsonl"

    books_cache = load_books_cache(books_path)
    all_entries = {e["review_id"]: e for e in read_jsonl(entries_path)}
    all_timeline = read_jsonl(timeline_path)
    # dedupe timeline by (review_id, date, event) since re-runs re-fetch it
    timeline_seen = {(t["review_id"], t["date"], t["event"]) for t in all_timeline}

    # Authenticated endpoints (shelf table, reading timeline) -- these sit
    # behind Goodreads' AWS WAF JS challenge, so a real browser is required.
    with sync_playwright() as p:
        browser, context = goodreads.load_browser_context(p, cookies_db)
        timeline_page = context.new_page()

        for shelf in shelves:
            print(f"\n== {shelf} ==")
            rss_items = {it["title"]: it for it in goodreads.get_shelf_rss(user_id, shelf)}
            reviews = goodreads.get_shelf_reviews(context, user_id, shelf)
            print(f"  {len(reviews)} entries")

            for r in reviews:
                rss = rss_items.get(r["title"], {})
                all_entries[r["review_id"]] = {
                    "review_id": r["review_id"],
                    "book_id": r["book_id"],
                    "title": r["title"],
                    "book_url": r["book_url"],
                    "shelf": shelf,
                    "date_added": rss.get("date_added"),
                    "date_read": rss.get("read_at"),
                }

                # Reading timeline (progress events)
                try:
                    events = goodreads.get_reading_timeline(timeline_page, r["review_id"])
                except Exception as e:
                    print(f"    WARN: failed to fetch reading timeline for {r['title']}: {e}")
                    events = []
                for d, desc in events:
                    key = (r["review_id"], str(d), desc)
                    if key not in timeline_seen:
                        timeline_seen.add(key)
                        all_timeline.append({"review_id": r["review_id"], "date": str(d), "event": desc})
                time.sleep(goodreads.REQUEST_DELAY)

                # Book metadata (cached by book_id) -- public page, plain requests
                if r["book_id"] and r["book_url"] and (args.refresh_books or r["book_id"] not in books_cache):
                    try:
                        details = goodreads.get_book_details(session, r["book_url"])
                        books_cache[r["book_id"]] = details
                        print(f"    fetched book metadata: {details.get('title')} ({details.get('num_pages')} pages)")
                    except Exception as e:
                        print(f"    WARN: failed to fetch book details for {r['title']}: {e}")
                    time.sleep(goodreads.REQUEST_DELAY)

            # Save after each shelf so a crash partway through a long run
            # (network hiccup, WAF hiccup) doesn't lose everything fetched so far.
            write_jsonl(books_path, list(books_cache.values()))
            write_jsonl(entries_path, list(all_entries.values()))
            write_jsonl(timeline_path, all_timeline)

        browser.close()

    print(f"\nSaved: {len(books_cache)} books, {len(all_entries)} shelf entries, {len(all_timeline)} timeline events")


if __name__ == "__main__":
    main()
