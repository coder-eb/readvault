"""
ReadVault API — fetches live data from HuggingFace and serves it via MCP + REST.

Env vars required on Render:
  HF_TOKEN   — HuggingFace read token
  HF_REPO    — dataset repo id (default: data-eb/readvault)

Run locally:
  HF_TOKEN=xxx uvicorn mock_server:app --host 0.0.0.0 --port 8000
"""
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_REPO  = os.getenv("HF_REPO", "data-eb/readvault")

app = FastAPI(title="ReadVault", version="1.0.0")

# ── in-memory store, populated at startup ────────────────────────────────────

_books: dict = {}          # book_id -> book record
_shelves: list = []        # shelf_entries
_reviews: dict = {}        # review_id -> review record


def _read_jsonl(path):
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_from_hf():
    from huggingface_hub import hf_hub_download

    files = ["books.jsonl", "shelf_entries.jsonl", "reviews.jsonl"]
    loaded = {}
    for fname in files:
        local = hf_hub_download(
            repo_id=HF_REPO,
            repo_type="dataset",
            filename=fname,
            token=HF_TOKEN or None,
        )
        loaded[fname] = _read_jsonl(local)
    return loaded


@app.on_event("startup")
def startup():
    global _books, _shelves, _reviews
    if not HF_TOKEN:
        print("WARNING: HF_TOKEN not set — serving empty data")
        return
    try:
        data = _load_from_hf()
        _books   = {b["book_id"]: b for b in data["books.jsonl"] if b.get("book_id")}
        _shelves = data["shelf_entries.jsonl"]
        _reviews = {r["review_id"]: r for r in data["reviews.jsonl"] if r.get("review_id")}
        print(f"Loaded {len(_books)} books, {len(_shelves)} shelf entries, {len(_reviews)} reviews")
    except Exception as e:
        print(f"ERROR loading from HuggingFace: {e}")


# ── data builders ────────────────────────────────────────────────────────────

def _build_current():
    out = []
    for e in _shelves:
        if e.get("shelf") != "currently-reading":
            continue
        book = _books.get(e["book_id"], {})
        out.append({
            "title":        e.get("title") or book.get("title"),
            "author":       (book.get("authors") or ["Unknown"])[0],
            "date_started": e.get("date_added"),
            "pages":        book.get("num_pages"),
        })
    return out


def _build_recent(limit=10):
    finished = [e for e in _shelves if e.get("shelf") == "read" and e.get("date_read")]
    finished.sort(key=lambda e: e["date_read"], reverse=True)
    out = []
    for e in finished[:limit]:
        book   = _books.get(e["book_id"], {})
        review = _reviews.get(e["review_id"], {})
        rec = {
            "title":     e.get("title") or book.get("title"),
            "author":    (book.get("authors") or ["Unknown"])[0],
            "rating":    review.get("rating"),
            "date_read": e.get("date_read"),
            "pages":     book.get("num_pages"),
            "genres":    book.get("genres", [])[:4],
        }
        text = review.get("review_text", "").strip()
        if text:
            rec["review"] = text[:300]
        out.append(rec)
    return out


def _build_want_to_read():
    out = []
    for e in _shelves:
        if e.get("shelf") not in ("to-read", "did-not-finish"):
            continue
        book = _books.get(e["book_id"], {})
        out.append({
            "title":  e.get("title") or book.get("title"),
            "author": (book.get("authors") or ["Unknown"])[0],
            "shelf":  e.get("shelf"),
        })
    return out


def _build_taste():
    finished = [e for e in _shelves if e.get("shelf") == "read"]
    ratings, genre_counter = [], Counter()
    for e in finished:
        review = _reviews.get(e.get("review_id", ""), {})
        r = review.get("rating")
        if r:
            ratings.append(r)
        book = _books.get(e.get("book_id", ""), {})
        for g in book.get("genres", [])[:3]:
            genre_counter[g] += 1

    author_counter = Counter()
    for e in finished:
        book = _books.get(e.get("book_id", ""), {})
        for a in book.get("authors", [])[:1]:
            author_counter[a] += 1

    avg = round(sum(ratings) / len(ratings), 2) if ratings else None
    dist = {str(i): ratings.count(i) for i in range(1, 6)}

    return {
        "books_finished_total": len(finished),
        "avg_rating_given": avg,
        "rating_distribution": dist,
        "note_on_ratings": "Ebran rates tougher than the crowd — a 4 from him is a strong endorsement",
        "top_genres": [g for g, _ in genre_counter.most_common(8)],
        "top_authors": [a for a, _ in author_counter.most_common(6)],
        "prefers": [
            "long immersive books he can live inside",
            "found-family dynamics and character loss with no plot armor",
            "morally complex characters and world-building with depth",
            "redemption and forgiveness themes",
            "series over standalones once hooked",
            "strong female characters who are not damsels in distress",
        ],
        "tends_to_avoid": [
            "over-hyped books (skeptical and will call it out)",
            "authors who spoon-feed the reader or over-explain",
            "romance-heavy plots with constant yearning",
        ],
        "faith_lens": "Strong Christian faith — reflects on eternal themes, reads theology/devotional alongside fiction",
    }


# ── MCP endpoint (stateless streamable-http) ─────────────────────────────────

MCP_TOOLS = [
    {
        "name": "get_taste",
        "description": "Ebran's reading taste profile — genres, authors, ratings, preferences. Always call this first before recommending.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent",
        "description": "Recently finished books with Ebran's personal ratings and reviews.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of books to return (default 10)"}
            },
        },
    },
    {
        "name": "get_current",
        "description": "Books Ebran is currently reading.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_want_to_read",
        "description": "Ebran's to-read and paused shelf. Never recommend these — he already knows about them.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


@app.post("/mcp")
async def mcp_handler(request: Request):
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")

    if req_id is None:
        return JSONResponse(status_code=202, content={})

    if method == "initialize":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "readvault", "version": "1.0.0"},
        }})

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}})

    if method == "tools/call":
        params    = body.get("params", {})
        name      = params.get("name")
        arguments = params.get("arguments", {})
        result    = None
        if name == "get_taste":        result = _build_taste()
        elif name == "get_recent":     result = _build_recent(arguments.get("limit", 10))
        elif name == "get_current":    result = _build_current()
        elif name == "get_want_to_read": result = _build_want_to_read()

        if result is None:
            return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
        }})

    return JSONResponse({"jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}})


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/current")
def get_current():
    return _build_current()

@app.get("/recent")
def get_recent(limit: int = 10):
    return _build_recent(limit)

@app.get("/want-to-read")
def get_want_to_read():
    return _build_want_to_read()

@app.get("/taste")
def get_taste():
    return _build_taste()

@app.get("/health", include_in_schema=False)
def health():
    return {"books": len(_books), "shelf_entries": len(_shelves), "reviews": len(_reviews)}

@app.get("/legal", include_in_schema=False)
def legal():
    return {"info": "Personal tool. Not for public use."}
