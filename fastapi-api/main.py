"""API FastAPI du benchmark : les 5 endpoints du contrat (CLAUDE.md §5), SQL direct via aiomysql.

Le corps JSON est lu et validé à la main (pas de modèle Pydantic) pour reproduire exactement
les règles des 3 autres versions (§5.6). Ordre des vérifications :
chemin -> corps -> auto-follow -> user -> post -> doublon.
"""
import json
import os
import re
from contextlib import asynccontextmanager

import aiomysql
import pymysql
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

BIGINT_MAX = 9223372036854775807
POST_MAX_LEN = 280
COMMENT_MAX_LEN = 500
DIGITS = re.compile(r"[0-9]+")
# Espaces blancs Unicode (propriété White_Space) : même ensemble dans les 4 versions (§5.6.9).
WS = "\u0009-\u000d \u0085   -     　"
TRIM = re.compile(f"^[{WS}]+|[{WS}]+$")

LIST_SQL = """SELECT p.id, p.user_id, u.username, p.content,
       DATE_FORMAT(p.created_at, '%%Y-%%m-%%dT%%H:%%i:%%sZ') AS created_at,
       (SELECT COUNT(*) FROM likes l    WHERE l.post_id = p.id) AS likes_count,
       (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count
FROM posts p
JOIN users u ON u.id = p.user_id
ORDER BY p.created_at DESC, p.id DESC
LIMIT %s OFFSET %s"""
READ_POST_SQL = ("SELECT id, user_id, content, DATE_FORMAT(created_at, '%%Y-%%m-%%dT%%H:%%i:%%sZ') "
                 "AS created_at FROM posts WHERE id = %s")
READ_COMMENT_SQL = ("SELECT id, post_id, user_id, content, DATE_FORMAT(created_at, '%%Y-%%m-%%dT%%H:%%i:%%sZ') "
                    "AS created_at FROM comments WHERE id = %s")

pool: aiomysql.Pool | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Un pool par worker uvicorn : maxsize=5 x 2 workers = 10 connexions (§7)."""
    global pool
    pool = await aiomysql.create_pool(
        host=os.environ.get("DB_HOST", "mysql"),
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ["DB_PASSWORD"],
        db=os.environ["DB_NAME"],
        charset="utf8mb4",
        autocommit=True,
        minsize=1,
        maxsize=5,
    )
    yield
    pool.close()
    await pool.wait_closed()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
              redirect_slashes=False)


# ------------------------------------------------------------------ erreurs

def error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def http_error(_request: Request, _exc: StarletteHTTPException):
    # Route inconnue (404) ou mauvaise méthode (405) -> 404 not_found (§5.6.6).
    return error(404, "not_found")


@app.exception_handler(Exception)
async def internal_error(_request: Request, _exc: Exception):
    return error(500, "internal_error")


# ------------------------------------------------------------------ validation

def digits_to_int(raw: str) -> int | None:
    """Chaîne de chiffres dans [1, BIGINT_MAX] -> entier ; sinon None (§5.6.1)."""
    if not DIGITS.fullmatch(raw):
        return None
    stripped = raw.lstrip("0")
    if not stripped or len(stripped) > 19:  # évite aussi la limite de int() sur les très longues chaînes
        return None
    value = int(stripped)
    return value if value <= BIGINT_MAX else None


def query_int(request: Request, name: str, default: int, maximum: int) -> int | None:
    """Absent -> défaut ; présent -> chiffres dans [1, maximum] (§5.6.2)."""
    raw = request.query_params.get(name)
    if raw is None:
        return default
    value = digits_to_int(raw)
    return value if value is not None and value <= maximum else None


def _reject_constant(_name: str):
    raise ValueError("NaN/Infinity refusés")


async def json_body(request: Request) -> dict | None:
    """Objet JSON attendu (UTF-8 strict), sinon None (§5.6.4)."""
    try:
        data = json.loads((await request.body()).decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def body_id(body: dict | None, field: str) -> int | None:
    """Entier JSON strict dans [1, BIGINT_MAX] ; bool, float, chaîne refusés (§5.6.3)."""
    if body is None:
        return None
    value = body.get(field)
    return value if type(value) is int and 1 <= value <= BIGINT_MAX else None


def body_content(body: dict | None, max_len: int) -> str | None:
    """Chaîne trimée, non vide, <= max_len points de code (§5.6.5)."""
    if body is None:
        return None
    value = body.get("content")
    if not isinstance(value, str):
        return None
    trimmed = TRIM.sub("", value)
    return trimmed if 1 <= len(trimmed) <= max_len else None


# ------------------------------------------------------------------ accès aux données

async def exists(cur, table: str, ident: int) -> bool:
    await cur.execute(f"SELECT 1 FROM {table} WHERE id = %s", (ident,))
    return await cur.fetchone() is not None


def is_duplicate(exc: pymysql.err.IntegrityError) -> bool:
    return bool(exc.args) and exc.args[0] == 1062


# ------------------------------------------------------------------ endpoints

@app.get("/api/posts")
async def list_posts(request: Request):
    page = query_int(request, "page", 1, BIGINT_MAX)
    limit = query_int(request, "limit", 20, 100)
    if page is None or limit is None or (page - 1) * limit > BIGINT_MAX:
        return error(400, "validation_error")

    async with pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute(LIST_SQL, (limit, (page - 1) * limit))
        rows = await cur.fetchall()
    data = [
        {"id": r[0], "user_id": r[1], "username": r[2], "content": r[3],
         "created_at": r[4], "likes_count": int(r[5]), "comments_count": int(r[6])}
        for r in rows
    ]
    return JSONResponse({"page": page, "limit": limit, "data": data}, status_code=200)


@app.post("/api/posts")
async def create_post(request: Request):
    body = await json_body(request)
    user_id = body_id(body, "user_id")
    content = body_content(body, POST_MAX_LEN)
    if user_id is None or content is None:
        return error(400, "validation_error")

    async with pool.acquire() as conn, conn.cursor() as cur:
        if not await exists(cur, "users", user_id):
            return error(404, "user_not_found")
        await cur.execute("INSERT INTO posts (user_id, content) VALUES (%s, %s)", (user_id, content))
        await cur.execute(READ_POST_SQL, (cur.lastrowid,))
        r = await cur.fetchone()
    return JSONResponse({"id": r[0], "user_id": r[1], "content": r[2], "created_at": r[3]},
                        status_code=201)


@app.post("/api/posts/{post_id}/like")
async def like_post(post_id: str, request: Request):
    pid = digits_to_int(post_id)
    if pid is None:
        return error(404, "not_found")
    user_id = body_id(await json_body(request), "user_id")
    if user_id is None:
        return error(400, "validation_error")

    async with pool.acquire() as conn, conn.cursor() as cur:
        if not await exists(cur, "users", user_id):
            return error(404, "user_not_found")
        if not await exists(cur, "posts", pid):
            return error(404, "post_not_found")
        try:
            await cur.execute("INSERT INTO likes (user_id, post_id) VALUES (%s, %s)", (user_id, pid))
        except pymysql.err.IntegrityError as exc:
            if is_duplicate(exc):
                return error(409, "already_liked")
            raise
    return JSONResponse({"post_id": pid, "user_id": user_id}, status_code=201)


@app.post("/api/users/{user_id}/follow")
async def follow_user(user_id: str, request: Request):
    followed_id = digits_to_int(user_id)
    if followed_id is None:
        return error(404, "not_found")
    follower_id = body_id(await json_body(request), "follower_id")
    if follower_id is None:
        return error(400, "validation_error")
    if follower_id == followed_id:
        return error(400, "cannot_follow_self")

    async with pool.acquire() as conn, conn.cursor() as cur:
        if not await exists(cur, "users", follower_id) or not await exists(cur, "users", followed_id):
            return error(404, "user_not_found")
        try:
            await cur.execute("INSERT INTO follows (follower_id, followed_id) VALUES (%s, %s)",
                              (follower_id, followed_id))
        except pymysql.err.IntegrityError as exc:
            if is_duplicate(exc):
                return error(409, "already_following")
            raise
    return JSONResponse({"follower_id": follower_id, "followed_id": followed_id}, status_code=201)


@app.post("/api/posts/{post_id}/comments")
async def create_comment(post_id: str, request: Request):
    pid = digits_to_int(post_id)
    if pid is None:
        return error(404, "not_found")
    body = await json_body(request)
    user_id = body_id(body, "user_id")
    content = body_content(body, COMMENT_MAX_LEN)
    if user_id is None or content is None:
        return error(400, "validation_error")

    async with pool.acquire() as conn, conn.cursor() as cur:
        if not await exists(cur, "users", user_id):
            return error(404, "user_not_found")
        if not await exists(cur, "posts", pid):
            return error(404, "post_not_found")
        await cur.execute("INSERT INTO comments (post_id, user_id, content) VALUES (%s, %s, %s)",
                          (pid, user_id, content))
        await cur.execute(READ_COMMENT_SQL, (cur.lastrowid,))
        r = await cur.fetchone()
    return JSONResponse({"id": r[0], "post_id": r[1], "user_id": r[2], "content": r[3], "created_at": r[4]},
                        status_code=201)
