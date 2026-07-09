"""
Normalize raw Goodreads scrapes into JSONL records and compute reading stats.

Three record types live in data/:
  books.jsonl          one row per book_id -- metadata cache (pages, author, ...)
  shelf_entries.jsonl   one row per (review_id) -- which shelf, when added/read
  reading_timeline.jsonl  one row per (review_id, date, event) -- raw progress events
"""
import json
from collections import Counter
from datetime import date
from pathlib import Path


def read_jsonl(path: Path):
    if not Path(path).exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


def load_books_cache(path: Path) -> dict:
    """book_id -> book record"""
    return {b["book_id"]: b for b in read_jsonl(path) if b.get("book_id")}


def books_finished_in_range(
    shelf_entries: list, books_by_id: dict, start: date, end: date
) -> list:
    """Shelf entries on the 'read' shelf with date_read inside [start, end]."""
    out = []
    for entry in shelf_entries:
        if entry.get("shelf") != "read":
            continue
        read_at = entry.get("date_read")
        if not read_at:
            continue
        d = date.fromisoformat(read_at) if isinstance(read_at, str) else read_at
        if start <= d <= end:
            book = books_by_id.get(entry.get("book_id"))
            if book:
                out.append({**entry, "book": book})
    return out


def summarize(finished: list) -> dict:
    """Compute headline stats for a list of {..., book} entries from books_finished_in_range."""
    page_counts = [f["book"]["num_pages"] for f in finished if f["book"].get("num_pages")]
    genre_counter = Counter()
    for f in finished:
        for g in f["book"].get("genres", [])[:3]:  # top few genres per book, avoid noise
            genre_counter[g] += 1

    return {
        "books_finished": len(finished),
        "books_with_page_data": len(page_counts),
        "total_pages": sum(page_counts),
        "avg_pages": round(sum(page_counts) / len(page_counts), 1) if page_counts else None,
        "shortest": min(page_counts) if page_counts else None,
        "longest": max(page_counts) if page_counts else None,
        "top_genres": genre_counter.most_common(10),
    }
