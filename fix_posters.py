"""
fix_posters.py — TMDB poster backfill + auto movie fetcher for KSync

WHAT THIS DOES
  Mode 1 — Poster backfill (default):
    Loops through every active movie in the database, searches TMDB by
    title, and overwrites poster_url / release_year / tmdb_rating with
    fresh data. Run this whenever you spot 404 poster images.

  Mode 2 — Auto-fetch new movies (--fetch):
    Pulls movies from TMDB's Now Playing, Popular, and Top Rated
    endpoints and inserts any that are not already in the database.
    Genre tags, cast, director, description, poster, and rating are all
    fetched automatically. Duplicate detection is by title (case-
    insensitive) so re-running is safe.

SETUP — run these once before using the script
  1. Get a free TMDB API key:
       https://www.themoviedb.org/settings/api
     (Sign up → Settings → API → Request an API key → choose "Developer")

  2. Install dependencies:
       pip install requests psycopg2-binary --break-system-packages

  3. Set environment variables:
       Windows PowerShell:
         $env:DATABASE_URL ="postgresql://ksync_db_user:VGfhw3q4rjYDJf0FR79Zeg9PdvpyxWVK@dpg-d8m73kb7uimc73d35kjg-a.oregon-postgres.render.com/ksync_db"
         $env:TMDB_API_KEY = "f0c3aec962b66e45380aa72d044d6bbf"

       macOS/Linux:
         export DATABASE_URL="postgresql://...your Render external URL..."
         export TMDB_API_KEY="your_tmdb_api_key_here"

USAGE
  python fix_posters.py                   # dry-run poster backfill only
  python fix_posters.py --apply           # apply poster backfill
  python fix_posters.py --fetch 50        # dry-run: fetch 50 new movies
  python fix_posters.py --fetch 50 --apply   # actually insert them
  python fix_posters.py --fetch 100 --apply  # insert up to 100 new movies

  The --fetch flag pulls from Now Playing first, then Popular, then
  Top Rated until it has collected enough unique candidates.

WHY A DRY RUN FIRST
  TMDB's title search can occasionally match the wrong film (e.g. a
  remake or an unrelated movie with the same name). Reviewing the dry-
  run output lets you catch mismatches before they hit the live database.
"""

import os
import sys
import time
import requests
import psycopg2
import psycopg2.extras

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL')
TMDB_API_KEY = os.environ.get('TMDB_API_KEY')

if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is not set.")
    sys.exit(1)
if not TMDB_API_KEY:
    print("ERROR: TMDB_API_KEY environment variable is not set.")
    print("Get a free key at https://www.themoviedb.org/settings/api")
    sys.exit(1)

TMDB_BASE          = "https://api.themoviedb.org/3"
TMDB_SEARCH_URL    = f"{TMDB_BASE}/search/movie"
TMDB_DISCOVER_URL  = f"{TMDB_BASE}/discover/movie"
TMDB_DETAIL_URL    = f"{TMDB_BASE}/movie/{{tmdb_id}}"
TMDB_CREDITS_URL   = f"{TMDB_BASE}/movie/{{tmdb_id}}/credits"
TMDB_IMAGE_BASE    = "https://image.tmdb.org/t/p/w500"

DRY_RUN    = '--apply' not in sys.argv

# Parse --fetch N
FETCH_COUNT = 0
if '--fetch' in sys.argv:
    idx = sys.argv.index('--fetch')
    try:
        FETCH_COUNT = int(sys.argv[idx + 1])
    except (IndexError, ValueError):
        print("ERROR: --fetch requires a number, e.g.  --fetch 50")
        sys.exit(1)

# TMDB genre_id → human label (subset most likely to appear in Nigerian cinemas)
TMDB_GENRE_MAP = {
    28:    'Action',
    12:    'Adventure',
    16:    'Animation',
    35:    'Comedy',
    80:    'Crime',
    99:    'Documentary',
    18:    'Drama',
    10751: 'Family',
    14:    'Fantasy',
    36:    'History',
    27:    'Horror',
    10402: 'Music',
    9648:  'Mystery',
    10749: 'Romance',
    878:   'Sci-Fi',
    10770: 'TV Movie',
    53:    'Thriller',
    10752: 'War',
    37:    'Western',
}

# ── TMDB helpers ──────────────────────────────────────────────────────────────

def tmdb_get(url, params=None):
    """Thin wrapper: adds api_key, returns JSON or None on error."""
    p = {'api_key': TMDB_API_KEY}
    if params:
        p.update(params)
    try:
        r = requests.get(url, params=p, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"    TMDB request failed ({url}): {e}")
        return None


def tmdb_search(title, year=None):
    """Search TMDB by title. Returns enriched dict or None."""
    params = {'query': title, 'include_adult': False}
    if year:
        params['year'] = year
    data = tmdb_get(TMDB_SEARCH_URL, params)
    if not data:
        return None
    results = data.get('results', [])
    if not results:
        return None
    best = results[0]
    poster_path = best.get('poster_path')
    if not poster_path:
        return None
    release_date = best.get('release_date', '')
    release_year = int(release_date[:4]) if release_date[:4].isdigit() else None
    return {
        'tmdb_id':      best.get('id'),
        'poster_url':   f"{TMDB_IMAGE_BASE}{poster_path}",
        'release_year': release_year,
        'tmdb_rating':  round(best.get('vote_average', 0), 1),
        'tmdb_title':   best.get('title', title),
        'description':  best.get('overview', ''),
        'genre_ids':    best.get('genre_ids', []),
    }


def tmdb_credits(tmdb_id):
    """Fetch cast (top 5) and director for a TMDB movie id."""
    data = tmdb_get(TMDB_CREDITS_URL.format(tmdb_id=tmdb_id))
    if not data:
        return '', ''
    cast = [p['name'] for p in data.get('cast', [])[:5]]
    directors = [p['name'] for p in data.get('crew', []) if p.get('job') == 'Director']
    return ', '.join(cast), directors[0] if directors else ''


def tmdb_fetch_page(endpoint, page=1, extra_params=None):
    """Fetch one page from a TMDB list endpoint (now_playing, popular, etc.)."""
    params = {'page': page, 'language': 'en-US'}
    if extra_params:
        params.update(extra_params)
    data = tmdb_get(f"{TMDB_BASE}/movie/{endpoint}", params)
    if not data:
        return [], 0
    return data.get('results', []), data.get('total_pages', 1)


def collect_tmdb_candidates(target_count):
    """
    Pull movies from Now Playing → Popular → Top Rated until we have
    at least `target_count` unique candidates. Returns a list of raw
    TMDB result dicts, deduplicated by tmdb id.
    """
    seen_ids = set()
    candidates = []

    sources = [
        ('now_playing', {}),
        ('popular',     {}),
        ('top_rated',   {}),
        # Wider discover sweep: last 3 years, sorted by popularity
        ('discover',    {'sort_by': 'popularity.desc', 'vote_count.gte': 100}),
    ]

    for source_name, extra in sources:
        if len(candidates) >= target_count:
            break
        endpoint = source_name if source_name != 'discover' else None
        page = 1
        while len(candidates) < target_count:
            if source_name == 'discover':
                params = {'page': page, 'language': 'en-US',
                          'sort_by': 'popularity.desc', 'vote_count.gte': 100}
                data    = tmdb_get(TMDB_DISCOVER_URL, params)
                results = data.get('results', []) if data else []
                total   = data.get('total_pages', 1) if data else 1
            else:
                results, total = tmdb_fetch_page(source_name, page, extra)

            for r in results:
                if r.get('id') not in seen_ids and r.get('poster_path'):
                    seen_ids.add(r['id'])
                    candidates.append(r)

            print(f"  [{source_name} p{page}] collected {len(candidates)} candidates so far…")
            if page >= total or page >= 10:   # cap at 10 pages per source
                break
            page += 1
            time.sleep(0.25)

    return candidates[:target_count * 2]   # pass extras so dedup against DB has room


# ── Database helpers ───────────────────────────────────────────────────────────

def get_existing_titles(cur):
    """Returns a set of lowercase titles already in the movies table."""
    cur.execute("SELECT LOWER(title) FROM movies")
    return {row[0] for row in cur.fetchall()}


def insert_movie(cur, m):
    """Insert a single movie row. Returns the new id or None."""
    cur.execute("""
        INSERT INTO movies
            (title, genre, description, duration_min, rating,
             poster_url, release_year, director, cast_list, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        RETURNING id
    """, (
        m['title'],
        m['genre'],
        m['description'],
        m.get('duration_min', 120),
        m.get('tmdb_rating', 0.0),
        m['poster_url'],
        m.get('release_year'),
        m.get('director', ''),
        m.get('cast_list', ''),
    ))
    row = cur.fetchone()
    return row['id'] if row else None


# ── Mode 1: Poster backfill ────────────────────────────────────────────────────

def run_poster_backfill(cur, db):
    cur.execute("SELECT id, title, release_year, poster_url FROM movies WHERE is_active=TRUE ORDER BY id")
    movies = cur.fetchall()

    print(f"Found {len(movies)} active movies in the database.")
    print(f"Mode: {'DRY RUN (no writes)' if DRY_RUN else 'APPLY (will update database)'}")
    print("=" * 70)

    updated, skipped, failed = 0, 0, 0

    for m in movies:
        title = m['title']
        print(f"\n[{m['id']}] {title}")

        result = tmdb_search(title, m.get('release_year'))
        if not result:
            print("    No TMDB match found — skipping")
            failed += 1
            time.sleep(0.3)
            continue

        print(f"    TMDB match : {result['tmdb_title']} ({result['release_year']})")
        print(f"    New poster : {result['poster_url']}")
        print(f"    Old poster : {m['poster_url']}")

        if m['poster_url'] == result['poster_url']:
            print("    Already up to date — skipping")
            skipped += 1
            time.sleep(0.3)
            continue

        if not DRY_RUN:
            cur.execute(
                "UPDATE movies SET poster_url=%s WHERE id=%s",
                (result['poster_url'], m['id'])
            )
            db.commit()
            print("    ✓ Updated")

        updated += 1
        time.sleep(0.3)

    print("\n" + "=" * 70)
    print(f"  Updated : {updated}")
    print(f"  Skipped (already correct) : {skipped}")
    print(f"  Failed  (no match found)  : {failed}")
    print("=" * 70)

    if DRY_RUN:
        print("\nThis was a DRY RUN — nothing was written to the database.")
        print("Run again with --apply to save changes:")
        print("    python fix_posters.py --apply")


# ── Mode 2: Auto-fetch new movies ─────────────────────────────────────────────

def run_fetch(cur, db):
    print(f"Fetching up to {FETCH_COUNT} new movies from TMDB…")
    print(f"Mode: {'DRY RUN (no writes)' if DRY_RUN else 'APPLY (will insert into database)'}")
    print("=" * 70)

    existing = get_existing_titles(cur)
    print(f"  {len(existing)} movies already in the database — these will be skipped.\n")

    candidates = collect_tmdb_candidates(FETCH_COUNT)
    print(f"\nCollected {len(candidates)} candidates from TMDB.\n" + "=" * 70)

    inserted, skipped, failed = 0, 0, 0

    for raw in candidates:
        if inserted >= FETCH_COUNT:
            break

        title = raw.get('title', '').strip()
        if not title or title.lower() in existing:
            skipped += 1
            continue

        poster_path = raw.get('poster_path')
        if not poster_path:
            skipped += 1
            continue

        # Map genre ids → first recognisable genre label
        genre_ids = raw.get('genre_ids', [])
        genre = next((TMDB_GENRE_MAP[g] for g in genre_ids if g in TMDB_GENRE_MAP), 'Drama')

        release_date = raw.get('release_date', '')
        release_year = int(release_date[:4]) if release_date[:4].isdigit() else None

        # Fetch cast + director (costs one extra API call per movie)
        tmdb_id  = raw['id']
        cast_str, director = tmdb_credits(tmdb_id)
        time.sleep(0.25)

        movie = {
            'title':        title,
            'genre':        genre,
            'description':  raw.get('overview', ''),
            'poster_url':   f"{TMDB_IMAGE_BASE}{poster_path}",
            'release_year': release_year,
            'tmdb_rating':  round(raw.get('vote_average', 0), 1),
            'director':     director,
            'cast_list':    cast_str,
            'duration_min': 120,   # TMDB list endpoints don't include runtime; detail call would add latency
        }

        print(f"  + {title} ({release_year}) · {genre} · ★{movie['tmdb_rating']}")
        if director: print(f"    Dir. {director}")
        if cast_str: print(f"    Cast: {cast_str}")

        if not DRY_RUN:
            try:
                new_id = insert_movie(cur, movie)
                db.commit()
                existing.add(title.lower())
                print(f"    ✓ Inserted (id={new_id})")
            except Exception as e:
                db.rollback()
                print(f"    ✗ Insert failed: {e}")
                failed += 1
                continue

        inserted += 1
        time.sleep(0.25)

    print("\n" + "=" * 70)
    print(f"  Inserted : {inserted}")
    print(f"  Skipped  (duplicate or no poster) : {skipped}")
    print(f"  Failed   (DB error)               : {failed}")
    print("=" * 70)

    if DRY_RUN:
        print("\nThis was a DRY RUN — nothing was written to the database.")
        print(f"Run again with --apply to insert these movies:")
        print(f"    python fix_posters.py --fetch {FETCH_COUNT} --apply")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    db  = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()

    if FETCH_COUNT > 0:
        run_fetch(cur, db)
    else:
        run_poster_backfill(cur, db)

    cur.close()
    db.close()


if __name__ == '__main__':
    main()
