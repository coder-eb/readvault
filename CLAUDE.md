# readvault — AI quick reference

Personal reading-data hoarder for Ebran Bright. Goodreads is the system of
record; this repo scrapes it into plain JSONL and syncs it to a private
HuggingFace dataset. See `README.md` for full setup/usage — this file is a
terse pointer for AI sessions.

**This repo must remain usable without AI.** Don't build features that only
work through a Claude session — every capability should be a runnable script.

## First step on a new device / fresh session
```bash
python scripts/pull_data.py    # pull data/ from HuggingFace
```

## Data available
| File | Contents |
|---|---|
| `data/books.jsonl` | book_id -> title, authors, isbn, num_pages, avg_rating, genres, publication_info |
| `data/shelf_entries.jsonl` | review_id -> book_id, shelf, date_added, date_read |
| `data/reading_timeline.jsonl` | review_id, date, event (raw progress events) |

## Common commands
```bash
python scripts/ingest_goodreads.py                # sync latest shelf/book data
python scripts/ingest_goodreads.py --refresh-books # re-fetch book metadata (ratings/genres can drift)
python scripts/push_data.py                        # push data/ to HuggingFace
python scripts/stats.py --year 2026                # avg pages, genre breakdown, etc. (local only, no network)
```

## Answering ad-hoc reading questions
`processors/books.py` has `read_jsonl`, `load_books_cache`,
`books_finished_in_range`, and `summarize` — reuse these instead of
re-deriving JSONL parsing logic. For anything `stats.py` doesn't already
compute, write a small one-off script against `data/*.jsonl` rather than
loading everything into a notebook-style exploration each time.

## Project layout
```
connectors/   Goodreads scraping primitives (session auth via browser cookie
              db, shelf RSS/HTML, reading timelines, book-page metadata)
processors/   JSONL I/O + normalization + stats (books.py)
scripts/      CLI entry points: ingest_goodreads, stats, push_data, pull_data
config/       settings.yaml — goodreads.user_id, goodreads.cookies_db,
              goodreads.shelves, hf_dataset_repo
data/         gitignored JSONL, synced via HuggingFace
```

## Adding a new data source
Follow the existing pattern: `connectors/<source>.py` (pure fetch functions,
no file I/O) -> `processors/<source>.py` (normalize to JSONL records) ->
`scripts/ingest_<source>.py` (CLI wiring, writes to `data/`). See
`connectors/goodreads.py` as the template — cookie-based auth, RSS-first for
speed, cache expensive per-item fetches (book metadata) by id.

## Known constraints
- Goodreads has no public API for new developers (closed 2020) — everything
  here is HTML/RSS scraping of the authenticated user's own account.
- `goodreads.cookies_db` points at a specific browser profile's cookie sqlite
  file. If auth starts failing, check the cookie DB path is still current
  (browser profile changes, reinstalls) rather than assuming Goodreads changed
  its markup.
- Book detail scraping depends on Goodreads' embedded schema.org JSON-LD
  block (`numberOfPages`, `isbn`, `author`, `aggregateRating`) with a
  `data-testid="pagesFormat"` fallback. If `ingest_goodreads.py` stops finding
  page counts, check whether that JSON-LD block moved/changed rather than
  rewriting the CSS selectors first.
