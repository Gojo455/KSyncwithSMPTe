"""
ksync — Seat-Aware Cinema Reservation System
Abuja, Nigeria | Flask + PostgreSQL | Hybrid Recommender | Paystack Payments
"""

from flask import Flask, render_template, request, jsonify, session, g, redirect
import hashlib, secrets, json, os, time, random, math
from datetime import datetime, timedelta
from functools import wraps
import psycopg2
import psycopg2.extras   # RealDictCursor — makes row["col"] work like sqlite3.Row

app = Flask(__name__)
app.secret_key = os.environ.get('cinema_SECRET', 'ksync-stable-secret-key-2026-do-not-share')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

# Postgree connection
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set.")

SEAT_LOCKS    = {}
LOCK_DURATION = 300

PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', 'sk_test_527e7adc01bbe74b54897f6b58cf1555e683ccaf')
PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC_KEY', 'pk_test_b695c7bf0b597ddebfe2f70ff4aa73927dd1d1de')


# DB Helpers 
def get_db():
    """One psycopg2 connection per request, stored on Flask g."""
    if '_db' not in g:
        g._db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        g._db.autocommit = False
    return g._db

@app.teardown_appcontext
def close_db(e):
    db = g.pop('_db', None)
    if db:
        db.close()


def qdb(sql, args=(), one=False):
    """Read query. Returns list of RealDictRow (or single row if one=True)."""
    db  = get_db()
    cur = db.cursor()
    cur.execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows

def xdb(sql, args=()):
    """Write query. Commits and returns the inserted row id via RETURNING id."""
    db  = get_db()
    cur = db.cursor()
    # Append RETURNING id only for INSERT statements that don't already have it
    returning_sql = sql if 'RETURNING' in sql.upper() else sql.rstrip(';') + ' RETURNING id'
    cur.execute(returning_sql, args)
    row = cur.fetchone()
    db.commit()
    cur.close()
    return row['id'] if row else None


def compute_seat_quality(row, col, total_rows, total_cols):

    rn = row / total_rows   # normalised: 0.0 = front row, 1.0 = back row
    cn = col / total_cols   # normalised: 0.0 = far left,  1.0 = far right

    opt_r = 0.525
    if rn <= opt_r:
        # Front half: linear decay from optimum to front wall
        rs = rn / opt_r
    else:
        # Back half: linear decay from optimum to back wall
        rs = (1.0 - rn) / (1.0 - opt_r)

    severe_front_penalty = rn < 0.20   # neck tilt beyond 35 degrees
    if severe_front_penalty:
        rs *= 0.40

    moderate_back_penalty = rn > 0.85   # screen subtends too small an angle
    if moderate_back_penalty:
        rs *= 0.70

    opt_c = 0.50
    cd = abs(cn - opt_c)

    # Linear decay: 1.0 at centre, 0.0 at the absolute edge (cd = 0.5)
    cs = 1.0 - (cd / 0.50)

    # Extra penalty for seats in the outer 20% — ITU-R lateral angle violation
    if cd > 0.30:
        cs *= 0.60

    q = (rs * 0.60 + cs * 0.40) * 10.0

    # ── Hard ceiling for severe ergonomic violations ──────────────────────
    # A 35-degree-plus neck tilt is a fixed physical discomfort that a good
    # horizontal position cannot offset. Without this cap, a centred seat
    # in a severely-penalised front row (e.g. row A, centre column) could
    # average back above 4.0 even though no real viewer would rate it that
    # well. SMPTE EG 18-1994 treats this as a disqualifying condition, not
    # a partial deduction, so we cap the final score rather than blend it.
    if severe_front_penalty:
        q = min(q, 3.0)

    return round(min(max(q, 0.5), 10.0), 2)


def classify_seat(row, col, total_rows, total_cols):
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


def seat_pref_match(available_seats, prefs):
  
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

        # --- Quality proximity (how close is Q_obj to the user's usual choice%s) ---
        qm = max(0.0, 1.0 - abs(q_obj - avg_q_pref) / 10.0)

        # Combined preference score for this seat (weighted sum)
        pref_score = pm * 0.40 + zm * 0.30 + qm * 0.30

        if pref_score > best_pref_score:
            best_pref_score = pref_score
            best_obj_q      = q_obj

    return round(best_pref_score, 4), round(best_obj_q, 2)

#  Recommendation Engine

def get_prefs(user_id):
    p = qdb("SELECT * FROM user_preferences WHERE user_id=%s", (user_id,), one=True)
    return dict(p) if p else {
        'genre_weights':'{}','seat_position_pref':'center',
        'seat_zone_pref':'middle','avg_quality_pref':7.0,'booking_count':0}


def split_genres(genre_string):
   
    return [g.strip() for g in genre_string.split('·') if g.strip()]


def jaccard(a, b):
    if not a and not b: return 0.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def collab_score(user_id, movie_id):
    
    raw_user = qdb(
        """SELECT DISTINCT m.genre FROM bookings b JOIN showtimes s ON b.showtime_id=s.id JOIN movies m ON s.movie_id=m.id WHERE b.user_id=%s AND b.status='confirmed'""", (user_id,))
    user_genres = set(g for r in raw_user for g in split_genres(r['genre']))

    tgt = qdb("SELECT genre FROM movies WHERE id=%s", (movie_id,), one=True)
    if not tgt: return 0.0
    tgt_genres = set(split_genres(tgt['genre']))

    # Jaccard similarity
    all_other_users = qdb(
        """SELECT DISTINCT b.user_id FROM bookings b
           JOIN showtimes s ON b.showtime_id=s.id JOIN movies m ON s.movie_id=m.id
           WHERE b.user_id!=%s AND b.status='confirmed'""", (user_id,))

    sims = []
    for o in all_other_users:
        raw_peer = qdb(
            """SELECT DISTINCT m.genre FROM bookings b
               JOIN showtimes s ON b.showtime_id=s.id JOIN movies m ON s.movie_id=m.id
               WHERE b.user_id=%s AND b.status='confirmed'""", (o['user_id'],))
        peer_genres = set(g for r in raw_peer for g in split_genres(r['genre']))
        # Only include peers who share at least one genre with the target movie
        if peer_genres & tgt_genres:
            sims.append(jaccard(user_genres, peer_genres))

    return min(sum(sims)/len(sims)*1.5, 1.0) if sims else 0.0


def recommend(user_id, limit=12):
  
    prefs = get_prefs(user_id)
    gw = json.loads(prefs.get('genre_weights', '{}'))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    showtimes = qdb(
        """SELECT s.*, m.title, m.genre, m.rating, m.description,
                  m.poster_url, m.duration_min, m.director, m.cast_list,
                  m.id as movie_id
           FROM showtimes s JOIN movies m ON s.movie_id=m.id
           WHERE s.showtime>=%s AND m.is_active=1 ORDER BY s.showtime""", (now,))

    scored = {}
    for st in showtimes:
        mid, sid = st['movie_id'], st['id']
        avail = qdb("SELECT * FROM seats WHERE showtime_id=%s AND status='available'", (sid,))
        total = qdb("SELECT COUNT(*) as c FROM seats WHERE showtime_id=%s", (sid,), one=True)['c']
        if total == 0: continue
        avail_ratio = len(avail) / total
        if avail_ratio == 0: continue

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

        # Key by showtime_id (not movie_id) so every qualifying showtime —
        # across every date — is kept, instead of collapsing each movie
        # down to only its single best-scoring showtime. This lets the
        # frontend filter the ranked list by date without first requiring
        # the user to open a movie to discover other available showtimes.
        scored[sid] = {
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

    ranked = sorted(scored.values(), key=lambda x: x['score'], reverse=True)

    # Cap how many showtimes any single movie contributes so the list
    # isn't dominated by one film with many dates, while still surfacing
    # every distinct date for movies the user sees.
    per_movie_count = {}
    final = []
    for item in ranked:
        c = per_movie_count.get(item['movie_id'], 0)
        if c >= 5:   # at most 5 dates/showtimes shown per movie
            continue
        per_movie_count[item['movie_id']] = c + 1
        final.append(item)
        if len(final) >= limit * 4:   # allow enough rows for date filtering
            break

    return final


def learn_preferences(user_id, movie_id, seat_id, db=None):
    own_db = db is None
    if own_db:
        db = get_db()

    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT genre FROM movies WHERE id=%s", (movie_id,))
    movie = cur.fetchone()
    cur.execute("SELECT * FROM seats WHERE id=%s", (seat_id,))
    seat = cur.fetchone()
    if not movie or not seat:
        cur.close()
        return

    cur.execute("SELECT * FROM user_preferences WHERE user_id=%s", (user_id,))
    prefs = cur.fetchone()

    tags     = json.loads(seat['position_tags']) if seat['position_tags'] else []
    new_pos  = 'center' if 'center' in tags else ('aisle' if 'aisle' in tags else 'edge')
    new_zone = 'middle' if 'middle' in tags else ('front' if 'front' in tags else 'back')

    if prefs:
        gw    = json.loads(prefs['genre_weights'])
        alpha = 0.3
        gw[movie['genre']] = round(alpha + (1 - alpha) * gw.get(movie['genre'], 0.0), 4)
        beta      = 0.25
        new_avg_q = (1 - beta) * float(prefs['avg_quality_pref']) + beta * float(seat['quality_score'])
        cur.execute(
            """UPDATE user_preferences SET genre_weights=%s, seat_position_pref=%s,
               seat_zone_pref=%s, avg_quality_pref=%s, booking_count=booking_count+1
               WHERE user_id=%s""",
            (json.dumps(gw), new_pos, new_zone, round(new_avg_q, 2), user_id))
    else:
        gw = {movie['genre']: 0.5}
        cur.execute(
            """INSERT INTO user_preferences
               (user_id, genre_weights, seat_position_pref, seat_zone_pref, avg_quality_pref, booking_count)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (user_id, json.dumps(gw), new_pos, new_zone, float(seat['quality_score']), 1))

    cur.close()
    if own_db:
        db.commit()
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
    db = psycopg2.connect(DATABASE_URL)
    db.autocommit = True
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_verifications (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            token TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            genre TEXT NOT NULL,
            description TEXT,
            duration_min INTEGER,
            rating REAL DEFAULT 0,
            poster_url TEXT,
            director TEXT,
            cast_list TEXT,
            release_year INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cinemas (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            address TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS halls (
            id SERIAL PRIMARY KEY,
            cinema_id INTEGER NOT NULL REFERENCES cinemas(id),
            name TEXT NOT NULL,
            total_rows INTEGER NOT NULL,
            total_cols INTEGER NOT NULL,
            capacity INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS showtimes (
            id SERIAL PRIMARY KEY,
            movie_id INTEGER NOT NULL REFERENCES movies(id),
            hall_id INTEGER NOT NULL REFERENCES halls(id),
            hall_name TEXT NOT NULL,
            cinema_name TEXT NOT NULL,
            showtime TIMESTAMP NOT NULL,
            price REAL NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seats (
            id SERIAL PRIMARY KEY,
            showtime_id INTEGER NOT NULL REFERENCES showtimes(id),
            hall_id INTEGER NOT NULL,
            row_num INTEGER NOT NULL,
            col_num INTEGER NOT NULL,
            row_label TEXT NOT NULL,
            seat_number INTEGER NOT NULL,
            quality_score REAL NOT NULL,
            position_tags TEXT,
            status TEXT DEFAULT 'available',
            locked_by INTEGER,
            locked_until TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            showtime_id INTEGER NOT NULL REFERENCES showtimes(id),
            seat_id INTEGER NOT NULL REFERENCES seats(id),
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            payment_ref TEXT,
            paystack_ref TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            id SERIAL PRIMARY KEY,
            user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
            genre_weights TEXT DEFAULT '{}',
            seat_position_pref TEXT DEFAULT 'center',
            seat_zone_pref TEXT DEFAULT 'middle',
            avg_quality_pref REAL DEFAULT 7.0,
            booking_count INTEGER DEFAULT 0
        )
    """)
    cur.close()

    # Seed only if movies table is empty
    check_cur = db.cursor()
    check_cur.execute("SELECT COUNT(*) as c FROM movies")
    count = check_cur.fetchone()[0]
    check_cur.close()
    if count == 0:
        seed(db)
    db.close()


def seed(db):
    import random; random.seed(99)
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

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
         "https://upload.wikimedia.org/wikipedia/en/thumb/3/3c/Wicked_%282024_film%29_poster.png/250px-Wicked_%282024_film%29_poster.png",
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
        ("Dune Part Two", "Sci-Fi · Action · Adventure",
         "Paul Atreides unites with the Fremen to wage war against those who destroyed his family.",
         166, 8.5, "English", "2024-03-01",
         "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
         "#1a1a0e", 5500, 1, 1, '["Timothée Chalamet","Zendaya","Rebecca Ferguson"]', "Denis Villeneuve", "PG-13"),
        ("Interstellar", "Sci-Fi · Drama · Adventure",
         "A team of explorers travel through a wormhole in space to ensure humanity's survival.",
         169, 8.7, "English", "2014-11-07",
         "https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg",
         "#0a0a2e", 3500, 1, 0, '["Matthew McConaughey","Anne Hathaway","Jessica Chastain"]', "Christopher Nolan", "PG-13"),
        ("The Notebook", "Romance · Drama",
         "A poor young man falls in love with a rich girl in 1940s South Carolina.",
         123, 7.9, "English", "2004-06-25",
         "https://spoilertown.com/wp-content/uploads/2024/10/the-notebook-2004.webp",
         "#1a1a2e", 2500, 0, 1, '["Ryan Gosling","Rachel McAdams","James Garner"]', "Nick Cassavetes", "PG-13"),
        ("Crazy Rich Asians", "Romance · Comedy · Drama",
         "A New York professor discovers her boyfriend is from one of Singapore's wealthiest families.",
         120, 6.9, "English", "2018-08-15",
         "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcS-_tYB9Il3Cc3l7yIEgr2Ph2z69R7aKZrvZ01leHjJqBqVDda_2c2X4FfleNNO1Ui5og8H&s=10",
         "#1a0a2e", 2500, 0, 1, '["Constance Wu","Henry Golding","Michelle Yeoh"]', "Jon M. Chu", "PG-13"),
        ("La La Land", "Romance · Musical · Drama",
         "A jazz pianist and an aspiring actress fall in love while chasing their dreams in Los Angeles.",
         128, 8.0, "English", "2016-12-09",
         "https://image.tmdb.org/t/p/w500/uDO8zWDhfWwoFdKS4fzkUJt0Rf0.jpg",
         "#0a1a3e", 2500, 1, 0, '["Ryan Gosling","Emma Stone","John Legend"]', "Damien Chazelle", "PG-13"),
        ("Me Before You", "Romance · Drama",
         "A small-town woman takes a job caring for a paralysed man and the two develop an unexpected bond.",
         110, 7.4, "English", "2016-06-03",
         "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcS1gpdh3TT2RWr9p8Z6kDRO1-U5srSTF4jauA&s",
         "#2a0a1e", 2500, 0, 0, '["Emilia Clarke","Sam Claflin","Janet McTeer"]', "Thea Sharrock", "PG-13"),
        ("Five Feet Apart", "Romance · Drama",
         "Two teenagers with cystic fibrosis fall in love in a hospital but must always remain five feet apart.",
         116, 7.2, "English", "2019-03-15",
         "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSb_gkx1eMFORA_-854raIlUDHguTeZnQfvYtSRoeAPOlhNniyY5McM35Gg4cQR6jEbkvCZTg&s=10",
         "#0a1a2e", 2500, 0, 1, '["Cole Sprouse","Haley Lu Richardson","Moises Arias"]', "Justin Baldoni", "PG-13"),
        ("Titanic", "Romance · Drama · History",
         "A young aristocrat falls in love with a penniless artist aboard the ill-fated RMS Titanic.",
         194, 7.9, "English", "1997-12-19",
         "https://image.tmdb.org/t/p/w500/9xjZS2rlVxm8SFx8kPC3aIGCOYQ.jpg",
         "#0a0a3e", 3500, 1, 0, '["Leonardo DiCaprio","Kate Winslet","Billy Zane"]', "James Cameron", "PG-13"),
        ("A Tribe Called Judah", "Drama · Crime · Nollywood",
         "A determined mother raises five sons in Lagos against poverty and hardship.",
         135, 7.2, "Yoruba/English", "2023-12-15",
         "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRfRhq1lhl7R6EH_4xONNP0zKevSHk6uvfEMUFj5x_8Vs1ZvZZaZYD8EE23Tw7gvvjjOgUC&s=10",
         "#1a2a0e", 2500, 1, 1, '["Funke Akindele","Timini Egbuson","Broda Shaggi"]', "Funke Akindele", "PG-13"),
        ("The Black Book", "Action · Thriller · Nollywood",
         "A deacon seeks revenge after his son is killed by corrupt police.",
         130, 7.0, "English", "2023-09-22",
         "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQPP6aFsZoPdkcfjuB2ioYRvesR7iSg_UrgNyHiUESVPvX_CcYci6KQsADT1_ubuy2fGO8v&s=10",
         "#0a0a1e", 2500, 0, 1, '["Richard Mofe-Damijo","Sam Dede","Ireti Doyle"]', "Editi Effiong", "PG-13"),
    ]

    movie_ids = []
    for m in movies:
        movie_data = (m[0], m[1], m[2], m[3], m[4], m[7], m[13], m[12], int(str(m[6])[:4]))
        cur.execute(
            """INSERT INTO movies (title,genre,description,duration_min,rating,
               poster_url,director,cast_list,release_year)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", movie_data)
        movie_ids.append(cur.fetchone()['id'])

    cinemas_data = [
        ("Silverbird Cinemas",   "Central Business District", "Silverbird Entertainment Centre, Herbert Macaulay Way, CBD, Abuja"),
        ("Genesis Cinemas",      "Ceddi Plaza, Central Area", "Ceddi Plaza, Michael Okpara Way, Wuse Zone 5, Abuja"),
        ("Ozone Cinemas",        "Jabi Lake Mall",            "Jabi Lake Mall, Jabi, Abuja"),
        ("FilmHouse Cinemas",    "Lugbe Dunamis Mall",        "Dunamis HQ, Airport Road, Lugbe, Abuja"),
    ]
    cinema_ids = []
    for c in cinemas_data:
        cur.execute("INSERT INTO cinemas (name,location,address) VALUES (%s,%s,%s) RETURNING id", c)
        cinema_ids.append(cur.fetchone()['id'])

    hall_configs = [
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
        cur.execute(
            "INSERT INTO halls (cinema_id,name,total_rows,total_cols,capacity) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (cinema_ids[ci], hname, rows, cols, rows*cols))
        hall_ids.append((cur.fetchone()['id'], ci, hname, rows, cols))

    cinema_names = [c[0] for c in cinemas_data]
    base   = datetime.now().replace(minute=0, second=0, microsecond=0)
    times  = [10, 13, 16, 19, 22]
    prices = [2500, 3000, 2000, 2000, 3500, 4000, 2500, 2000]

    RL = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    showtime_records = []
    for day in range(5):
        for t in times:
            hall_info = random.choice(hall_ids)
            hid, ci, hname, rows, cols = hall_info
            mid      = random.choice(movie_ids)
            show_dt  = (base + timedelta(days=day)).replace(hour=t)
            price    = prices[hall_ids.index(hall_info)]
            cname    = cinema_names[ci]
            cur.execute(
                "INSERT INTO showtimes (movie_id,hall_id,hall_name,cinema_name,showtime,price) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (mid, hid, hname, cname, show_dt.strftime('%Y-%m-%d %H:%M:%S'), price))
            showtime_records.append((cur.fetchone()['id'], hid, rows, cols))

    for stid, hid, tr, tc in showtime_records:
        for r in range(1, tr+1):
            for c in range(1, tc+1):
                q      = compute_seat_quality(r, c, tr, tc)
                tags   = classify_seat(r, c, tr, tc)
                status = 'booked' if random.random() < 0.25 else 'available'
                cur.execute(
                    """INSERT INTO seats
                       (showtime_id,hall_id,row_num,col_num,row_label,seat_number,quality_score,position_tags,status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (stid, hid, r, c, RL[r-1], c, q, json.dumps(tags), status))

    # Admin + demo users
    for uname, email, pw, is_admin in [
        ("admin", "admin@ksync.ng", "Averturgaze", 1),
        ("demo",  "demo@ksync.ng",  "demo123",  0),
    ]:
        cur.execute(
            """INSERT INTO users (username,email,password_hash,is_admin,is_verified)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (uname, email, hash_pw(pw), is_admin, 1))
    db.commit()

    # Synthetic peer users for collaborative filtering
    synthetic = [
        ("peer_action", "p1@ksync.ng", ["Action", "Superhero", "Adventure"]),
        ("peer_comedy", "p2@ksync.ng", ["Comedy", "Animation", "Family"]),
        ("peer_drama",  "p3@ksync.ng", ["Drama", "History", "Musical"]),
        ("peer_horror", "p4@ksync.ng", ["Horror", "Thriller", "Sci-Fi"]),
        ("peer_mixed",  "p5@ksync.ng", ["Action", "Comedy", "Drama", "Sci-Fi"]),
    ]

    cur.execute(
        "SELECT s.id as sid, m.genre as mgenre, m.id as mid FROM showtimes s JOIN movies m ON s.movie_id=m.id"
    )
    all_shows = cur.fetchall()

    for uname, email, liked_genres in synthetic:
        cur.execute(
            """INSERT INTO users (username,email,password_hash,is_admin,is_verified)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
            (uname, email, hash_pw("peer123"), 0, 1))
        row = cur.fetchone()
        if row:
            uid = row['id']
        else:
            cur.execute("SELECT id FROM users WHERE username=%s", (uname,))
            uid = cur.fetchone()['id']

        booked_movies = set()
        for show in random.sample(list(all_shows), min(8, len(all_shows))):
            show_genres = [g.strip() for g in show['mgenre'].split('·') if g.strip()]
            if not any(g in liked_genres for g in show_genres): continue
            if show['mid'] in booked_movies: continue
            cur.execute(
                "SELECT id FROM seats WHERE showtime_id=%s AND status='available' LIMIT 1",
                (show['sid'],))
            avail = cur.fetchone()
            if not avail: continue
            cur.execute(
                "INSERT INTO bookings (user_id,showtime_id,seat_id,amount,status,payment_ref) VALUES (%s,%s,%s,%s,%s,%s)",
                (uid, show['sid'], avail['id'], 3000, 'confirmed', f"SEED-{uid}-{show['sid']}"))
            cur.execute("UPDATE seats SET status='booked' WHERE id=%s", (avail['id'],))
            booked_movies.add(show['mid'])
            learn_preferences(uid, show['mid'], avail['id'], db=db)

    db.commit()
    cur.close()



@app.route('/')
def index():
    return render_template('index.html', paystack_public_key=PAYSTACK_PUBLIC_KEY)

@app.route('/admin')
def admin():
    if not session.get('is_admin'): return redirect('/')
    return render_template('admin.html')


#  Auth APIs
@app.route('/api/register', methods=['POST'])
def register():
    d = request.get_json()
    u, e, p = d.get('username','').strip(), d.get('email','').strip().lower(), d.get('password','')
    if not u or not e or not p:
        return jsonify({'error': 'All fields required'}), 400
    if len(p) < 6:
        return jsonify({'error': 'Password min 6 characters'}), 400
    if qdb("SELECT id FROM users WHERE username=%s OR email=%s", (u, e), one=True):
        return jsonify({'error': 'Username or email already exists'}), 409
    uid = xdb(
        "INSERT INTO users (username,email,password_hash,is_verified) VALUES (%s,%s,%s,%s)",
        (u, e, hash_pw(p), 0))
    token = secrets.token_urlsafe(32)
    xdb("INSERT INTO email_verifications (user_id,token) VALUES (%s,%s)", (uid, token))
    return jsonify({'success': True, 'username': u, 'verification_token': token,
                    'message': 'Account created. Use the verification token to confirm your email.'})


@app.route('/api/verify-email', methods=['POST'])
def verify_email():
    token = (request.get_json() or {}).get('token', '').strip()
    if not token:
        return jsonify({'error': 'Token required'}), 400
    record = qdb("SELECT * FROM email_verifications WHERE token=%s AND verified=0", (token,), one=True)
    if not record:
        return jsonify({'error': 'Invalid or already-used token'}), 400
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE users SET is_verified=1 WHERE id=%s", (record['user_id'],))
    cur.execute("UPDATE email_verifications SET verified=1 WHERE id=%s", (record['id'],))
    db.commit()
    cur.close()
    return jsonify({'success': True, 'message': 'Email verified successfully'})


@app.route('/api/login', methods=['POST'])
def login():
    d = request.get_json()
    user = qdb("SELECT * FROM users WHERE username=%s OR email=%s", (d.get('username',''),)*2, one=True)
    error_msg = 'Invalid email or password'
    if not user: return jsonify({'error': error_msg}), 401
    if not verify_pw(user['password_hash'], d.get('password','')): return jsonify({'error': error_msg}), 401
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


# Movie APIs
@app.route('/api/movies')
def get_movies():
    genre  = request.args.get('genre')
    search = request.args.get('search')
    sql = "SELECT * FROM movies WHERE is_active=1"
    args = []
    if genre:  sql += " AND genre=%s";                           args.append(genre)
    if search: sql += " AND (title ILIKE %s OR description ILIKE %s)"; args += [f'%{search}%']*2
    sql += " ORDER BY rating DESC"
    return jsonify([dict(m) for m in qdb(sql, args)])

@app.route('/api/movies/<int:mid>')
def get_movie(mid):
    m = qdb("SELECT * FROM movies WHERE id=%s", (mid,), one=True)
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
           WHERE s.movie_id=%s AND s.showtime>=%s
           GROUP BY s.id, c.name, c.address ORDER BY s.showtime""", (mid, now))
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
    d = request.get_json() or {}
    genres_wanted = d.get('genres', [])
    seat_position = d.get('seat_position', '')
    seat_zone     = d.get('seat_zone', '')
    min_rating    = float(d.get('min_rating', 0))
    max_results   = int(d.get('max_results', 10))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    sql = "SELECT * FROM movies WHERE is_active=1"
    args = []
    if genres_wanted:
        placeholders = ','.join('%s' * len(genres_wanted))
        sql += f" AND genre IN ({placeholders})"
        args += genres_wanted
    if min_rating > 0:
        sql += " AND rating >= %s"
        args.append(min_rating)
    sql += " ORDER BY rating DESC"

    movies_rows = qdb(sql, args)
    if not movies_rows: return jsonify([])

    results = []
    for m in movies_rows:
        mid = m['id']
        showtimes = qdb("""
            SELECT s.*, COUNT(se.id) as total_seats,
                   SUM(CASE WHEN se.status='available' THEN 1 ELSE 0 END) as avail_seats,
                   AVG(CASE WHEN se.status='available' THEN se.quality_score END) as avg_quality
            FROM showtimes s
            LEFT JOIN seats se ON se.showtime_id=s.id
            WHERE s.movie_id=%s AND s.showtime>=%s
            GROUP BY s.id HAVING SUM(CASE WHEN se.status='available' THEN 1 ELSE 0 END) > 0
            ORDER BY s.showtime
        """, (mid, now))
        if not showtimes: continue

        best_st, best_score = None, -1
        for st in showtimes:
            avail_seats = qdb("SELECT * FROM seats WHERE showtime_id=%s AND status='available'", (st['id'],))
            if not avail_seats: continue
            score, matched = 0, 0
            for seat in avail_seats:
                tags = json.loads(seat['position_tags']) if seat['position_tags'] else []
                score += (float(seat['quality_score'])/10) + (1 if seat_position in tags else 0)*0.5 + (1 if seat_zone in tags else 0)*0.3
                matched += 1
            if matched > 0:
                avg_score = score/matched
                if avg_score > best_score:
                    best_score, best_st = avg_score, st

        if not best_st: continue

        avail = qdb("SELECT * FROM seats WHERE showtime_id=%s AND status='available' ORDER BY quality_score DESC", (best_st['id'],))
        best_seat, best_seat_score = None, -1
        for seat in avail:
            tags = json.loads(seat['position_tags']) if seat['position_tags'] else []
            s = float(seat['quality_score'])
            if seat_position in tags: s += 3
            if seat_zone in tags:     s += 2
            if s > best_seat_score:
                best_seat_score, best_seat = s, dict(seat)

        total  = best_st['total_seats'] or 1
        avail_n = best_st['avail_seats'] or 0
        results.append({
            'movie_id': mid, 'showtime_id': best_st['id'],
            'title': m['title'], 'genre': m['genre'], 'rating': m['rating'],
            'description': m['description'], 'poster_url': m['poster_url'],
            'duration_min': m['duration_min'], 'director': m['director'],
            'cast_list': m['cast_list'], 'showtime': str(best_st['showtime']),
            'hall_name': best_st['hall_name'], 'cinema_name': best_st['cinema_name'],
            'price': best_st['price'], 'available_seats': avail_n, 'total_seats': total,
            'avail_pct': round(avail_n/total*100), 'best_seat': best_seat,
            'match_score': round(best_score, 3),
        })

    results.sort(key=lambda x: (x['rating'], x['match_score']), reverse=True)
    return jsonify(results[:max_results])

@app.route('/api/cinemas')
def get_cinemas():
    return jsonify([dict(c) for c in qdb("SELECT * FROM cinemas")])

@app.route('/api/my-preferences')
@auth_required
def my_prefs():
    return jsonify(get_prefs(session['user_id']))


#  Seat API 
@app.route('/api/seats/<int:sid>')
def get_seats(sid):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE seats SET status='available', locked_by=NULL, locked_until=NULL
        WHERE showtime_id=%s AND status='locked' AND locked_until<%s
    """, (sid, now))
    db.commit()
    cur.close()

    seats = qdb(
        """SELECT id, row_num, col_num, row_label, seat_number,
                  quality_score, position_tags, status,
                  CASE WHEN locked_by=%s THEN 1 ELSE 0 END as my_lock
           FROM seats WHERE showtime_id=%s ORDER BY row_num, col_num""",
        (session.get('user_id', -1), sid))

    st = qdb(
        "SELECT s.*, m.title FROM showtimes s JOIN movies m ON s.movie_id=m.id WHERE s.id=%s",
        (sid,), one=True)
    if not st: return jsonify({'error': 'Not found'}), 404

    user_id = session.get('user_id')
    prefs   = get_prefs(user_id) if user_id else None
    seat_list = []
    for s in seats:
        row = dict(s)
        row['q_obj'] = round(float(s['quality_score']), 2)
        if prefs and s['status'] == 'available':
            tags     = json.loads(s['position_tags']) if s['position_tags'] else []
            q_obj    = float(s['quality_score'])
            pos_pref = prefs.get('seat_position_pref', 'center')
            zone_pref= prefs.get('seat_zone_pref',     'middle')
            avg_q    = float(prefs.get('avg_quality_pref', 7.0))
            pm = 1.0 if pos_pref in tags else (0.5 if 'aisle' in tags else 0.2)
            zm = 1.0 if zone_pref in tags else 0.35
            qm = max(0.0, 1.0 - abs(q_obj - avg_q) / 10.0)
            row['q_pref']     = round(pm*0.40 + zm*0.30 + qm*0.30, 4)
            row['q_pref_pct'] = round(row['q_pref']*100)
        else:
            row['q_pref'] = row['q_pref_pct'] = None
        # Convert datetime fields to string for JSON serialisation
        for k, v in row.items():
            if hasattr(v, 'strftime'): row[k] = str(v)
        seat_list.append(row)

    st_dict = dict(st)
    for k, v in st_dict.items():
        if hasattr(v, 'strftime'): st_dict[k] = str(v)
    return jsonify({'seats': seat_list, 'showtime': st_dict})


@app.route('/api/seats/lock', methods=['POST'])
@auth_required
def lock_seat():
    d = request.get_json()
    seat_id, showtime_id = d.get('seat_id'), d.get('showtime_id')
    now = datetime.utcnow()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    db  = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE seats SET status='available', locked_by=NULL, locked_until=NULL
        WHERE status='locked' AND locked_until<%s
    """, (now_str,))
    cur.execute("SELECT * FROM seats WHERE id=%s AND showtime_id=%s", (seat_id, showtime_id))
    seat = cur.fetchone()
    if not seat: cur.close(); return jsonify({'error':'Seat not found'}), 404
    if seat['status'] == 'booked': cur.close(); return jsonify({'error':'Seat already booked'}), 409
    if seat['status'] == 'locked' and seat['locked_by'] != session['user_id']:
        cur.close(); return jsonify({'error':'Seat is held by another user'}), 409
    cur.execute("""
        UPDATE seats SET status='available', locked_by=NULL, locked_until=NULL
        WHERE showtime_id=%s AND locked_by=%s AND status='locked'
    """, (showtime_id, session['user_id']))
    lock_until = (now + timedelta(seconds=LOCK_DURATION)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("UPDATE seats SET status='locked', locked_by=%s, locked_until=%s WHERE id=%s",
                (session['user_id'], lock_until, seat_id))
    db.commit()
    cur.close()
    return jsonify({'success': True, 'locked_until': lock_until})


@app.route('/api/seats/unlock', methods=['POST'])
@auth_required
def unlock_seat():
    seat_id = request.get_json().get('seat_id')
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE seats SET status='available',locked_by=NULL,locked_until=NULL WHERE id=%s AND locked_by=%s",
                (seat_id, session['user_id']))
    db.commit()
    cur.close()
    return jsonify({'success':True})


#  Booking API 
@app.route('/api/bookings/initiate', methods=['POST'])
@auth_required
def initiate_booking():
    d = request.get_json()
    sid, seat_id = d.get('showtime_id'), d.get('seat_id')
    seat = qdb("SELECT * FROM seats WHERE id=%s AND showtime_id=%s AND locked_by=%s",
               (seat_id, sid, session['user_id']), one=True)
    if not seat: return jsonify({'error':'Seat not held by you. Please select again.'}), 400
    st = qdb("SELECT * FROM showtimes WHERE id=%s", (sid,), one=True)
    if not st: return jsonify({'error':'Showtime not found'}), 404
    pay_ref = f"ABC-{secrets.token_urlsafe(10).upper()}"
    amount  = float(st['price'])
    bid = xdb("INSERT INTO bookings (user_id,showtime_id,seat_id,amount,status,payment_ref) VALUES (%s,%s,%s,%s,%s,%s)",
              (session['user_id'], sid, seat_id, amount, 'pending', pay_ref))
    user = qdb("SELECT email FROM users WHERE id=%s", (session['user_id'],), one=True)
    return jsonify({'booking_id':bid,'payment_ref':pay_ref,'amount':amount,
                    'amount_kobo':int(amount*100),'email':user['email'],
                    'public_key':PAYSTACK_PUBLIC_KEY})


@app.route('/api/bookings/verify', methods=['POST'])
@auth_required
def verify_booking():
    d = request.get_json()
    bid, ps_ref = d.get('booking_id'), d.get('paystack_ref')
    booking = qdb("SELECT * FROM bookings WHERE id=%s AND user_id=%s",
                  (bid, session['user_id']), one=True)
    if not booking: return jsonify({'error':'Booking not found'}), 404
    db  = get_db()
    cur = db.cursor()
    cur.execute("UPDATE bookings SET status='confirmed',paystack_ref=%s WHERE id=%s", (ps_ref, bid))
    cur.execute("UPDATE seats SET status='booked',locked_by=NULL,locked_until=NULL WHERE id=%s", (booking['seat_id'],))
    db.commit()
    cur.close()
    st = qdb("SELECT movie_id FROM showtimes WHERE id=%s", (booking['showtime_id'],), one=True)
    if st: learn_preferences(session['user_id'], st['movie_id'], booking['seat_id'])
    det = qdb(
        """SELECT b.*,m.title,m.poster_url,s.showtime,s.hall_name,s.cinema_name,s.price,
                  se.row_label,se.seat_number,se.quality_score,se.position_tags
           FROM bookings b
           JOIN showtimes s ON b.showtime_id=s.id
           JOIN movies m ON s.movie_id=m.id
           JOIN seats se ON b.seat_id=se.id
           WHERE b.id=%s""", (bid,), one=True)
    det_dict = dict(det)
    for k, v in det_dict.items():
        if hasattr(v, 'strftime'): det_dict[k] = str(v)
    return jsonify({'success':True,'booking':det_dict})


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
           WHERE b.user_id=%s ORDER BY b.created_at DESC""", (session['user_id'],))
    result = []
    for r in rows:
        row = dict(r)
        for k, v in row.items():
            if hasattr(v, 'strftime'): row[k] = str(v)
        result.append(row)
    return jsonify(result)


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
               WHERE b.status='confirmed' GROUP BY m.id,m.title ORDER BY bookings DESC LIMIT 5""")],
    })


@app.route('/api/admin/movies', methods=['GET','POST'])
@admin_required
def admin_movies():
    if request.method == 'POST':
        d = request.get_json()
        mid = xdb(
            "INSERT INTO movies (title,genre,description,duration_min,rating,poster_url,director,cast_list,release_year) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (d['title'],d['genre'],d.get('description',''),d.get('duration_min',120),d.get('rating',7.0),
             d.get('poster_url',''),d.get('director',''),d.get('cast_list',''),d.get('release_year',2026)))
        return jsonify({'success':True,'movie_id':mid})
    return jsonify([dict(m) for m in qdb("SELECT * FROM movies ORDER BY created_at DESC")])


@app.route('/api/admin/movies/<int:mid>', methods=['PUT','DELETE'])
@admin_required
def admin_movie(mid):
    if request.method == 'DELETE':
        xdb("UPDATE movies SET is_active=0 WHERE id=%s RETURNING id", (mid,))
        return jsonify({'success':True})
    d = request.get_json()
    xdb("UPDATE movies SET title=%s,genre=%s,description=%s,duration_min=%s,rating=%s,poster_url=%s,director=%s,cast_list=%s,release_year=%s,is_active=%s WHERE id=%s RETURNING id",
        (d['title'],d['genre'],d.get('description',''),d.get('duration_min',120),d.get('rating',7.0),
         d.get('poster_url',''),d.get('director',''),d.get('cast_list',''),d.get('release_year',2026),
         d.get('is_active',1),mid))
    return jsonify({'success':True})


@app.route('/api/admin/showtimes', methods=['GET','POST'])
@admin_required
def admin_showtimes():
    if request.method == 'POST':
        d = request.get_json()
        hall = qdb("SELECT h.*,c.name as cname FROM halls h JOIN cinemas c ON h.cinema_id=c.id WHERE h.id=%s",
                   (d['hall_id'],), one=True)
        if not hall: return jsonify({'error': 'Hall not found'}), 404

        conflict = qdb("""
            SELECT s.id, m.title, s.showtime, m.duration_min
            FROM showtimes s JOIN movies m ON s.movie_id=m.id
            WHERE s.hall_id=%s
              AND (s.showtime + (m.duration_min || ' minutes')::interval) > %s::timestamp
              AND s.showtime < (%s::timestamp + ((SELECT duration_min FROM movies WHERE id=%s) || ' minutes')::interval)
        """, (d['hall_id'], d['showtime'], d['showtime'], d['movie_id']), one=True)
        if conflict:
            return jsonify({'error': f"Hall conflict: \"{conflict['title']}\" is already scheduled at {conflict['showtime']} and runs for {conflict['duration_min']} min."}), 409

        stid = xdb(
            "INSERT INTO showtimes (movie_id,hall_id,hall_name,cinema_name,showtime,price) VALUES (%s,%s,%s,%s,%s,%s)",
            (d['movie_id'], d['hall_id'], hall['name'], hall['cname'], d['showtime'], d['price']))

        db  = get_db()
        cur = db.cursor()
        RL = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        for r in range(1, hall['total_rows']+1):
            for c in range(1, hall['total_cols']+1):
                q    = compute_seat_quality(r, c, hall['total_rows'], hall['total_cols'])
                tags = classify_seat(r, c, hall['total_rows'], hall['total_cols'])
                cur.execute(
                    "INSERT INTO seats (showtime_id,hall_id,row_num,col_num,row_label,seat_number,quality_score,position_tags,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (stid, hall['id'], r, c, RL[r-1], c, q, json.dumps(tags), 'available'))
        db.commit()
        cur.close()
        return jsonify({'success': True, 'showtime_id': stid})

    rows = qdb("SELECT s.*,m.title FROM showtimes s JOIN movies m ON s.movie_id=m.id ORDER BY s.showtime DESC LIMIT 60")
    result = []
    for r in rows:
        row = dict(r)
        for k, v in row.items():
            if hasattr(v, 'strftime'): row[k] = str(v)
        result.append(row)
    return jsonify(result)


@app.route('/api/admin/showtimes/<int:sid>', methods=['PUT','DELETE'])
@admin_required
def admin_showtime_detail(sid):
    if request.method == 'DELETE':
        xdb("DELETE FROM seats WHERE showtime_id=%s RETURNING showtime_id", (sid,))
        xdb("DELETE FROM bookings WHERE showtime_id=%s RETURNING showtime_id", (sid,))
        xdb("DELETE FROM showtimes WHERE id=%s", (sid,))
        return jsonify({'success': True})
    d = request.get_json()
    showtime, price = d.get('showtime'), d.get('price')
    if not showtime or price is None: return jsonify({'error': 'showtime and price required'}), 400
    st = qdb("SELECT * FROM showtimes WHERE id=%s", (sid,), one=True)
    if not st: return jsonify({'error': 'Showtime not found'}), 404
    conflict = qdb("""
        SELECT s.id, m.title, s.showtime, m.duration_min
        FROM showtimes s JOIN movies m ON s.movie_id=m.id
        WHERE s.hall_id=%s AND s.id!=%s
          AND (s.showtime + (m.duration_min || ' minutes')::interval) > %s::timestamp
          AND s.showtime < (%s::timestamp + ((SELECT duration_min FROM movies WHERE id=%s) || ' minutes')::interval)
    """, (st['hall_id'], sid, showtime, showtime, st['movie_id']), one=True)
    if conflict:
        return jsonify({'error': f"Hall conflict: \"{conflict['title']}\" is already scheduled at {conflict['showtime']} and runs for {conflict['duration_min']} min."}), 409
    xdb("UPDATE showtimes SET showtime=%s, price=%s WHERE id=%s", (showtime, float(price), sid))
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
    result = []
    for r in rows:
        row = dict(r)
        for k, v in row.items():
            if hasattr(v, 'strftime'): row[k] = str(v)
        result.append(row)
    return jsonify(result)


@app.route('/api/halls')
def get_halls():
    return jsonify([dict(h) for h in qdb("SELECT h.*,c.name as cinema_name FROM halls h JOIN cinemas c ON h.cinema_id=c.id")])


@app.route('/api/admin/analytics')
@admin_required
def admin_analytics():
    genres_raw = qdb("""
        SELECT m.genre, COUNT(b.id) as bookings
        FROM bookings b
        JOIN showtimes st ON b.showtime_id=st.id
        JOIN movies m ON st.movie_id=m.id
        WHERE b.status='confirmed'
        GROUP BY m.genre ORDER BY bookings DESC LIMIT 8
    """)
    genre_counts = {}
    for row in genres_raw:
        for g in row['genre'].split('·'):
            g = g.strip()
            if g: genre_counts[g] = genre_counts.get(g, 0) + row['bookings']
    genres = [{'label': k, 'value': v}
              for k, v in sorted(genre_counts.items(), key=lambda x: -x[1])[:8]]

    movies_raw = qdb("""
        SELECT m.title, COUNT(b.id) as bookings
        FROM movies m
        LEFT JOIN showtimes st ON st.movie_id=m.id
        LEFT JOIN bookings b ON b.showtime_id=st.id AND b.status='confirmed'
        GROUP BY m.id, m.title ORDER BY bookings DESC LIMIT 6
    """)
    movies_data = [{'label': r['title'], 'value': r['bookings']} for r in movies_raw]

    zones_raw = qdb("""
        SELECT c.name as zone_name, COUNT(b.id) as bookings
        FROM cinemas c
        LEFT JOIN halls h ON h.cinema_id=c.id
        LEFT JOIN showtimes st ON st.hall_id=h.id
        LEFT JOIN bookings b ON b.showtime_id=st.id AND b.status='confirmed'
        GROUP BY c.id, c.name ORDER BY bookings DESC
    """)
    zones = [{'label': r['zone_name'], 'value': r['bookings']} for r in zones_raw]
    return jsonify({'genres': genres, 'movies': movies_data, 'zones': zones})


with app.app_context():
    init_db()

if __name__ == '__main__':
    print("="*55)
    print("  ksync — Cinema Reservation System")
    print("  Abuja, Nigeria")
    print("  Admin: admin / admin123")
    print("  Demo:  demo  / demo123")
    print("="*55)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)