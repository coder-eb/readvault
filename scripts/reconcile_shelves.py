"""
Reconcile local shelf membership with Goodreads, across all configured shelves.

Unlike resync_active.py (which only looks at date-filtered changes), this lists
every shelf and diffs it against data/shelf_entries.jsonl:

  - On a shelf locally and remotely: kept (dates refreshed from RSS).
  - Remote shelf differs from local (book moved): shelf updated, book re-fetched.
  - On Goodreads but not local: added (entry, review page data, book metadata).
  - Local but on no shelf on Goodreads: removed, along with its review/timeline.

Usage:
  python scripts/reconcile_shelves.py            # apply changes
  python scripts/reconcile_shelves.py --dry-run  # show the diff only
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
DEFAULT_SHELVES = ["read", "currently-reading", "to-read", "did-not-finish"]


def load_settings():
    with open(Path(__file__).parent.parent / "config" / "settings.yaml") as f:
        return yaml.safe_load(f) or {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show the diff without writing anything")
    args = parser.parse_args()

    gr = load_settings().get("goodreads", {})
    user_id = gr.get("user_id")
    cookies_db = gr.get("cookies_db")
    shelves = gr.get("shelves", DEFAULT_SHELVES)
    if not user_id or not cookies_db:
        print("ERROR: set goodreads.user_id and goodreads.cookies_db in config/settings.yaml")
        sys.exit(1)

    books_path = DATA_DIR / "books.jsonl"
    entries_path = DATA_DIR / "shelf_entries.jsonl"
    timeline_path = DATA_DIR / "reading_timeline.jsonl"
    reviews_path = DATA_DIR / "reviews.jsonl"

    books_cache = load_books_cache(books_path)
    all_entries = {e["review_id"]: e for e in read_jsonl(entries_path)}
    all_timeline = read_jsonl(timeline_path)
    timeline_seen = {(t["review_id"], t["date"], t["event"]) for t in all_timeline}
    all_reviews = {r["review_id"]: r for r in read_jsonl(reviews_path)}

    with sync_playwright() as p:
        browser, context = goodreads.load_browser_context(p, cookies_db)

        remote = {}  # review_id -> {"shelf", "review", "rss"}
        for shelf in shelves:
            rss_by_title = {it["title"]: it for it in goodreads.get_shelf_rss(user_id, shelf)}
            listing = goodreads.get_shelf_reviews(context, user_id, shelf)
            print(f"{shelf}: {len(listing)} on Goodreads", flush=True)
            if not listing and shelf in ("read", "to-read"):
                print(f"ERROR: {shelf} listing came back empty -- refusing to prune. Check auth/WAF.")
                sys.exit(1)
            for r in listing:
                remote[r["review_id"]] = {"shelf": shelf, "review": r, "rss": rss_by_title.get(r["title"], {})}

        added = remote.keys() - all_entries.keys()
        moved = {rid for rid in remote.keys() & all_entries.keys() if all_entries[rid]["shelf"] != remote[rid]["shelf"]}
        removed = all_entries.keys() - remote.keys()
        kept = remote.keys() & all_entries.keys() - moved

        print(f"\nlocal: {len(all_entries)} | Goodreads: {len(remote)}")
        print(f"  kept: {len(kept)}  added: {len(added)}  moved: {len(moved)}  removed: {len(removed)}\n")
        for rid in sorted(added, key=lambda r: remote[r]["review"]["title"]):
            print(f"  + {remote[rid]['review']['title']}  ({remote[rid]['shelf']})")
        for rid in moved:
            print(f"  > {all_entries[rid]['title']}  ({all_entries[rid]['shelf']} -> {remote[rid]['shelf']})")
        for rid in removed:
            print(f"  - {all_entries[rid]['title']}  (was {all_entries[rid]['shelf']})")

        if args.dry_run:
            print("\nDry run -- nothing written.")
            browser.close()
            return

        for rid in kept:
            rss = remote[rid]["rss"]
            if rss.get("date_added"):
                all_entries[rid]["date_added"] = rss["date_added"]
            if rss.get("read_at"):
                all_entries[rid]["date_read"] = rss["read_at"]

        removed_book_ids = {all_entries[rid]["book_id"] for rid in removed}
        for rid in removed:
            del all_entries[rid]
            all_reviews.pop(rid, None)
        all_timeline = [t for t in all_timeline if t["review_id"] not in removed]

        timeline_page = context.new_page()
        book_page = context.new_page()
        targets = sorted(added | moved, key=lambda r: remote[r]["review"]["title"])
        for i, rid in enumerate(targets, start=1):
            r, rss, shelf = remote[rid]["review"], remote[rid]["rss"], remote[rid]["shelf"]
            print(f"\n[{i}/{len(targets)}] ({shelf}) {r['title']}", flush=True)
            all_entries[rid] = {
                "review_id": rid,
                "book_id": r["book_id"],
                "title": r["title"],
                "book_url": r["book_url"],
                "shelf": shelf,
                "date_added": rss.get("date_added"),
                "date_read": rss.get("read_at"),
            }
            try:
                page_data = goodreads.get_review_page_data(timeline_page, rid)
            except Exception as e:
                print(f"  WARN: failed to fetch review page: {e}", flush=True)
                page_data = None

            if page_data is not None:
                new_events = 0
                for d, desc in page_data["events"]:
                    key = (rid, str(d), desc)
                    if key not in timeline_seen:
                        timeline_seen.add(key)
                        all_timeline.append({"review_id": rid, "date": str(d), "event": desc})
                        new_events += 1
                all_reviews[rid] = {
                    "review_id": rid,
                    "book_id": r["book_id"],
                    "title": r["title"],
                    "rating": page_data["rating"],
                    "rating_text": page_data["rating_text"],
                    "review_text": page_data["review_text"],
                }
                print(f"  {new_events} new timeline events", flush=True)
            else:
                # Keep any previously stored review rather than overwriting it with nulls.
                all_reviews.setdefault(rid, {
                    "review_id": rid,
                    "book_id": r["book_id"],
                    "title": r["title"],
                    "rating": None,
                    "rating_text": None,
                    "review_text": None,
                })
            time.sleep(goodreads.REQUEST_DELAY)

            if r["book_id"] and r["book_url"] and r["book_id"] not in books_cache:
                try:
                    details = goodreads.get_book_details(book_page, r["book_url"])
                    books_cache[r["book_id"]] = details
                    print(f"  fetched book metadata: {details.get('title')} ({details.get('num_pages')} pages)", flush=True)
                except Exception as e:
                    print(f"  WARN: failed to fetch book details: {e}", flush=True)
                time.sleep(goodreads.REQUEST_DELAY)

        browser.close()

    # Drop cached metadata only for removed books that no other entry references.
    live_book_ids = {e["book_id"] for e in all_entries.values()}
    for bid in removed_book_ids - live_book_ids:
        books_cache.pop(bid, None)

    write_jsonl(books_path, list(books_cache.values()))
    write_jsonl(entries_path, list(all_entries.values()))
    write_jsonl(timeline_path, all_timeline)
    write_jsonl(reviews_path, list(all_reviews.values()))
    print(f"\nSaved: {len(all_entries)} shelf entries, {len(books_cache)} books cached.")


if __name__ == "__main__":
    main()
