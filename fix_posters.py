"""
fix_posters.py — One-time TMDB poster backfill for KSync

WHAT THIS DOES
  Loops through every movie currently in the live database, searches
  TMDB by title, and overwrites poster_url with a real, working image
  link. Run this once after broken/placeholder poster URLs are spotted
  (e.g. 404s in the browser console for image.tmdb.org/.../placeholder*.jpg
  or any other dead link).

  This script does NOT touch app.py or any live request-handling code —
  it is a standalone maintenance tool you run manually from your terminal
  or the Render shell.

SETUP — run these once before using the script
  1. Get a free TMDB API key:
       https://www.themoviedb.org/settings/api
     (Sign up → Settings → API → Request an API key → choose "Developer")

  2. Install the one extra dependency:
       pip install requests --break-system-packages

  3. Set two environment variables in the same terminal session:
       Windows PowerShell:
         $env:DATABASE_URL ="postgresql://ksync_db_user:VGfhw3q4rjYDJf0FR79Zeg9PdvpyxWVK@dpg-d8m73kb7uimc73d35kjg-a.oregon-postgres.render.com/ksync_db"
         $env:TMDB_API_KEY = "f0c3aec962b66e45380aa72d044d6bbf"

       macOS/Linux:
         export DATABASE_URL="postgresql://...your Render external URL..."
         export TMDB_API_KEY="your_tmdb_api_key_here"

USAGE
  python fix_posters.py            # dry run — shows what WOULD change, no writes
  python fix_posters.py --apply    # actually updates the database

WHY A DRY RUN FIRST
  TMDB's title search can occasionally match the wrong film (e.g. a
  remake, a foreign-language version, or an unrelated movie with the
  same name). Reviewing the dry-run output first lets you catch any
  mismatches before they're written to your live database.
"""

import os
import sys
import time
import requests
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get('DATABASE_URL')
TMDB_API_KEY = os.environ.get('TMDB_API_KEY')

if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is not set.")
    sys.exit(1)
if not TMDB_API_KEY:
    print("ERROR: TMDB_API_KEY environment variable is not set.")
    print("Get a free key at https://www.themoviedb.org/settings/api")
    sys.exit(1)

TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

DRY_RUN = '--apply' not in sys.argv


def tmdb_search(title, year=None):
    """
    Searches TMDB for a movie by title (optionally narrowed by year).
    Returns the poster_url, release_year, and tmdb_rating of the best
    match, or None if nothing was found.
    """
    params = {
        'api_key': TMDB_API_KEY,
        'query': title,
        'include_adult': False,
    }
    if year:
        params['year'] = year

    try:
        resp = requests.get(TMDB_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get('results', [])
    except requests.RequestException as e:
        print(f"    TMDB request failed for '{title}': {e}")
        return None

    if not results:
        return None

    best = results[0]   # TMDB already ranks by relevance/popularity
    poster_path = best.get('poster_path')
    if not poster_path:
        return None

    release_date = best.get('release_date', '')
    release_year = int(release_date[:4]) if release_date[:4].isdigit() else None

    return {
        'poster_url':   f"{TMDB_IMAGE_BASE}{poster_path}",
        'release_year': release_year,
        'tmdb_rating':  round(best.get('vote_average', 0), 1),
        'tmdb_title':   best.get('title', title),
    }


def main():
    db  = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()

    cur.execute("SELECT id, title, release_year, poster_url FROM movies WHERE is_active=1 ORDER BY id")
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
            time.sleep(0.3)   # be polite to TMDB's rate limits
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
        time.sleep(0.3)   # TMDB free tier: stay comfortably under rate limits

    cur.close()
    db.close()

    print("\n" + "=" * 70)
    print(f"  Updated : {updated}")
    print(f"  Skipped (already correct) : {skipped}")
    print(f"  Failed (no match found)   : {failed}")
    print("=" * 70)

    if DRY_RUN:
        print("\nThis was a DRY RUN — nothing was written to the database.")
        print("Review the matches above, then run again with --apply to save changes:")
        print("    python fix_posters.py --apply")


if __name__ == '__main__':
    main()
