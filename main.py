from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import hmac
import logging
import asyncio
import os
from scraper import MyDramaListScraper, ScraperError
import time

# In-memory cache for calendar data
_calendar_cache = {
    "data": None,
    "timestamp": 0,
    "ttl_seconds": 3600  # 1 hour
}

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

tags_metadata = [
    {
        "name": "Search",
        "description": "Search for dramas by title.",
    },
    {
        "name": "Drama",
        "description": "Get full drama details, cast, reviews, and recommendations.",
    },
    {
        "name": "Episodes",
        "description": (
            "Episode data at three levels of detail:\n\n"
            "- **`/episodes`** — list (title + air date)\n"
            "- **`/episodes/{n}`** — single episode: description, cover image, rating, season\n"
            "- **`/episodes/all`** — all episodes enriched concurrently"
        ),
    },
    {
        "name": "People & Lists",
        "description": "Person profiles, seasonal charts, user-created lists, and watchlists.",
    },
    {
        "name": "Calendar",
        "description": "Currently airing dramas grouped by day of the week.",
    },
    {
        "name": "Utility",
        "description": "Health check and diagnostics.",
    },
]

app = FastAPI(
    title="MyDramaList Unofficial API",
    description="""
## MyDramaList Unofficial Scraper API

An unofficial, serverless REST API that scrapes public data from [MyDramaList.com](https://mydramalist.com).
Built with **FastAPI + BeautifulSoup4 + curl_cffi** for browser-impersonated requests.

---

### 📺 Episodes — 3 levels of detail

| Endpoint | Data returned |
|----------|--------------|
| `/api/id/{slug}/episodes` | Episode list (number, title, air date) |
| `/api/id/{slug}/episodes/{n}` | Single episode: **description, cover image**, rating, season |
| `/api/id/{slug}/episodes/all` | All episodes with full details (concurrent fetching) |

> **Slug format**: `{id}-{drama-name}`, e.g. `58651-run-on`, `746993-my-demon`

---

### ⚠️ Rate limits & timeouts
- Every endpoint has a built-in **1 s delay**.
- `/episodes/all` makes one request per episode in batches of 4 (0.5 s between batches).
  Expect **5–15 s** for a 16-episode drama.
- On Vercel free tier (10 s timeout), prefer `/episodes/{n}` for individual lookups.

---

### 🔴 Error format
```json
{ "code": 404, "error": true, "description": "404 Not Found" }
```
""",
    version="1.1.0",
    openapi_tags=tags_metadata,
    license_info={"name": "Educational use only"},
    contact={"name": "GitHub", "url": "https://github.com/B1PL0B/MyDramaList-Unofficial-API"},
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize scraper
scraper = MyDramaListScraper()

# --- Optional API key ------------------------------------------------------
# A self-hosted deployment is a public URL, and every request it serves costs
# the owner a scrape against MyDramaList. Setting MDL_API_KEY in the
# environment locks /api/* behind an `x-api-key` header.
#
# If MDL_API_KEY is unset the guard is a no-op, so existing deployments (and
# anyone running this locally) keep working exactly as before — the lock is
# opt-in, not a breaking change.
#
# /api/health stays open on purpose: uptime checks shouldn't need a secret,
# and it reveals nothing.
API_KEY = os.environ.get("MDL_API_KEY", "").strip()
OPEN_PATHS = {"/api/health"}


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    path = request.url.path
    if API_KEY and path.startswith("/api/") and path not in OPEN_PATHS:
        supplied = request.headers.get("x-api-key", "")
        # compare_digest: don't leak the key one byte at a time via timing.
        if not hmac.compare_digest(supplied, API_KEY):
            return JSONResponse(
                status_code=401,
                content={"code": 401, "error": True,
                         "description": "Missing or invalid x-api-key header"},
            )
    return await call_next(request)

@app.on_event("shutdown")
async def shutdown_event():
    await scraper.close()

@app.exception_handler(ScraperError)
async def scraper_error_handler(request: Request, exc: ScraperError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "error": True, "description": exc.detail or exc.suggested_action}
    )

@app.get("/")
async def root():
    """Redirect to static index page"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")

@app.get("/api/search/q/{query}", tags=["Search"],
         summary="Search dramas",
         description="Search MyDramaList by title. Returns up to 20 results including title, slug, year, image, rating, and URL.")
async def search_dramas(query: str):
    """Search for dramas by title query."""
    logger.info(f"Searching for: {query}")
    await asyncio.sleep(1)  # Rate limiting
    results = await scraper.search_dramas(query)
    return results

@app.get("/api/id/{slug}", tags=["Drama"],
         summary="Get drama details",
         description="Get full details for a drama by its slug (e.g. `58651-run-on`). Includes title, synopsis, genres, cast overview, rating, year, and more.")
async def get_drama_details(slug: str):
    """Get drama details by slug."""
    logger.info(f"Getting drama details for: {slug}")
    await asyncio.sleep(1)  # Rate limiting
    details = await scraper.get_drama_details(slug)
    return details

@app.get("/api/id/{slug}/cast", tags=["Drama"],
         summary="Get cast & crew",
         description="Returns cast and crew grouped by role (Main Role, Support Role, Guest Role, Director, Screenwriter, etc.).")
async def get_drama_cast(slug: str):
    """Get cast and crew for a drama."""
    logger.info(f"Getting cast for: {slug}")
    await asyncio.sleep(1)  # Rate limiting
    cast = await scraper.get_drama_cast(slug)
    return cast

@app.get("/api/id/{slug}/episodes", tags=["Episodes"],
         summary="Get episode list",
         description="Returns the full episode list for a drama: episode number, title, and air date. No per-episode page visits — fast.")
async def get_drama_episodes(slug: str):
    """Get episode list (number, title, air date) for a drama."""
    logger.info(f"Getting episodes for: {slug}")
    await asyncio.sleep(1)  # Rate limiting
    episodes = await scraper.get_drama_episodes(slug)
    return episodes

@app.get("/api/id/{slug}/episodes/all", tags=["Episodes"],
         summary="Get all episodes enriched",
         description="Fetches the episode list then **concurrently visits each episode page** (batches of 4, 0.5 s delay between batches) to retrieve description, cover image, rating, and season for every episode. Expect 5–15 s for a 16-episode drama.")
async def get_drama_episodes_all(slug: str):
    """Get all episodes with full details — description, cover image, rating, season."""
    logger.info(f"Getting all episode details for: {slug}")
    await asyncio.sleep(1)  # Rate limiting
    result = await scraper.get_drama_episodes_all(slug)
    return result

@app.get("/api/id/{slug}/episodes/{episode_number}", tags=["Episodes"],
         summary="Get single episode details",
         description="Visits `/{slug}/episode/{n}` on MyDramaList and returns: **title, description, cover image, air date, rating, season**. One extra HTTP request per call.")
async def get_episode_details(slug: str, episode_number: int):
    """Get full details for a single episode by number."""
    logger.info(f"Getting episode {episode_number} details for: {slug}")
    await asyncio.sleep(1)  # Rate limiting
    detail = await scraper.get_episode_details(slug, episode_number)
    return detail

@app.get("/api/id/{slug}/reviews", tags=["Drama"],
         summary="Get reviews",
         description="Returns up to 10 user reviews for a drama, including review text, overall score, story/acting/music/rewatch scores, author, and date.")
async def get_drama_reviews(slug: str):
    """Get user reviews for a drama."""
    logger.info(f"Getting reviews for: {slug}")
    await asyncio.sleep(1)  # Rate limiting
    reviews = await scraper.get_drama_reviews(slug)
    return reviews

@app.get("/api/id/{slug}/recs", tags=["Drama"],
         summary="Get recommendations",
         description="Returns drama recommendations for a given drama, including the recommended title, reasons given by users, vote count, and recommender username.")
async def get_drama_recommendations(slug: str):
    """Get drama recommendations with reasons and votes."""
    logger.info(f"Getting recommendations for: {slug}")
    await asyncio.sleep(1)  # Rate limiting
    recs = await scraper.get_drama_recommendations(slug)
    return recs

@app.get("/api/people/{people_id}", tags=["People & Lists"],
         summary="Get person details",
         description="Returns biography, birthday, nationality, filmography, and social links for an actor/director/crew member. Use the slug from their MDL profile URL (e.g. `14472-song-kang`).")
async def get_person_details(people_id: str):
    """Get person details by slug (e.g. 14472-song-kang)."""
    logger.info(f"Getting person details for: {people_id}")
    await asyncio.sleep(1)  # Rate limiting
    person = await scraper.get_person_details(people_id)
    return person

@app.get("/api/people/{people_id}/photos", tags=["People & Lists"],
         summary="Get person photos",
         description="Returns up to `limit` (default 12) full-size photos from a person's photo gallery, each with its thumbnail and gallery page URL.")
async def get_person_photos(people_id: str, limit: int = 12):
    """Get a person's gallery photos by slug (e.g. 15843-li-yu-jie)."""
    logger.info(f"Getting person photos for: {people_id}")
    await asyncio.sleep(1)  # Rate limiting
    # Clamp: `limit` is user input and each photo is only a URL, but an
    # unbounded value invites a caller to ask for a person's entire gallery.
    limit = max(1, min(limit, 60))
    photos = await scraper.get_person_photos(people_id, limit=limit)
    return photos

@app.get("/api/seasonal/{year}/{quarter}", tags=["People & Lists"],
         summary="Get seasonal dramas",
         description="Returns the top dramas for a specific year and quarter. Quarter values: `1`=Winter, `2`=Spring, `3`=Summer, `4`=Fall. Example: `/api/seasonal/2023/4`")
async def get_seasonal_dramas(year: int, quarter: int):
    """Get top dramas for a year and quarter (1=Winter, 2=Spring, 3=Summer, 4=Fall)."""
    try:
        if quarter not in [1, 2, 3, 4]:
            raise HTTPException(
                status_code=400,
                detail={"code": 400, "error": True, "description": "Quarter must be 1, 2, 3, or 4"}
            )
        
        logger.info(f"Getting seasonal dramas for: {year} Q{quarter}")
        await asyncio.sleep(1)  # Rate limiting
        dramas = await scraper.get_seasonal_dramas(year, quarter)
        return dramas
    except HTTPException:
        raise

@app.get("/api/list/{list_id}", tags=["People & Lists"],
         summary="Get drama list",
         description="Returns all dramas in a user-created public MDL list. Returns 400 if the list is private. Use the numeric list ID from the MDL list URL.")
async def get_drama_list(list_id: str):
    """Get dramas in a public MDL list by list ID."""
    logger.info(f"Getting drama list: {list_id}")
    await asyncio.sleep(1)  # Rate limiting
    drama_list = await scraper.get_drama_list(list_id)
    return drama_list

@app.get("/api/dramalist/{user_id}", tags=["People & Lists"],
         summary="Get user watchlist",
         description="Returns a user's public drama watchlist. Returns 400 if the watchlist is private. Use the MDL username or user ID.")
async def get_user_drama_list(user_id: str):
    """Get a user's public watchlist by user ID or username."""
    logger.info(f"Getting user drama list for: {user_id}")
    await asyncio.sleep(1)  # Rate limiting
    user_list = await scraper.get_user_drama_list(user_id)
    return user_list

@app.get("/api/calendar", tags=["Calendar"],
         summary="Get airing calendar",
         description="Returns currently airing dramas grouped by day of the week (Monday-Sunday).")
async def get_airing_calendar():
    """Get currently airing dramas from the calendar."""
    logger.info("Getting airing calendar")

    now = time.time()
    if _calendar_cache["data"] is not None and (now - _calendar_cache["timestamp"]) < _calendar_cache["ttl_seconds"]:
        # Return cached data, add cache hit header
        response = JSONResponse(content=_calendar_cache["data"])
        response.headers["X-Cache"] = "HIT"
        response.headers["X-Cache-Age"] = str(int(now - _calendar_cache["timestamp"])) + "s"
        return response

    await asyncio.sleep(1)  # Rate limiting
    calendar_data = await scraper.get_airing_calendar()

    # Store in cache
    _calendar_cache["data"] = calendar_data
    _calendar_cache["timestamp"] = now

    response = JSONResponse(content=calendar_data)
    response.headers["X-Cache"] = "MISS"
    return response

# Health check endpoint
@app.get("/api/health", tags=["Utility"], summary="Health check")
async def health_check():
    """Returns healthy status if the API is running."""
    return {"status": "healthy", "version": "1.1.0", "message": "MyDramaList Unofficial API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
