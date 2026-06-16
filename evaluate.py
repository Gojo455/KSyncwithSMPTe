# ─────────────────────────────────────────────────────────
# evaluate.py  —  Offline Evaluation for KSync Recommender
# Run from your project folder:
#   python evaluate.py
# ─────────────────────────────────────────────────────────

import csv
import math
import random
from collections import defaultdict


# ════════════════════════════════════════════════════════
# STEP 1 — LOAD DATA FROM CSV FILES
# These are the files you exported from your Render database
# ════════════════════════════════════════════════════════

def load_bookings(path='bookings.csv'):
    """
    Loads every confirmed booking.
    Each row tells us: which user booked which movie.
    """
    bookings = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            bookings.append({
                'user_id':  int(row['user_id']),
                'movie_id': int(row['movie_id']),
                'title':    row['title'],
                'genre':    row['genre'],
                'rating':   float(row['rating']),
            })
    return bookings


def load_movies(path='movies.csv'):
    """
    Loads all movies in the system.
    The recommender picks from this pool.
    """
    movies = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            movies.append({
                'id':     int(row['id']),
                'title':  row['title'],
                'genre':  row['genre'],
                'rating': float(row['rating']),
            })
    return movies


# ════════════════════════════════════════════════════════
# STEP 2 — SPLIT INTO TRAINING SET AND TEST SET
#
# For each user:
#   First 80% of their bookings → training (recommender learns)
#   Last  20% of their bookings → test     (we hide and check)
# ════════════════════════════════════════════════════════

def split_train_test(bookings):
    # Group bookings by user
    user_bookings = defaultdict(list)
    for b in bookings:
        user_bookings[b['user_id']].append(b)

    train, test = [], []
    for uid, blist in user_bookings.items():
        if len(blist) < 2:
            # User only has 1 booking — cannot split, put in training
            train.extend(blist)
            continue
        # e.g. 5 bookings → first 4 = train, last 1 = test
        split_point = max(1, int(len(blist) * 0.8))
        train.extend(blist[:split_point])
        test.extend(blist[split_point:])

    return train, test


# ════════════════════════════════════════════════════════
# STEP 3 — SIMPLE GENRE-BASED RECOMMENDER
#
# This mirrors your actual hybrid engine logic.
# It builds genre weights from training bookings
# then scores every unseen movie for that user.
# ════════════════════════════════════════════════════════

def build_genre_weights(train_bookings, user_id):
    """
    Looks at what genres this user booked in the training set.
    Returns a dictionary like: {'Action': 0.7, 'Comedy': 0.4}
    Higher number = stronger preference.
    """
    gw = {}
    for b in train_bookings:
        if b['user_id'] != user_id:
            continue
        # Each genre in a multi-genre string e.g. "Action · Comedy"
        for g in b['genre'].split('·'):
            g = g.strip()
            if g:
                # Alpha = 0.3 learning rate — same as your learn_preferences()
                gw[g] = round(0.3 + 0.7 * gw.get(g, 0.0), 4)
    return gw


def recommend_for_user(user_id, train_bookings, all_movies, top_n=10):
    """
    Scores every movie the user has NOT seen in training.
    Returns a ranked list of movie IDs (best first).

    Score = 60% genre affinity + 40% IMDb rating
    This mirrors your hybrid engine weight logic.
    """
    gw = build_genre_weights(train_bookings, user_id)

    # Movies this user already booked in training — exclude from recommendations
    already_seen = {
        b['movie_id'] for b in train_bookings
        if b['user_id'] == user_id
    }

    scored = []
    for m in all_movies:
        if m['id'] in already_seen:
            continue  # Skip already watched films

        # Genre score: how much does this film's genre match user taste?
        genre_score = 0.0
        for g in m['genre'].split('·'):
            g = g.strip()
            genre_score = max(genre_score, gw.get(g, 0.0))

        # Rating score: normalise IMDb rating to 0-1 scale
        rating_score = m['rating'] / 10.0

        # Combined score — uses cold-start vs personalised logic
        if gw:
            # Returning user: weight genre preference more heavily
            final = 0.60 * genre_score + 0.40 * rating_score
        else:
            # Cold start: no history — fall back to pure rating
            final = rating_score

        scored.append((m['id'], m['title'], final))

    # Sort highest score first
    scored.sort(key=lambda x: x[2], reverse=True)

    # Return just the movie IDs in ranked order
    return [mid for mid, title, score in scored[:top_n]]


# ════════════════════════════════════════════════════════
# STEP 4 — THE THREE METRICS
# ════════════════════════════════════════════════════════

def precision_at_k(recommended_ids, relevant_ids, k=5):
    """
    Precision@5 = of the top 5 recommendations,
                  what fraction did the user actually book?

    Example: top 5 = [A, B, C, D, E]
             user actually booked [B, D]
             hits = 2, Precision@5 = 2/5 = 0.40
    """
    top_k = recommended_ids[:k]
    hits  = sum(1 for mid in top_k if mid in relevant_ids)
    return hits / k


def recall_at_k(recommended_ids, relevant_ids, k=5):
    """
    Recall@5 = of ALL the films the user actually booked,
               how many appeared in the top 5?

    Example: user booked [B, D, F] (3 total)
             top 5 caught [B, D] = 2 of them
             Recall@5 = 2/3 = 0.67
    """
    if not relevant_ids:
        return 0.0
    top_k = recommended_ids[:k]
    hits  = sum(1 for mid in top_k if mid in relevant_ids)
    return hits / len(relevant_ids)


def ndcg_at_k(recommended_ids, relevant_ids, k=5):
    """
    NDCG@5 = rewards relevant films appearing HIGHER in the list.
             A relevant film at position 1 scores more than at position 5.

    Example: top 5 = [B, A, D, C, E], relevant = [B, D]
             B is at position 1 → score = 1/log2(2) = 1.0
             D is at position 3 → score = 1/log2(4) = 0.5
             DCG = 1.5

             Best possible (both at top 2):
             IDCG = 1/log2(2) + 1/log2(3) = 1.63

             NDCG = 1.5/1.63 = 0.92
    """
    top_k = recommended_ids[:k]

    # Actual score
    dcg = sum(
        1.0 / math.log2(i + 2)
        for i, mid in enumerate(top_k)
        if mid in relevant_ids
    )

    # Best possible score
    idcg = sum(
        1.0 / math.log2(i + 2)
        for i in range(min(len(relevant_ids), k))
    )

    return dcg / idcg if idcg > 0 else 0.0


# ════════════════════════════════════════════════════════
# STEP 5 — BASELINES TO COMPARE AGAINST
# ════════════════════════════════════════════════════════

def random_baseline(all_movies, relevant_ids, k=5):
    """
    Picks k films completely at random.
    This is the worst-case baseline —
    your recommender must beat this.
    """
    all_ids  = [m['id'] for m in all_movies]
    random_recs = random.sample(all_ids, min(k, len(all_ids)))
    return (
        precision_at_k(random_recs, relevant_ids, k),
        recall_at_k(random_recs,    relevant_ids, k),
        ndcg_at_k(random_recs,      relevant_ids, k),
    )


def popularity_baseline(train_bookings, all_movies, relevant_ids, k=5):
    """
    Recommends the most-booked films in the training set first.
    This is a stronger baseline — like recommending blockbusters.
    Your recommender should beat this too.
    """
    counts = defaultdict(int)
    for b in train_bookings:
        counts[b['movie_id']] += 1

    sorted_movies = sorted(
        all_movies,
        key=lambda m: counts[m['id']],
        reverse=True
    )
    popular_ids = [m['id'] for m in sorted_movies[:k]]
    return (
        precision_at_k(popular_ids, relevant_ids, k),
        recall_at_k(popular_ids,    relevant_ids, k),
        ndcg_at_k(popular_ids,      relevant_ids, k),
    )


# ════════════════════════════════════════════════════════
# STEP 6 — RUN EVERYTHING AND PRINT RESULTS
# ════════════════════════════════════════════════════════

def average(lst):
    return sum(lst) / len(lst) if lst else 0.0


def run():
    print("=" * 55)
    print("  KSync Recommender — Offline Evaluation")
    print("=" * 55)

    # Load CSVs
    print("\nLoading data...")
    bookings   = load_bookings('bookings.csv')
    all_movies = load_movies('movies.csv')
    print(f"  Confirmed bookings : {len(bookings)}")
    print(f"  Movies in system   : {len(all_movies)}")

    # Split
    train, test = split_train_test(bookings)
    print(f"  Training bookings  : {len(train)}")
    print(f"  Test bookings      : {len(test)}")

    # Group test set by user
    test_by_user = defaultdict(list)
    for b in test:
        test_by_user[b['user_id']].append(b['movie_id'])

    print(f"  Users to evaluate  : {len(test_by_user)}")

    # ── Collect scores for each method ───────────────────
    ksync_p, ksync_r, ksync_n   = [], [], []
    random_p, random_r, random_n = [], [], []
    pop_p,    pop_r,    pop_n    = [], [], []

    print("\nRunning evaluation...")
    for uid, relevant_ids in test_by_user.items():
        relevant_set = set(relevant_ids)

        # KSync Hybrid
        recs = recommend_for_user(uid, train, all_movies)
        ksync_p.append(precision_at_k(recs, relevant_set))
        ksync_r.append(recall_at_k(recs,    relevant_set))
        ksync_n.append(ndcg_at_k(recs,      relevant_set))

        # Random baseline
        p, r, n = random_baseline(all_movies, relevant_set)
        random_p.append(p); random_r.append(r); random_n.append(n)

        # Popularity baseline
        p, r, n = popularity_baseline(train, all_movies, relevant_set)
        pop_p.append(p); pop_r.append(r); pop_n.append(n)

    # ── Print results table ───────────────────────────────
    print("\n" + "=" * 55)
    print("  RESULTS")
    print("=" * 55)
    print(f"  {'Method':<24} {'P@5':>6}  {'R@5':>6}  {'NDCG@5':>8}")
    print(f"  {'-' * 49}")
    print(f"  {'Random baseline':<24} "
          f"{average(random_p):>6.4f}  "
          f"{average(random_r):>6.4f}  "
          f"{average(random_n):>8.4f}")
    print(f"  {'Popularity baseline':<24} "
          f"{average(pop_p):>6.4f}  "
          f"{average(pop_r):>6.4f}  "
          f"{average(pop_n):>8.4f}")
    print(f"  {'KSync Hybrid':<24} "
          f"{average(ksync_p):>6.4f}  "
          f"{average(ksync_r):>6.4f}  "
          f"{average(ksync_n):>8.4f}")
    print("=" * 55)

    # ── Improvement over baselines ────────────────────────
    ndcg_lift_random = average(ksync_n) - average(random_n)
    ndcg_lift_pop    = average(ksync_n) - average(pop_n)
    print(f"\n  KSync improvement over random     : "
          f"+{ndcg_lift_random:.4f} NDCG@5")
    print(f"  KSync improvement over popularity : "
          f"+{ndcg_lift_pop:.4f} NDCG@5")
    print(f"\n  Users evaluated : {len(test_by_user)}")
    print(f"  Dataset size    : {len(bookings)} confirmed bookings")
    print("=" * 55)


def run_random_baseline(test_by_user):
    """Randomly ordered recommendations — pure chance."""
    import random
    db  = psycopg2.connect(DATABASE_URL,
              cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()
    cur.execute("SELECT id FROM movies WHERE is_active=1")
    all_movie_ids = [r['id'] for r in cur.fetchall()]
    cur.close(); db.close()

    p5_scores, ndcg_scores = [], []
    for uid, relevant in test_by_user.items():
        random_recs = random.sample(all_movie_ids, min(10, len(all_movie_ids)))
        p5_scores.append(precision_at_k(random_recs, relevant, k=5))
        ndcg_scores.append(ndcg_at_k(random_recs,    relevant, k=5))

    print(f"\nRandom baseline   → P@5: {sum(p5_scores)/len(p5_scores):.4f}  "
          f"NDCG@5: {sum(ndcg_scores)/len(ndcg_scores):.4f}")


def run_popularity_baseline(test_by_user):
    """Most-booked films first — popularity ranking."""
    db  = psycopg2.connect(DATABASE_URL,
              cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()
    cur.execute("""
        SELECT m.id FROM movies m
        LEFT JOIN showtimes s  ON s.movie_id  = m.id
        LEFT JOIN bookings  b  ON b.showtime_id = s.id
                               AND b.status = 'confirmed'
        WHERE m.is_active = 1
        GROUP BY m.id ORDER BY COUNT(b.id) DESC
    """)
    popular_ids = [r['id'] for r in cur.fetchall()]
    cur.close(); db.close()

    p5_scores, ndcg_scores = [], []
    for uid, relevant in test_by_user.items():
        p5_scores.append(precision_at_k(popular_ids, relevant, k=5))
        ndcg_scores.append(ndcg_at_k(popular_ids,    relevant, k=5))

    print(f"Popularity baseline → P@5: {sum(p5_scores)/len(p5_scores):.4f}  "
          f"NDCG@5: {sum(ndcg_scores)/len(ndcg_scores):.4f}")0

if __name__ == '__main__':
    run()