"""
Mock ReadVault API — real 2026 data, hardcoded.
Replace with live JSONL reads once plugin integration is confirmed working.

Run:  uvicorn api.mock_server:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI, Security, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi_mcp import FastApiMCP

import os

API_KEY = os.getenv("API_KEY", "readvault-mock-key")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://readvault-5zo3.onrender.com")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(
    title="ReadVault",
    description="Ebran's personal reading data — current reads, recent finishes, taste profile, and want-to-read list.",
    version="0.1.0",
)


def require_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ── plugin manifest ───────────────────────────────────────────────────────────

@app.get("/.well-known/ai-plugin.json", include_in_schema=False)
def plugin_manifest():
    return JSONResponse({
        "schema_version": "v1",
        "name_for_human": "ReadVault",
        "name_for_model": "readvault",
        "description_for_human": "Access Ebran's personal reading data — current reads, recent finishes, taste profile, and to-read list.",
        "description_for_model": (
            "Use this plugin to access Ebran's personal reading data from Goodreads. "
            "Always call /taste before making recommendations to understand his preferences. "
            "Call /recent to see what he has enjoyed lately with his personal ratings (he rates tougher than the crowd — a 4 is a strong endorsement). "
            "Call /current to know what he is mid-way through. "
            "Call /want-to-read for his existing queue. "
            "Never recommend a book he has already read, is currently reading, or has already queued."
        ),
        "auth": {
            "type": "service_http",
            "authorization_type": "bearer",
            "verification_token": API_KEY,
        },
        "api": {
            "type": "openapi",
            "url": f"{PUBLIC_URL}/openapi.json",
        },
        "logo_url": f"{PUBLIC_URL}/logo.png",
        "contact_email": "ebranbright@gmail.com",
        "legal_info_url": f"{PUBLIC_URL}/legal",
    })


@app.get("/legal", include_in_schema=False)
def legal():
    return {"info": "Personal tool. Not for public use."}


# ── data (real 2026 reads) ────────────────────────────────────────────────────

CURRENT = [
    {"title": "East of Eden", "author": "John Steinbeck", "shelf": "currently-reading", "date_started": "2026-07-04", "pages": 602},
    {"title": "Rebecca", "author": "Daphne du Maurier", "shelf": "currently-reading", "date_started": "2026-07-04", "pages": None},
    {"title": "The Iliad", "author": "Homer", "shelf": "currently-reading", "date_started": "2026-06-11", "pages": 683},
    {"title": "Gilead", "author": "Marilynne Robinson", "shelf": "currently-reading", "date_started": "2026-04-03", "pages": 247},
    {"title": "On the Incarnation", "author": "Athanasius of Alexandria", "shelf": "currently-reading", "date_started": "2026-07-01", "pages": 115},
    {"title": "Redeeming Productivity", "author": "Reagan Rose", "shelf": "currently-reading", "date_started": "2026-04-27", "pages": 160},
]

RECENT = [
    {"title": "The Seven Husbands of Evelyn Hugo", "author": "Taylor Jenkins Reid", "rating": 3, "date_read": "2026-07-01", "pages": 389, "genres": ["fiction", "historical fiction", "romance"]},
    {"title": "The Memory Police", "author": "Yōko Ogawa", "rating": 2, "date_read": "2026-07-01", "pages": 274, "genres": ["fiction", "literary fiction"], "review": "did not resonate with me much. felt vague."},
    {"title": "The Final Empire", "author": "Brandon Sanderson", "rating": 4, "date_read": "2026-06-26", "pages": 647, "genres": ["fantasy"], "review": "This book was what I expected 'Six of Crows' to be. A much better heist story with well defined magical system."},
    {"title": "Jade Legacy", "author": "Fonda Lee", "rating": 4, "date_read": "2026-06-17", "pages": 752, "genres": ["fantasy"], "review": "Awesome ending to an awesome franchise. Will really miss these characters."},
    {"title": "Jade War", "author": "Fonda Lee", "rating": 4, "date_read": "2026-06-02", "pages": 624, "genres": ["fantasy"], "review": "Fonda Lee did a good job expanding the world."},
    {"title": "Jade City", "author": "Fonda Lee", "rating": 5, "date_read": "2026-05-26", "pages": 529, "genres": ["fantasy"], "review": "Brilliant! Fast paced! No plot armors here. Really reminded me why I love reading books."},
    {"title": "The Count of Monte Cristo", "author": "Alexandre Dumas", "rating": 5, "date_read": "2026-05-17", "pages": 1276, "genres": ["classics", "historical fiction", "adventure"], "review": "6 stars for me. Made me realize why I love longer books and find solace in them."},
    {"title": "Project Hail Mary", "author": "Andy Weir", "rating": 5, "date_read": "2026-01-24", "pages": 476, "genres": ["science fiction"], "review": "Such a page turner. Rocky is really a cool character and the bromance is delightful."},
    {"title": "The Screwtape Letters", "author": "C.S. Lewis", "rating": 5, "date_read": "2026-04-26", "pages": 209, "genres": ["classics", "christian", "theology"], "review": "Even after 50+ years the ideas still ring true. This book definitely needs a reread."},
    {"title": "Between Two Fires", "author": "Christopher Buehlman", "rating": 4, "date_read": "2026-04-15", "pages": 433, "genres": ["fantasy", "historical fiction", "horror"], "review": "Loved this book. Theme of forgiveness and redemption played well."},
]

WANT_TO_READ = [
    {"title": "Words of Radiance", "author": "Brandon Sanderson", "note": "sequel to The Way of Kings"},
    {"title": "The Mahabharata: A Modern Rendering", "author": "Ramesh Menon", "note": "paused, will resume"},
    {"title": "The Shadow of the Wind", "author": "Carlos Ruiz Zafón", "note": "paused, will resume"},
    {"title": "The Name of the Rose", "author": "Umberto Eco", "note": "paused — heavy sentences, will return when in the mindset"},
    {"title": "Fourth Wing", "author": "Rebecca Yarros", "note": "paused, may not be my cup of tea"},
    {"title": "The Mortification of Sin", "author": "John Owen", "note": "theology, paused"},
    {"title": "Ponniyin Selvan, Part 2", "author": "Kalki Krishnamurthy", "note": "continuation of Part 1 which I loved"},
]

TASTE = {
    "top_genres": ["fantasy", "classics", "historical fiction", "science fiction", "horror", "theology/devotional"],
    "favourite_authors": ["Fonda Lee", "Alexandre Dumas", "Brandon Sanderson", "Andy Weir", "C.S. Lewis"],
    "avg_rating_given": 3.5,
    "goodreads_crowd_avg_for_same_books": 4.19,
    "note_on_ratings": "Ebran rates tougher than the crowd — a 4 from him is a strong endorsement",
    "books_finished_2026": 32,
    "pages_read_2026": 12286,
    "avg_pages_per_book": 384.6,
    "reading_pace": "~1.3 books/week, projected 60+ books for the full year",
    "reading_style": "frequently leaves a book 'open' for weeks then blitzes through in a few sessions; binge-reads series once hooked",
    "prefers": [
        "long immersive books he can live inside",
        "found-family dynamics and character loss with no plot armor",
        "morally complex characters",
        "world-building with depth",
        "redemption and forgiveness themes",
        "series over standalones once hooked",
        "strong female characters who are not damsels in distress",
    ],
    "tends_to_avoid": [
        "over-hyped books (skeptical and will call it out)",
        "authors who spoon-feed the reader or over-explain",
        "romance-heavy plots with constant yearning",
        "minimal prose styles with no world-building",
        "books that feel flat without twists",
    ],
    "faith_lens": "Ebran has a strong Christian faith that shows up in his reading — he reads theology/devotional books alongside fiction and often reflects on eternal themes in his reviews",
    "rated_1_star": ["All My Cats — Bohumil Hrabal"],
    "rated_2_star": ["Tokyo Express", "The Decagon House Murders", "Six of Crows", "The Memory Police", "Foster", "A Short Stay in Hell"],
    "rated_5_star": ["Pride and Prejudice", "Project Hail Mary", "The Screwtape Letters", "The Count of Monte Cristo", "Jade City"],
}


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/current", summary="Books currently being read")
def get_current(_=Security(require_key)):
    return CURRENT


@app.get("/recent", summary="Recently finished books with ratings")
def get_recent(limit: int = 10, _=Security(require_key)):
    return RECENT[:limit]


@app.get("/want-to-read", summary="To-read and paused shelf")
def get_want_to_read(_=Security(require_key)):
    return WANT_TO_READ


@app.get("/taste", summary="Derived taste profile — call this before making any recommendations")
def get_taste(_=Security(require_key)):
    return TASTE


# ── MCP mount (exposes all routes above as MCP tools) ─────────────────────────
mcp = FastApiMCP(app)
mcp.mount_http()
