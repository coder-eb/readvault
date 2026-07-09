"""
Compute reading stats from local data/*.jsonl (no network calls).

Usage:
  python scripts/stats.py                 # current calendar year
  python scripts/stats.py --year 2025
  python scripts/stats.py --from 2026-01-01 --to 2026-06-30
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from processors.books import books_finished_in_range, load_books_cache, read_jsonl, summarize

DATA_DIR = Path(__file__).parent.parent / "data"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, help="Calendar year, e.g. 2026")
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    args = parser.parse_args()

    if args.date_from and args.date_to:
        start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    else:
        year = args.year or date.today().year
        start, end = date(year, 1, 1), date(year, 12, 31)

    books_by_id = load_books_cache(DATA_DIR / "books.jsonl")
    shelf_entries = read_jsonl(DATA_DIR / "shelf_entries.jsonl")

    if not books_by_id or not shelf_entries:
        print("No data yet. Run: python scripts/ingest_goodreads.py")
        sys.exit(1)

    finished = books_finished_in_range(shelf_entries, books_by_id, start, end)
    stats = summarize(finished)

    print(f"Reading stats: {start} .. {end}\n")
    print(f"  Books finished:        {stats['books_finished']}")
    print(f"  (with page data:       {stats['books_with_page_data']})")
    print(f"  Total pages:           {stats['total_pages']}")
    print(f"  Average pages/book:    {stats['avg_pages']}")
    print(f"  Shortest / longest:    {stats['shortest']} / {stats['longest']}")
    if stats["top_genres"]:
        print("\n  Top genres:")
        for genre, count in stats["top_genres"]:
            print(f"    {genre}: {count}")


if __name__ == "__main__":
    main()
