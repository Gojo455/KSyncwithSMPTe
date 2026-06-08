"""
ksync — Seat-Aware Cinema Reservation System
Abuja, Nigeria | Flask + Turso/SQLite | Hybrid Recommender | Paystack Payments
"""

from flask import Flask, render_template, request, jsonify, session, g, redirect
import hashlib, secrets, json, os, time, random, math
from datetime import datetime, timedelta
from functools import wraps

# ── DATABASE BACKEND ─────────────────────────────────────────────────────────
# Uses Turso (cloud SQLite) when both env vars are present — data survives
# Render restarts and deploys forever.
# Falls back to a local SQLite file automatically when running on your laptop.
TURSO_URL   = os.environ.get('TURSO_DB_URL')       # e.g. libsql://ksync-yourname.turso.io
TURSO_TOKEN = os.environ.get('TURSO_AUTH_TOKEN')
USE_TURSO   = bool(TURSO_URL and TURSO_TOKEN)

if USE_TURSO:
    import libsql
else:
    import sqlite3
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get('cinema_SECRET', 'ksync-stable-secret-key-2026-do-not-share')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

# Local SQLite path — only used when Turso env vars are NOT set
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'instance', 'ksync.db'))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

SEAT_LOCKS = {}   # { "showtime:row:col": {user_id, expires} }
LOCK_DURATION = 300  # seconds

# Paystack keys — replace with your own from dashboard.paystack.com
# These are Paystack's official test keys (safe to use for demos)
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', 'sk_test_527e7adc01bbe74b54897f6b58cf1555e683ccaf')
PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC_KEY', 'pk_test_b695c7bf0b597ddebfe2f70ff4aa73927dd1d1de')

# ── DB Helpers ────────────────────────────────────────────────────────────────
#
#  TursoRow wraps plain tuple rows from libsql so that row['col'] keeps
#  working everywhere — libsql does not support sqlite3's row_factory.
#
class TursoRow:
    def __init__(self, description, values):
        self._keys   = [d[0] for d in description]
        self._values = list(values)
        self._map    = dict(zip(self._keys, self._values))
    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._map[key]
    def __iter__(self):        return iter(self._values)
    def keys(self):            return self._keys
    def get(self, k, d=None):  return self._map.get(k, d)
    def __repr__(self):        return str(self._map)

def _wrap(cursor, rows):
    if not rows or cursor.description is None: return rows
    return [TursoRow(cursor.description, r) for r in rows]


def get_db():
    """One connection per request, stored on Flask g."""
    if '_db' not in g:
        if USE_TURSO:
            # CHANGE TO:
            g._db = libsql.connect(
    TURSO_URL,
    auth_token = TURSO_TOKEN,
          )

            g._db.sync()   # pull latest data from Turso on open
        else:
            g._db = sqlite3.connect(DB_PATH)
            g._db.row_factory = sqlite3.Row
            g._db.execute("PRAGMA journal_mode=DELETE")
            g._db.execute("PRAGMA foreign_keys=ON")
    return g._db


@app.teardown_appcontext
def close_db(e):
    db = g.pop('_db', None)
    if db: db.close()


def qdb(sql, args=(), one=False):
    db  = get_db()
    cur = db.execute(sql, args)
    raw = cur.fetchall()
    rows = _wrap(cur, raw) if USE_TURSO else raw
    return (rows[0] if rows else None) if one else rows


def xdb(sql, args=()):
    db  = get_db()
    cur = db.execute(sql, args)
    db.commit()
    if USE_TURSO: db.sync()   # push write to Turso cloud immediately
    return cur.lastrowid


#  SCORE 1 — OBJECTIVE SEAT QUALITY  (Q_obj)
#
#  Based on:
#   • SMPTE EG 18-1994  — optimal viewing distance 1.5–2× screen width
#   • THX reference-seat spec — ~55–65 % back, horizontally centred
#   • ITU-R BT.2022     — horizontal viewing angle must stay ≤ 30°
#   • Dolby Atmos design guide — surround sweet-spot = central middle rows
#   • Visual ergonomics  — neck tilt > 35° causes fatigue (front-row penalty)
#
#  This score belongs to the SEAT, not the user.
#  It is calculated ONCE at showtime creation and stored in seats.quality_score.
#  It is identical for every user who looks at the same seat.

#  SCORE 1 — OBJECTIVE SEAT QUALITY  (Q_obj)
#
#  Derived from SMPTE EG-18-1994 (Society of Motion Picture and
#  Television Engineers) viewing angle specifications:
#
#   • Optimal horizontal viewing angle: 40–50 degrees
#     → seats in the zone 40–65% back from the screen score highest
#   • Minimum horizontal angle (farthest seat): 30 degrees
#     → seats beyond 85% back fall below this threshold and are penalised
#   • Vertical discomfort threshold: 35 degrees
#     → seats in the front 20% of rows exceed this and are penalised
#   • Horizontal centre constraint: ITU-R BT.2022 ≤ 30° lateral angle
#     → seats in the outer 20% of columns on either side are penalised
#
#  This score belongs to the SEAT, not the user.
#  It is calculated ONCE at showtime creation and stored in seats.quality_score.
#  It is identical for every user who looks at the same seat.
# ═══════════════════════════════════════════════════════════════════
def compute_seat_quality(row, col, total_rows, total_cols):
    
    rn = row / total_rows   # normalised: 0.0 = front row, 1.0 = back row
    cn = col / total_cols   # normalised: 0.0 = far left,  1.0 = far right

    # ── ROW SCORE ──────────────────────────────────────────────────
    # SMPTE optimal zone: 40–65% back. Midpoint = 0.525
    # Score = 1.0 at optimum, decays toward 0.0 at the boundaries (0.0 and 1.0)
    opt_r = 0.525
    if rn <= opt_r:
        # Front half: linear decay from optimum to front wall
        rs = rn / opt_r
    else:
        # Back half: linear decay from optimum to back wall
        rs = (1.0 - rn) / (1.0 - opt_r)

    # SMPTE EG-18-1994: vertical angle > 35 degrees causes physical discomfort
    # This threshold maps to approximately the front 20% of rows
    if rn < 0.20:
        rs *= 0.40   # severe penalty — viewer must tilt neck beyond 35 degrees

    # SMPTE minimum 30-degree horizontal angle violated beyond 85% back
    if rn > 0.85:
        rs *= 0.70   # moderate penalty — screen subtends too small an angle

    # ── COLUMN SCORE ───────────────────────────────────────────────
    # ITU-R BT.2022 / SMPTE: lateral angle must stay ≤ 30 degrees from centre
    # Dead centre (0.5) = perfect. Outer 20% each side exceeds acceptable angle.
    opt_c = 0.50
    cd = abs(cn - opt_c)   # distance from centre: 0.0 (centre) to 0.5 (edge)

    # Linear decay: 1.0 at centre, 0.0 at the absolute edge (cd = 0.5)
    cs = 1.0 - (cd / 0.50)

    # Extra penalty for seats in the outer 20% — ITU-R lateral angle violation
    if cd > 0.30:
        cs *= 0.60

    #  COMBINE AND SCALE
    # Row: 60% weight  |  Column: 40% weight  (SMPTE longitudinal emphasis)
    q = (rs * 0.60 + cs * 0.40) * 10.0
    return round(min(max(q, 0.5), 10.0), 2)


def classify_seat(row, col, total_rows, total_cols):
    """
    Assign two categorical position tags to a seat.
    These tags are used by the preference-matching engine (Score 2)
    to compare a seat's position against what the user prefers.

    Lateral  : center | aisle | edge
    Longitudinal: front | middle | back
    """
    tags = []
    cr = col / total_cols
    rr = row / total_rows
    if   cr < 0.15 or cr > 0.85:  tags.append('edge')
    elif cr < 0.28 or cr > 0.72:  tags.append('aisle')
    else:                          tags.append('center')
    if   rr < 0.30:  tags.append('front')
    elif rr > 0.68:  tags.append('back')
    else:            tags.append('middle')
    return tags



#  SCORE 2 SUBJECTIVE PREFERENCE MATCH  (Q_pref)
#
#  This score belongs to the USER, not the seat.
#  It is computed live during recommendations and seat selection.
#  Two users looking at the same seat will receive DIFFERENT Q_pref scores.
#
#  It compares the seat's position tags and objective quality against
#  three preference dimensions learned from the user's booking history:
#    • seat_position_pref  — do they prefer center / aisle / edge?
#    • seat_zone_pref      — do they prefer front / middle / back?
#    • avg_quality_pref    — what objective quality level do they usually choose?
#
#  Weights:  position match 40 %  |  zone match 30 %  |  quality proximity 30 %
# ═══════════════════════════════════════════════════════════════════
def seat_pref_match(available_seats, prefs):
    """
    Score how well the best available seat matches this user's learned preferences.
    Returns (best_pref_score 0–1, obj_quality_of_that_seat 0–10).

    Called during recommendation scoring; Q_pref is kept completely separate
    from Q_obj so the two contributions to the final ranking are transparent.
    """
    if not available_seats:
        return 0.0, 0.0

    pos_pref   = prefs.get('seat_position_pref', 'center')
    zone_pref  = prefs.get('seat_zone_pref',     'middle')
    avg_q_pref = float(prefs.get('avg_quality_pref', 7.0))

    best_pref_score = 0.0
    best_obj_q      = 0.0

    for s in available_seats:
        tags  = json.loads(s['position_tags']) if s['position_tags'] else []
        q_obj = float(s['quality_score'])   # ← this is Q_obj, already computed

        # --- Position match (lateral: center / aisle / edge) ---
        if   pos_pref in tags:   pm = 1.0   # exact match
        elif 'aisle' in tags:    pm = 0.5   # partial match (aisle is middle ground)
        else:                    pm = 0.2   # mismatch

        # --- Zone match (longitudinal: front / middle / back) ---
        zm = 1.0 if zone_pref in tags else 0.35

        # --- Quality proximity (how close is Q_obj to the user's usual choice?) ---
        qm = max(0.0, 1.0 - abs(q_obj - avg_q_pref) / 10.0)

        # Combined preference score for this seat (weighted sum)
        pref_score = pm * 0.40 + zm * 0.30 + qm * 0.30

        if pref_score > best_pref_score:
            best_pref_score = pref_score
            best_obj_q      = q_obj

    return round(best_pref_score, 4), round(best_obj_q, 2)

#  Recommendation Engine

def get_prefs(user_id):
    p = qdb("SELECT * FROM user_preferences WHERE user_id=?", (user_id,), one=True)
    return dict(p) if p else {
        'genre_weights':'{}','seat_position_pref':'center',
        'seat_zone_pref':'middle','avg_quality_pref':7.0,'booking_count':0}


def split_genres(genre_string):
    """
    Split a compound genre string into individual tokens.
    e.g. 'Action · Comedy · Superhero' → ['Action', 'Comedy', 'Superhero']
    Used everywhere we need to compare genres so that Jaccard similarity
    works on individual genre tokens, not useless compound strings.
    """
    return [g.strip() for g in genre_string.split('·') if g.strip()]


def jaccard(a, b):
    if not a and not b: return 0.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def collab_score(user_id, movie_id):
    """
    Jaccard-based collaborative filtering on genre booking history.
    FIX: Now tokenises compound genre strings ('Action · Comedy') into
    individual genres before computing similarity, so that partial genre
    overlaps are detected correctly instead of requiring exact compound matches.
    """
    # Build this user's genre set from confirmed booking history
    raw_user = qdb(
        """SELECT DISTINCT m.genre FROM bookings b
           JOIN showtimes s ON b.showtime_id=s.id JOIN movies m ON s.movie_id=m.id
           WHERE b.user_id=? AND b.status='confirmed'""", (user_id,))
    user_genres = set(g for r in raw_user for g in split_genres(r['genre']))

    tgt = qdb("SELECT genre FROM movies WHERE id=?", (movie_id,), one=True)
    if not tgt: return 0.0
    tgt_genres = set(split_genres(tgt['genre']))

    # Find peer users who have booked ANY movie sharing at least one genre token
    # with the target movie. This is broader than the old exact-string match.
    all_other_users = qdb(
        """SELECT DISTINCT b.user_id FROM bookings b
           JOIN showtimes s ON b.showtime_id=s.id JOIN movies m ON s.movie_id=m.id
           WHERE b.user_id!=? AND b.status='confirmed'""", (user_id,))

    sims = []
    for o in all_other_users:
        raw_peer = qdb(
            """SELECT DISTINCT m.genre FROM bookings b
               JOIN showtimes s ON b.showtime_id=s.id JOIN movies m ON s.movie_id=m.id
               WHERE b.user_id=? AND b.status='confirmed'""", (o['user_id'],))
        peer_genres = set(g for r in raw_peer for g in split_genres(r['genre']))
        # Only include peers who share at least one genre with the target movie
        if peer_genres & tgt_genres:
            sims.append(jaccard(user_genres, peer_genres))

    return min(sum(sims)/len(sims)*1.5, 1.0) if sims else 0.0




def recommend(user_id, limit=12):
    """
    Full hybrid recommendation combining:
    1. Content-based (genre weights from booking history)
    2. Collaborative (Jaccard similarity)
    3. Context-aware: seat quality, seat preference match, showtime proximity, availability
    4. Rating bonus
    """
    prefs = get_prefs(user_id)
    gw = json.loads(prefs.get('genre_weights', '{}'))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    showtimes = qdb(
        """SELECT s.*, m.title, m.genre, m.rating, m.description,
                  m.poster_url, m.duration_min, m.director, m.cast_list,
                  m.id as movie_id
           FROM showtimes s JOIN movies m ON s.movie_id=m.id
           WHERE s.showtime>=? AND m.is_active=1 ORDER BY s.showtime""", (now,))

    scored = {}
    for st in showtimes:
        mid, sid = st['movie_id'], st['id']
        avail = qdb("SELECT * FROM seats WHERE showtime_id=? AND status='available'", (sid,))
        total = qdb("SELECT COUNT(*) as c FROM seats WHERE showtime_id=?", (sid,), one=True)['c']
        if total == 0: continue
        avail_ratio = len(avail) / total
        if avail_ratio == 0: continue

        # 1. Content-based genre score
        # FIX: tokenise the compound genre string and take the MAX weight
        # across all individual genres the movie belongs to.
        # e.g. 'Action · Comedy' checks both 'Action' and 'Comedy' weights.
        movie_genres = split_genres(st['genre'])
        genre_s = min(max((gw.get(g, 0.0) for g in movie_genres), default=0.0), 1.0)

        #  2. Collaborative
        collab  = collab_score(user_id, mid)

        #  3a. Subjective seat preference match
        pref_s, best_q = seat_pref_match(avail, prefs)

        # 3b. Objective seat quality (avg of available seats)
        avg_q = sum(float(s['quality_score']) for s in avail) / len(avail) if avail else 0
        quality_s = avg_q / 10.0

        # 3c. Availability
        avail_s = min(avail_ratio * 1.2, 1.0)

        # 3d. Showtime proximity (2–12 hrs = ideal)
        try:
            hrs = (datetime.strptime(st['showtime'], '%Y-%m-%d %H:%M:%S') - datetime.now()).total_seconds()/3600
            if 2 <= hrs <= 12:    time_s = 1.0
            elif hrs < 2:         time_s = 0.25
            else:                 time_s = max(0.2, 1-(hrs-12)/72)
        except: time_s = 0.5

        # 4. Rating bonus
        rating_s = float(st['rating'] or 0) / 10.0

        #  Weighted final score
        # For new users with no history, genre_s and collab will be 0.
        # Shift weight to rating + quality so results still rank meaningfully.
        has_history = bool(gw)
        if has_history:
            W = dict(genre=0.20, collab=0.15, pref=0.20, quality=0.15,
                     avail=0.10, time=0.10, rating=0.10)
        else:
            W = dict(genre=0.0, collab=0.0, pref=0.0, quality=0.35,
                     avail=0.20, time=0.15, rating=0.30)
        score = (W['genre']*genre_s + W['collab']*collab + W['pref']*pref_s
                 + W['quality']*quality_s + W['avail']*avail_s
                 + W['time']*time_s + W['rating']*rating_s)

        # Penalise if best seat is very poor quality or almost sold out
        if best_q < 3.0:         score *= 0.65
        if avail_ratio < 0.05:   score *= 0.55

        if mid not in scored or scored[mid]['score'] < score:
            scored[mid] = {
                'movie_id': mid, 'showtime_id': sid,
                'title': st['title'], 'genre': st['genre'],
                'rating': st['rating'], 'description': st['description'],
                'poster_url': st['poster_url'], 'duration_min': st['duration_min'],
                'director': st['director'], 'cast_list': st['cast_list'],
                'showtime': st['showtime'], 'hall_name': st['hall_name'],
                'cinema_name': st['cinema_name'], 'price': st['price'],
                'score': round(score, 4), 'available_seats': len(avail),
                'total_seats': total, 'best_quality': round(best_q, 1),
                'pref_match': round(pref_s*100), 'avail_pct': round(avail_ratio*100),
            }
    return sorted(scored.values(), key=lambda x: x['score'], reverse=True)[:limit]


def learn_preferences(user_id, movie_id, seat_id, db=None):
    """Implicit preference learning after each confirmed booking."""
    own_db = db is None
    if own_db:
        db = get_db()
    
    movie = db.execute("SELECT genre FROM movies WHERE id=?", (movie_id,)).fetchone()
    seat  = db.execute("SELECT * FROM seats WHERE id=?", (seat_id,)).fetchone()
    if not movie or not seat: return
    
    prefs = db.execute("SELECT * FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
    tags = json.loads(seat['position_tags']) if seat['position_tags'] else []
    new_pos  = 'center' if 'center' in tags else ('aisle' if 'aisle' in tags else 'edge')
    new_zone = 'middle' if 'middle' in tags else ('front' if 'front' in tags else 'back')

    if prefs:
        gw = json.loads(prefs['genre_weights'])
        alpha = 0.3
        gw[movie['genre']] = round(alpha + (1 - alpha) * gw.get(movie['genre'], 0.0), 4)
        beta = 0.25
        new_avg_q = (1 - beta) * float(prefs['avg_quality_pref']) + beta * float(seat['quality_score'])
        db.execute(
            """UPDATE user_preferences SET genre_weights=?, seat_position_pref=?,
               seat_zone_pref=?, avg_quality_pref=?, booking_count=booking_count+1
               WHERE user_id=?""",
            (json.dumps(gw), new_pos, new_zone, round(new_avg_q, 2), user_id))
    else:
        gw = {movie['genre']: 0.5}
        db.execute(
            """INSERT INTO user_preferences
               (user_id, genre_weights, seat_position_pref, seat_zone_pref, avg_quality_pref, booking_count)
               VALUES (?,?,?,?,?,?)""",
            (user_id, json.dumps(gw), new_pos, new_zone, float(seat['quality_score']), 1))
    
    if own_db:
        db.commit()
        if USE_TURSO: db.sync()

#  Auth Helpers

def hash_pw(pw):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 100000)
    return f"{salt}:{h.hex()}"


def verify_pw(stored, given):
    try:
        salt, h = stored.split(':')
        return hashlib.pbkdf2_hmac('sha256', given.encode(), salt.encode(), 100000).hex() == h
    except: return False


def auth_required(f):
    @wraps(f)
    def d(*a, **kw):
        if 'user_id' not in session: return jsonify({'error':'Login required'}), 401
        return f(*a, **kw)
    return d


def admin_required(f):
    @wraps(f)
    def d(*a, **kw):
        if not session.get('is_admin'): return jsonify({'error':'Admins only'}), 403
        return f(*a, **kw)
    return d

#  Database Init


def init_db():
    if USE_TURSO:
        db = libsql.connect(
    TURSO_URL,
    auth_token = TURSO_TOKEN,
        )
        
        db.sync()
    else:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS email_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL,
        verified INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, genre TEXT NOT NULL, description TEXT,
        duration_min INTEGER, rating REAL DEFAULT 0,
        poster_url TEXT, director TEXT, cast_list TEXT,
        release_year INTEGER, is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS cinemas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, location TEXT NOT NULL, address TEXT
    );
    CREATE TABLE IF NOT EXISTS halls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cinema_id INTEGER NOT NULL, name TEXT NOT NULL,
        total_rows INTEGER NOT NULL, total_cols INTEGER NOT NULL,
        capacity INTEGER NOT NULL,
        FOREIGN KEY (cinema_id) REFERENCES cinemas(id)
    );
    CREATE TABLE IF NOT EXISTS showtimes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movie_id INTEGER NOT NULL, hall_id INTEGER NOT NULL,
        hall_name TEXT NOT NULL, cinema_name TEXT NOT NULL,
        showtime TIMESTAMP NOT NULL, price REAL NOT NULL,
        FOREIGN KEY (movie_id) REFERENCES movies(id),
        FOREIGN KEY (hall_id) REFERENCES halls(id)
    );
    CREATE TABLE IF NOT EXISTS seats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        showtime_id INTEGER NOT NULL, hall_id INTEGER NOT NULL,
        row_num INTEGER NOT NULL, col_num INTEGER NOT NULL,
        row_label TEXT NOT NULL, seat_number INTEGER NOT NULL,
        quality_score REAL NOT NULL, position_tags TEXT,
        status TEXT DEFAULT 'available',
        locked_by INTEGER, locked_until TIMESTAMP,
        FOREIGN KEY (showtime_id) REFERENCES showtimes(id)
    );
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, showtime_id INTEGER NOT NULL,
        seat_id INTEGER NOT NULL, amount REAL NOT NULL,
        status TEXT DEFAULT 'pending',
        payment_ref TEXT, paystack_ref TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (showtime_id) REFERENCES showtimes(id),
        FOREIGN KEY (seat_id) REFERENCES seats(id)
    );
    CREATE TABLE IF NOT EXISTS user_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        genre_weights TEXT DEFAULT '{}',
        seat_position_pref TEXT DEFAULT 'center',
        seat_zone_pref TEXT DEFAULT 'middle',
        avg_quality_pref REAL DEFAULT 7.0,
        booking_count INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """)
    db.commit()
    if db.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0:
        seed(db)
    db.close()


def seed(db):
    import random; random.seed(99)

    #  Movies data (from TMDB)
    TMDB = "https://image.tmdb.org/t/p/w500"
    movies = [
        
        ("Deadpool & Wolverine", "Action · Comedy · Superhero",
         "The MCU's most unlikely duo team up to save the multiverse in this irreverent, R-rated adventure.",
         127, 8.1, "English", "2024-07-26",
         "https://image.tmdb.org/t/p/w500/8cdWjvZQUExUUTzyp4t6EDMubfO.jpg",
         "#8B1A1A", 5500, 1, 1, '["Ryan Reynolds","Hugh Jackman","Emma Corrin"]', "Shawn Levy", "R"),

        ("Inside Out 2", "Animation · Family · Comedy",
         "Riley enters her teenage years and new emotions join the team inside her head.",
         100, 7.9, "English", "2024-06-14",
         "https://image.tmdb.org/t/p/w500/vpnVM9B6NMmQpWeZvzLvDESb2QY.jpg",
         "#FF6B35", 4500, 1, 1, '["Amy Poehler","Maya Hawke","Kensington Tallman"]', "Kelsey Mann", "PG"),

        ("Alien: Romulus", "Horror · Sci-Fi · Thriller",
         "Young colonizers face the most terrifying life form in the universe aboard an abandoned space station.",
         119, 7.2, "English", "2024-08-16",
         "https://image.tmdb.org/t/p/w500/b33nnKl1GSFbao4l3fZDDqsMx0F.jpg",
         "#1a2a1a", 4000, 0, 1, '["Cailee Spaeny","David Jonsson","Archie Renaux"]', "Fede Álvarez", "R"),

        ("Twisters", "Action · Adventure · Thriller",
         "Storm chasers pursue devastating tornadoes across Oklahoma in this high-octane spectacle.",
         122, 7.1, "English", "2024-07-19",
         "https://image.tmdb.org/t/p/w500/pjnD08FlMAIXsfOLKQbvmO0f0MD.jpg",
         "#1a3a4a", 4500, 0, 0, '["Daisy Edgar-Jones","Glen Powell","Anthony Ramos"]', "Lee Isaac Chung", "PG-13"),

        ("The Wild Robot", "Animation · Family · Drama",
         "A robot shipwrecked on an uninhabited island must adapt and form bonds with native animals.",
         102, 8.3, "English", "2024-09-27",
         "https://image.tmdb.org/t/p/w500/wTnV3PCVW5O92JMrFvvrRcV39RU.jpg",
         "#2d4a2d", 4500, 1, 0, '["Lupita Nyongo","Pedro Pascal","Kit Connor"]', "Chris Sanders", "PG"),

        ("Wicked", "Musical · Drama · Fantasy",
         "The untold story of the witches of Oz — Elphaba and Glinda's unlikely friendship.",
         160, 8.0, "English", "2024-11-22",
         "https://image.tmdb.org/t/p/w500/c5UPPqLwHBmSiK9vS6hiSnDXDNR.jpg",
         "#2d1a3a", 5500, 1, 1, '["Cynthia Erivo","Ariana Grande","Jonathan Bailey"]', "Jon M. Chu", "PG"),

        ("Gladiator II", "Action · Drama · History",
         "Lucius is forced into the Colosseum after his home is conquered by tyrannical Roman emperors.",
         148, 7.4, "English", "2024-11-22",
         "https://image.tmdb.org/t/p/w500/2cxhvwyEwRlysAmRH4iodkvo0z5.jpg",
         "#3a2a1a", 5000, 1, 1, '["Paul Mescal","Denzel Washington","Pedro Pascal"]', "Ridley Scott", "R"),

        ("Moana 2", "Animation · Family · Adventure",
         "Moana journeys to the far seas of Oceania and into dangerous, long-lost waters.",
         100, 7.0, "English", "2024-11-27",
         "https://image.tmdb.org/t/p/w500/4YZpsylmjHbqeWzjKpUEF8gcLNW.jpg",
         "#0a2a3a", 4500, 0, 1, '["Auli Cravalho","Dwayne Johnson","Alan Tudyk"]', "Dana Ledoux Miller", "PG"),

        ("A Quiet Place: Day One", "Horror · Sci-Fi · Thriller",
         "Experience the day the deadly creatures first arrived on Earth and New York City fell silent.",
         99, 6.9, "English", "2024-06-28",
         "https://image.tmdb.org/t/p/w500/yrpPYKijwdMHyTGIOd1iK1h0Wb6.jpg",
         "#1a0a0a", 4000, 0, 0, '["Lupita Nyongo","Joseph Quinn","Alex Wolff"]', "Michael Sarnoski", "PG-13"),

        ("Kingdom of the Planet of the Apes", "Sci-Fi · Action · Adventure",
         "A young ape embarks on a journey that causes him to question everything he has been taught.",
         145, 7.1, "English", "2024-05-10",
         "https://image.tmdb.org/t/p/w500/gKkl37BQuKTanygYQG1pyYgLVgf.jpg",
         "#2a1a0a", 4500, 0, 0, '["Owen Teague","Freya Allan","Kevin Durand"]', "Wes Ball", "PG-13"),

        ("Despicable Me 4", "Animation · Comedy · Family",
         "Gru and his family are forced on the run after he makes a powerful new enemy.",
         95, 6.8, "English", "2024-07-03",
         "https://image.tmdb.org/t/p/w500/wWba3TaojhK7NdycyUk0dna3Pra.jpg",
         "#f5a623", 4000, 0, 0, '["Steve Carell","Kristen Wiig","Will Ferrell"]', "Chris Renaud", "PG"),

        ("Bad Boys: Ride or Die", "Action · Comedy · Crime",
         "Miami detectives Lowrey and Burnett become outlaws when the Miami PD is threatened.",
         115, 7.0, "English", "2024-06-07",
         "https://image.tmdb.org/t/p/w500/oGythE98MYleE6mZlGs5oBGkux1.jpg",
         "#1a1a0a", 4500, 0, 1, '["Will Smith","Martin Lawrence","Vanessa Hudgens"]', "Adil El Arbi", "R"),

        # ROMANCE
        ("The Notebook", "Romance · Drama",
         "A poor young man falls in love with a rich girl in 1940s South Carolina, but her parents disapprove. Years later their love story is read to a woman with dementia.",
         123, 7.9, "English", "2004-06-25",
         "https://image.tmdb.org/t/p/w500/qom1SZSENdmHFNZBXbtLAGselQQ.jpg",
         "#1a1a2e", 2500, 0, 1,
         '["Ryan Gosling","Rachel McAdams","James Garner"]', "Nick Cassavetes", "PG-13"),

        ("Crazy Rich Asians", "Romance · Comedy · Drama",
         "A New York professor discovers her boyfriend is from one of Singapore's wealthiest families when she accompanies him to a wedding.",
         120, 6.9, "English", "2018-08-15",
         "https://image.tmdb.org/t/p/w500/lhkzVhXVfEkNxluTkSRfZPAXFHs.jpg",
         "#1a0a2e", 2500, 0, 1,
         '["Constance Wu","Henry Golding","Michelle Yeoh"]', "Jon M. Chu", "PG-13"),

        ("La La Land", "Romance · Musical · Drama",
         "A jazz pianist and an aspiring actress fall in love while chasing their dreams in Los Angeles, testing whether love and ambition can coexist.",
         128, 8.0, "English", "2016-12-09",
         "https://image.tmdb.org/t/p/w500/uDO8zWDhfWwoFdKS4fzkUJt0Rf0.jpg",
         "#0a1a3e", 2500, 1, 0,
         '["Ryan Gosling","Emma Stone","John Legend"]', "Damien Chazelle", "PG-13"),

        ("Me Before You", "Romance · Drama",
         "A small-town woman takes a job caring for a paralysed man and the two develop an unexpected bond that changes both their lives forever.",
         110, 7.4, "English", "2016-06-03",
         "https://image.tmdb.org/t/p/w500/qGABQGMAPzSuQk2eLhlpEjqJPuB.jpg",
         "#2a0a1e", 2500, 0, 0,
         '["Emilia Clarke","Sam Claflin","Janet McTeer"]', "Thea Sharrock", "PG-13"),

        ("A Walk to Remember", "Romance · Drama",
         "A popular teenager falls in love with a minister's daughter battling a serious illness, transforming his priorities completely.",
         102, 7.4, "English", "2002-01-25",
         "https://image.tmdb.org/t/p/w500/4aJoO4GtCMxFMNQcJVvXy2tGTVk.jpg",
         "#1a0a0e", 2500, 0, 0,
         '["Mandy Moore","Shane West","Peter Coyote"]', "Adam Shankman", "PG"),

        ("Pride and Prejudice", "Romance · Drama · Period",
         "Spirited Elizabeth Bennet meets the proud Mr Darcy in 19th-century England and both must overcome their prejudices to find love.",
         129, 7.8, "English", "2005-11-11",
         "https://image.tmdb.org/t/p/w500/bPuCBDTVgMIUjSGcMOsOkEjsJhd.jpg",
         "#2a1a0e", 2500, 0, 0,
         '["Keira Knightley","Matthew Macfadyen","Judi Dench"]', "Joe Wright", "PG"),

        ("About Time", "Romance · Drama · Fantasy",
         "A young man who can travel back in time uses the ability to improve his love life but learns the best moments are worth living only once.",
         123, 7.8, "English", "2013-11-01",
         "https://image.tmdb.org/t/p/w500/zBHkpVFb9lkzAMgFGQqGFFlEh2k.jpg",
         "#0a2a1e", 2500, 0, 1,
         '["Domhnall Gleeson","Rachel McAdams","Bill Nighy"]', "Richard Curtis", "R"),

        ("The Proposal", "Romance · Comedy",
         "A Canadian book editor facing deportation convinces her assistant to marry her. They travel to Alaska to meet his family and feelings complicate the arrangement.",
         108, 6.7, "English", "2009-06-19",
         "https://image.tmdb.org/t/p/w500/nGwivQGqMhANMGJlSiCiMrfZDSC.jpg",
         "#1a2a2e", 2500, 0, 0,
         '["Sandra Bullock","Ryan Reynolds","Betty White"]', "Anne Fletcher", "PG-13"),

        ("Five Feet Apart", "Romance · Drama",
         "Two teenagers with cystic fibrosis fall in love in a hospital but their condition means they must always remain five feet apart.",
         116, 7.2, "English", "2019-03-15",
         "https://image.tmdb.org/t/p/w500/2Ah63TIvVmZM3hzUwR5hXFg2LEk.jpg",
         "#0a1a2e", 2500, 0, 1,
         '["Cole Sprouse","Haley Lu Richardson","Moises Arias"]', "Justin Baldoni", "PG-13"),

        ("To All the Boys I've Loved Before", "Romance · Comedy · Drama",
         "A high schooler's secret love letters are accidentally sent to all her crushes, forcing her into a fake relationship that slowly becomes real.",
         99, 7.1, "English", "2018-08-17",
         "https://image.tmdb.org/t/p/w500/sAPSAZhfKnZCluBqaB5pgE7dRJF.jpg",
         "#2a1a3e", 2500, 0, 0,
         '["Lana Condor","Noah Centineo","Janel Parrish"]', "Susan Johnson", "PG"),

        ("Titanic", "Romance · Drama · History",
         "A young aristocrat falls in love with a penniless artist aboard the ill-fated RMS Titanic on its doomed maiden voyage.",
         194, 7.9, "English", "1997-12-19",
         "https://image.tmdb.org/t/p/w500/9xjZS2rlVxm8SFx8kPC3aIGCOYQ.jpg",
         "#0a0a3e", 3500, 1, 0,
         '["Leonardo DiCaprio","Kate Winslet","Billy Zane"]', "James Cameron", "PG-13"),

        ("Your Name", "Romance · Animation · Fantasy",
         "Two teenagers in Japan mysteriously begin swapping bodies and must find each other before a catastrophic event tears them apart forever.",
         106, 8.4, "Japanese", "2016-08-26",
         "https://image.tmdb.org/t/p/w500/q719jXXEzOoYaps6babgKnONONX.jpg",
         "#1a0a3e", 3000, 0, 1,
         '["Ryunosuke Kamiki","Mone Kamishiraishi"]', "Makoto Shinkai", "PG"),

        # NOLLYWOOD
        ("A Tribe Called Judah", "Drama · Crime · Nollywood",
         "A determined mother raises five sons in Lagos against poverty and hardship, only to watch her children drift toward very different fates as adults.",
         135, 7.2, "Yoruba/English", "2023-12-15",
         "https://image.tmdb.org/t/p/w500/placeholder.jpg",
         "#1a2a0e", 2500, 1, 1,
         '["Funke Akindele","Timini Egbuson","Broda Shaggi"]', "Funke Akindele", "PG-13"),

        ("The Black Book", "Action · Thriller · Nollywood",
         "A deacon seeks revenge after his son is killed by corrupt police, uncovering a conspiracy that reaches the highest levels of power.",
         130, 7.0, "English", "2023-09-22",
         "https://image.tmdb.org/t/p/w500/placeholder2.jpg",
         "#0a0a1e", 2500, 0, 1,
         '["Richard Mofe-Damijo","Sam Dede","Ireti Doyle"]', "Editi Effiong", "PG-13"),

        # SCI-FI / ACTION
        ("Dune Part Two", "Sci-Fi · Action · Adventure",
         "Paul Atreides unites with the Fremen to wage war against those who destroyed his family, fulfilling a dangerous ancient prophecy.",
         166, 8.5, "English", "2024-03-01",
         "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
         "#1a1a0e", 5500, 1, 1,
         '["Timothée Chalamet","Zendaya","Rebecca Ferguson"]', "Denis Villeneuve", "PG-13"),

        ("Interstellar", "Sci-Fi · Drama · Adventure",
         "A team of explorers travel through a wormhole in space to ensure humanity's survival as Earth faces extinction.",
         169, 8.7, "English", "2014-11-07",
         "https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg",
         "#0a0a2e", 3500, 1, 0,
         '["Matthew McConaughey","Anne Hathaway","Jessica Chastain"]', "Christopher Nolan", "PG-13"),
    ]
    
    # Insert movies (extracting only needed fields from 15-element tuples)
    for m in movies:
        # Extract: title, genre, description, duration_min, rating, poster_url, director, cast_list, release_year
        movie_data = (m[0], m[1], m[2], m[3], m[4], m[7], m[13], m[12], m[6])
        db.execute(
            """INSERT INTO movies (title,genre,description,duration_min,rating,
               poster_url,director,cast_list,release_year) VALUES (?,?,?,?,?,?,?,?,?)""", movie_data)

    # Real Abuja cinemas
    cinemas_data = [
        ("Silverbird Cinemas",   "Central Business District", "Silverbird Entertainment Centre, Herbert Macaulay Way, CBD, Abuja"),
        ("Genesis Cinemas",      "Ceddi Plaza, Central Area", "Ceddi Plaza, Michael Okpara Way, Wuse Zone 5, Abuja"),
        ("Ozone Cinemas",        "Jabi Lake Mall",            "Jabi Lake Mall, Jabi, Abuja"),
        ("FilmHouse Cinemas",    "Lugbe Dunamis Mall",        "Dunamis HQ, Airport Road, Lugbe, Abuja"),
    ]
    cinema_ids = []
    for c in cinemas_data:
        cur = db.execute("INSERT INTO cinemas (name,location,address) VALUES (?,?,?)", c)
        cinema_ids.append(cur.lastrowid)

    # Halls per cinema
    hall_configs = [
        # (cinema_idx, hall_name, rows, cols)
        (0, "Hall 1 – Main",    12, 16),
        (0, "Hall 2 – Premium", 10, 14),
        (1, "Screen A",         10, 12),
        (1, "Screen B",          8, 10),
        (2, "Ozone Main",       12, 18),
        (2, "Ozone VIP",         8, 10),
        (3, "FilmHouse Gold",   10, 14),
        (3, "FilmHouse Silver",  8, 12),
    ]
    hall_ids = []
    for ci, hname, rows, cols in hall_configs:
        cur = db.execute(
            """INSERT INTO halls (cinema_id,name,total_rows,total_cols,capacity)
               VALUES (?,?,?,?,?)""",
            (cinema_ids[ci], hname, rows, cols, rows*cols))
        hall_ids.append((cur.lastrowid, ci, hname, rows, cols))

    movie_ids = [r[0] for r in db.execute("SELECT id FROM movies").fetchall()]
    cinema_names = [c[0] for c in cinemas_data]

    # Showtimes: next 5 days, multiple slots
    base    = datetime.now().replace(minute=0, second=0, microsecond=0)
    times   = [10, 13, 16, 19, 22]
    prices  = [2500, 3000, 2000, 2000, 3500, 4000, 2500, 2000]  # per hall

    RL = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    showtime_records = []
    for day in range(5):
        for t in times:
            hall_info = random.choice(hall_ids)
            hid, ci, hname, rows, cols = hall_info
            mid  = random.choice(movie_ids)
            show_dt = (base + timedelta(days=day)).replace(hour=t)
            price = prices[hall_ids.index(hall_info)]
            cname = cinema_names[ci]
            cur = db.execute(
                """INSERT INTO showtimes
                   (movie_id,hall_id,hall_name,cinema_name,showtime,price)
                   VALUES (?,?,?,?,?,?)""",
                (mid, hid, hname, cname,
                 show_dt.strftime('%Y-%m-%d %H:%M:%S'), price))
            showtime_records.append((cur.lastrowid, hid, rows, cols))

    #  Seats for each showtime
    for stid, hid, rows, cols in showtime_records:
        raw_hall = db.execute("SELECT * FROM halls WHERE id=?", (hid,)).fetchone()
        # wrap for Turso compatibility (plain tuple → dict-like row)
        cur_desc = db.execute("PRAGMA table_info(halls)").fetchall()
        hall = raw_hall  # already a sqlite3.Row locally; TursoRow not needed here
        # use the rows/cols we already have from hall_configs instead
        tr, tc = rows, cols
        for r in range(1, tr+1):
            for c in range(1, tc+1):
                q    = compute_seat_quality(r, c, tr, tc)
                tags = classify_seat(r, c, tr, tc)
                # Pre-book ~25% of seats randomly
                status = 'booked' if random.random() < 0.25 else 'available'
                db.execute(
                    """INSERT INTO seats
                       (showtime_id,hall_id,row_num,col_num,row_label,
                        seat_number,quality_score,position_tags,status)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (stid, hid, r, c, RL[r-1], c, q,
                     json.dumps(tags), status))

    # Admin + demo users (pre-verified so they work without the email flow)
    db.execute("INSERT OR IGNORE INTO users (username,email,password_hash,is_admin,is_verified) VALUES (?,?,?,?,?)",
               ("admin","admin@ksync.ng", hash_pw("admin123"), 1, 1))
    db.execute("INSERT OR IGNORE INTO users (username,email,password_hash,is_admin,is_verified) VALUES (?,?,?,?,?)",
               ("demo","demo@ksync.ng", hash_pw("demo123"), 0, 1))
    db.commit()
    if USE_TURSO: db.sync()

    # ── SYNTHETIC PEER USERS ────────────────────────────────────────────────
    # These five users exist purely to give the collaborative filter enough
    # peer booking history to produce non-zero Jaccard similarity scores.
    # Without them the collab component is silent for a freshly seeded DB.
    # Each user has a distinct genre profile so similarity values are varied.
    synthetic = [
        ("peer_action",  "p1@ksync.ng", ["Action", "Superhero", "Adventure"]),
        ("peer_comedy",  "p2@ksync.ng", ["Comedy", "Animation", "Family"]),
        ("peer_drama",   "p3@ksync.ng", ["Drama", "History", "Musical"]),
        ("peer_horror",  "p4@ksync.ng", ["Horror", "Thriller", "Sci-Fi"]),
        ("peer_mixed",   "p5@ksync.ng", ["Action", "Comedy", "Drama", "Sci-Fi"]),
    ]

    all_shows = db.execute(
        "SELECT s.id as sid, m.genre as mgenre, m.id as mid "
        "FROM showtimes s JOIN movies m ON s.movie_id=m.id"
    ).fetchall()

    for uname, email, liked_genres in synthetic:
        cur = db.execute(
            "INSERT OR IGNORE INTO users (username,email,password_hash,is_admin,is_verified) VALUES (?,?,?,?,?)",
            (uname, email, hash_pw("peer123"), 0, 1)
        )
        uid = cur.lastrowid
        if not uid:  # already inserted on a re-seed
            row = db.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
            uid = row[0]

        booked_movies = set()
        for show in random.sample(list(all_shows), min(8, len(all_shows))):
            show_genres = [g.strip() for g in show['mgenre'].split('·') if g.strip()]
            if not any(g in liked_genres for g in show_genres):
                continue
            if show['mid'] in booked_movies:
                continue
            avail = db.execute(
                "SELECT id FROM seats WHERE showtime_id=? AND status='available' LIMIT 1",
                (show['sid'],)
            ).fetchone()
            if not avail:
                continue
            db.execute(
                "INSERT INTO bookings (user_id,showtime_id,seat_id,amount,status,payment_ref) "
                "VALUES (?,?,?,?,?,?)",
                (uid, show['sid'], avail[0], 3000, 'confirmed', f"SEED-{uid}-{show['sid']}")
            )
            db.execute("UPDATE seats SET status='booked' WHERE id=?", (avail[0],))
            booked_movies.add(show['mid'])
            learn_preferences(uid, show['mid'], avail[0], db=db)

    db.commit()
    if USE_TURSO: db.sync()

# Page Routes
@app.route('/')
def index():
    return render_template('index.html', paystack_public_key=PAYSTACK_PUBLIC_KEY)

@app.route('/admin')
def admin():
    if not session.get('is_admin'): return redirect('/')
    return render_template('admin.html')

# Auth API
@app.route('/api/register', methods=['POST'])
def register():
    d = request.get_json()
    u, e, p = d.get('username','').strip(), d.get('email','').strip().lower(), d.get('password','')
    if not u or not e or not p:
        return jsonify({'error': 'All fields required'}), 400
    if len(p) < 6:
        return jsonify({'error': 'Password min 6 characters'}), 400
    if qdb("SELECT id FROM users WHERE username=? OR email=?", (u, e), one=True):
        return jsonify({'error': 'Username or email already exists'}), 409

    uid = xdb(
        "INSERT INTO users (username,email,password_hash,is_verified) VALUES (?,?,?,?)",
        (u, e, hash_pw(p), 0)
    )
    # Generate email verification token
    token = secrets.token_urlsafe(32)
    xdb("INSERT INTO email_verifications (user_id,token) VALUES (?,?)", (uid, token))


    # NOTE: In production, send `token` via SMTP (SendGrid / AWS SES).
    # For this demo deployment, the token is returned in the response
    # so it can be verified immediately without an email server.
    return jsonify({
        'success': True,
        'username': u,
        'verification_token': token,
        'message': 'Account created. Use the verification token to confirm your email.'
    })


@app.route('/api/verify-email', methods=['POST'])
def verify_email():
    """
    Accepts the verification token returned at registration (or sent via email
    in production) and marks the user account as verified.
    """
    token = (request.get_json() or {}).get('token', '').strip()
    if not token:
        return jsonify({'error': 'Token required'}), 400
    record = qdb(
        "SELECT * FROM email_verifications WHERE token=? AND verified=0",
        (token,), one=True
    )
    if not record:
        return jsonify({'error': 'Invalid or already-used token'}), 400
    db = get_db()
    db.execute("UPDATE users SET is_verified=1 WHERE id=?", (record['user_id'],))
    db.execute("UPDATE email_verifications SET verified=1 WHERE id=?", (record['id'],))
    db.commit()
    return jsonify({'success': True, 'message': 'Email verified successfully'})


@app.route('/api/login', methods=['POST'])
def login():
    d = request.get_json()
    user = qdb("SELECT * FROM users WHERE username=? OR email=?", (d.get('username', ''),) * 2, one=True)

    # Define the generic message here
    error_msg = 'Invalid email or password'

    if not user:
        return jsonify({'error': error_msg}), 401

    if not verify_pw(user['password_hash'], d.get('password', '')):
        return jsonify({'error': error_msg}), 401

    session.update({'user_id': user['id'], 'username': user['username'], 'is_admin': bool(user['is_admin'])})
    return jsonify({'success': True, 'username': user['username'], 'is_admin': bool(user['is_admin'])})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear(); return jsonify({'success':True})

@app.route('/api/me')
def me():
    if 'user_id' not in session: return jsonify({'logged_in':False})
    return jsonify({'logged_in':True,'user_id':session['user_id'],
                    'username':session['username'],'is_admin':session.get('is_admin',False)})

# Movie API
@app.route('/api/movies')
def get_movies():
    genre  = request.args.get('genre')
    search = request.args.get('search')
    sql = "SELECT * FROM movies WHERE is_active=1"
    args = []
    if genre:  sql += " AND genre=?"; args.append(genre)
    if search: sql += " AND (title LIKE ? OR description LIKE ?)"; args += [f'%{search}%']*2
    sql += " ORDER BY rating DESC"
    return jsonify([dict(m) for m in qdb(sql, args)])

@app.route('/api/movies/<int:mid>')
def get_movie(mid):
    m = qdb("SELECT * FROM movies WHERE id=?", (mid,), one=True)
    if not m: return jsonify({'error':'Not found'}), 404
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sts = qdb(
        """SELECT s.*, c.name as cinema_full_name, c.address,
                  COUNT(se.id) as total_seats,
                  SUM(CASE WHEN se.status='available' THEN 1 ELSE 0 END) as available_seats
           FROM showtimes s
           LEFT JOIN halls h ON s.hall_id=h.id
           LEFT JOIN cinemas c ON h.cinema_id=c.id
           LEFT JOIN seats se ON se.showtime_id=s.id
           WHERE s.movie_id=? AND s.showtime>=?
           GROUP BY s.id ORDER BY s.showtime""", (mid, now))
    return jsonify({'movie':dict(m),'showtimes':[dict(s) for s in sts]})

@app.route('/api/recommendations')
@auth_required
def recommendations():
    return jsonify(recommend(session['user_id']))

@app.route('/api/genres')
def genres():
    return jsonify([r['genre'] for r in qdb("SELECT DISTINCT genre FROM movies WHERE is_active=1 ORDER BY genre")])

@app.route('/api/genre-matcher', methods=['POST'])
def genre_matcher():
    """
    Rule-based genre + seat preference matcher.
    Accepts: genres (list), seat_position (str), seat_zone (str), min_rating (float)
    Returns: ranked list of matching movies with showtimes and seat info.
    """
    d = request.get_json() or {}
    genres_wanted   = d.get('genres', [])
    seat_position   = d.get('seat_position', '')   # center / aisle / edge
    seat_zone       = d.get('seat_zone', '')        # front / middle / back
    min_rating      = float(d.get('min_rating', 0))
    max_results     = int(d.get('max_results', 10))

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Build movie filter
    sql = "SELECT * FROM movies WHERE is_active=1"
    args = []
    if genres_wanted:
        placeholders = ','.join('?' * len(genres_wanted))
        sql += f" AND genre IN ({placeholders})"
        args += genres_wanted
    if min_rating > 0:
        sql += " AND rating >= ?"
        args.append(min_rating)
    sql += " ORDER BY rating DESC"

    movies = qdb(sql, args)
    if not movies:
        return jsonify([])

    results = []
    for m in movies:
        mid = m['id']
        # Find best showtime for this movie
        showtimes = qdb("""
            SELECT s.*, COUNT(se.id) as total_seats,
                   SUM(CASE WHEN se.status='available' THEN 1 ELSE 0 END) as avail_seats,
                   AVG(CASE WHEN se.status='available' THEN se.quality_score END) as avg_quality
            FROM showtimes s
            LEFT JOIN seats se ON se.showtime_id = s.id
            WHERE s.movie_id=? AND s.showtime>=?
            GROUP BY s.id HAVING avail_seats > 0
            ORDER BY s.showtime
        """, (mid, now))

        if not showtimes:
            continue

        # Score each showtime by seat preference match
        best_st = None
        best_score = -1

        for st in showtimes:
            avail_seats = qdb("""
                SELECT * FROM seats WHERE showtime_id=? AND status='available'
            """, (st['id'],))

            if not avail_seats:
                continue

            score = 0
            matched_seats = 0

            for seat in avail_seats:
                tags = json.loads(seat['position_tags']) if seat['position_tags'] else []
                pos_match  = 1 if seat_position in tags else 0
                zone_match = 1 if seat_zone in tags else 0
                score += (float(seat['quality_score']) / 10) + pos_match * 0.5 + zone_match * 0.3
                matched_seats += 1

            if matched_seats > 0:
                avg_score = score / matched_seats
                if avg_score > best_score:
                    best_score = avg_score
                    best_st = st

        if not best_st:
            continue

        # Find best individual seat matching preferences
        avail = qdb("""
            SELECT * FROM seats WHERE showtime_id=? AND status='available'
            ORDER BY quality_score DESC
        """, (best_st['id'],))

        best_seat = None
        best_seat_score = -1
        for seat in avail:
            tags = json.loads(seat['position_tags']) if seat['position_tags'] else []
            s = float(seat['quality_score'])
            if seat_position in tags: s += 3
            if seat_zone in tags:     s += 2
            if s > best_seat_score:
                best_seat_score = s
                best_seat = dict(seat)

        total   = best_st['total_seats'] or 1
        avail_n = best_st['avail_seats'] or 0

        results.append({
            'movie_id':       mid,
            'showtime_id':    best_st['id'],
            'title':          m['title'],
            'genre':          m['genre'],
            'rating':         m['rating'],
            'description':    m['description'],
            'poster_url':     m['poster_url'],
            'duration_min':   m['duration_min'],
            'director':       m['director'],
            'cast_list':      m['cast_list'],
            'showtime':       best_st['showtime'],
            'hall_name':      best_st['hall_name'],
            'cinema_name':    best_st['cinema_name'],
            'price':          best_st['price'],
            'available_seats': avail_n,
            'total_seats':    total,
            'avail_pct':      round(avail_n / total * 100),
            'best_seat':      best_seat,
            'match_score':    round(best_score, 3),
        })

    # Sort by rating desc then match score
    results.sort(key=lambda x: (x['rating'], x['match_score']), reverse=True)
    return jsonify(results[:max_results])

@app.route('/api/cinemas')
def get_cinemas():
    return jsonify([dict(c) for c in qdb("SELECT * FROM cinemas")])

@app.route('/api/my-preferences')
@auth_required
def my_prefs():
    return jsonify(get_prefs(session['user_id']))


# Seat API (Fixed for Timezone Sync)
@app.route('/api/seats/<int:sid>')
def get_seats(sid):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    db  = get_db()
    db.execute("""
        UPDATE seats
        SET status='available', locked_by=NULL, locked_until=NULL
        WHERE showtime_id=? AND status='locked' AND locked_until<?
    """, (sid, now))
    db.commit()

    seats = qdb(
        """SELECT id, row_num, col_num, row_label, seat_number,
                  quality_score, position_tags, status,
                  CASE WHEN locked_by=? THEN 1 ELSE 0 END as my_lock
           FROM seats WHERE showtime_id=? ORDER BY row_num, col_num""",
        (session.get('user_id', -1), sid))

    st = qdb(
        """SELECT s.*, m.title
           FROM showtimes s JOIN movies m ON s.movie_id=m.id
           WHERE s.id=?""", (sid,), one=True)
    if not st:
        return jsonify({'error': 'Not found'}), 404

    # ── Compute Q_pref for every available seat if the user is logged in ──
    # Q_obj  (quality_score) is already stored on the seat — no computation needed.
    # Q_pref is personal: we compare each seat's tags + Q_obj against the
    # user's learned preferences from user_preferences.
    user_id = session.get('user_id')
    prefs   = get_prefs(user_id) if user_id else None

    seat_list = []
    for s in seats:
        row = dict(s)

        # Q_obj — already stored, just label it clearly
        row['q_obj']  = round(float(s['quality_score']), 2)

        # Q_pref — computed live against this user's preference profile
        if prefs and s['status'] == 'available':
            tags      = json.loads(s['position_tags']) if s['position_tags'] else []
            q_obj     = float(s['quality_score'])
            pos_pref  = prefs.get('seat_position_pref', 'center')
            zone_pref = prefs.get('seat_zone_pref',     'middle')
            avg_q     = float(prefs.get('avg_quality_pref', 7.0))

            pm = 1.0 if pos_pref in tags else (0.5 if 'aisle' in tags else 0.2)
            zm = 1.0 if zone_pref in tags else 0.35
            qm = max(0.0, 1.0 - abs(q_obj - avg_q) / 10.0)

            row['q_pref']      = round(pm * 0.40 + zm * 0.30 + qm * 0.30, 4)
            row['q_pref_pct']  = round(row['q_pref'] * 100)   # 0–100 for display
        else:
            row['q_pref']     = None   # not logged in or seat not available
            row['q_pref_pct'] = None

        seat_list.append(row)

    return jsonify({'seats': seat_list, 'showtime': dict(st)})


@app.route('/api/seats/lock', methods=['POST'])
@auth_required
def lock_seat():
    d = request.get_json()
    seat_id, showtime_id = d.get('seat_id'), d.get('showtime_id')

    # Fix: Use utcnow() for consistent server-side timing
    now = datetime.utcnow()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    db = get_db()

    # Clear expired locks globally before checking availability
    db.execute("""
        UPDATE seats SET status='available', locked_by=NULL, locked_until=NULL 
        WHERE status='locked' AND locked_until<?
    """, (now_str,))

    seat = qdb("SELECT * FROM seats WHERE id=? AND showtime_id=?", (seat_id, showtime_id), one=True)
    if not seat: return jsonify({'error': 'Seat not found'}), 404
    if seat['status'] == 'booked': return jsonify({'error': 'Seat already booked'}), 409

    if seat['status'] == 'locked' and seat['locked_by'] != session['user_id']:
        return jsonify({'error': 'Seat is held by another user'}), 409

    # Release any other seat this specific user was holding for this showtime
    db.execute("""
        UPDATE seats SET status='available', locked_by=NULL, locked_until=NULL 
        WHERE showtime_id=? AND locked_by=? AND status='locked'
    """, (showtime_id, session['user_id']))

    # Fix: Set expiration and format as string for SQLite
    lock_until_dt = now + timedelta(seconds=LOCK_DURATION)
    lock_until_str = lock_until_dt.strftime('%Y-%m-%d %H:%M:%S')

    db.execute("""
        UPDATE seats SET status='locked', locked_by=?, locked_until=? 
        WHERE id=?
    """, (session['user_id'], lock_until_str, seat_id))
    db.commit()

    # Return the string. Important: In your frontend app.js, append 'Z' to this string so Javascript knows it is UTC!
    return jsonify({'success': True, 'locked_until': lock_until_str})
@app.route('/api/seats/unlock', methods=['POST'])
@auth_required
def unlock_seat():
    seat_id = request.get_json().get('seat_id')
    db = get_db()
    db.execute("UPDATE seats SET status='available',locked_by=NULL,locked_until=NULL WHERE id=? AND locked_by=?", (seat_id, session['user_id']))
    db.commit()
    return jsonify({'success':True})

# Booking APIs
@app.route('/api/bookings/initiate', methods=['POST'])
@auth_required
def initiate_booking():
    d = request.get_json()
    sid, seat_id = d.get('showtime_id'), d.get('seat_id')
    seat = qdb("SELECT * FROM seats WHERE id=? AND showtime_id=? AND locked_by=?", (seat_id, sid, session['user_id']), one=True)
    if not seat: return jsonify({'error':'Seat not held by you. Please select again.'}), 400
    st = qdb("SELECT * FROM showtimes WHERE id=?", (sid,), one=True)
    if not st: return jsonify({'error':'Showtime not found'}), 404
    pay_ref = f"ABC-{secrets.token_urlsafe(10).upper()}"
    amount  = float(st['price'])
    bid = xdb("INSERT INTO bookings (user_id,showtime_id,seat_id,amount,status,payment_ref) VALUES (?,?,?,?,?,?)",
              (session['user_id'], sid, seat_id, amount, 'pending', pay_ref))
    user = qdb("SELECT email FROM users WHERE id=?", (session['user_id'],), one=True)
    return jsonify({'booking_id':bid,'payment_ref':pay_ref,'amount':amount,
                    'amount_kobo':int(amount*100),'email':user['email'],
                    'public_key':PAYSTACK_PUBLIC_KEY})

@app.route('/api/bookings/verify', methods=['POST'])
@auth_required
def verify_booking():
    """Called only after Paystack inline popup returns success."""
    d = request.get_json()
    bid, ps_ref = d.get('booking_id'), d.get('paystack_ref')
    booking = qdb("SELECT * FROM bookings WHERE id=? AND user_id=?", (bid, session['user_id']), one=True)
    if not booking: return jsonify({'error':'Booking not found'}), 404
    # In production, verify with Paystack API using PAYSTACK_SECRET_KEY:
    # GET https://api.paystack.co/transaction/verify/{ps_ref}
    # For the scope of this project, we trust the reference returned by Paystack JS inline.
    db = get_db()
    db.execute("UPDATE bookings SET status='confirmed',paystack_ref=? WHERE id=?", (ps_ref, bid))
    db.execute("UPDATE seats SET status='booked',locked_by=NULL,locked_until=NULL WHERE id=?", (booking['seat_id'],))
    db.commit()
    st = qdb("SELECT movie_id FROM showtimes WHERE id=?", (booking['showtime_id'],), one=True)
    if st: learn_preferences(session['user_id'], st['movie_id'], booking['seat_id'])
    
    det = qdb(
        """SELECT b.*,m.title,m.poster_url,s.showtime,s.hall_name,s.cinema_name,s.price,
                  se.row_label,se.seat_number,se.quality_score,se.position_tags
           FROM bookings b
           JOIN showtimes s ON b.showtime_id=s.id
           JOIN movies m ON s.movie_id=m.id
           JOIN seats se ON b.seat_id=se.id
           WHERE b.id=?""", (bid,), one=True)
    return jsonify({'success':True,'booking':dict(det)})

@app.route('/api/my-bookings')
@auth_required
def my_bookings():
    rows = qdb(
        """SELECT b.*,m.title,m.genre,m.poster_url,s.showtime,s.hall_name,s.cinema_name,s.price,
                  se.row_label,se.seat_number,se.quality_score,se.position_tags
           FROM bookings b
           JOIN showtimes s ON b.showtime_id=s.id
           JOIN movies m ON s.movie_id=m.id
           JOIN seats se ON b.seat_id=se.id
           WHERE b.user_id=?
           ORDER BY b.created_at DESC""", (session['user_id'],))
    return jsonify([dict(r) for r in rows])

# Admin API
@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    return jsonify({
        'total_users':    qdb("SELECT COUNT(*) as c FROM users WHERE is_admin=0", one=True)['c'],
        'total_movies':   qdb("SELECT COUNT(*) as c FROM movies", one=True)['c'],
        'total_bookings': qdb("SELECT COUNT(*) as c FROM bookings WHERE status='confirmed'", one=True)['c'],
        'total_revenue':  qdb("SELECT COALESCE(SUM(amount),0) as s FROM bookings WHERE status='confirmed'", one=True)['s'],
        'popular_movies': [dict(r) for r in qdb(
            """SELECT m.title,COUNT(b.id) as bookings FROM bookings b
               JOIN showtimes s ON b.showtime_id=s.id JOIN movies m ON s.movie_id=m.id
               WHERE b.status='confirmed' GROUP BY m.id ORDER BY bookings DESC LIMIT 5""")],
    })

@app.route('/api/admin/movies', methods=['GET','POST'])
@admin_required
def admin_movies():
    if request.method == 'POST':
        d = request.get_json()
        mid = xdb("INSERT INTO movies (title,genre,description,duration_min,rating,poster_url,director,cast_list,release_year) VALUES (?,?,?,?,?,?,?,?,?)",
                  (d['title'],d['genre'],d.get('description',''),d.get('duration_min',120),d.get('rating',7.0),d.get('poster_url',''),d.get('director',''),d.get('cast_list',''),d.get('release_year',2026)))
        return jsonify({'success':True,'movie_id':mid})
    return jsonify([dict(m) for m in qdb("SELECT * FROM movies ORDER BY created_at DESC")])

@app.route('/api/admin/movies/<int:mid>', methods=['PUT','DELETE'])
@admin_required
def admin_movie(mid):
    if request.method == 'DELETE':
        xdb("UPDATE movies SET is_active=0 WHERE id=?", (mid,))
        return jsonify({'success':True})
    d = request.get_json()
    xdb("UPDATE movies SET title=?,genre=?,description=?,duration_min=?,rating=?,poster_url=?,director=?,is_active=? WHERE id=?",
        (d['title'],d['genre'],d.get('description'),d.get('duration_min',120),d.get('rating',7),d.get('poster_url'),d.get('director'),d.get('is_active',1),mid))
    return jsonify({'success':True})

@app.route('/api/admin/showtimes', methods=['GET', 'POST'])
@admin_required
def admin_showtimes():
    if request.method == 'POST':
        d = request.get_json()
        hall = qdb("SELECT h.*,c.name as cname FROM halls h JOIN cinemas c ON h.cinema_id=c.id WHERE h.id=?",
                   (d['hall_id'],), one=True)
        if not hall:
            return jsonify({'error': 'Hall not found'}), 404

        # Conflict check — block overlapping showtimes in same hall
        conflict = qdb("""
            SELECT s.id, m.title, s.showtime, m.duration_min
            FROM showtimes s JOIN movies m ON s.movie_id = m.id
            WHERE s.hall_id = ?
              AND datetime(s.showtime, '+' || m.duration_min || ' minutes') > datetime(?)
              AND datetime(s.showtime) < datetime(?, '+' || (
                    SELECT duration_min FROM movies WHERE id=?
                  ) || ' minutes')
        """, (d['hall_id'], d['showtime'], d['showtime'], d['movie_id']), one=True)

        if conflict:
            return jsonify({
                'error': f"Hall conflict: \"{conflict['title']}\" is already scheduled at "
                         f"{conflict['showtime']} and runs for {conflict['duration_min']} min. "
                         f"Please choose a different time or hall."
            }), 409

        stid = xdb(
            "INSERT INTO showtimes (movie_id,hall_id,hall_name,cinema_name,showtime,price) VALUES (?,?,?,?,?,?)",
            (d['movie_id'], d['hall_id'], hall['name'], hall['cname'], d['showtime'], d['price'])
        )
        db = get_db()
        RL = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        for r in range(1, hall['total_rows'] + 1):
            for c in range(1, hall['total_cols'] + 1):
                q    = compute_seat_quality(r, c, hall['total_rows'], hall['total_cols'])
                tags = classify_seat(r, c, hall['total_rows'], hall['total_cols'])
                db.execute(
                    "INSERT INTO seats (showtime_id,hall_id,row_num,col_num,row_label,seat_number,quality_score,position_tags,status) VALUES (?,?,?,?,?,?,?,?,?)",
                    (stid, hall['id'], r, c, RL[r-1], c, q, json.dumps(tags), 'available')
                )
        db.commit()
        if USE_TURSO: db.sync()
        return jsonify({'success': True, 'showtime_id': stid})

    rows = qdb("SELECT s.*,m.title FROM showtimes s JOIN movies m ON s.movie_id=m.id ORDER BY s.showtime DESC LIMIT 60")
    return jsonify([dict(r) for r in rows])

# For editing showtimw
@app.route('/api/admin/showtimes/<int:sid>', methods=['PUT', 'DELETE'])
@admin_required
def admin_showtime_detail(sid):
    if request.method == 'DELETE':
        xdb("DELETE FROM seats WHERE showtime_id=?", (sid,))
        xdb("DELETE FROM bookings WHERE showtime_id=?", (sid,))
        xdb("DELETE FROM showtimes WHERE id=?", (sid,))
        return jsonify({'success': True})

    # PUT — edit existing showtime
    d = request.get_json()
    showtime = d.get('showtime')
    price    = d.get('price')
    if not showtime or price is None:
        return jsonify({'error': 'showtime and price are required'}), 400

    st = qdb("SELECT * FROM showtimes WHERE id=?", (sid,), one=True)
    if not st:
        return jsonify({'error': 'Showtime not found'}), 404

    # Conflict check — exclude the showtime being edited
    conflict = qdb("""
        SELECT s.id, m.title, s.showtime, m.duration_min
        FROM showtimes s JOIN movies m ON s.movie_id = m.id
        WHERE s.hall_id = ?
          AND s.id != ?
          AND datetime(s.showtime, '+' || m.duration_min || ' minutes') > datetime(?)
          AND datetime(s.showtime) < datetime(?, '+' || (
                SELECT duration_min FROM movies WHERE id=?
              ) || ' minutes')
    """, (st['hall_id'], sid, showtime, showtime, st['movie_id']), one=True)

    if conflict:
        return jsonify({
            'error': f"Hall conflict: \"{conflict['title']}\" is already scheduled at "
                     f"{conflict['showtime']} and runs for {conflict['duration_min']} min. "
                     f"Please choose a different time."
        }), 409

    xdb("UPDATE showtimes SET showtime=?, price=? WHERE id=?",
        (showtime, float(price), sid))
    return jsonify({'success': True})

@app.route('/api/admin/bookings')
@admin_required
def admin_bookings():
    rows = qdb(
        """SELECT b.*,u.username,m.title,s.showtime,s.hall_name,s.cinema_name,
                  se.row_label,se.seat_number
           FROM bookings b JOIN users u ON b.user_id=u.id
           JOIN showtimes s ON b.showtime_id=s.id
           JOIN movies m ON s.movie_id=m.id
           JOIN seats se ON b.seat_id=se.id
           ORDER BY b.created_at DESC LIMIT 100""")
    return jsonify([dict(r) for r in rows])

@app.route('/api/halls')
def get_halls():
    return jsonify([dict(h) for h in qdb("SELECT h.*,c.name as cinema_name FROM halls h JOIN cinemas c ON h.cinema_id=c.id")])

@app.route('/api/admin/analytics')
@admin_required
def admin_analytics():
    with get_db() as conn:
        # Bookings by genre → data.genres
        genres_raw = conn.execute("""
            SELECT m.genre, COUNT(b.id) as bookings
            FROM bookings b
            JOIN showtimes st ON b.showtime_id = st.id
            JOIN movies m ON st.movie_id = m.id
            WHERE b.status='confirmed'
            GROUP BY m.genre
            ORDER BY bookings DESC
            LIMIT 8
        """).fetchall()

        # Flatten genres (each movie has "Action · Comedy · Sci-Fi" etc.)
        genre_counts = {}
        for row in genres_raw:
            for g in row['genre'].split('·'):
                g = g.strip()
                if g:
                    genre_counts[g] = genre_counts.get(g, 0) + row['bookings']
        genres = [{'label': k, 'value': v}
                  for k, v in sorted(genre_counts.items(), key=lambda x: -x[1])[:8]]

        # Bookings by movie → data.movies
        movies_raw = conn.execute("""
            SELECT m.title, COUNT(b.id) as bookings
            FROM movies m
            LEFT JOIN showtimes st ON st.movie_id = m.id
            LEFT JOIN bookings b ON b.showtime_id = st.id AND b.status='confirmed'
            GROUP BY m.id
            ORDER BY bookings DESC
            LIMIT 6
        """).fetchall()
        movies = [{'label': r['title'], 'value': r['bookings']} for r in movies_raw]

        # Bookings by cinema → data.zones
        zones_raw = conn.execute("""
            SELECT c.name as zone_name, COUNT(b.id) as bookings
            FROM cinemas c
            LEFT JOIN halls h ON h.cinema_id = c.id
            LEFT JOIN showtimes st ON st.hall_id = h.id
            LEFT JOIN bookings b ON b.showtime_id = st.id AND b.status='confirmed'
            GROUP BY c.id
            ORDER BY bookings DESC
        """).fetchall()
        zones = [{'label': r['zone_name'], 'value': r['bookings']} for r in zones_raw]

        return jsonify({
            'genres': genres,
            'movies': movies,
            'zones':  zones,
        })

with app.app_context():
    init_db()

if __name__ == '__main__':
    init_db()
    print("="*55)
    print("  ksync — Cinema Reservation System")
    print("  Abuja, Nigeria")
    print("  Admin: admin / admin123")
    print("  Demo:  demo  / demo123")
    print("="*55)

    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
