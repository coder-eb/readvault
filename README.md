# readvault

Personal reading-data hoarder. Goodreads is the system of record for reading
activity (shelves, dates, progress); this repo scrapes it, normalizes it into
plain JSONL, and stores it durably in a private HuggingFace dataset — so the
data outlives any single AI tool, browser, or laptop.

**Design goal: this repo should be fully usable with plain Python, no AI
required.** Every script is a normal CLI entry point with `--help`-able
arguments. AI (Claude Code or otherwise) is a convenience layer on top for
answering ad-hoc questions about the data — not a dependency for collecting it.

## Why scrape instead of using the Goodreads API

Goodreads closed its public API to new developers in 2020. Scraping the
authenticated shelf pages + public book pages is the only way to get shelf
history, reading timelines, and page counts as an individual user today.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in HF_TOKEN
```

Edit `config/settings.yaml`:
- `goodreads.user_id` — your Goodreads numeric-id-and-slug (from your profile URL)
- `goodreads.cookies_db` — path to your browser's cookie sqlite db (used to read
  session cookies live, in memory only — never written to disk by this repo)
- `hf_dataset_repo` — `your-username/reading-data` (create as a **private**
  dataset at https://huggingface.co/new-dataset)

On a new device, pull existing data first:
```bash
python scripts/pull_data.py
```

## Syncing your Goodreads data

```bash
python scripts/ingest_goodreads.py                # incremental sync, all configured shelves
python scripts/ingest_goodreads.py --shelf read    # just one shelf
python scripts/ingest_goodreads.py --refresh-books # re-fetch metadata for every book (ratings/genres drift over time)
python scripts/push_data.py                        # sync data/ to HuggingFace
```

Run `ingest_goodreads.py` then `push_data.py` after any Goodreads update you
want reflected in the dataset. Book metadata (page count, author, genres) is
cached by `book_id` in `data/books.jsonl` and only re-fetched with
`--refresh-books`, since it rarely changes and re-scraping every book on every
run is slow and unnecessary.

## Answering questions about your reading

```bash
python scripts/stats.py                       # this calendar year
python scripts/stats.py --year 2025
python scripts/stats.py --from 2026-01-01 --to 2026-06-30
```

Gives: books finished, total/average pages, shortest/longest book, top genres.
This runs entirely against local `data/*.jsonl` — no network calls.

For anything not covered by `stats.py`, the JSONL files are plain and meant to
be queried directly (`jq`, `pandas`, a one-off script, or handed to an AI
model as context) — see [Data layout](#data-layout).

## Data layout

```
data/                       gitignored, synced via HuggingFace
  books.jsonl                one row per book_id: title, authors, isbn,
                              num_pages, avg_rating, genres, publication_info
  shelf_entries.jsonl        one row per review: book_id, shelf, date_added,
                              date_read
  reading_timeline.jsonl     one row per (review_id, date, event): raw
                              progress events scraped from each book's
                              "reading timeline" (shelved / started / % / finished)
```

## Project layout

```
connectors/    Goodreads scraping primitives (session auth, shelf/RSS/timeline/book-page parsing)
processors/    normalization + stats (JSONL I/O, date-range filtering, summarize())
scripts/       CLI entry points (ingest, stats, push/pull to HuggingFace)
config/        settings.yaml (Goodreads user id, cookie db path, HF repo id)
data/          gitignored JSONL, synced via HuggingFace (see above)
```

## Adding new data sources

The pattern is: a `connectors/<source>.py` module with pure fetch functions, a
`processors/<source>.py` module that normalizes raw scrapes into JSONL
records, and a `scripts/ingest_<source>.py` CLI that wires them together and
writes to `data/`. Follow `connectors/goodreads.py` /
`processors/books.py` / `scripts/ingest_goodreads.py` as the template.
