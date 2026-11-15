"""Remet la base à l'état du seed (database/reset.sql) et vérifie les comptages.

Usage : python scripts/reset_db.py
Code de sortie 0 si les comptages correspondent au seed et que le reset dure < 3 s.
"""
import sys
import time

import db

MAX_SECONDS = 3.0


def main() -> int:
    env = db.load_env()
    t0 = time.perf_counter()
    db.run_file(db.DATABASE_DIR / "reset.sql", env)
    elapsed = time.perf_counter() - t0
    print(f"reset.sql exécuté en {elapsed:.2f} s (limite {MAX_SECONDS:.0f} s)")

    ok = db.print_counts(db.table_counts(env)) and elapsed < MAX_SECONDS
    print("Reset OK" if ok else "ERREUR : reset incomplet ou trop lent")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
