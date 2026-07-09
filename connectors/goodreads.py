"""
Goodreads scraping primitives.

Goodreads shut down its public API to new developers in 2020, so this reads
the site the same way a browser does:

- Shelf listings (read / currently-reading / to-read / did-not-finish / ...)
  come from the authenticated `review/list` HTML table. As of mid-2026,
  Goodreads' authenticated pages sit behind an AWS WAF JS challenge that a
  plain `requests` session can never pass (it gets back a 202 with an empty
  body, `x-amzn-waf-action: challenge`, regardless of cookie validity) --
  so authenticated pages are loaded with a real (Playwright-driven) browser
  seeded with session cookies pulled live from a local browser cookie store
  (never written to disk -- see `load_browser_context()`).
- Public RSS shelf feeds (`review/list_rss`) are used as a fast, unauthenticated
  way to get read_at / date_added without paginating the HTML table -- these
  are NOT behind the WAF challenge, so plain `requests` still works for them.
- Per-review "reading timeline" (shelved / started / % progress / finished
  events) comes from the review page, which is also authenticated and thus
  also needs the Playwright browser context.
- Book metadata (page count, author, ISBN, rating, genres) comes from the
  public book page, which embeds a schema.org JSON-LD block plus a couple of
  `data-testid` attributes for the fields not in the JSON-LD. Public, so
  plain `requests` works here too.

Nothing in this module holds Goodreads credentials in code -- the cookie DB
path and user id live in config/settings.yaml.
"""
import json
import re
import sqlite3
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
REQUEST_DELAY = 0.3  # seconds between paginated/authenticated requests


def _load_goodreads_cookies(cookies_db: str) -> list[dict]:
    """Read Goodreads cookies from a local browser's cookie store as Playwright cookie dicts.

    Cookie values are read into memory only -- never written to disk.
    """
    con = sqlite3.connect(f"file:{cookies_db}?immutable=1", uri=True)
    rows = con.execute(
        "SELECT name, value, host, path, expiry, isSecure, isHttpOnly "
        "FROM moz_cookies WHERE host LIKE '%goodreads%'"
    ).fetchall()
    con.close()

    cookies = []
    for name, value, host, path, expiry, is_secure, is_http_only in rows:
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": host,
                "path": path,
                # moz_cookies stores expiry in milliseconds; Playwright wants seconds
                # (or -1 for a session cookie).
                "expires": (expiry / 1000) if expiry and expiry > 0 else -1,
                "secure": bool(is_secure),
                "httpOnly": bool(is_http_only),
            }
        )
    return cookies


def load_browser_context(playwright, cookies_db: str, headless: bool = True):
    """Launch a Chromium browser seeded with Goodreads session cookies.

    A real browser is required (not `requests`) because Goodreads' authenticated
    pages are behind an AWS WAF JS challenge -- only a JS-executing browser can
    pass it. Returns (browser, context); caller is responsible for
    `browser.close()`.
    """
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(user_agent=HEADERS["User-Agent"])
    context.add_cookies(_load_goodreads_cookies(cookies_db))
    return browser, context


def load_session(cookies_db: str) -> requests.Session:
    """Build a requests.Session for PUBLIC Goodreads endpoints (RSS, book pages).

    Do not use this for authenticated pages (review/list, review/show) -- those
    are behind a WAF JS challenge that requests can't pass. Use
    load_browser_context() instead.
    """
    con = sqlite3.connect(f"file:{cookies_db}?immutable=1", uri=True)
    rows = con.execute(
        "SELECT name, value, host, path FROM moz_cookies WHERE host LIKE '%goodreads%'"
    ).fetchall()
    con.close()

    session = requests.Session()
    for name, value, host, path in rows:
        session.cookies.set(name, value, domain=host, path=path)
    session.headers.update(HEADERS)
    return session


def _parse_rss_date(s: str):
    if not s:
        return None
    try:
        y, m, d = s.split("/")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def get_shelf_rss(user_id: str, shelf: str):
    """Return [{title, read_at, date_added}] for a shelf via the public RSS feed.

    Fast and needs no auth -- used to get read_at/date_added without paginating
    the authenticated HTML table.
    """
    items = []
    page = 1
    while True:
        resp = requests.get(
            f"https://www.goodreads.com/review/list_rss/{user_id}",
            params={"shelf": shelf, "page": page},
            headers=HEADERS,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "xml")
        page_items = soup.find_all("item")
        if not page_items:
            break
        for it in page_items:
            title = it.find("title").get_text(strip=True) if it.find("title") else "?"
            desc = it.find("description").get_text(" ", strip=True) if it.find("description") else ""
            read_at = re.search(r"read at:\s*([\d/]*)", desc)
            date_added = re.search(r"date added:\s*([\d/]*)", desc)
            items.append(
                {
                    "title": title,
                    "read_at": _parse_rss_date(read_at.group(1) if read_at else ""),
                    "date_added": _parse_rss_date(date_added.group(1) if date_added else ""),
                }
            )
        if len(page_items) < 100:
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    return items


BOOK_URL_RE = re.compile(r"/book/show/(\d+)")


def get_shelf_reviews(context, user_id: str, shelf: str, title_filter=None):
    """Return [{review_id, book_id, title, book_url}] for a shelf.

    Requires a Playwright browser `context` seeded with session cookies (see
    load_browser_context()) -- review/list is behind Goodreads' authenticated
    WAF-JS-challenge wall, so a plain requests session can't reach it. If
    title_filter is given, only rows whose title is in it are returned -- used
    to cheaply narrow ~200 shelf rows down to a handful of candidates found via
    the RSS feed first.
    """
    reviews = []
    page = 1
    pg = context.new_page()
    while True:
        pg.goto(
            f"https://www.goodreads.com/review/list/{user_id}"
            f"?shelf={shelf}&page={page}&per_page=100",
            timeout=30000,
        )
        pg.wait_for_load_state("networkidle", timeout=20000)
        soup = BeautifulSoup(pg.content(), "html.parser")
        rows = soup.select("tr.bookalike")
        if not rows:
            break
        for tr in rows:
            rid = tr.get("id", "").replace("review_", "")
            title_a = tr.select_one("td.field.title a")
            title = title_a.get_text(" ", strip=True) if title_a else "?"
            if not rid:
                continue
            if title_filter is not None and title not in title_filter:
                continue
            book_url = None
            book_id = None
            if title_a and title_a.get("href"):
                book_url = "https://www.goodreads.com" + title_a["href"]
                m = BOOK_URL_RE.search(title_a["href"])
                if m:
                    book_id = m.group(1)
            reviews.append(
                {"review_id": rid, "book_id": book_id, "title": title, "book_url": book_url}
            )
        if len(rows) < 100:
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    pg.close()
    return reviews


DATE_LINE_RE = re.compile(r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}$")


def _date_from_str(s: str) -> date:
    s = re.sub(r"\s+", " ", s).strip()
    return datetime.strptime(s, "%B %d, %Y").date()


def get_reading_timeline(page, review_id: str):
    """Return [(date, event_text), ...] from a review page's reading timeline.

    `page` is a Playwright Page from a browser context seeded with session
    cookies (see load_browser_context()) -- review/show is behind the same
    authenticated WAF-JS-challenge wall as review/list. Pass the same `page`
    across calls (one per review) to avoid opening/closing a page each time.
    """
    page.goto(f"https://www.goodreads.com/review/show/{review_id}", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=20000)
    soup = BeautifulSoup(page.content(), "html.parser")
    timeline = soup.select_one(".readingTimeline")
    if not timeline:
        return []
    text = timeline.get_text("\n", strip=True)
    lines = [l for l in text.split("\n") if l.strip()]

    date_idxs = [i for i, l in enumerate(lines) if DATE_LINE_RE.match(l)]
    events = []
    for n, idx in enumerate(date_idxs):
        d = _date_from_str(lines[idx])
        end = date_idxs[n + 1] if n + 1 < len(date_idxs) else len(lines)
        desc = None
        for l in lines[idx + 1 : end]:
            l = l.strip()
            if l in ("–", "-", "") or l.startswith('"'):
                continue
            desc = l
            break
        if desc:
            events.append((d, desc))
    return events


def get_book_details(session: requests.Session, book_url: str) -> dict:
    """Scrape a public book page for metadata: pages, author, isbn, rating, genres.

    Primary source is the schema.org JSON-LD block Goodreads embeds in every
    book page; falls back to `data-testid` attributes for fields JSON-LD
    sometimes omits (e.g. page count on editions without an ISBN).
    """
    resp = session.get(book_url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    ld = {}
    script = soup.find("script", {"type": "application/ld+json"})
    if script and script.string:
        try:
            ld = json.loads(script.string)
        except json.JSONDecodeError:
            ld = {}

    num_pages = ld.get("numberOfPages")
    if num_pages is None:
        pages_format = soup.select_one('[data-testid="pagesFormat"]')
        if pages_format:
            m = re.search(r"([\d,]+)\s*pages", pages_format.get_text())
            if m:
                num_pages = int(m.group(1).replace(",", ""))

    authors = [a.get("name") for a in ld.get("author", []) if isinstance(a, dict) and a.get("name")]

    rating = ld.get("aggregateRating", {}) or {}

    genres_el = soup.select_one('[data-testid="genresList"]')
    genres = []
    if genres_el:
        for a in genres_el.select("a"):
            g = a.get_text(strip=True)
            if g and g.lower() != "...more":
                genres.append(g)

    publication_info = None
    pub_el = soup.select_one('[data-testid="publicationInfo"]')
    if pub_el:
        publication_info = pub_el.get_text(strip=True)

    return {
        "book_id": (BOOK_URL_RE.search(book_url).group(1) if BOOK_URL_RE.search(book_url) else None),
        "title": ld.get("name"),
        "authors": authors,
        "isbn": ld.get("isbn"),
        "book_format": ld.get("bookFormat"),
        "num_pages": num_pages,
        "avg_rating": rating.get("ratingValue"),
        "rating_count": rating.get("ratingCount"),
        "review_count": rating.get("reviewCount"),
        "genres": genres,
        "publication_info": publication_info,
        "url": book_url,
    }
