package cd.upc.mri.benchmark;

import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.dao.DataAccessException;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/**
 * Les 5 endpoints du contrat (CLAUDE.md §5), en SQL direct via JdbcTemplate (pas de JPA).
 * Le corps est lu en octets bruts et validé à la main (§5.6), comme dans les 3 autres versions.
 * Ordre des vérifications : chemin -> corps -> auto-follow -> user -> post -> doublon.
 */
@RestController
public class ApiController {

    private static final int POST_MAX_LEN = 280;
    private static final int COMMENT_MAX_LEN = 500;
    private static final Pattern DIGITS = Pattern.compile("[0-9]+");
    // Espaces blancs Unicode (propriété White_Space) : même ensemble dans les 4 versions (§5.6.9).
    private static final String WS =
            "\\u0009-\\u000D\\u0020\\u0085\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000";
    private static final Pattern TRIM = Pattern.compile("^[" + WS + "]+|[" + WS + "]+$");

    private static final String LIST_SQL = """
            SELECT p.id, p.user_id, u.username, p.content,
                   DATE_FORMAT(p.created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at,
                   (SELECT COUNT(*) FROM likes l    WHERE l.post_id = p.id) AS likes_count,
                   (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count
            FROM posts p
            JOIN users u ON u.id = p.user_id
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT ? OFFSET ?""";
    private static final String READ_POST_SQL =
            "SELECT id, user_id, content, DATE_FORMAT(created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at FROM posts WHERE id = ?";
    private static final String READ_COMMENT_SQL =
            "SELECT id, post_id, user_id, content, DATE_FORMAT(created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at FROM comments WHERE id = ?";

    private final JdbcTemplate jdbc;
    private final Json json;

    public ApiController(JdbcTemplate jdbc, Json json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    /** GET /api/posts?page=&limit= */
    @GetMapping("/api/posts")
    public ResponseEntity<byte[]> listPosts(HttpServletRequest request) {
        Long page = queryInt(request, "page", 1, Long.MAX_VALUE);
        Long limit = queryInt(request, "limit", 20, 100);
        if (page == null || limit == null || (page - 1) > Long.MAX_VALUE / limit) {
            return json.error(400, "validation_error");
        }

        List<Map<String, Object>> data = jdbc.query(LIST_SQL, (rs, i) -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", rs.getLong("id"));
            row.put("user_id", rs.getLong("user_id"));
            row.put("username", rs.getString("username"));
            row.put("content", rs.getString("content"));
            row.put("created_at", rs.getString("created_at"));
            row.put("likes_count", rs.getLong("likes_count"));
            row.put("comments_count", rs.getLong("comments_count"));
            return row;
        }, limit, (page - 1) * limit);

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("page", page);
        body.put("limit", limit);
        body.put("data", data);
        return json.ok(200, body);
    }

    /** POST /api/posts */
    @PostMapping("/api/posts")
    public ResponseEntity<byte[]> createPost(@RequestBody(required = false) byte[] raw) {
        JsonNode body = json.parseObject(raw);
        Long userId = bodyId(body, "user_id");
        String content = bodyContent(body, POST_MAX_LEN);
        if (userId == null || content == null) {
            return json.error(400, "validation_error");
        }
        if (!exists("users", userId)) {
            return json.error(404, "user_not_found");
        }

        long id = insert("INSERT INTO posts (user_id, content) VALUES (?, ?)", userId, content);
        Map<String, Object> out = jdbc.queryForObject(READ_POST_SQL, (rs, i) -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", rs.getLong("id"));
            row.put("user_id", rs.getLong("user_id"));
            row.put("content", rs.getString("content"));
            row.put("created_at", rs.getString("created_at"));
            return row;
        }, id);
        return json.ok(201, out);
    }

    /** POST /api/posts/{id}/like */
    @PostMapping("/api/posts/{id}/like")
    public ResponseEntity<byte[]> likePost(@PathVariable("id") String rawId,
                                           @RequestBody(required = false) byte[] raw) {
        Long postId = digitsToLong(rawId);
        if (postId == null) {
            return json.error(404, "not_found");
        }
        Long userId = bodyId(json.parseObject(raw), "user_id");
        if (userId == null) {
            return json.error(400, "validation_error");
        }
        if (!exists("users", userId)) {
            return json.error(404, "user_not_found");
        }
        if (!exists("posts", postId)) {
            return json.error(404, "post_not_found");
        }
        try {
            jdbc.update("INSERT INTO likes (user_id, post_id) VALUES (?, ?)", userId, postId);
        } catch (DataAccessException e) {
            if (isDuplicate(e)) {
                return json.error(409, "already_liked");
            }
            throw e;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("post_id", postId);
        out.put("user_id", userId);
        return json.ok(201, out);
    }

    /** POST /api/users/{id}/follow */
    @PostMapping("/api/users/{id}/follow")
    public ResponseEntity<byte[]> followUser(@PathVariable("id") String rawId,
                                             @RequestBody(required = false) byte[] raw) {
        Long followedId = digitsToLong(rawId);
        if (followedId == null) {
            return json.error(404, "not_found");
        }
        Long followerId = bodyId(json.parseObject(raw), "follower_id");
        if (followerId == null) {
            return json.error(400, "validation_error");
        }
        if (followerId.equals(followedId)) {
            return json.error(400, "cannot_follow_self");
        }
        if (!exists("users", followerId) || !exists("users", followedId)) {
            return json.error(404, "user_not_found");
        }
        try {
            jdbc.update("INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)", followerId, followedId);
        } catch (DataAccessException e) {
            if (isDuplicate(e)) {
                return json.error(409, "already_following");
            }
            throw e;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("follower_id", followerId);
        out.put("followed_id", followedId);
        return json.ok(201, out);
    }

    /** POST /api/posts/{id}/comments */
    @PostMapping("/api/posts/{id}/comments")
    public ResponseEntity<byte[]> createComment(@PathVariable("id") String rawId,
                                                @RequestBody(required = false) byte[] raw) {
        Long postId = digitsToLong(rawId);
        if (postId == null) {
            return json.error(404, "not_found");
        }
        JsonNode body = json.parseObject(raw);
        Long userId = bodyId(body, "user_id");
        String content = bodyContent(body, COMMENT_MAX_LEN);
        if (userId == null || content == null) {
            return json.error(400, "validation_error");
        }
        if (!exists("users", userId)) {
            return json.error(404, "user_not_found");
        }
        if (!exists("posts", postId)) {
            return json.error(404, "post_not_found");
        }

        long id = insert("INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)", postId, userId, content);
        Map<String, Object> out = jdbc.queryForObject(READ_COMMENT_SQL, (rs, i) -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", rs.getLong("id"));
            row.put("post_id", rs.getLong("post_id"));
            row.put("user_id", rs.getLong("user_id"));
            row.put("content", rs.getString("content"));
            row.put("created_at", rs.getString("created_at"));
            return row;
        }, id);
        return json.ok(201, out);
    }

    // ---------------------------------------------------------------- accès aux données

    private boolean exists(String table, long id) {
        return !jdbc.queryForList("SELECT 1 FROM " + table + " WHERE id = ?", id).isEmpty();
    }

    /** INSERT puis identifiant généré (renvoyé par MySQL dans la réponse OK, sans requête de plus). */
    private long insert(String sql, Object... args) {
        KeyHolder keys = new GeneratedKeyHolder();
        jdbc.update(con -> {
            PreparedStatement ps = con.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS);
            for (int i = 0; i < args.length; i++) {
                ps.setObject(i + 1, args[i]);
            }
            return ps;
        }, keys);
        return keys.getKey().longValue();
    }

    private static boolean isDuplicate(DataAccessException e) {
        for (Throwable t = e; t != null; t = t.getCause()) {
            if (t instanceof SQLException sql && sql.getErrorCode() == 1062) {
                return true;
            }
        }
        return false;
    }

    // ---------------------------------------------------------------- validation

    /** Chaîne de chiffres dans [1, BIGINT_MAX] -> long ; sinon null (§5.6.1). */
    static Long digitsToLong(String raw) {
        if (raw == null || !DIGITS.matcher(raw).matches()) {
            return null;
        }
        String t = raw.replaceFirst("^0+", "");
        if (t.isEmpty() || t.length() > 19) {
            return null;
        }
        try {
            return Long.parseLong(t);
        } catch (NumberFormatException e) { // > 9223372036854775807
            return null;
        }
    }

    /** Absent -> défaut ; présent (dernière valeur) -> chiffres dans [1, max] (§5.6.2). */
    private static Long queryInt(HttpServletRequest request, String name, long def, long max) {
        String[] values = request.getParameterValues(name);
        if (values == null) {
            return def;
        }
        Long v = digitsToLong(values[values.length - 1]);
        return (v != null && v <= max) ? v : null;
    }

    /** Entier JSON strict dans [1, BIGINT_MAX] (§5.6.3). */
    private static Long bodyId(JsonNode body, String field) {
        if (body == null) {
            return null;
        }
        JsonNode node = body.get(field);
        if (node == null || !node.isIntegralNumber() || !node.canConvertToLong()) {
            return null;
        }
        long v = node.longValue();
        return v > 0 ? v : null;
    }

    /** Chaîne trimée, non vide, <= max points de code (§5.6.5). */
    private static String bodyContent(JsonNode body, int max) {
        if (body == null) {
            return null;
        }
        JsonNode node = body.get("content");
        if (node == null || !node.isString()) {
            return null;
        }
        String trimmed = TRIM.matcher(node.stringValue()).replaceAll("");
        int len = trimmed.codePointCount(0, trimmed.length());
        return (len >= 1 && len <= max) ? trimmed : null;
    }
}
