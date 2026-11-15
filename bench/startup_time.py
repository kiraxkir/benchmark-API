"""Temps de démarrage à froid des API (CLAUDE.md §10).

Usage :
    python bench/startup_time.py                    # 30 démarrages par API
    python bench/startup_time.py --essais 1 --out-dir <dossier>   # vérification rapide

Pour chaque essai : arrêt des conteneurs de l'API (`docker compose stop`, aucune suppression),
puis chronométrage de `docker compose up -d --no-build <api>` jusqu'à la première réponse 200
sur GET /api/posts?limit=1 (interrogation toutes les 50 ms). Le processus de l'API repart de
zéro (JVM, interpréteur, OPcache vide) ; le cache disque de l'hôte, lui, reste chaud.
Après 5 s au repos, la RAM de l'API est relevée (docker stats --no-stream ; Laravel = php-fpm + nginx).

Sorties (dans --out-dir, raw-results/ par défaut) :
    demarrage.csv        technologie,essai,demarrage_ms
    ram_repos.csv        technologie,essai,ram_repos_mo
    images.csv           technologie,image,taille_mo
"""
import argparse
import csv
import sys
import time
from pathlib import Path

from common import ALL_API_SERVICES, ROOT, TECHS, compose, docker, mysql_healthy, parse_mem_mib, wait_ready

IMAGES = {
    "laravel": ["benchmark-laravel:latest", "nginx:alpine"],
    "fastapi": ["benchmark-fastapi:latest"],
    "spring": ["benchmark-spring:latest"],
    "gin": ["benchmark-gin:latest"],
}
IDLE_S = 5


def append_rows(path: Path, header: list[str], rows: list[list]) -> None:
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--techs", default="laravel,fastapi,spring,gin")
    ap.add_argument("--essais", type=int, default=30)
    ap.add_argument("--out-dir", default=str(ROOT / "raw-results"))
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    techs = [t.strip() for t in args.techs.split(",") if t.strip()]

    if not mysql_healthy():
        raise SystemExit("mysql-dev n'est pas « healthy » : lancer docker compose up -d mysql")
    if (out_dir / "demarrage.csv").exists():
        raise SystemExit(f"{out_dir / 'demarrage.csv'} existe déjà : choisir un autre --out-dir "
                         "(aucune donnée n'est écrasée).")

    image_rows = []
    for tech in techs:
        size = sum(int(docker("image", "inspect", "-f", "{{.Size}}", img).stdout.strip()) for img in IMAGES[tech])
        image_rows.append([tech, "+".join(IMAGES[tech]), round(size / 1024 ** 2, 1)])
    append_rows(out_dir / "images.csv", ["technologie", "image", "taille_mo"], image_rows)

    compose("stop", *ALL_API_SERVICES)
    for tech in techs:
        cfg = TECHS[tech]
        for essai in range(1, args.essais + 1):
            compose("stop", *cfg["services"])
            t0 = time.perf_counter()
            compose("up", "-d", "--no-build", *cfg["services"])
            wait_ready(cfg["port"], timeout_s=180, poll_s=0.05)
            startup_ms = round((time.perf_counter() - t0) * 1000, 1)

            time.sleep(IDLE_S)
            stats = docker("stats", "--no-stream", "--format", "{{.Name}};{{.MemUsage}}", *cfg["containers"]).stdout
            ram = round(sum(parse_mem_mib(line.split(";")[1]) for line in stats.splitlines() if ";" in line), 1)

            append_rows(out_dir / "demarrage.csv", ["technologie", "essai", "demarrage_ms"], [[tech, essai, startup_ms]])
            append_rows(out_dir / "ram_repos.csv", ["technologie", "essai", "ram_repos_mo"], [[tech, essai, ram]])
            print(f"{tech:<8} essai {essai:02d} : démarrage {startup_ms:.0f} ms, RAM au repos {ram:.1f} Mio")
        compose("stop", *cfg["services"])
    print("Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
