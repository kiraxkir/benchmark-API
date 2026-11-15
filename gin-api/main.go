// API Gin du benchmark : les 5 endpoints du contrat (CLAUDE.md §5), SQL direct via database/sql.
//
// Le corps JSON est lu et validé à la main (§5.6), comme dans les 3 autres versions.
// Ordre des vérifications : chemin -> corps -> auto-follow -> user -> post -> doublon.
package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/gin-gonic/gin"
	"github.com/go-sql-driver/mysql"
)

const (
	postMaxLen    = 280
	commentMaxLen = 500

	listSQL = `SELECT p.id, p.user_id, u.username, p.content,
       DATE_FORMAT(p.created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at,
       (SELECT COUNT(*) FROM likes l    WHERE l.post_id = p.id) AS likes_count,
       (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count
FROM posts p
JOIN users u ON u.id = p.user_id
ORDER BY p.created_at DESC, p.id DESC
LIMIT ? OFFSET ?`
	readPostSQL    = "SELECT id, user_id, content, DATE_FORMAT(created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at FROM posts WHERE id = ?"
	readCommentSQL = "SELECT id, post_id, user_id, content, DATE_FORMAT(created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at FROM comments WHERE id = ?"
)

var (
	db        *sql.DB
	digitsRe  = regexp.MustCompile(`^[0-9]+$`)
	jsonIntRe = regexp.MustCompile(`^-?[0-9]+$`)
)

// Structures de réponse : l'ordre des champs fixe l'ordre des clés JSON.
type postItem struct {
	ID            int64  `json:"id"`
	UserID        int64  `json:"user_id"`
	Username      string `json:"username"`
	Content       string `json:"content"`
	CreatedAt     string `json:"created_at"`
	LikesCount    int64  `json:"likes_count"`
	CommentsCount int64  `json:"comments_count"`
}

type listResponse struct {
	Page  int64      `json:"page"`
	Limit int64      `json:"limit"`
	Data  []postItem `json:"data"`
}

type postCreated struct {
	ID        int64  `json:"id"`
	UserID    int64  `json:"user_id"`
	Content   string `json:"content"`
	CreatedAt string `json:"created_at"`
}

type likeCreated struct {
	PostID int64 `json:"post_id"`
	UserID int64 `json:"user_id"`
}

type followCreated struct {
	FollowerID int64 `json:"follower_id"`
	FollowedID int64 `json:"followed_id"`
}

type commentCreated struct {
	ID        int64  `json:"id"`
	PostID    int64  `json:"post_id"`
	UserID    int64  `json:"user_id"`
	Content   string `json:"content"`
	CreatedAt string `json:"created_at"`
}

type errorBody struct {
	Error string `json:"error"`
}

func main() {
	gin.SetMode(gin.ReleaseMode)

	cfg := mysql.NewConfig()
	cfg.User = envOr("DB_USER", "root")
	cfg.Passwd = os.Getenv("DB_PASSWORD")
	cfg.Net = "tcp"
	cfg.Addr = envOr("DB_HOST", "mysql") + ":" + envOr("DB_PORT", "3306")
	cfg.DBName = os.Getenv("DB_NAME")
	cfg.Collation = "utf8mb4_0900_ai_ci"
	// Paramètres insérés côté client : 1 aller-retour par requête, comme les pilotes
	// Python, Java et PHP (émulation) - décision du groupe, CLAUDE.md §5.6.12.
	cfg.InterpolateParams = true
	connector, err := mysql.NewConnector(cfg)
	if err != nil {
		log.Fatal(err)
	}
	db = sql.OpenDB(connector)
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(10)

	// gin.New() + Recovery (réponse 500 au format du contrat), sans middleware Logger.
	r := gin.New()
	r.Use(gin.CustomRecovery(func(c *gin.Context, _ any) {
		writeError(c, http.StatusInternalServerError, "internal_error")
		c.Abort()
	}))
	r.RedirectTrailingSlash = false
	r.RedirectFixedPath = false
	r.HandleMethodNotAllowed = false
	r.NoRoute(func(c *gin.Context) { writeError(c, http.StatusNotFound, "not_found") })

	r.GET("/api/posts", listPosts)
	r.POST("/api/posts", createPost)
	r.POST("/api/posts/:id/like", likePost)
	r.POST("/api/users/:id/follow", followUser)
	r.POST("/api/posts/:id/comments", createComment)

	if err := r.Run(":8000"); err != nil {
		log.Fatal(err)
	}
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// ------------------------------------------------------------------ réponses

// writeJSON sérialise sans échappement HTML ni saut de ligne final, Content-Type fixe.
func writeJSON(c *gin.Context, status int, v any) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		panic(err)
	}
	c.Data(status, "application/json", bytes.TrimSuffix(buf.Bytes(), []byte("\n")))
}

func writeError(c *gin.Context, status int, code string) {
	writeJSON(c, status, errorBody{Error: code})
}

// ------------------------------------------------------------------ validation

// digitsToInt : chaîne de chiffres dans [1, BIGINT_MAX] -> entier (§5.6.1).
func digitsToInt(raw string) (int64, bool) {
	if !digitsRe.MatchString(raw) {
		return 0, false
	}
	t := strings.TrimLeft(raw, "0")
	if t == "" || len(t) > 19 {
		return 0, false
	}
	v, err := strconv.ParseInt(t, 10, 64)
	if err != nil { // > 9223372036854775807
		return 0, false
	}
	return v, true
}

// queryInt : absent -> défaut ; présent (dernière valeur) -> chiffres dans [1, max] (§5.6.2).
func queryInt(c *gin.Context, name string, def, max int64) (int64, bool) {
	values, present := c.Request.URL.Query()[name]
	if !present {
		return def, true
	}
	v, ok := digitsToInt(values[len(values)-1])
	if !ok || v > max {
		return 0, false
	}
	return v, true
}

// jsonBody : objet JSON attendu (UTF-8 strict, aucun contenu après l'objet), sinon nil (§5.6.4).
func jsonBody(c *gin.Context) map[string]any {
	raw, err := io.ReadAll(c.Request.Body)
	if err != nil || len(raw) == 0 || !utf8.Valid(raw) {
		return nil
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil
	}
	obj, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	return obj
}

// bodyID : entier JSON strict dans [1, BIGINT_MAX] ; chaîne, booléen, décimal refusés (§5.6.3).
func bodyID(body map[string]any, field string) (int64, bool) {
	if body == nil {
		return 0, false
	}
	n, ok := body[field].(json.Number)
	if !ok || !jsonIntRe.MatchString(n.String()) {
		return 0, false
	}
	v, err := strconv.ParseInt(n.String(), 10, 64)
	if err != nil || v <= 0 {
		return 0, false
	}
	return v, true
}

// isWhiteSpace : propriété Unicode White_Space, même ensemble dans les 4 versions (§5.6.9).
func isWhiteSpace(r rune) bool {
	switch {
	case r >= 0x0009 && r <= 0x000D, r == 0x0020, r == 0x0085, r == 0x00A0, r == 0x1680,
		r >= 0x2000 && r <= 0x200A, r == 0x2028, r == 0x2029, r == 0x202F, r == 0x205F, r == 0x3000:
		return true
	}
	return false
}

// bodyContent : chaîne trimée, non vide, <= max points de code (§5.6.5).
func bodyContent(body map[string]any, max int) (string, bool) {
	if body == nil {
		return "", false
	}
	s, ok := body["content"].(string)
	if !ok {
		return "", false
	}
	t := strings.TrimFunc(s, isWhiteSpace)
	n := utf8.RuneCountInString(t)
	if n < 1 || n > max {
		return "", false
	}
	return t, true
}

// ------------------------------------------------------------------ accès aux données

func exists(table string, id int64) bool {
	var one int
	err := db.QueryRow(fmt.Sprintf("SELECT 1 FROM %s WHERE id = ?", table), id).Scan(&one)
	if errors.Is(err, sql.ErrNoRows) {
		return false
	}
	if err != nil {
		panic(err)
	}
	return true
}

func isDuplicate(err error) bool {
	var me *mysql.MySQLError
	return errors.As(err, &me) && me.Number == 1062
}

// ------------------------------------------------------------------ endpoints

func listPosts(c *gin.Context) {
	page, okPage := queryInt(c, "page", 1, math.MaxInt64)
	limit, okLimit := queryInt(c, "limit", 20, 100)
	if !okPage || !okLimit || page-1 > math.MaxInt64/limit {
		writeError(c, http.StatusBadRequest, "validation_error")
		return
	}

	rows, err := db.Query(listSQL, limit, (page-1)*limit)
	if err != nil {
		panic(err)
	}
	defer rows.Close()
	data := make([]postItem, 0, limit)
	for rows.Next() {
		var p postItem
		if err := rows.Scan(&p.ID, &p.UserID, &p.Username, &p.Content, &p.CreatedAt, &p.LikesCount, &p.CommentsCount); err != nil {
			panic(err)
		}
		data = append(data, p)
	}
	if err := rows.Err(); err != nil {
		panic(err)
	}
	writeJSON(c, http.StatusOK, listResponse{Page: page, Limit: limit, Data: data})
}

func createPost(c *gin.Context) {
	body := jsonBody(c)
	userID, okUser := bodyID(body, "user_id")
	content, okContent := bodyContent(body, postMaxLen)
	if !okUser || !okContent {
		writeError(c, http.StatusBadRequest, "validation_error")
		return
	}
	if !exists("users", userID) {
		writeError(c, http.StatusNotFound, "user_not_found")
		return
	}

	res, err := db.Exec("INSERT INTO posts (user_id, content) VALUES (?, ?)", userID, content)
	if err != nil {
		panic(err)
	}
	id, _ := res.LastInsertId()
	var out postCreated
	if err := db.QueryRow(readPostSQL, id).Scan(&out.ID, &out.UserID, &out.Content, &out.CreatedAt); err != nil {
		panic(err)
	}
	writeJSON(c, http.StatusCreated, out)
}

func likePost(c *gin.Context) {
	postID, ok := digitsToInt(c.Param("id"))
	if !ok {
		writeError(c, http.StatusNotFound, "not_found")
		return
	}
	userID, ok := bodyID(jsonBody(c), "user_id")
	if !ok {
		writeError(c, http.StatusBadRequest, "validation_error")
		return
	}
	if !exists("users", userID) {
		writeError(c, http.StatusNotFound, "user_not_found")
		return
	}
	if !exists("posts", postID) {
		writeError(c, http.StatusNotFound, "post_not_found")
		return
	}
	if _, err := db.Exec("INSERT INTO likes (user_id, post_id) VALUES (?, ?)", userID, postID); err != nil {
		if isDuplicate(err) {
			writeError(c, http.StatusConflict, "already_liked")
			return
		}
		panic(err)
	}
	writeJSON(c, http.StatusCreated, likeCreated{PostID: postID, UserID: userID})
}

func followUser(c *gin.Context) {
	followedID, ok := digitsToInt(c.Param("id"))
	if !ok {
		writeError(c, http.StatusNotFound, "not_found")
		return
	}
	followerID, ok := bodyID(jsonBody(c), "follower_id")
	if !ok {
		writeError(c, http.StatusBadRequest, "validation_error")
		return
	}
	if followerID == followedID {
		writeError(c, http.StatusBadRequest, "cannot_follow_self")
		return
	}
	if !exists("users", followerID) || !exists("users", followedID) {
		writeError(c, http.StatusNotFound, "user_not_found")
		return
	}
	if _, err := db.Exec("INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)", followerID, followedID); err != nil {
		if isDuplicate(err) {
			writeError(c, http.StatusConflict, "already_following")
			return
		}
		panic(err)
	}
	writeJSON(c, http.StatusCreated, followCreated{FollowerID: followerID, FollowedID: followedID})
}

func createComment(c *gin.Context) {
	postID, ok := digitsToInt(c.Param("id"))
	if !ok {
		writeError(c, http.StatusNotFound, "not_found")
		return
	}
	body := jsonBody(c)
	userID, okUser := bodyID(body, "user_id")
	content, okContent := bodyContent(body, commentMaxLen)
	if !okUser || !okContent {
		writeError(c, http.StatusBadRequest, "validation_error")
		return
	}
	if !exists("users", userID) {
		writeError(c, http.StatusNotFound, "user_not_found")
		return
	}
	if !exists("posts", postID) {
		writeError(c, http.StatusNotFound, "post_not_found")
		return
	}

	res, err := db.Exec("INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)", postID, userID, content)
	if err != nil {
		panic(err)
	}
	id, _ := res.LastInsertId()
	var out commentCreated
	if err := db.QueryRow(readCommentSQL, id).Scan(&out.ID, &out.PostID, &out.UserID, &out.Content, &out.CreatedAt); err != nil {
		panic(err)
	}
	writeJSON(c, http.StatusCreated, out)
}
