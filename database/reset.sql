-- Remet la base à l'état initial du seed entre deux essais (CLAUDE.md §6).
-- Supprime tout ce qui dépasse le seed, dans l'ordre imposé, puis remet les
-- compteurs AUTO_INCREMENT à leur valeur de départ (identifiants reproductibles).
SET NAMES utf8mb4;

DELETE FROM comments WHERE post_id > 10000;
DELETE FROM likes    WHERE post_id > 10000;
DELETE FROM comments WHERE id > 20000;
DELETE FROM likes    WHERE id > 20000;
DELETE FROM follows  WHERE id > 5000;
DELETE FROM posts    WHERE id > 10000;

ALTER TABLE comments AUTO_INCREMENT = 20001;
ALTER TABLE likes    AUTO_INCREMENT = 20001;
ALTER TABLE follows  AUTO_INCREMENT = 5001;
ALTER TABLE posts    AUTO_INCREMENT = 10001;
