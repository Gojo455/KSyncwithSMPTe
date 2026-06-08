# add_movies.py — run with: python add_movies.py
import sqlite3, os, json

DB_PATH = os.path.join('instance', 'ksync.db')

movies = [
    # ROMANCE (heavy focus as requested)
    ("The Notebook", "Romance · Drama",
     "A poor young man falls in love with a rich girl in 1940s South Carolina, but her parents disapprove. Years later, an old man reads their love story to a woman with dementia.",
     123, 7.9, "English", "2004-06-25",
     "https://image.tmdb.org/t/p/w500/qom1SZSENdmHFNZBXbtLAGselQQ.jpg",
     "#1a1a2e", 2500, 0, 1,
     '["Ryan Gosling","Rachel McAdams","James Garner"]', "Nick Cassavetes", "PG-13"),

    ("Crazy Rich Asians", "Romance · Comedy · Drama",
     "A New York professor accompanies her boyfriend to Singapore for his best friend's wedding, only to discover his family is one of the richest in Asia.",
     120, 6.9, "English", "2018-08-15",
     "https://image.tmdb.org/t/p/w500/lhkzVhXVfEkNxluTkSRfZPAXFHs.jpg",
     "#1a0a2e", 2500, 0, 1,
     '["Constance Wu","Henry Golding","Michelle Yeoh"]', "Jon M. Chu", "PG-13"),

    ("La La Land", "Romance · Musical · Drama",
     "A jazz pianist and an aspiring actress fall in love while pursuing their dreams in Los Angeles, testing whether love and ambition can coexist.",
     128, 8.0, "English", "2016-12-09",
     "https://image.tmdb.org/t/p/w500/uDO8zWDhfWwoFdKS4fzkUJt0Rf0.jpg",
     "#0a1a3e", 2500, 1, 0,
     '["Ryan Gosling","Emma Stone","John Legend"]', "Damien Chazelle", "PG-13"),

    ("Me Before You", "Romance · Drama",
     "A small-town woman takes a job caring for a paralysed man, and the two develop an unexpected bond that changes both their lives forever.",
     110, 7.4, "English", "2016-06-03",
     "https://image.tmdb.org/t/p/w500/qGABQGMAPzSuQk2eLhlpEjqJPuB.jpg",
     "#2a0a1e", 2500, 0, 0,
     '["Emilia Clarke","Sam Claflin","Janet McTeer"]', "Thea Sharrock", "PG-13"),

    ("A Walk to Remember", "Romance · Drama",
     "A popular teenager falls in love with a minister's daughter who is battling a serious illness, transforming his life and priorities completely.",
     102, 7.4, "English", "2002-01-25",
     "https://image.tmdb.org/t/p/w500/4aJoO4GtCMxFMNQcJVvXy2tGTVk.jpg",
     "#1a0a0e", 2500, 0, 0,
     '["Mandy Moore","Shane West","Peter Coyote"]', "Adam Shankman", "PG"),

    ("Pride & Prejudice", "Romance · Drama · Period",
     "Spirited Elizabeth Bennet meets the proud and seemingly arrogant Mr Darcy in 19th-century England, and both must overcome their prejudices to find love.",
     129, 7.8, "English", "2005-11-11",
     "https://image.tmdb.org/t/p/w500/bPuCBDTVgMIUjSGcMOsOkEjsJhd.jpg",
     "#2a1a0e", 2500, 0, 0,
     '["Keira Knightley","Matthew Macfadyen","Judi Dench"]', "Joe Wright", "PG"),

    ("About Time", "Romance · Drama · Fantasy",
     "A young man discovers he can travel back in time and uses this ability to improve his love life, but learns that the best moments are worth living only once.",
     123, 7.8, "English", "2013-11-01",
     "https://image.tmdb.org/t/p/w500/zBHkpVFb9lkzAMgFGQqGFFlEh2k.jpg",
     "#0a2a1e", 2500, 0, 1,
     '["Domhnall Gleeson","Rachel McAdams","Bill Nighy"]', "Richard Curtis", "R"),

    ("The Proposal", "Romance · Comedy",
     "A Canadian book editor faces deportation and convinces her assistant to marry her. They travel to Alaska to meet his family, and feelings start to complicate the plan.",
     108, 6.7, "English", "2009-06-19",
     "https://image.tmdb.org/t/p/w500/nGwivQGqMhANMGJlSiCiMrfZDSC.jpg",
     "#1a2a2e", 2500, 0, 0,
     '["Sandra Bullock","Ryan Reynolds","Betty White"]', "Anne Fletcher", "PG-13"),

    ("Five Feet Apart", "Romance · Drama",
     "Two teenagers with cystic fibrosis meet in a hospital and fall in love, but their condition means they must remain at least five feet apart at all times.",
     116, 7.2, "English", "2019-03-15",
     "https://image.tmdb.org/t/p/w500/2Ah63TIvVmZM3hzUwR5hXFg2LEk.jpg",
     "#0a1a2e", 2500, 0, 1,
     '["Cole Sprouse","Haley Lu Richardson","Moises Arias"]', "Justin Baldoni", "PG-13"),

    ("To All the Boys I've Loved Before", "Romance · Comedy · Drama",
     "A high schooler's secret love letters are sent out to all her crushes, forcing her to navigate the fallout — including a fake relationship that becomes real.",
     99, 7.1, "English", "2018-08-17",
     "https://image.tmdb.org/t/p/w500/sAPSAZhfKnZCluBqaB5pgE7dRJF.jpg",
     "#2a1a3e", 2500, 0, 0,
     '["Lana Condor","Noah Centineo","Janel Parrish"]', "Susan Johnson", "TV-G"),

    # NOLLYWOOD
    ("A Tribe Called Judah", "Drama · Crime · Nollywood",
     "A determined mother raises five sons in the face of poverty and hardship in Lagos, only to watch her children drift toward different fates as adults.",
     135, 7.2, "Yoruba/English", "2023-12-15",
     "https://image.tmdb.org/t/p/w500/placeholder.jpg",
     "#1a2a0e", 2500, 1, 1,
     '["Funke Akindele","Timini Egbuson","Broda Shaggi"]', "Funke Akindele", "PG-13"),

    ("The Black Book", "Action · Thriller · Nollywood",
     "A deacon seeks revenge after his son is killed by corrupt police officers, uncovering a web of conspiracy that reaches the highest levels of power.",
     130, 7.0, "English", "2023-09-22",
     "https://image.tmdb.org/t/p/w500/placeholder2.jpg",
     "#0a0a1e", 2500, 0, 1,
     '["Richard Mofe-Damijo","Sam Dede","Ireti Doyle"]', "Editi Effiong", "PG-13"),

    # ACTION / SCI-FI
    ("Dune: Part Two", "Sci-Fi · Action · Adventure",
     "Paul Atreides unites with the Fremen people of Arrakis to wage war against those who destroyed his family, fulfilling an ancient prophecy.",
     166, 8.5, "English", "2024-03-01",
     "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
     "#1a1a0e", 5500, 1, 1,
     '["Timothée Chalamet","Zendaya","Rebecca Ferguson"]', "Denis Villeneuve", "PG-13"),

    ("Interstellar", "Sci-Fi · Drama · Adventure",
     "A team of explorers travel through a wormhole in space in an attempt to ensure humanity's survival as Earth faces extinction.",
     169, 8.7, "English", "2014-11-07",
     "https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg",
     "#0a0a2e", 3500, 1, 0,
     '["Matthew McConaughey","Anne Hathaway","Jessica Chastain"]', "Christopher Nolan", "PG-13"),

    ("The Substance", "Horror · Sci-Fi · Body Horror",
     "A fading celebrity takes a black market substance that creates a younger version of herself, but the two must share one body — a week each — with horrifying consequences.",
     140, 7.3, "English", "2024-09-20",
     "https://image.tmdb.org/t/p/w500/lqoMzCcZYEFK729d6qzt349fB4o.jpg",
     "#2a0a0e", 4000, 0, 1,
     '["Demi Moore","Margaret Qualley","Dennis Quaid"]', "Coralie Fargeat", "R"),
]

def seed_movies():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    added = 0
    skipped = 0
    for m in movies:
        existing = conn.execute(
            "SELECT id FROM movies WHERE title=?", (m[0],)
        ).fetchone()
        if existing:
            print(f"  ⏭  Skipping (already exists): {m[0]}")
            skipped += 1
            continue
        conn.execute("""
            INSERT INTO movies
            (title, genre, description, duration_min, rating, language, release_date,
             poster_url, poster_color, price, is_featured, is_hot,
             cast_list, director, age_rating, is_active)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """, m)
        print(f"  ✅ Added: {m[0]}")
        added += 1

    conn.commit()
    conn.close()
    print(f"\nDone — {added} movies added, {skipped} skipped.")
    print("Commit app.py to GitHub and Render will redeploy automatically.")

if __name__ == '__main__':
    seed_movies()
