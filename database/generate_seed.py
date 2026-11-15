"""Génère database/seed.sql : jeu de données déterministe du benchmark.

Graine aléatoire fixe (42) -> le fichier produit est identique à chaque exécution.
Volumes (CLAUDE.md §5) : 1 000 users, 10 000 posts, 20 000 likes, 5 000 follows,
20 000 comments ; inserts groupés par 1 000 lignes ; dates à partir du 2026-01-01.

Usage : python database/generate_seed.py
"""
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 42
N_USERS = 1_000
N_POSTS = 10_000
N_LIKES = 20_000
N_FOLLOWS = 5_000
N_COMMENTS = 20_000
BATCH = 1_000

START = datetime(2026, 1, 1, 0, 0, 0)
END = datetime(2026, 10, 31, 23, 59, 59)
SPAN_S = int((END - START).total_seconds())

# Vocabulaire fixe (ASCII + quelques accents pour exercer l'UTF-8), sans apostrophe ni antislash.
WORDS = (
    "api benchmark réseau social post commentaire like abonnement serveur base données "
    "requête réponse latence débit charge test mesure résultat analyse graphique "
    "rapide lent performance mémoire processeur conteneur docker mysql index table "
    "utilisateur message journée soirée café université étudiant cours projet code "
    "laravel fastapi spring gin python java go php kinshasa congo musique sport "
    "photo voyage idée question merci bravo super intéressant génial vraiment aujourd hui"
).split()

OUT = Path(__file__).resolve().parent / "seed.sql"


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def sentence(rng: random.Random, max_len: int, min_words: int, max_words: int) -> str:
    """Phrase aléatoire de longueur <= max_len caractères."""
    n = rng.randint(min_words, max_words)
    text = " ".join(rng.choice(WORDS) for _ in range(n))
    text = text[0].upper() + text[1:]
    return text[:max_len].rstrip()


def sql_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "''") + "'"


def write_batches(out, table: str, columns: str, rows: list[str]) -> None:
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        out.write(f"INSERT INTO {table} ({columns}) VALUES\n")
        out.write(",\n".join(chunk))
        out.write(";\n")


def main() -> None:
    rng = random.Random(SEED)

    # Utilisateurs : user1 ... user1000, inscrits à partir du 2026-01-01 (1 minute d'écart).
    users = [
        f"({i}, 'user{i}', '{fmt(START + timedelta(minutes=i - 1))}')"
        for i in range(1, N_USERS + 1)
    ]

    # Posts : dates aléatoires triées -> l'ordre des id suit l'ordre chronologique.
    post_times = sorted(START + timedelta(seconds=rng.randint(0, SPAN_S)) for _ in range(N_POSTS))
    posts = []
    for pid, t in enumerate(post_times, start=1):
        uid = rng.randint(1, N_USERS)
        posts.append(f"({pid}, {uid}, {sql_str(sentence(rng, 280, 3, 40))}, '{fmt(t)}')")

    # Likes : couples (user, post) uniques, datés après le post (0 à 7 jours).
    seen = set()
    likes = []
    while len(likes) < N_LIKES:
        uid = rng.randint(1, N_USERS)
        pid = rng.randint(1, N_POSTS)
        if (uid, pid) in seen:
            continue
        seen.add((uid, pid))
        t = post_times[pid - 1] + timedelta(seconds=rng.randint(60, 7 * 86400))
        likes.append(f"({len(likes) + 1}, {uid}, {pid}, '{fmt(t)}')")

    # Follows : couples uniques, jamais d'auto-abonnement.
    seen = set()
    follows = []
    while len(follows) < N_FOLLOWS:
        a = rng.randint(1, N_USERS)
        b = rng.randint(1, N_USERS)
        if a == b or (a, b) in seen:
            continue
        seen.add((a, b))
        t = START + timedelta(seconds=rng.randint(0, SPAN_S))
        follows.append(f"({len(follows) + 1}, {a}, {b}, '{fmt(t)}')")

    # Commentaires : datés après le post (0 à 7 jours).
    comments = []
    for cid in range(1, N_COMMENTS + 1):
        pid = rng.randint(1, N_POSTS)
        uid = rng.randint(1, N_USERS)
        t = post_times[pid - 1] + timedelta(seconds=rng.randint(60, 7 * 86400))
        comments.append(f"({cid}, {pid}, {uid}, {sql_str(sentence(rng, 500, 1, 25))}, '{fmt(t)}')")

    with OUT.open("w", encoding="utf-8", newline="\n") as out:
        out.write("-- Jeu de données déterministe généré par database/generate_seed.py (graine 42).\n")
        out.write("-- NE PAS MODIFIER À LA MAIN : relancer le générateur.\n")
        out.write("SET NAMES utf8mb4;\nSET foreign_key_checks = 0;\nSET unique_checks = 0;\n\n")
        write_batches(out, "users", "id, username, created_at", users)
        write_batches(out, "posts", "id, user_id, content, created_at", posts)
        write_batches(out, "likes", "id, user_id, post_id, created_at", likes)
        write_batches(out, "follows", "id, follower_id, followed_id, created_at", follows)
        write_batches(out, "comments", "id, post_id, user_id, content, created_at", comments)
        out.write("\nSET unique_checks = 1;\nSET foreign_key_checks = 1;\n")

    print(f"{OUT} écrit : {len(users)} users, {len(posts)} posts, {len(likes)} likes, "
          f"{len(follows)} follows, {len(comments)} comments")


if __name__ == "__main__":
    main()
