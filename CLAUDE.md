# CLAUDE.md — Benchmark d'API REST avec MySQL (TP2 MRI, UPC)

Ce fichier est lu par Claude Code à chaque session. Il fait foi pour tout le projet :
**si une implémentation diverge de ce document, c'est l'implémentation qui a tort.**
La feuille de route (ordre des phases) est dans `FEUILLE_DE_ROUTE.md`.

---

## 1. Contexte

TP2 du cours « Méthode de recherche en informatique » (FASI, UPC).
Objectif : écrire **la même API REST** (mini réseau social) avec **4 technologies**, la soumettre
à des tests de charge **répétés 30 fois** par technologie et par niveau de charge, puis analyser
les mesures. Livrables finaux : rapport, code des 4 versions, **fichiers de données brutes**.

| Technologie | Langage | Port hôte | Dossier |
|---|---|---|---|
| Laravel (dernière version stable compatible PHP 8.3), php-fpm + nginx | PHP 8.3 | 8001 | `laravel-api/` |
| FastAPI (uvicorn) | Python 3.12 | 8002 | `fastapi-api/` |
| Spring Boot (dernière version stable compatible Java 25, cf. start.spring.io) | Java 25 | 8003 | `spring-boot-api/` |
| Gin | Go 1.27 | 8004 | `gin-api/` |
| MySQL 8.4 (InnoDB) | — | **3307** | `database/` |

---

## 2. Environnement de la machine (une seule machine : développement ET mesures)

- **Windows** (utilisateur `HP`), Intel Core 12e génération, **12 threads**, **16 Go de RAM**.
- Docker Desktop (backend WSL 2). Fichier `%UserProfile%\.wslconfig` recommandé :
  ```
  [wsl2]
  memory=10GB
  processors=10
  ```
- Outils installés sur l'hôte : Git 2.49, **k6 v2.4.0**, PHP 8.3.30 + Composer 2.11 (via Laragon),
  **Java 25**, **Go 1.27.4**. Python 3.12 : à vérifier (`python --version`), requis pour les scripts.
- Le terminal peut être PowerShell ou Git Bash : **tous les scripts d'automatisation sont écrits en
  Python** (pas de bash, pas de Makefile, pas de redirection `<` qui ne marche pas sous PowerShell).
  Les chemins utilisent `pathlib`.

### MySQL
- Conteneur existant : **`mysql-dev`** (image `mysql:8.4`, version 8.4.13), port hôte **3307** → 3306.
- Utilisateur **`root`**, base **`mri`**. Le mot de passe est dans `.env` (`MYSQL_ROOT_PASSWORD`),
  **jamais en dur dans le code** ; `.env` est dans `.gitignore`, un `.env.example` sans mot de passe est versionné.
- Ce conteneur a été créé à la main, sans limites ni configuration. Il doit être **remplacé par le
  service `mysql` du `docker-compose.yml`** (même nom de conteneur `mysql-dev`, même port 3307, même
  base `mri`, même mot de passe) avec un volume nommé et la configuration du §6.
  → **Claude Code ne supprime pas `mysql-dev` lui-même** : il prépare le compose, puis demande à
  l'utilisateur de lancer `docker stop mysql-dev` et `docker rm mysql-dev` (la base ne contient encore aucune table).
- Accès depuis l'hôte (Workbench, scripts Python) : `localhost:3307`.
  Accès depuis les conteneurs API : `mysql:3306` (réseau du compose).
- Les tables ne sont **jamais** créées à la main dans Workbench : uniquement via `database/schema.sql`.

---

## 3. Règles absolues

1. **Ne jamais inventer, estimer ou « compléter » un résultat de mesure.** Toute valeur provient d'un
   fichier de `raw-results/`. Si une donnée manque, on le dit.
2. **Équivalence fonctionnelle stricte** : mêmes routes, mêmes validations, mêmes codes HTTP,
   mêmes corps JSON (mêmes clés, mêmes types), **même SQL**.
3. **Accès aux données en SQL direct dans les 4 versions** (pas d'ORM) :
   - Laravel : `DB::select / DB::insert` (PDO), pas d'Eloquent ;
   - FastAPI : `aiomysql` (pool asynchrone), pas d'ORM ;
   - Spring Boot : `JdbcTemplate` + HikariCP, pas de JPA ;
   - Gin : `database/sql` + `github.com/go-sql-driver/mysql`.
4. **Même budget : 10 connexions MySQL maximum par API** (§7).
5. Pas de cache applicatif, pas de compteur dénormalisé, logs d'accès désactivés, mode debug coupé,
   dans **toutes** les versions.
6. Aucune fonctionnalité hors périmètre (pas d'auth, pas de pagination différente, pas d'endpoint en plus).
7. Toute modification du contrat (§5) ou du schéma (§6) est appliquée aux 4 versions **dans le même
   changement**, puis `tests/contract_test.py` doit repasser sur les 4.
8. Noms de code en anglais, commentaires et documentation en français.
9. **Ne jamais lancer la campagne complète** (`run_campaign.py` sans `--essais 1`) ni supprimer de
   conteneur, de volume ou de données sans l'accord explicite de l'utilisateur.
10. Versions des dépendances : prendre les versions stables actuelles et les **épingler** (lockfiles
    `composer.lock`, `requirements.txt` avec `==`, `go.sum`, versions Maven fixes).

---

## 4. Arborescence du dépôt

```
benchmark-api/
├── CLAUDE.md
├── FEUILLE_DE_ROUTE.md
├── docker-compose.yml          # mysql + 4 API (+ profil "observability" optionnel)
├── .env / .env.example         # MYSQL_ROOT_PASSWORD, MYSQL_DATABASE=mri
├── .gitignore
├── database/
│   ├── schema.sql              # tables, index, contraintes
│   ├── generate_seed.py        # génère seed.sql (graine aléatoire fixe = 42)
│   ├── seed.sql                # jeu de données déterministe (versionné)
│   └── reset.sql               # remet la base à l'état initial entre deux essais
├── scripts/
│   ├── db.py                   # helpers : exécuter un .sql dans mysql-dev (docker exec -i, stdin)
│   ├── init_db.py              # applique schema.sql + seed.sql sur la base mri
│   └── reset_db.py             # applique reset.sql et vérifie les comptages
├── laravel-api/  (Dockerfile + nginx/default.conf)
├── fastapi-api/  (Dockerfile)
├── spring-boot-api/  (Dockerfile multi-étapes Maven)
├── gin-api/  (Dockerfile multi-étapes)
├── tests/
│   └── contract_test.py        # tests fonctionnels identiques pour les 4 API (stdlib seulement)
├── load-tests/
│   └── mixed.js                # scénario k6 unique, requêtes taguées par endpoint
├── bench/
│   ├── run_campaign.py         # orchestre la campagne, écrit les CSV bruts
│   └── startup_time.py         # temps de démarrage à froid
├── analysis/
│   ├── analyse.py              # agrégats, tests statistiques, graphiques, tables LaTeX
│   ├── figures/
│   └── tables/
├── observability/              # optionnel : prometheus.yml, grafana/
└── raw-results/
    ├── k6/                     # un JSON de résumé k6 par essai
    ├── stats/                  # échantillons docker stats par essai
    ├── mesures.csv             # 1 ligne = 1 essai × 1 endpoint
    ├── demarrage.csv           # temps de démarrage à froid
    └── environnement.txt       # versions, CPU, RAM, OS, images Docker
```

Les scripts Python exécutent le SQL via `subprocess.run(["docker", "exec", "-i", "mysql-dev",
"mysql", "-uroot", f"-p{pwd}", "mri"], input=sql_bytes)` (mot de passe lu dans `.env`, sans
dépendance externe). Aucun paquet Python requis sauf pour `analysis/` (pandas, matplotlib, scipy).

---

## 5. Contrat de l'API (source de vérité)

Conventions communes :
- Préfixe `/api`, JSON en entrée et en sortie (`Content-Type: application/json`).
- Identifiants : entiers positifs (BIGINT).
- Dates : **toujours formatées par MySQL** avec `DATE_FORMAT(col, '%Y-%m-%dT%H:%i:%sZ')` et
  renvoyées telles quelles (chaîne). Aucune version ne formate les dates elle-même.
- Erreurs : corps `{"error": "<code>"}` et rien d'autre. Il faut **remplacer les formats d'erreur par
  défaut** de Laravel (422), FastAPI (422) et Spring (`/error`, page Whitelabel).
- **Ordre des vérifications** (identique partout) :
  1. paramètre de chemin non entier ou ≤ 0 → `404 not_found` ;
  2. JSON invalide, champ manquant, mauvais type, contrainte de longueur → `400 validation_error` ;
  3. règle métier « auto-follow » → `400 cannot_follow_self` ;
  4. utilisateur inexistant → `404 user_not_found` ;
  5. post inexistant → `404 post_not_found` ;
  6. doublon (erreur MySQL **1062** sur l'INSERT) → `409 already_liked` / `409 already_following`.
- Un entier passé en chaîne (`"12"`), un booléen ou un décimal est **refusé** (400) partout.
- `content` : chaîne, `trim()` appliqué, non vide après trim ; post ≤ 280 caractères,
  commentaire ≤ 500 caractères (caractères Unicode, pas octets). Le contenu **trimé** est enregistré.
- Champs supplémentaires inconnus dans le JSON : ignorés.
- Route ou méthode inconnue → `404 {"error":"not_found"}`.

### 5.1 `GET /api/posts?page=1&limit=20`
- `page` ≥ 1 (défaut 1), `limit` entre 1 et 100 (défaut 20). Valeur non entière ou hors bornes → `400 validation_error`.
- Réponse `200` :
```json
{
  "page": 1,
  "limit": 20,
  "data": [
    {"id": 10000, "user_id": 37, "username": "user37", "content": "...",
     "created_at": "2026-10-01T12:00:00Z", "likes_count": 3, "comments_count": 1}
  ]
}
```
SQL **exact** (identique dans les 4 versions, `OFFSET = (page-1)*limit`) :
```sql
SELECT p.id, p.user_id, u.username, p.content,
       DATE_FORMAT(p.created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at,
       (SELECT COUNT(*) FROM likes l    WHERE l.post_id = p.id) AS likes_count,
       (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments_count
FROM posts p
JOIN users u ON u.id = p.user_id
ORDER BY p.created_at DESC, p.id DESC
LIMIT ? OFFSET ?;
```
`likes_count` et `comments_count` sont des **nombres** JSON (pas des chaînes) dans les 4 versions.

### 5.2 `POST /api/posts`
Corps `{"user_id": 12, "content": "Mon premier post"}`.
1. `SELECT 1 FROM users WHERE id = ?` → sinon `404 user_not_found`.
2. `INSERT INTO posts (user_id, content) VALUES (?, ?)`.
3. Relecture : `SELECT id, user_id, content, DATE_FORMAT(created_at, '%Y-%m-%dT%H:%i:%sZ') AS created_at FROM posts WHERE id = ?`.

Réponse `201` : `{"id": 10001, "user_id": 12, "content": "Mon premier post", "created_at": "..."}`

### 5.3 `POST /api/posts/{id}/like`
Corps `{"user_id": 12}`. `SELECT 1` sur user puis sur post, puis
`INSERT INTO likes (user_id, post_id) VALUES (?, ?)` ; erreur 1062 → `409 already_liked`.
Réponse `201` : `{"post_id": 45, "user_id": 12}`

### 5.4 `POST /api/users/{id}/follow`
Corps `{"follower_id": 12}`. `follower_id == id` → `400 cannot_follow_self`. Vérifier
`follower_id` puis `id` (absent → `404 user_not_found`), puis
`INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)` ; 1062 → `409 already_following`.
Réponse `201` : `{"follower_id": 12, "followed_id": 27}`

### 5.5 `POST /api/posts/{id}/comments`
Corps `{"user_id": 12, "content": "Très intéressant !"}`. Vérifier user puis post, insérer, relire
comme en 5.2. Réponse `201` :
`{"id": 20001, "post_id": 45, "user_id": 12, "content": "Très intéressant !", "created_at": "..."}`

Pas de transaction explicite : chaque requête en autocommit (même comportement partout).

---

## 6. Base de données

- MySQL 8.4, InnoDB, `utf8mb4` / `utf8mb4_0900_ai_ci`, isolation par défaut (REPEATABLE READ).
- Tables : `users`, `posts`, `likes`, `follows`, `comments`, toutes avec
  `id BIGINT AUTO_INCREMENT PRIMARY KEY` et `created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP`.
  `users(username VARCHAR(50))`, `posts(content VARCHAR(280))`, `comments(content VARCHAR(500))`.
- Contraintes : FK partout ; `users.username` UNIQUE ; `likes UNIQUE(user_id, post_id)` ;
  `follows UNIQUE(follower_id, followed_id)` + `CHECK (follower_id <> followed_id)`.
- Index : `posts(created_at, id)`, `posts(user_id)`, `likes(post_id)`, `comments(post_id, created_at)`,
  `follows(followed_id)`. **Figés avant le premier test.**
- Jeu de données (`generate_seed.py`, `random.seed(42)`, dates entre 2026-01-01 et 2026-10-31) :
  1 000 users (`user1`…`user1000`), 10 000 posts, 20 000 likes, 5 000 follows, 20 000 comments,
  sans doublon ni auto-follow. Inserts groupés par 1 000 lignes.
- `reset.sql` remet la base à l'état du seed : supprimer `comments` et `likes` liés aux posts `> 10000`,
  puis `comments (id > 20000)`, `likes (id > 20000)`, `follows (id > 5000)`, `posts (id > 10000)`.
  `reset_db.py` vérifie ensuite les comptages exacts. Doit prendre quelques secondes au plus.
- Service `mysql` du compose : `container_name: mysql-dev`, port `3307:3306`, volume nommé,
  healthcheck `mysqladmin ping`, limites `cpus: "2"`, `mem_limit: 2g`, et options :
  `--innodb-buffer-pool-size=512M --max-connections=200 --skip-name-resolve`.

---

## 7. Configuration d'exécution (équité)

Toutes les API en Docker, **mêmes limites : `cpus: "2"`, `mem_limit: 1g`**. k6 tourne sur l'hôte (Windows).
Les API attendent MySQL via `depends_on: condition: service_healthy`.

| API | Image de base | Serveur / concurrence | Connexions MySQL |
|---|---|---|---|
| Laravel | `php:8.3-fpm` (+ `pdo_mysql`, OPcache) + conteneur `nginx:alpine` séparé | php-fpm `pm = static`, `pm.max_children = 10` ; `APP_DEBUG=false`, `APP_ENV=production` ; `config:cache` + `route:cache` ; sessions/middlewares web inutiles retirés des routes API | 1 par worker → 10 |
| FastAPI | `python:3.12-slim` | `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2 --no-access-log` | pool aiomysql `maxsize=5` par worker → 10 |
| Spring Boot | build `maven:3.9-eclipse-temurin-25`, exécution `eclipse-temurin:25-jre` | Tomcat par défaut, `-Xmx512m`, logs niveau WARN | HikariCP `maximum-pool-size=10` |
| Gin | build `golang:1.27-alpine`, exécution `alpine` | `gin.SetMode(gin.ReleaseMode)`, `gin.New()` + `gin.Recovery()` (pas de Logger) | `SetMaxOpenConns(10)`, `SetMaxIdleConns(10)` |

Si une étiquette d'image n'existe pas, prendre la plus proche disponible et la noter dans
`environnement.txt`. Interdit pour les mesures : `php artisan serve`, `uvicorn --reload`,
`mvn spring-boot:run`, `go run`. Toute différence non évitable est notée dans `environnement.txt`.

---

## 8. Tests fonctionnels — `tests/contract_test.py`

- Python stdlib uniquement (`urllib`, `json`). Usage : `python tests/contract_test.py --port 8001`
  ou `--all` (8001 à 8004). Appelle `scripts/reset_db.py` avant de commencer.
- Couvre, pour chaque endpoint : cas nominal, 400 (champ manquant, mauvais type, `"12"`, contenu vide,
  trop long), 404 user / post / route, 409 doublon, 400 auto-follow, pagination (`page`, `limit`, bornes) et tri.
- **Compare le corps exact** des réponses entre les 4 API (hors `id` et `created_at` des créations).
- Affiche un tableau PASS/FAIL par API et écrit `raw-results/contract_test.txt`.
- **Aucun benchmark tant que les 4 API ne sont pas à 100 % PASS.**

---

## 9. Scénario de charge — `load-tests/mixed.js` (k6 v2.4)

- Variables : `BASE_URL`, `VUS`, `DURATION` (défaut `15s`), `OUT` (chemin du JSON de résumé).
- Exécuteur `constant-vus`. Tirage pondéré à chaque itération :
  GET posts 50 % · POST posts 15 % · like 15 % · follow 10 % · comment 10 %.
- Identifiants tirés dans le seed : users 1–1000, posts 1–10000, page 1–50, limit 20 ; follow sans auto-follow.
- Chaque requête porte `tags: { name: 'list_posts' | 'create_post' | 'like' | 'follow' | 'comment' }`.
- `http.setResponseCallback(http.expectedStatuses(200, 201, 409))` : un 409 est une réponse métier
  normale. `check()` sur le code attendu.
- Un seuil factice par endpoint (ex. `'http_req_duration{name:like}': ['p(95)<100000']`) pour que
  les sous-métriques apparaissent dans le résumé.
- Résumé écrit par `handleSummary(data)` dans `OUT` (ne pas dépendre de `--summary-export`).
  `summaryTrendStats: ['avg','min','med','max','p(90)','p(95)','p(99)']` dans `options`.
- k6 v2 peut différer de la documentation v0.x : **vérifier avec `k6 run --help` et un essai court**
  que les options et la structure du JSON sont bien celles attendues avant d'écrire le parseur.

---

## 10. Campagne — `bench/run_campaign.py`

Paramètres par défaut : technologies `laravel, fastapi, spring, gin` ; charges `10, 50, 100` VU ;
**30 essais** ; durée 15 s ; échauffement 10 s à 10 VU après chaque démarrage d'API.

```
pour chaque tour r de 1 à 3 (10 essais par tour) :
    ordre des technologies décalé d'un cran à chaque tour (alternance)
    pour chaque technologie :
        démarrer uniquement son/ses conteneur(s) (les autres API arrêtées), attendre une réponse 200
        échauffement
        pour chaque essai (10) et chaque charge (10, 50, 100) :
            reset_db ; pause 2 s
            lancer k6 + échantillonner `docker stats --no-stream` de l'API et de mysql-dev (~1/s, thread séparé)
            ajouter les lignes au CSV
        arrêter le(s) conteneur(s)
```
Pour Laravel, le CPU/RAM de l'API = php-fpm **+** nginx (sommés, et noté dans le rapport).
- `--resume` : saute les essais déjà présents dans `mesures.csv`. Options `--techs`, `--vus`,
  `--essais`, `--duration` pour les essais rapides (`--essais 1 --duration 5s`).
- Affiche la progression et une estimation du temps restant.

`raw-results/mesures.csv` (séparateur `,`, point décimal, UTF-8), **1 ligne par essai × endpoint**
(+ une ligne `endpoint=all`) :
```
technologie,endpoint,utilisateurs,essai,horodatage,requetes,req_s,lat_moy_ms,p50_ms,p90_ms,p95_ms,p99_ms,lat_max_ms,erreurs_pct,cpu_api_moy_pct,ram_api_max_mo,cpu_mysql_moy_pct
```
JSON k6 et échantillons `docker stats` bruts conservés : `raw-results/k6/<tech>_<vus>vu_essai<NN>.json`,
`raw-results/stats/<tech>_<vus>vu_essai<NN>.csv`.

`bench/startup_time.py` : 30 démarrages à froid par API (`docker compose up -d <api>` → première
réponse 200 sur `GET /api/posts?limit=1`), dans `raw-results/demarrage.csv`
(`technologie,essai,demarrage_ms`). Relève aussi la RAM au repos et la taille de chaque image.

---

## 11. Analyse — `analysis/analyse.py`

pandas + matplotlib + scipy. Lit uniquement `raw-results/*.csv`.
- Agrégats par technologie × endpoint × charge : médiane, moyenne, écart-type, IC 95 % (30 essais).
- Kruskal-Wallis sur p95 entre technologies (par endpoint, à 50 VU) ; signaler les écarts non significatifs.
- Figures PDF dans `analysis/figures/` : axes nommés **avec unité**, un message par figure,
  **barres** pour comparer à charge fixe, **courbes** pour l'évolution avec la charge, courbes
  distinguées par **style de trait + marqueur** (lisible en noir et blanc), largeur ~3,4 pouces.
  Minimum : p95 par techno (barres + IC), débit vs VU, p95 vs VU, boîtes à moustaches p95 à 50 VU,
  CPU/RAM par techno, taux d'erreur, temps de démarrage.
- Tables `booktabs` dans `analysis/tables/*.tex`.

---

## 12. Commandes utiles (PowerShell)

```powershell
docker compose up -d mysql                      # base seule (conteneur mysql-dev, port 3307)
python scripts/init_db.py                       # schéma + seed
python scripts/reset_db.py                      # remise à zéro + vérification
docker compose up -d --build laravel            # une API : laravel | fastapi | spring | gin
python tests/contract_test.py --all
k6 run -e BASE_URL=http://localhost:8004 -e VUS=10 -e DURATION=10s -e OUT=tmp.json load-tests/mixed.js
python bench/run_campaign.py --essais 1 --duration 5s     # essai à blanc
python bench/run_campaign.py --resume                     # campagne complète (avec accord)
python analysis/analyse.py
```

---

## 13. Façon de travailler (pour Claude Code)

- Suivre l'ordre de `FEUILLE_DE_ROUTE.md`. Une étape = un commit clair.
- Une API est « terminée » quand : l'image se construit, le conteneur démarre avec les limites du §7,
  `contract_test.py` est à 100 % sur elle.
- Tester réellement (curl / scripts) avant d'annoncer qu'une chose marche ; montrer la sortie.
- En cas de doute sur un comportement : relire §5, sinon **demander** plutôt que choisir seul un
  comportement qui différerait des autres versions.
- Remplir `raw-results/environnement.txt` au fur et à mesure (versions exactes des frameworks,
  des images et des dépendances principales).
