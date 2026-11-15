-- Schéma de la base du benchmark (MySQL 8.4, InnoDB, utf8mb4).
-- FIGÉ avant le premier test : toute modification doit être reportée dans les 4 API (CLAUDE.md §2.7).
-- Le script est idempotent : il supprime puis recrée les tables.

SET NAMES utf8mb4;

DROP TABLE IF EXISTS comments;
DROP TABLE IF EXISTS follows;
DROP TABLE IF EXISTS likes;
DROP TABLE IF EXISTS posts;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id         BIGINT      NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username   VARCHAR(50) NOT NULL,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_users_username UNIQUE (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE posts (
    id         BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id    BIGINT       NOT NULL,
    content    VARCHAR(280) NOT NULL,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_posts_user FOREIGN KEY (user_id) REFERENCES users (id),
    INDEX idx_posts_created_id (created_at, id),
    INDEX idx_posts_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE likes (
    id         BIGINT   NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id    BIGINT   NOT NULL,
    post_id    BIGINT   NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_likes_user FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_likes_post FOREIGN KEY (post_id) REFERENCES posts (id),
    CONSTRAINT uq_likes_user_post UNIQUE (user_id, post_id),
    INDEX idx_likes_post (post_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE follows (
    id          BIGINT   NOT NULL AUTO_INCREMENT PRIMARY KEY,
    follower_id BIGINT   NOT NULL,
    followed_id BIGINT   NOT NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_follows_follower FOREIGN KEY (follower_id) REFERENCES users (id),
    CONSTRAINT fk_follows_followed FOREIGN KEY (followed_id) REFERENCES users (id),
    CONSTRAINT uq_follows_pair UNIQUE (follower_id, followed_id),
    CONSTRAINT chk_follows_not_self CHECK (follower_id <> followed_id),
    INDEX idx_follows_followed (followed_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Remarque : InnoDB crée automatiquement un index pour la FK comments.user_id
-- (aucun index existant ne commence par cette colonne).
CREATE TABLE comments (
    id         BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    post_id    BIGINT       NOT NULL,
    user_id    BIGINT       NOT NULL,
    content    VARCHAR(500) NOT NULL,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_comments_post FOREIGN KEY (post_id) REFERENCES posts (id),
    CONSTRAINT fk_comments_user FOREIGN KEY (user_id) REFERENCES users (id),
    INDEX idx_comments_post_created (post_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
