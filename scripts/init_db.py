"""(Ré)initialise complètement la base : schema.sql puis seed.sql, et vérifie les comptages.

Usage : python scripts/init_db.py
Attention : supprime et recrée toutes les tables (données perdues).
"""
import sys
import time

import db


def main() -> int:
    env = db.load_env()
    print(f"Conteneur {env['MYSQL_CONTAINER']}, base {env['MYSQL_DATABASE']}")

    db.ensure_database(env)
    t0 = time.perf_counter()
    db.run_file(db.DATABASE_DIR / "schema.sql", env)
    t1 = time.perf_counter()
    print(f"schema.sql appliqué en {t1 - t0:.2f} s")

    db.run_file(db.DATABASE_DIR / "seed.sql", env)
    t2 = time.perf_counter()
    print(f"seed.sql chargé en {t2 - t1:.2f} s")

    ok = db.print_counts(db.table_counts(env))
    print("Initialisation OK" if ok else "ERREUR : comptages inattendus")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
