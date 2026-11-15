"""Éléments partagés par les scripts de bench/ (stdlib uniquement)."""
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import db  # noqa: E402

MYSQL_CONTAINER = "mysql-dev"

# Technologie -> port hôte, services compose à démarrer, conteneurs à mesurer (API).
TECHS = {
    "laravel": {"port": 8001, "services": ["laravel-fpm", "laravel"], "containers": ["laravel-fpm", "laravel-nginx"]},
    "fastapi": {"port": 8002, "services": ["fastapi"], "containers": ["fastapi-api"]},
    "spring": {"port": 8003, "services": ["spring"], "containers": ["spring-api"]},
    "gin": {"port": 8004, "services": ["gin"], "containers": ["gin-api"]},
}
ALL_API_SERVICES = [s for t in TECHS.values() for s in t["services"]]


def docker(*args, check=True, capture=True) -> subprocess.CompletedProcess:
    proc = subprocess.run([db.docker_bin(), *args], cwd=ROOT, capture_output=capture, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} a échoué : {proc.stderr.strip()}")
    return proc


def compose(*args, check=True) -> subprocess.CompletedProcess:
    return docker("compose", *args, check=check)


def http_status(url: str, timeout: float = 2.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return None


def wait_ready(port: int, timeout_s: float = 180.0, poll_s: float = 0.05) -> float:
    """Attend une réponse 200 sur GET /api/posts?limit=1 ; renvoie le temps écoulé (s)."""
    url = f"http://localhost:{port}/api/posts?limit=1"
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        if http_status(url, timeout=1.0) == 200:
            return time.perf_counter() - t0
        time.sleep(poll_s)
    raise RuntimeError(f"aucune réponse 200 sur le port {port} après {timeout_s:.0f} s")


def mysql_healthy() -> bool:
    proc = docker("inspect", "-f", "{{.State.Health.Status}}", MYSQL_CONTAINER, check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "healthy"


def parse_mem_mib(text: str) -> float:
    """'123.4MiB / 1GiB' -> 123.4 (Mio)."""
    value = text.split("/")[0].strip()
    units = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "kB": 1000 / 1024 ** 2, "MB": 1e6 / 1024 ** 2,
             "GB": 1e9 / 1024 ** 2, "B": 1 / 1024 ** 2}
    for unit in sorted(units, key=len, reverse=True):
        if value.endswith(unit):
            return float(value[: -len(unit)]) * units[unit]
    raise ValueError(f"mémoire illisible : {text!r}")


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s"
