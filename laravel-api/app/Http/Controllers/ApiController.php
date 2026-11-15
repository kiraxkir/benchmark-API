<?php

namespace App\Http\Controllers;

use App\Support\ApiError;
use Illuminate\Database\QueryException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;

/**
 * Les 5 endpoints du contrat (CLAUDE.md §5), en SQL direct (pas d'Eloquent).
 * Ordre des vérifications : chemin -> corps -> auto-follow -> user -> post -> doublon.
 */
class ApiController extends Controller
{
    private const BIGINT_MAX = '9223372036854775807';
    private const POST_MAX_LEN = 280;
    private const COMMENT_MAX_LEN = 500;
    private const DATE_FMT = "'%Y-%m-%dT%H:%i:%sZ'";

    // Espaces blancs Unicode (propriété White_Space) : même ensemble dans les 4 versions.
    private const WS = '\x{0009}-\x{000D}\x{0020}\x{0085}\x{00A0}\x{1680}\x{2000}-\x{200A}\x{2028}\x{2029}\x{202F}\x{205F}\x{3000}';

    private const LIST_SQL = <<<'SQL'
SELECT p.id, p.user_id, u.username, p.content,
       DATE_FORMAT(p.created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at,
       (SELECT COUNT(*) FROM likes l    WHERE l.post_id = p.id) AS likes_count,
       (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count
FROM posts p
JOIN users u ON u.id = p.user_id
ORDER BY p.created_at DESC, p.id DESC
LIMIT ? OFFSET ?
SQL;

    /** GET /api/posts?page=&limit= */
    public function listPosts(Request $request): JsonResponse
    {
        $page = $this->queryInt($request, 'page', 1, PHP_INT_MAX);
        $limit = $this->queryInt($request, 'limit', 20, 100);
        if ($page === null || $limit === null || ($page - 1) > intdiv(PHP_INT_MAX, $limit)) {
            return ApiError::response(400, 'validation_error');
        }

        $rows = DB::select(self::LIST_SQL, [$limit, ($page - 1) * $limit]);
        $data = [];
        foreach ($rows as $r) {
            $data[] = [
                'id' => (int) $r->id,
                'user_id' => (int) $r->user_id,
                'username' => $r->username,
                'content' => $r->content,
                'created_at' => $r->created_at,
                'likes_count' => (int) $r->likes_count,
                'comments_count' => (int) $r->comments_count,
            ];
        }

        return $this->json(['page' => $page, 'limit' => $limit, 'data' => $data], 200);
    }

    /** POST /api/posts */
    public function createPost(Request $request): JsonResponse
    {
        $body = $this->jsonBody($request);
        $userId = $this->bodyId($body, 'user_id');
        $content = $this->bodyContent($body, self::POST_MAX_LEN);
        if ($userId === null || $content === null) {
            return ApiError::response(400, 'validation_error');
        }
        if (! $this->userExists($userId)) {
            return ApiError::response(404, 'user_not_found');
        }

        DB::insert('INSERT INTO posts (user_id, content) VALUES (?, ?)', [$userId, $content]);
        $id = (int) DB::getPdo()->lastInsertId();
        $row = DB::select(
            'SELECT id, user_id, content, DATE_FORMAT(created_at, '.self::DATE_FMT.') AS created_at FROM posts WHERE id = ?',
            [$id]
        )[0];

        return $this->json([
            'id' => (int) $row->id,
            'user_id' => (int) $row->user_id,
            'content' => $row->content,
            'created_at' => $row->created_at,
        ], 201);
    }

    /** POST /api/posts/{id}/like */
    public function likePost(Request $request, string $id): JsonResponse
    {
        $postId = $this->pathId($id);
        if ($postId === null) {
            return ApiError::response(404, 'not_found');
        }
        $userId = $this->bodyId($this->jsonBody($request), 'user_id');
        if ($userId === null) {
            return ApiError::response(400, 'validation_error');
        }
        if (! $this->userExists($userId)) {
            return ApiError::response(404, 'user_not_found');
        }
        if (! $this->postExists($postId)) {
            return ApiError::response(404, 'post_not_found');
        }

        try {
            DB::insert('INSERT INTO likes (user_id, post_id) VALUES (?, ?)', [$userId, $postId]);
        } catch (QueryException $e) {
            if ($this->isDuplicate($e)) {
                return ApiError::response(409, 'already_liked');
            }
            throw $e;
        }

        return $this->json(['post_id' => $postId, 'user_id' => $userId], 201);
    }

    /** POST /api/users/{id}/follow */
    public function followUser(Request $request, string $id): JsonResponse
    {
        $followedId = $this->pathId($id);
        if ($followedId === null) {
            return ApiError::response(404, 'not_found');
        }
        $followerId = $this->bodyId($this->jsonBody($request), 'follower_id');
        if ($followerId === null) {
            return ApiError::response(400, 'validation_error');
        }
        if ($followerId === $followedId) {
            return ApiError::response(400, 'cannot_follow_self');
        }
        if (! $this->userExists($followerId) || ! $this->userExists($followedId)) {
            return ApiError::response(404, 'user_not_found');
        }

        try {
            DB::insert('INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)', [$followerId, $followedId]);
        } catch (QueryException $e) {
            if ($this->isDuplicate($e)) {
                return ApiError::response(409, 'already_following');
            }
            throw $e;
        }

        return $this->json(['follower_id' => $followerId, 'followed_id' => $followedId], 201);
    }

    /** POST /api/posts/{id}/comments */
    public function createComment(Request $request, string $id): JsonResponse
    {
        $postId = $this->pathId($id);
        if ($postId === null) {
            return ApiError::response(404, 'not_found');
        }
        $body = $this->jsonBody($request);
        $userId = $this->bodyId($body, 'user_id');
        $content = $this->bodyContent($body, self::COMMENT_MAX_LEN);
        if ($userId === null || $content === null) {
            return ApiError::response(400, 'validation_error');
        }
        if (! $this->userExists($userId)) {
            return ApiError::response(404, 'user_not_found');
        }
        if (! $this->postExists($postId)) {
            return ApiError::response(404, 'post_not_found');
        }

        DB::insert('INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)', [$postId, $userId, $content]);
        $commentId = (int) DB::getPdo()->lastInsertId();
        $row = DB::select(
            'SELECT id, post_id, user_id, content, DATE_FORMAT(created_at, '.self::DATE_FMT.') AS created_at FROM comments WHERE id = ?',
            [$commentId]
        )[0];

        return $this->json([
            'id' => (int) $row->id,
            'post_id' => (int) $row->post_id,
            'user_id' => (int) $row->user_id,
            'content' => $row->content,
            'created_at' => $row->created_at,
        ], 201);
    }

    // ---------------------------------------------------------------- utilitaires

    private function json(array $data, int $status): JsonResponse
    {
        return response()->json($data, $status, [], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }

    private function userExists(int $id): bool
    {
        return DB::select('SELECT 1 FROM users WHERE id = ?', [$id]) !== [];
    }

    private function postExists(int $id): bool
    {
        return DB::select('SELECT 1 FROM posts WHERE id = ?', [$id]) !== [];
    }

    private function isDuplicate(QueryException $e): bool
    {
        return (int) ($e->errorInfo[1] ?? 0) === 1062;
    }

    /** Chaîne de chiffres dans [1, BIGINT_MAX] -> entier ; sinon null (§5.6.1). */
    private function digitsToInt(string $s): ?int
    {
        if (preg_match('/^[0-9]+$/D', $s) !== 1) {
            return null;
        }
        $t = ltrim($s, '0');
        if ($t === '' || strlen($t) > 19 || (strlen($t) === 19 && strcmp($t, self::BIGINT_MAX) > 0)) {
            return null;
        }

        return (int) $t;
    }

    private function pathId(string $raw): ?int
    {
        return $this->digitsToInt($raw);
    }

    /** Paramètre de requête : absent -> défaut ; sinon chiffres dans [1, max] (§5.6.2). */
    private function queryInt(Request $request, string $name, int $default, int $max): ?int
    {
        if (! $request->query->has($name)) {
            return $default;
        }
        $raw = $request->query->all()[$name];
        if (! is_string($raw)) {
            return null;
        }
        $v = $this->digitsToInt($raw);

        return ($v !== null && $v <= $max) ? $v : null;
    }

    /** Corps JSON : objet attendu, sinon null (§5.6.4). */
    private function jsonBody(Request $request): ?object
    {
        $decoded = json_decode($request->getContent(), false, 512);
        if (json_last_error() !== JSON_ERROR_NONE || ! is_object($decoded)) {
            return null;
        }

        return $decoded;
    }

    /** Entier JSON strict dans [1, BIGINT_MAX] (§5.6.3). */
    private function bodyId(?object $body, string $field): ?int
    {
        if ($body === null || ! property_exists($body, $field)) {
            return null;
        }
        $v = $body->{$field};

        return (is_int($v) && $v > 0) ? $v : null;
    }

    /** Chaîne trimée, non vide, <= max points de code (§5.6.5). */
    private function bodyContent(?object $body, int $max): ?string
    {
        if ($body === null || ! property_exists($body, 'content') || ! is_string($body->content)) {
            return null;
        }
        $trimmed = preg_replace('/^['.self::WS.']+|['.self::WS.']+$/u', '', $body->content);
        if ($trimmed === null) {
            return null;
        }
        $len = mb_strlen($trimmed, 'UTF-8');

        return ($len >= 1 && $len <= $max) ? $trimmed : null;
    }
}
