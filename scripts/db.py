"""Utilitaires communs pour piloter MySQL depuis Python (stdlib uniquement).

Toutes les commandes passent par `docker exec <conteneur> mysql ...` : aucun client
MySQL ni paquet pip n'est nécessaire sur l'hôte. Le conteneur, la base et le mot de
passe sont lus dans le fichier .env à la racine du dépôt (les variables d'environnement
du shell sont prioritaires).
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = ROOT / "database"

# Valeurs attendues après le seed (CLAUDE.md §5).
EXPECTED_COUNTS = {
    "users": 1_000,
    "posts": 10_000,
    "likes": 20_000,
    "follows": 5_000,
    "comments": 20_000,
}

# Emplacements habituels de docker.exe sous Windows si le PATH n'est pas à jour.
_DOCKER_FALLBACKS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe",
    Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),
]


def load_env() -> dict:
    """Lit .env (format CLE=valeur) puis applique les surcharges du shell."""
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    for key in list(env):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def docker_bin() -> str:
    found = shutil.which("docker")
    if found:
        return found
    for candidate in _DOCKER_FALLBACKS:
        if candidate.exists():
            return str(candidate)
    raise SystemExit("docker introuvable : vérifier que Docker Desktop est installé et dans le PATH")


def _mysql_cmd(env: dict, extra: list[str], use_db: bool = True) -> list[str]:
    cmd = [
        docker_bin(), "exec", "-i",
        "-e", f"MYSQL_PWD={env['MYSQL_ROOT_PASSWORD']}",
        env["MYSQL_CONTAINER"],
        "mysql", "-uroot", "--default-character-set=utf8mb4",
        *extra,
    ]
    return cmd + [env["MYSQL_DATABASE"]] if use_db else cmd


def ensure_database(env: dict | None = None) -> None:
    """Crée la base MYSQL_DATABASE si elle n'existe pas (aucune suppression)."""
    env = env or load_env()
    name = env["MYSQL_DATABASE"]
    run_sql(f"CREATE DATABASE IF NOT EXISTS `{name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;", env, use_db=False)


def run_sql(sql: str, env: dict | None = None, use_db: bool = True) -> str:
    """Exécute un script SQL (texte) ; lève une erreur si mysql échoue."""
    env = env or load_env()
    proc = subprocess.run(
        _mysql_cmd(env, ["-N", "-B"], use_db),
        input=sql.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Échec MySQL ({proc.returncode}) : {proc.stderr.decode('utf-8', 'replace')}")
    return proc.stdout.decode("utf-8")


def run_file(path: Path, env: dict | None = None) -> str:
    return run_sql(Path(path).read_text(encoding="utf-8"), env)


def query(sql: str, env: dict | None = None) -> list[list[str]]:
    """Exécute une requête et renvoie les lignes (valeurs en texte)."""
    out = run_sql(sql, env)
    return [line.split("\t") for line in out.splitlines() if line]


def table_counts(env: dict | None = None) -> dict:
    parts = [f"SELECT '{t}', COUNT(*) FROM {t}" for t in EXPECTED_COUNTS]
    rows = query(" UNION ALL ".join(parts) + ";", env)
    return {name: int(n) for name, n in rows}


def print_counts(counts: dict) -> bool:
    """Affiche les comptages face aux valeurs attendues ; renvoie True si tout correspond."""
    ok = True
    print(f"{'table':<10}{'attendu':>10}{'obtenu':>10}  statut")
    for table, expected in EXPECTED_COUNTS.items():
        got = counts.get(table)
        status = "OK" if got == expected else "ÉCART"
        ok &= got == expected
        print(f"{table:<10}{expected:>10}{got:>10}  {status}")
    return ok
