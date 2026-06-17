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
          f"NDCG@5: {sum(ndcg_scores)/len(ndcg_scores):.4f}")