"""Tests fonctionnels du contrat (CLAUDE.md §5 et §8), identiques pour les 4 API.

Usage :
    python tests/contract_test.py --port 8001      # une API
    python tests/contract_test.py --all            # les 4 API (8001 à 8004) + comparaison des corps

Python stdlib uniquement. Avant chaque API, la base est remise à l'état du seed
(scripts/reset_db.py) : toutes les API partent du même état, ce qui permet de comparer les
corps exacts des réponses (hors `id` et `created_at` des créations).
Le résultat est affiché et écrit dans raw-results/contract_test.txt.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import db  # noqa: E402

APIS = {8001: "laravel", 8002: "fastapi", 8003: "spring", 8004: "gin"}
OUT_FILE = ROOT / "raw-results" / "contract_test.txt"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
BIGINT_MAX = 9223372036854775807


# ---------------------------------------------------------------- données de test (tirées du seed)

def seed_fixtures() -> dict:
    """Couples libres (non présents dans le seed) et couples existants, calculés une fois."""
    free_like_user = db.query(
        "SELECT u.id FROM users u WHERE NOT EXISTS "
        "(SELECT 1 FROM likes l WHERE l.post_id = 5 AND l.user_id = u.id) ORDER BY u.id LIMIT 1;")[0][0]
    seed_like = db.query("SELECT user_id, post_id FROM likes WHERE id = 1;")[0]
    free_follow = db.query(
        "SELECT a.id, b.id FROM users a JOIN users b ON b.id = a.id + 1 WHERE NOT EXISTS "
        "(SELECT 1 FROM follows f WHERE f.follower_id = a.id AND f.followed_id = b.id) "
        "ORDER BY a.id LIMIT 1;")[0]
    seed_follow = db.query("SELECT follower_id, followed_id FROM follows WHERE id = 1;")[0]
    comments_on_7 = int(db.query("SELECT COUNT(*) FROM comments WHERE post_id = 7;")[0][0])
    return {
        "like_user": int(free_like_user),
        "seed_like": (int(seed_like[0]), int(seed_like[1])),
        "follow": (int(free_follow[0]), int(free_follow[1])),
        "seed_follow": (int(seed_follow[0]), int(seed_follow[1])),
        "comments_on_7": comments_on_7,
    }


# ---------------------------------------------------------------- client HTTP

def call(port: int, method: str, path: str, body: str | None):
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://localhost:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status, raw, ctype = r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        status, raw, ctype = e.code, e.read(), e.headers.get("Content-Type", "")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        parsed = None
    return status, parsed, ctype, raw


# ---------------------------------------------------------------- validateurs

def err(code):
    return {"error": code}


def is_int(v):
    return type(v) is int


def check_list(expected_page, expected_limit, expected_len=None):
    """Structure, types, tri (created_at DESC, id DESC) et comptes recoupés avec MySQL."""
    def check(body, ctx):
        if not isinstance(body, dict) or list(body.keys()) != ["page", "limit", "data"]:
            return f"clés inattendues : {body if not isinstance(body, dict) else list(body)}"
        if body["page"] != expected_page or body["limit"] != expected_limit:
            return f"page/limit = {body['page']}/{body['limit']}"
        if not is_int(body["page"]) or not is_int(body["limit"]):
            return "page/limit ne sont pas des entiers"
        data = body["data"]
        if expected_len is not None and len(data) != expected_len:
            return f"{len(data)} éléments au lieu de {expected_len}"
        keys = ["id", "user_id", "username", "content", "created_at", "likes_count", "comments_count"]
        for item in data:
            if list(item.keys()) != keys:
                return f"clés d'un post : {list(item.keys())}"
            for k in ("id", "user_id", "likes_count", "comments_count"):
                if not is_int(item[k]):
                    return f"{k} n'est pas un entier : {item[k]!r}"
            if not DATE_RE.match(item["created_at"]):
                return f"created_at mal formé : {item['created_at']!r}"
            if item["username"] != f"user{item['user_id']}":
                return f"username {item['username']} pour user_id {item['user_id']}"
        order = [(i["created_at"], i["id"]) for i in data]
        if order != sorted(order, reverse=True) or len(set(order)) != len(order):
            return "tri created_at DESC, id DESC non respecté"
        if data:
            ids = ",".join(str(i["id"]) for i in data)
            truth = {int(r[0]): (int(r[1]), int(r[2])) for r in db.query(
                f"SELECT p.id, (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id), "
                f"(SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) FROM posts p WHERE p.id IN ({ids});")}
            for item in data:
                if truth.get(item["id"]) != (item["likes_count"], item["comments_count"]):
                    return f"comptes faux pour le post {item['id']} : MySQL {truth.get(item['id'])}"
        return None
    return check


def check_page_continuity(body, ctx):
    """page=2&limit=5 doit correspondre aux éléments 6 à 10 de page=1&limit=10."""
    _, ref, _, _ = call(ctx["port"], "GET", "/api/posts?page=1&limit=10", None)
    if body["data"] != ref["data"][5:10]:
        return "page 2 (limit 5) différente des éléments 6-10 de page 1 (limit 10)"
    return check_list(2, 5, 5)(body, ctx)


def check_created_post(expected_user, expected_content):
    def check(body, ctx):
        if not isinstance(body, dict) or list(body.keys()) != ["id", "user_id", "content", "created_at"]:
            return f"corps inattendu : {body}"
        if not is_int(body["id"]) or body["id"] <= 10000:
            return f"id inattendu : {body['id']!r}"
        if body["user_id"] != expected_user or body["content"] != expected_content:
            return f"user_id/content : {body['user_id']!r} / {body['content']!r}"
        if not DATE_RE.match(body["created_at"]):
            return f"created_at mal formé : {body['created_at']!r}"
        stored = db.query(f"SELECT user_id, content FROM posts WHERE id = {body['id']};")
        if stored != [[str(expected_user), expected_content]]:
            return f"ligne en base : {stored}"
        ctx["last_post_id"] = body["id"]
        return None
    return check


def check_new_post_first(body, ctx):
    """Le post créé juste avant apparaît en tête de liste, sans like ni commentaire."""
    first = body["data"][0] if body.get("data") else None
    if not first or first["id"] != ctx.get("last_post_id"):
        return f"le post créé ({ctx.get('last_post_id')}) n'est pas en tête : {first}"
    if first["username"] != "user12" or first["likes_count"] != 0 or first["comments_count"] != 0:
        return f"post en tête inattendu : {first}"
    return check_list(1, 1, 1)(body, ctx)


def check_exact(expected):
    def check(body, ctx):
        return None if body == expected else f"corps {body} au lieu de {expected}"
    return check


def check_like_row(user, post):
    def check(body, ctx):
        if body != {"post_id": post, "user_id": user}:
            return f"corps {body}"
        if db.query(f"SELECT COUNT(*) FROM likes WHERE user_id = {user} AND post_id = {post};") != [["1"]]:
            return "like absent en base"
        return None
    return check


def check_follow_row(follower, followed):
    def check(body, ctx):
        if body != {"follower_id": follower, "followed_id": followed}:
            return f"corps {body}"
        q = f"SELECT COUNT(*) FROM follows WHERE follower_id = {follower} AND followed_id = {followed};"
        if db.query(q) != [["1"]]:
            return "follow absent en base"
        return None
    return check


def check_comment(post, user, content):
    def check(body, ctx):
        keys = ["id", "post_id", "user_id", "content", "created_at"]
        if not isinstance(body, dict) or list(body.keys()) != keys:
            return f"corps inattendu : {body}"
        if not is_int(body["id"]) or body["id"] <= 20000:
            return f"id inattendu : {body['id']!r}"
        if (body["post_id"], body["user_id"], body["content"]) != (post, user, content):
            return f"champs : {body}"
        if not DATE_RE.match(body["created_at"]):
            return f"created_at mal formé : {body['created_at']!r}"
        n = int(db.query(f"SELECT COUNT(*) FROM comments WHERE post_id = {post};")[0][0])
        if n != ctx["fx"]["comments_on_7"] + ctx["comments_added"] + 1:
            return f"{n} commentaires en base sur le post {post}"
        ctx["comments_added"] += 1
        return None
    return check


# ---------------------------------------------------------------- liste des cas

def build_cases(fx: dict) -> list[tuple]:
    """(catégorie, nom, méthode, chemin, corps JSON brut, statut attendu, attendu)."""
    lu = fx["like_user"]
    su, sp = fx["seed_like"]
    fa, fb = fx["follow"]
    sfa, sfb = fx["seed_follow"]
    e280, e281 = "é" * 280, "é" * 281
    a500, a501 = "a" * 500, "a" * 501
    V = err("validation_error")
    NF = err("not_found")
    UNF = err("user_not_found")
    PNF = err("post_not_found")
    c = []
    L = "GET posts"
    c += [
        (L, "défauts (page 1, limit 20)", "GET", "/api/posts", None, 200, check_list(1, 20, 20)),
        (L, "limit=2", "GET", "/api/posts?limit=2", None, 200, check_list(1, 2, 2)),
        (L, "limit=1 (borne basse)", "GET", "/api/posts?limit=1", None, 200, check_list(1, 1, 1)),
        (L, "limit=100 (borne haute) + tri", "GET", "/api/posts?limit=100", None, 200, check_list(1, 100, 100)),
        (L, "page=2&limit=5 (continuité)", "GET", "/api/posts?page=2&limit=5", None, 200, check_page_continuity),
        (L, "page=50 (page du scénario k6)", "GET", "/api/posts?page=50", None, 200, check_list(50, 20, 20)),
        (L, "dernière page pleine (2000×5)", "GET", "/api/posts?page=2000&limit=5", None, 200, check_list(2000, 5, 5)),
        (L, "page au-delà des données", "GET", "/api/posts?page=2001&limit=5", None, 200, check_list(2001, 5, 0)),
        (L, "page=007 lue comme 7", "GET", "/api/posts?page=007&limit=3", None, 200, check_list(7, 3, 3)),
        (L, "page=0", "GET", "/api/posts?page=0", None, 400, V),
        (L, "page=-1", "GET", "/api/posts?page=-1", None, 400, V),
        (L, "page=abc", "GET", "/api/posts?page=abc", None, 400, V),
        (L, "page=1.5", "GET", "/api/posts?page=1.5", None, 400, V),
        (L, "page=+1", "GET", "/api/posts?page=%2B1", None, 400, V),
        (L, "page= (vide)", "GET", "/api/posts?page=", None, 400, V),
        (L, "limit=0", "GET", "/api/posts?limit=0", None, 400, V),
        (L, "limit=101", "GET", "/api/posts?limit=101", None, 400, V),
        (L, "limit= (vide)", "GET", "/api/posts?limit=", None, 400, V),
        (L, "limit=abc", "GET", "/api/posts?limit=abc", None, 400, V),
        (L, "page > BIGINT", "GET", "/api/posts?page=9223372036854775808", None, 400, V),
        (L, "(page-1)×limit > BIGINT", "GET", "/api/posts?page=9223372036854775807&limit=2", None, 400, V),
    ]
    P = "POST posts"
    c += [
        (P, "nominal", "POST", "/api/posts", '{"user_id":12,"content":"Mon premier post"}', 201,
         check_created_post(12, "Mon premier post")),
        (L, "post créé en tête de liste", "GET", "/api/posts?limit=1", None, 200, check_new_post_first),
        (P, "trim Unicode + emoji", "POST", "/api/posts",
         '{"user_id":12,"content":"\\u3000\\u00a0 Bonjour été 🎉\\n\\t"}', 201,
         check_created_post(12, "Bonjour été 🎉")),
        (P, "U+200B non retiré", "POST", "/api/posts", '{"user_id":12,"content":"\\u200bx"}', 201,
         check_created_post(12, "​x")),
        (P, "280 caractères accentués", "POST", "/api/posts", '{"user_id":12,"content":"' + e280 + '"}', 201,
         check_created_post(12, e280)),
        (P, "champ supplémentaire ignoré", "POST", "/api/posts",
         '{"user_id":12,"content":"x","extra":[1,{"a":2}]}', 201, check_created_post(12, "x")),
        (P, "281 caractères", "POST", "/api/posts", '{"user_id":12,"content":"' + e281 + '"}', 400, V),
        (P, "user_id manquant", "POST", "/api/posts", '{"content":"x"}', 400, V),
        (P, "content manquant", "POST", "/api/posts", '{"user_id":12}', 400, V),
        (P, 'user_id "12" (chaîne)', "POST", "/api/posts", '{"user_id":"12","content":"x"}', 400, V),
        (P, "user_id 12.0", "POST", "/api/posts", '{"user_id":12.0,"content":"x"}', 400, V),
        (P, "user_id 1e1", "POST", "/api/posts", '{"user_id":1e1,"content":"x"}', 400, V),
        (P, "user_id true", "POST", "/api/posts", '{"user_id":true,"content":"x"}', 400, V),
        (P, "user_id null", "POST", "/api/posts", '{"user_id":null,"content":"x"}', 400, V),
        (P, "user_id 0", "POST", "/api/posts", '{"user_id":0,"content":"x"}', 400, V),
        (P, "user_id -3", "POST", "/api/posts", '{"user_id":-3,"content":"x"}', 400, V),
        (P, "user_id > BIGINT", "POST", "/api/posts", '{"user_id":9223372036854775808,"content":"x"}', 400, V),
        (P, "content nombre", "POST", "/api/posts", '{"user_id":12,"content":5}', 400, V),
        (P, "content vide", "POST", "/api/posts", '{"user_id":12,"content":""}', 400, V),
        (P, "content espaces seulement", "POST", "/api/posts", '{"user_id":12,"content":" \\u3000 "}', 400, V),
        (P, "JSON invalide", "POST", "/api/posts", '{"user_id":12,', 400, V),
        (P, "corps vide", "POST", "/api/posts", '', 400, V),
        (P, "corps tableau", "POST", "/api/posts", '[{"user_id":12,"content":"x"}]', 400, V),
        (P, "corps null", "POST", "/api/posts", 'null', 400, V),
        (P, "texte après l'objet", "POST", "/api/posts", '{"user_id":12,"content":"x"} x', 400, V),
        (P, "NaN dans le corps", "POST", "/api/posts", '{"user_id":12,"content":"x","n":NaN}', 400, V),
        (P, "user inexistant", "POST", "/api/posts", '{"user_id":5000,"content":"x"}', 404, UNF),
        (P, "ordre : 400 avant user inexistant", "POST", "/api/posts", '{"user_id":5000,"content":""}', 400, V),
    ]
    K = "POST like"
    c += [
        (K, "nominal", "POST", "/api/posts/5/like", f'{{"user_id":{lu}}}', 201, check_like_row(lu, 5)),
        (K, "doublon -> 409", "POST", "/api/posts/5/like", f'{{"user_id":{lu}}}', 409, err("already_liked")),
        (K, "doublon d'un like du seed", "POST", f"/api/posts/{sp}/like", f'{{"user_id":{su}}}', 409,
         err("already_liked")),
        (K, "chemin 007 lu comme 7", "POST", "/api/posts/007/like", f'{{"user_id":{lu}}}', 201,
         check_like_row(lu, 7)),
        (K, "chemin abc", "POST", "/api/posts/abc/like", '{"user_id":1}', 404, NF),
        (K, "chemin 0", "POST", "/api/posts/0/like", '{"user_id":1}', 404, NF),
        (K, "chemin -1", "POST", "/api/posts/-1/like", '{"user_id":1}', 404, NF),
        (K, "chemin +5", "POST", "/api/posts/+5/like", '{"user_id":1}', 404, NF),
        (K, "chemin 1.5", "POST", "/api/posts/1.5/like", '{"user_id":1}', 404, NF),
        (K, "chemin > BIGINT", "POST", "/api/posts/9223372036854775808/like", '{"user_id":1}', 404, NF),
        (K, "ordre : chemin avant corps", "POST", "/api/posts/abc/like", '{bad', 404, NF),
        (K, "user_id manquant", "POST", "/api/posts/5/like", '{}', 400, V),
        (K, 'user_id "1" (chaîne)', "POST", "/api/posts/5/like", '{"user_id":"1"}', 400, V),
        (K, "ordre : 400 avant post inexistant", "POST", "/api/posts/99999/like", '{"user_id":"1"}', 400, V),
        (K, "user inexistant", "POST", "/api/posts/5/like", '{"user_id":5000}', 404, UNF),
        (K, "post inexistant", "POST", "/api/posts/99999/like", '{"user_id":1}', 404, PNF),
        (K, "post BIGINT max inexistant", "POST", "/api/posts/9223372036854775807/like", '{"user_id":1}', 404, PNF),
        (K, "ordre : user avant post", "POST", "/api/posts/99999/like", '{"user_id":5000}', 404, UNF),
    ]
    F = "POST follow"
    c += [
        (F, "nominal", "POST", f"/api/users/{fb}/follow", f'{{"follower_id":{fa}}}', 201,
         check_follow_row(fa, fb)),
        (F, "doublon -> 409", "POST", f"/api/users/{fb}/follow", f'{{"follower_id":{fa}}}', 409,
         err("already_following")),
        (F, "doublon d'un follow du seed", "POST", f"/api/users/{sfb}/follow", f'{{"follower_id":{sfa}}}', 409,
         err("already_following")),
        (F, "auto-follow", "POST", "/api/users/3/follow", '{"follower_id":3}', 400, err("cannot_follow_self")),
        (F, "ordre : auto-follow avant user inexistant", "POST", "/api/users/3000/follow",
         '{"follower_id":3000}', 400, err("cannot_follow_self")),
        (F, "follower inexistant", "POST", "/api/users/3/follow", '{"follower_id":4000}', 404, UNF),
        (F, "suivi inexistant", "POST", "/api/users/3000/follow", '{"follower_id":4}', 404, UNF),
        (F, "chemin x", "POST", "/api/users/x/follow", '{"follower_id":4}', 404, NF),
        (F, "chemin 0", "POST", "/api/users/0/follow", '{"follower_id":4}', 404, NF),
        (F, "follower_id manquant", "POST", "/api/users/3/follow", '{}', 400, V),
        (F, 'follower_id "4" (chaîne)', "POST", "/api/users/3/follow", '{"follower_id":"4"}', 400, V),
        (F, "follower_id 4.0", "POST", "/api/users/3/follow", '{"follower_id":4.0}', 400, V),
        (F, "JSON invalide", "POST", "/api/users/3/follow", 'follower_id=4', 400, V),
    ]
    C = "POST comment"
    c += [
        (C, "nominal", "POST", "/api/posts/7/comments", '{"user_id":2,"content":"Très intéressant !"}', 201,
         check_comment(7, 2, "Très intéressant !")),
        (C, "chemin 007 + trim", "POST", "/api/posts/007/comments", '{"user_id":2,"content":"  ok  "}', 201,
         check_comment(7, 2, "ok")),
        (C, "500 caractères", "POST", "/api/posts/7/comments", '{"user_id":2,"content":"' + a500 + '"}', 201,
         check_comment(7, 2, a500)),
        (C, "501 caractères", "POST", "/api/posts/7/comments", '{"user_id":2,"content":"' + a501 + '"}', 400, V),
        (C, "content vide", "POST", "/api/posts/7/comments", '{"user_id":2,"content":"   "}', 400, V),
        (C, "content manquant", "POST", "/api/posts/7/comments", '{"user_id":2}', 400, V),
        (C, "user_id manquant", "POST", "/api/posts/7/comments", '{"content":"x"}', 400, V),
        (C, 'user_id "2" (chaîne)', "POST", "/api/posts/7/comments", '{"user_id":"2","content":"x"}', 400, V),
        (C, "user inexistant", "POST", "/api/posts/7/comments", '{"user_id":2000,"content":"x"}', 404, UNF),
        (C, "post inexistant", "POST", "/api/posts/70000/comments", '{"user_id":2,"content":"x"}', 404, PNF),
        (C, "ordre : user avant post", "POST", "/api/posts/70000/comments", '{"user_id":2000,"content":"x"}',
         404, UNF),
        (C, "chemin abc", "POST", "/api/posts/abc/comments", '{"user_id":2,"content":"x"}', 404, NF),
        (C, "chemin > BIGINT", "POST", "/api/posts/9223372036854775808/comments", '{"user_id":2,"content":"x"}',
         404, NF),
    ]
    R = "Routes"
    c += [
        (R, "route inconnue", "GET", "/api/nope", None, 404, NF),
        (R, "hors préfixe /api", "GET", "/posts", None, 404, NF),
        (R, "racine", "GET", "/", None, 404, NF),
        (R, "barre oblique finale", "GET", "/api/posts/", None, 404, NF),
        (R, "GET sur une route POST", "GET", "/api/posts/1/like", None, 404, NF),
        (R, "DELETE /api/posts", "DELETE", "/api/posts", None, 404, NF),
        (R, "PUT /api/posts", "PUT", "/api/posts", '{"user_id":1,"content":"x"}', 404, NF),
        (R, "POST /api/posts/1", "POST", "/api/posts/1", '{"user_id":1}', 404, NF),
        (R, "GET /api/users/1/follow", "GET", "/api/users/1/follow", None, 404, NF),
    ]
    return c


# ---------------------------------------------------------------- exécution

def normalize(status, body):
    """Corps comparables entre API : on retire id et created_at des créations (§8)."""
    if status == 201 and isinstance(body, dict) and "created_at" in body:
        return {k: v for k, v in body.items() if k not in ("id", "created_at")}
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        data = [{k: v for k, v in i.items() if not (k == "created_at" and i.get("id", 0) > 10000)}
                if isinstance(i, dict) else i for i in body["data"]]
        return {**body, "data": data}
    return body


def reset() -> None:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "reset_db.py")], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit("reset_db.py a échoué :\n" + proc.stdout + proc.stderr)


def run_api(port: int, cases: list[tuple], fx: dict) -> list[dict]:
    reset()
    ctx = {"port": port, "fx": fx, "comments_added": 0}
    results = []
    for cat, name, method, path, body, exp_status, expected in cases:
        status, parsed, ctype, raw = call(port, method, path, body)
        problem = None
        if status != exp_status:
            problem = f"statut {status} au lieu de {exp_status} ; corps {raw[:120]!r}"
        elif not ctype.startswith("application/json"):
            problem = f"Content-Type {ctype!r}"
        elif parsed is None:
            problem = f"corps non JSON : {raw[:120]!r}"
        elif callable(expected):
            problem = expected(parsed, ctx)
        elif parsed != expected:
            problem = f"corps {parsed} au lieu de {expected}"
        results.append({"ok": problem is None, "problem": problem, "status": status,
                        "norm": normalize(status, parsed)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--port", type=int, choices=sorted(APIS), help="tester une seule API")
    group.add_argument("--all", action="store_true", help="tester les 4 API et comparer leurs réponses")
    args = parser.parse_args()
    ports = sorted(APIS) if args.all else [args.port]

    for port in ports:
        try:
            call(port, "GET", "/api/posts?limit=1", None)
        except OSError as exc:
            raise SystemExit(f"{APIS[port]} (port {port}) ne répond pas : {exc}")

    reset()
    fx = seed_fixtures()
    cases = build_cases(fx)
    results = {port: run_api(port, cases, fx) for port in ports}
    reset()

    lines = [f"Test de contrat — {datetime.now():%Y-%m-%d %H:%M:%S} — "
             + ", ".join(f"{APIS[p]} ({p})" for p in ports), ""]
    names = [APIS[p] for p in ports]
    header = f"{'#':>3}  {'catégorie':<13}{'cas':<42}" + "".join(f"{n:>9}" for n in names)
    if len(ports) > 1:
        header += f"{'identiques':>12}"
    lines += [header, "-" * len(header)]
    same_count = 0
    for i, (cat, name, *_rest) in enumerate(cases):
        row = f"{i + 1:>3}  {cat:<13}{name[:41]:<42}" + "".join(
            f"{'PASS' if results[p][i]['ok'] else 'FAIL':>9}" for p in ports)
        if len(ports) > 1:
            first = results[ports[0]][i]
            same = all((results[p][i]["status"], results[p][i]["norm"]) == (first["status"], first["norm"])
                       for p in ports)
            same_count += same
            row += f"{'oui' if same else 'NON':>12}"
        lines.append(row)
    lines.append("-" * len(header))

    all_ok = True
    for p in ports:
        n_ok = sum(r["ok"] for r in results[p])
        all_ok &= n_ok == len(cases)
        lines.append(f"{APIS[p]:<8} : {n_ok}/{len(cases)} PASS ({100 * n_ok / len(cases):.0f} %)")
    if len(ports) > 1:
        all_ok &= same_count == len(cases)
        lines.append(f"Corps identiques entre les {len(ports)} API (hors id/created_at des créations) : "
                     f"{same_count}/{len(cases)}")

    details = []
    for p in ports:
        for i, r in enumerate(results[p]):
            if not r["ok"]:
                details.append(f"  [{APIS[p]}] #{i + 1} {cases[i][0]} / {cases[i][1]} : {r['problem']}")
    if len(ports) > 1:
        for i in range(len(cases)):
            first = results[ports[0]][i]
            for p in ports[1:]:
                r = results[p][i]
                if (r["status"], r["norm"]) != (first["status"], first["norm"]):
                    details.append(f"  [écart] #{i + 1} {cases[i][1]} : {APIS[ports[0]]} {first['status']} "
                                   f"{first['norm']} / {APIS[p]} {r['status']} {r['norm']}")
    if details:
        lines += ["", "Détail des échecs :"] + details
    lines += ["", "RÉSULTAT : " + ("100 % PASS" if all_ok else "ÉCHEC")]

    text = "\n".join(lines)
    sys.stdout.reconfigure(errors="replace")
    print(text)
    OUT_FILE.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"\n(écrit dans {OUT_FILE.relative_to(ROOT)})")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
