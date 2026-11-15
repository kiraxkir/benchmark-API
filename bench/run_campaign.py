"""Campagne de mesures (CLAUDE.md §10) : k6 + docker stats, une API à la fois.

Usage :
    python bench/run_campaign.py --essais 1 --duration 5s      # essai à blanc
    python bench/run_campaign.py --resume                      # campagne complète (avec accord !)

Déroulement : les essais sont répartis en 3 tours (10 essais par tour pour 30 essais) ; l'ordre des
technologies est décalé d'un cran à chaque tour. Pour chaque technologie : démarrage de ses seuls
conteneurs (les autres API arrêtées), attente d'une réponse 200, échauffement (10 s à 10 VU),
puis pour chaque essai et chaque charge : reset de la base, pause 2 s, k6 + échantillonnage
`docker stats` (API + mysql-dev) dans un thread séparé, ajout des lignes au CSV.

Sorties (dans --out-dir, raw-results/ par défaut) :
    mesures.csv                         1 ligne par essai × endpoint (+ endpoint=all)
    k6/<tech>_<vus>vu_essai<NN>.json    résumé k6 complet
    stats/<tech>_<vus>vu_essai<NN>.csv  échantillons docker stats bruts
    campagne.json                       paramètres de la campagne (contrôlés par --resume)

Unités : CPU en % d'un cœur, tel que rapporté par docker stats (2 cœurs = 200 %) ; pour Laravel,
CPU et RAM de l'API = php-fpm + nginx (sommés). RAM en Mio.
"""
import argparse
import csv
import json
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from common import (ALL_API_SERVICES, MYSQL_CONTAINER, ROOT, TECHS, compose, db, fmt_duration,
                    mysql_healthy, parse_mem_mib, wait_ready)

SCENARIO = ROOT / "load-tests" / "mixed.js"
ENDPOINTS = ["list_posts", "create_post", "like", "follow", "comment"]
COLUMNS = ["technologie", "endpoint", "utilisateurs", "essai", "horodatage", "requetes", "req_s",
           "lat_moy_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "lat_max_ms", "erreurs_pct",
           "cpu_api_moy_pct", "ram_api_max_mo", "cpu_mysql_moy_pct"]
ROUNDS = 3
WARMUP_VUS, WARMUP_DURATION = 10, "10s"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ---------------------------------------------------------------- échantillonnage docker stats

class StatsSampler:
    """Lit `docker stats` en flux continu (rafraîchi par Docker ~2 fois/s) dans un thread séparé."""

    def __init__(self, containers: list[str]):
        self.containers = containers
        self.samples: list[tuple[float, str, float, float]] = []  # (t, conteneur, cpu %, mem Mio)
        self._proc = None
        self._thread = None
        self._t0 = 0.0

    def start(self):
        self._t0 = time.perf_counter()
        self._proc = subprocess.Popen(
            [db.docker_bin(), "stats", "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}}", *self.containers],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        for line in self._proc.stdout:
            line = ANSI.sub("", line).strip()
            parts = line.split(";")
            if len(parts) != 3 or parts[0] not in self.containers or parts[1] in ("", "--"):
                continue
            try:
                cpu = float(parts[1].rstrip("%"))
                mem = parse_mem_mib(parts[2])
            except ValueError:
                continue
            self.samples.append((round(time.perf_counter() - self._t0, 3), parts[0], cpu, mem))

    def stop(self):
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._thread.join(timeout=5)

    def per_refresh(self, containers: list[str]) -> list[tuple[float, float]]:
        """Somme (cpu, mem) des conteneurs donnés à chaque rafraîchissement complet."""
        groups: dict[int, dict[str, tuple[float, float]]] = {}
        index = {c: 0 for c in self.containers}
        for _t, name, cpu, mem in self.samples:
            groups.setdefault(index[name], {})[name] = (cpu, mem)
            index[name] += 1
        out = []
        for refresh in groups.values():
            if all(c in refresh for c in containers):
                out.append((sum(refresh[c][0] for c in containers), sum(refresh[c][1] for c in containers)))
        return out


# ---------------------------------------------------------------- k6

def run_k6(base_url: str, vus: int, duration: str, out: Path) -> None:
    cmd = ["k6", "run", "--quiet", "--no-usage-report",
           "-e", f"BASE_URL={base_url}", "-e", f"VUS={vus}", "-e", f"DURATION={duration}", "-e", f"OUT={out}",
           str(SCENARIO)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"k6 a échoué (code {proc.returncode}) :\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")


def k6_row(metrics: dict, suffix: str) -> dict | None:
    """Valeurs k6 d'un endpoint (suffix '{name:like}') ou du global (suffix '')."""
    reqs = metrics.get(f"http_reqs{suffix}")
    dur = metrics.get(f"http_req_duration{suffix}")
    failed = metrics.get(f"http_req_failed{suffix}")
    if not reqs or not dur or not failed or reqs["values"].get("count", 0) == 0:
        return None
    v = dur["values"]
    return {
        "requetes": int(reqs["values"]["count"]),
        "req_s": round(reqs["values"]["rate"], 3),
        "lat_moy_ms": round(v["avg"], 3), "p50_ms": round(v["med"], 3), "p90_ms": round(v["p(90)"], 3),
        "p95_ms": round(v["p(95)"], 3), "p99_ms": round(v["p(99)"], 3), "lat_max_ms": round(v["max"], 3),
        "erreurs_pct": round(100 * failed["values"]["rate"], 4),
    }


# ---------------------------------------------------------------- campagne

def trial_name(tech: str, vus: int, essai: int) -> str:
    return f"{tech}_{vus}vu_essai{essai:02d}"


def load_done(csv_path: Path) -> set[tuple[str, int, int]]:
    if not csv_path.exists():
        return set()
    with csv_path.open(encoding="utf-8", newline="") as f:
        return {(r["technologie"], int(r["utilisateurs"]), int(r["essai"]))
                for r in csv.DictReader(f) if r["endpoint"] == "all"}


def split_rounds(essais: int) -> list[list[int]]:
    n = min(ROUNDS, essais)
    size, extra = divmod(essais, n)
    rounds, start = [], 1
    for r in range(n):
        count = size + (1 if r < extra else 0)
        rounds.append(list(range(start, start + count)))
        start += count
    return rounds


def check_meta(out_dir: Path, params: dict, resume: bool, already: int) -> None:
    meta_path = out_dir / "campagne.json"
    if already and not resume:
        raise SystemExit(f"{out_dir / 'mesures.csv'} contient déjà {already} essai(s). Utiliser --resume pour "
                         "compléter cette campagne, ou choisir un autre --out-dir.")
    if already and meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        for key in ("duration", "warmup"):
            if old.get(key) != params[key]:
                raise SystemExit(f"--resume refusé : {key} = {params[key]!r} alors que les essais existants "
                                 f"ont été faits avec {old.get(key)!r} ({meta_path}).")
    if not meta_path.exists():
        meta_path.write_text(json.dumps(params, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--techs", default="laravel,fastapi,spring,gin")
    ap.add_argument("--vus", default="10,50,100")
    ap.add_argument("--essais", type=int, default=30)
    ap.add_argument("--duration", default="15s")
    ap.add_argument("--resume", action="store_true", help="sauter les essais déjà présents dans mesures.csv")
    ap.add_argument("--out-dir", default=str(ROOT / "raw-results"))
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace", line_buffering=True)

    techs = [t.strip() for t in args.techs.split(",") if t.strip()]
    unknown = [t for t in techs if t not in TECHS]
    if unknown:
        raise SystemExit(f"technologie(s) inconnue(s) : {unknown}")
    vus_levels = [int(v) for v in args.vus.split(",")]
    out_dir = Path(args.out_dir)
    (out_dir / "k6").mkdir(parents=True, exist_ok=True)
    (out_dir / "stats").mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "mesures.csv"

    if not mysql_healthy():
        raise SystemExit("mysql-dev n'est pas « healthy » : lancer docker compose up -d mysql")

    done = load_done(csv_path)
    params = {"techs": techs, "vus": vus_levels, "essais": args.essais, "duration": args.duration,
              "warmup": f"{WARMUP_DURATION} à {WARMUP_VUS} VU", "rounds": ROUNDS,
              "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    check_meta(out_dir, params, args.resume, len(done))

    rounds = split_rounds(args.essais)
    plan = []  # (tour, tech, [(essai, vus), ...])
    for r, essais in enumerate(rounds):
        order = techs[r % len(techs):] + techs[:r % len(techs)]
        for tech in order:
            todo = [(e, v) for e in essais for v in vus_levels if (tech, v, e) not in done]
            if todo:
                plan.append((r + 1, tech, todo))
    total = sum(len(t) for _, _, t in plan)
    print(f"Campagne : {total} essai(s) à faire ({len(done)} déjà présents), durée {args.duration}, "
          f"{len(rounds)} tour(s), sortie {out_dir}")
    if total == 0:
        return 0

    new_file = not csv_path.exists() or csv_path.stat().st_size == 0
    trial_times: list[float] = []
    block_overheads: list[float] = []
    count = 0
    t_campaign = time.perf_counter()
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
            f.flush()
        for block, (tour, tech, todo) in enumerate(plan):
            cfg = TECHS[tech]
            base_url = f"http://localhost:{cfg['port']}"
            t_block = time.perf_counter()
            # Toutes les API arrêtées (y compris celle-ci) : chaque bloc part d'un démarrage neuf.
            compose("stop", *ALL_API_SERVICES)
            compose("up", "-d", "--no-build", *cfg["services"])
            ready = wait_ready(cfg["port"])
            print(f"\n== Tour {tour} — {tech} : prêt en {ready:.1f} s ; échauffement {WARMUP_DURATION} à {WARMUP_VUS} VU")
            with tempfile.TemporaryDirectory() as tmp:
                run_k6(base_url, WARMUP_VUS, WARMUP_DURATION, Path(tmp) / "warmup.json")
            block_overheads.append(time.perf_counter() - t_block)

            for essai, vus in todo:
                t_trial = time.perf_counter()
                db.run_file(db.DATABASE_DIR / "reset.sql")
                time.sleep(2)
                name = trial_name(tech, vus, essai)
                k6_json = out_dir / "k6" / f"{name}.json"
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                sampler = StatsSampler(cfg["containers"] + [MYSQL_CONTAINER])
                sampler.start()
                try:
                    run_k6(base_url, vus, args.duration, k6_json)
                finally:
                    sampler.stop()

                with (out_dir / "stats" / f"{name}.csv").open("w", encoding="utf-8", newline="") as sf:
                    sw = csv.writer(sf)
                    sw.writerow(["t_s", "conteneur", "cpu_pct", "mem_mio"])
                    sw.writerows(sampler.samples)
                api = sampler.per_refresh(cfg["containers"])
                mysql = sampler.per_refresh([MYSQL_CONTAINER])
                if not api or not mysql:
                    raise RuntimeError(f"aucun échantillon docker stats pour {name}")
                resources = {
                    "cpu_api_moy_pct": round(statistics.fmean(c for c, _ in api), 3),
                    "ram_api_max_mo": round(max(m for _, m in api), 3),
                    "cpu_mysql_moy_pct": round(statistics.fmean(c for c, _ in mysql), 3),
                }

                metrics = json.loads(k6_json.read_text(encoding="utf-8"))["metrics"]
                rows = []
                for endpoint, suffix in [(e, f"{{name:{e}}}") for e in ENDPOINTS] + [("all", "")]:
                    values = k6_row(metrics, suffix)
                    if values is None:
                        print(f"   ATTENTION : aucune requête « {endpoint} » dans {name} (ligne omise)")
                        continue
                    rows.append({"technologie": tech, "endpoint": endpoint, "utilisateurs": vus, "essai": essai,
                                 "horodatage": stamp, **values, **resources})
                writer.writerows(rows)
                f.flush()

                count += 1
                trial_times.append(time.perf_counter() - t_trial)
                g = next(r for r in rows if r["endpoint"] == "all")
                remaining_trials = total - count
                remaining_blocks = len(plan) - block - 1
                eta = (remaining_trials * statistics.fmean(trial_times)
                       + remaining_blocks * statistics.fmean(block_overheads))
                print(f"[{count}/{total}] {tech:<8}{vus:>4} VU essai {essai:02d} : {g['requetes']} req, "
                      f"{g['req_s']:.1f} req/s, p95 {g['p95_ms']:.1f} ms, erreurs {g['erreurs_pct']:.2f} %, "
                      f"CPU API {resources['cpu_api_moy_pct']:.0f} %, RAM {resources['ram_api_max_mo']:.0f} Mio "
                      f"— reste ~{fmt_duration(eta)}")
            compose("stop", *cfg["services"])

    print(f"\nTerminé : {count} essai(s) en {fmt_duration(time.perf_counter() - t_campaign)}. "
          f"Base remise à zéro.")
    db.run_file(db.DATABASE_DIR / "reset.sql")
    return 0


if __name__ == "__main__":
    sys.exit(main())
