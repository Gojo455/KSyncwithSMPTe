import psycopg2
import psycopg2.extras
import json
import os
import math
from app import recommend, get_prefs, DATABASE_URL

def get_all_confirmed_bookings():
    """Pull every confirmed booking with user and movie info."""
    db  = psycopg2.connect(DATABASE_URL,
              cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()
    cur.execute("""
        SELECT b.user_id, m.id as movie_id, m.title, m.genre,
               b.created_at
        FROM bookings b
        JOIN showtimes s ON b.showtime_id = s.id
        JOIN movies m    ON s.movie_id    = m.id
        WHERE b.status = 'confirmed'
        ORDER BY b.user_id, b.created_at
    """)
    rows = cur.fetchall()
    cur.close()
    db.close()
    return [dict(r) for r in rows]


def split_train_test(bookings):
    """
    For each user: first 80% of their bookings = training set
                   last 20%                    = test set
    """
    from collections import defaultdict
    user_bookings = defaultdict(list)
    for b in bookings:
        user_bookings[b['user_id']].append(b)

    train, test = [], []
    for uid, blist in user_bookings.items():
        # Need at least 2 bookings to split meaningfully
        if len(blist) < 2:
            train.extend(blist)
            continue
        split_point = max(1, int(len(blist) * 0.8))
        train.extend(blist[:split_point])
        test.extend(blist[split_point:])

    return train, test


def precision_at_k(recommended_movie_ids, relevant_movie_ids, k=5):
    """Of the top K recommendations, how many were actually booked."""
    top_k = recommended_movie_ids[:k]
    hits  = sum(1 for mid in top_k if mid in relevant_movie_ids)
    return hits / k


def recall_at_k(recommended_movie_ids, relevant_movie_ids, k=5):
    """Of all films the user booked, how many appeared in top K."""
    if not relevant_movie_ids:
        return 0.0
    top_k = recommended_movie_ids[:k]
    hits  = sum(1 for mid in top_k if mid in relevant_movie_ids)
    return hits / len(relevant_movie_ids)


def ndcg_at_k(recommended_movie_ids, relevant_movie_ids, k=5):
    """
    Normalised Discounted Cumulative Gain.
    Rewards relevant items appearing higher in the list.
    """
    top_k = recommended_movie_ids[:k]

    # DCG — actual score
    dcg = 0.0
    for i, mid in enumerate(top_k):
        if mid in relevant_movie_ids:
            dcg += 1.0 / math.log2(i + 2)  # +2 because log2(1) = 0

    # IDCG — best possible score (all relevant items at top)
    idcg = sum(
        1.0 / math.log2(i + 2)
        for i in range(min(len(relevant_movie_ids), k))
    )

    return dcg / idcg if idcg > 0 else 0.0


def run_evaluation():
    print("Loading bookings...")
    all_bookings = get_all_confirmed_bookings()
    print(f"  Total confirmed bookings: {len(all_bookings)}")

    train_set, test_set = split_train_test(all_bookings)
    print(f"  Training bookings : {len(train_set)}")
    print(f"  Test bookings     : {len(test_set)}")

    # Group test bookings by user
    from collections import defaultdict
    test_by_user = defaultdict(list)
    for b in test_set:
        test_by_user[b['user_id']].append(b['movie_id'])

    results_p5, results_r5, results_ndcg5 = [], [], []
    skipped = 0

    print("\nEvaluating users...")
    for uid, relevant_movie_ids in test_by_user.items():
        # Get the recommender's ranked list for this user
        recs = recommend(uid)   # returns list of dicts with 'movie_id'
        if not recs:
            skipped += 1
            continue

        rec_movie_ids = [r['movie_id'] for r in recs]

        p5    = precision_at_k(rec_movie_ids, relevant_movie_ids, k=5)
        r5    = recall_at_k(rec_movie_ids,    relevant_movie_ids, k=5)
        ndcg5 = ndcg_at_k(rec_movie_ids,      relevant_movie_ids, k=5)

        results_p5.append(p5)
        results_r5.append(r5)
        results_ndcg5.append(ndcg5)

    print(f"  Users evaluated : {len(results_p5)}")
    print(f"  Users skipped   : {skipped}")
    print("\n── RESULTS ──────────────────────────────")
    print(f"  Precision@5  : {sum(results_p5)   / len(results_p5):.4f}")
    print(f"  Recall@5     : {sum(results_r5)   / len(results_r5):.4f}")
    print(f"  NDCG@5       : {sum(results_ndcg5)/ len(results_ndcg5):.4f}")
    print("─────────────────────────────────────────")


if __name__ == '__main__':
    # Push an app context so recommend() can use Flask's g
    from app import app
    with app.app_context():
        run_evaluation()