"""
Targeted resync: refresh only books likely to have changed since the last run,
instead of walking every shelf in full (see ingest_goodreads.py for that).

Three groups get resynced:
  1. Everything currently on the currently-reading shelf (always, in full --
     these are the books actively being read).
  2. Books on the read shelf with date_read after the last resync.
  3. Books on the did-not-finish shelf with date_added after the last resync.

Usage:
  python scripts/resync_active.py

State (last resync date) is kept in data/.cache/last_resync.json -- not
synced to HuggingFace (push_data.py already ignores .cache/*).
"""
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from playwright.sync_api import sync_playwright

from connectors import goodreads
from processors.books import load_books_cache, read_jsonl, write_jsonl

DATA_DIR = Path(__file__).parent.parent / "data"
STATE_PATH = DATA_DIR / ".cache" / "last_resync.json"
DEFAULT_LOOKBACK_DAYS = 14


def load_settings():
    with open(Path(__file__).parent.parent / "config" / "settings.yaml") as f:
        return yaml.safe_load(f) or {}


def load_last_resync() -> date:
    if STATE_PATH.exists():
        return date.fromisoformat(json.loads(STATE_PATH.read_text())["last_resync"])
    fallback = date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    print(f"No previous resync state found -- defaulting cutoff to {fallback} "
          f"({DEFAULT_LOOKBACK_DAYS} days ago).")
    return fallback


def save_last_resync():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"last_resync": date.today().isoformat()}))


def main():
    settings = load_settings()
    gr = settings.get("goodreads", {})
    user_id = gr.get("user_id")
    cookies_db = gr.get("cookies_db")
    if not user_id or not cookies_db:
        print("ERROR: set goodreads.user_id and goodreads.cookies_db in config/settings.yaml")
        sys.exit(1)

    cutoff = load_last_resync()
    print(f"Resyncing changes since {cutoff}\n")

    books_path = DATA_DIR / "books.jsonl"
    entries_path = DATA_DIR / "shelf_entries.jsonl"
    timeline_path = DATA_DIR / "reading_timeline.jsonl"
    reviews_path = DATA_DIR / "reviews.jsonl"

    books_cache = load_books_cache(books_path)
    all_entries = {e["review_id"]: e for e in read_jsonl(entries_path)}
    all_timeline = read_jsonl(timeline_path)
    timeline_seen = {(t["review_id"], t["date"], t["event"]) for t in all_timeline}
    all_reviews = {r["review_id"]: r for r in read_jsonl(reviews_path)}

    # 1. Currently-reading: full resync, no filtering.
    currently_reading_rss = {it["title"]: it for it in goodreads.get_shelf_rss(user_id, "currently-reading")}
    print(f"currently-reading: {len(currently_reading_rss)} books (full resync)")

    # 2. Read: only books finished after the cutoff.
    read_rss = goodreads.get_shelf_rss(user_id, "read")
    newly_read = {it["title"]: it for it in read_rss if it["read_at"] and it["read_at"] > cutoff}
    print(f"read: {len(newly_read)} newly finished since {cutoff}")

    # 3. Did-not-finish: only books dropped after the cutoff.
    dnf_rss = goodreads.get_shelf_rss(user_id, "did-not-finish")
    newly_dnf = {it["title"]: it for it in dnf_rss if it["date_added"] and it["date_added"] > cutoff}
    print(f"did-not-finish: {len(newly_dnf)} newly dropped since {cutoff}")

    plan = [
        ("currently-reading", currently_reading_rss, None),
        ("read", newly_read, newly_read),
        ("did-not-finish", newly_dnf, newly_dnf),
    ]

    if not any(titles for _, titles, _ in plan):
        print("\nNothing to resync.")
        save_last_resync()
        return

    with sync_playwright() as p:
        browser, context = goodreads.load_browser_context(p, cookies_db)
        timeline_page = context.new_page()
        book_page = context.new_page()

        targets = {}  # review_id -> {shelf, title, rss}
        for shelf, rss_by_title, title_filter in plan:
            if not rss_by_title:
                continue
            reviews = goodreads.get_shelf_reviews(
                context, user_id, shelf,
                title_filter=set(title_filter) if title_filter else None,
            )
            for r in reviews:
                rss = rss_by_title.get(r["title"], {})
                targets[r["review_id"]] = {"shelf": shelf, "review": r, "rss": rss}

        print(f"\n{len(targets)} books to fetch\n")

        for i, (review_id, t) in enumerate(targets.items(), start=1):
            r, rss, shelf = t["review"], t["rss"], t["shelf"]
            print(f"[{i}/{len(targets)}] ({shelf}) {r['title']}", flush=True)

            all_entries[review_id] = {
                "review_id": review_id,
                "book_id": r["book_id"],
                "title": r["title"],
                "book_url": r["book_url"],
                "shelf": shelf,
                "date_added": rss.get("date_added"),
                "date_read": rss.get("read_at"),
            }

            try:
                page_data = goodreads.get_review_page_data(timeline_page, review_id)
            except Exception as e:
                print(f"  WARN: failed to fetch review page: {e}", flush=True)
                page_data = {"events": [], "rating": None, "rating_text": None, "review_text": None}

            new_events = 0
            for d, desc in page_data["events"]:
                key = (review_id, str(d), desc)
                if key not in timeline_seen:
                    timeline_seen.add(key)
                    all_timeline.append({"review_id": review_id, "date": str(d), "event": desc})
                    new_events += 1

            all_reviews[review_id] = {
                "review_id": review_id,
                "book_id": r["book_id"],
                "title": r["title"],
                "rating": page_data["rating"],
                "rating_text": page_data["rating_text"],
                "review_text": page_data["review_text"],
            }
            print(f"  {new_events} new timeline events", flush=True)
            time.sleep(goodreads.REQUEST_DELAY)

            # Book metadata (cached by book_id) -- covers books newly seen on
            # this shelf (e.g. just finished) that ingest_goodreads.py hasn't
            # fetched yet.
            if r["book_id"] and r["book_url"] and r["book_id"] not in books_cache:
                try:
                    details = goodreads.get_book_details(book_page, r["book_url"])
                    books_cache[r["book_id"]] = details
                    print(f"  fetched book metadata: {details.get('title')} ({details.get('num_pages')} pages)", flush=True)
                except Exception as e:
                    print(f"  WARN: failed to fetch book details: {e}", flush=True)
                time.sleep(goodreads.REQUEST_DELAY)

        browser.close()

    write_jsonl(books_path, list(books_cache.values()))
    write_jsonl(entries_path, list(all_entries.values()))
    write_jsonl(timeline_path, all_timeline)
    write_jsonl(reviews_path, list(all_reviews.values()))
    save_last_resync()

    print(f"\nSaved: {len(targets)} books refreshed, {len(books_cache)} books cached, {len(all_timeline)} total timeline events.")


if __name__ == "__main__":
    main()
