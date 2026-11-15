# Feuille de route — TP2 MRI : Laravel vs FastAPI vs Spring Boot vs Gin

La spécification technique complète (contrat d'API, SQL, configuration) est dans `CLAUDE.md`.
Ce document décrit **comment le projet se déroule, étape par étape**.

---

## Vue d'ensemble

| # | Phase | Résultat attendu |
|---|---|---|
| 0 | Installation des outils | Tous les outils répondent en ligne de commande |
| 1 | Socle commun : dépôt, schéma, jeu de données, MySQL | Base remplie et réinitialisable |
| 2 | Développement des 4 API | 4 images Docker qui démarrent |
| 3 | Tests fonctionnels et équivalence | `contract_test.py` 100 % PASS sur les 4 |
| 4 | Scénario k6, script de campagne, essai à blanc | Un CSV de test correct |
| 5 | Campagne de mesures complète | `raw-results/` rempli |
| 6 | Analyse et graphiques | Figures + tables LaTeX |
| 7 | Rédaction du rapport | PDF de 4 à 6 pages |
| 8 | Relecture, archive, envoi | Mail envoyé |

> Barème (20 pts) : équivalence + cahier des charges 3 · **métriques justifiées 4** · tests et qualité
> des données 4 · **graphiques et interprétation 5** · rédaction et modèle 4.
> → Ne sacrifiez ni l'analyse ni le rapport pour peaufiner le code.

---

## Phase 0 — Installation

### Technologies à installer

Le plus important : **tout tourne dans Docker**. Les langages installés « en local » servent surtout
à développer et à laisser Claude Code tester rapidement ; les mesures, elles, se font dans les conteneurs.

| Outil | Version | Rôle | Obligatoire ? |
|---|---|---|---|
| **Docker Desktop** (+ WSL 2 sous Windows) | récente | Exécuter MySQL et les 4 API à ressources égales | **Oui** |
| **Git** | récente | Versionner le code, lien de dépôt dans le mail | **Oui** |
| **k6** | v1.x | Générateur de charge | **Oui** |
| **Python** | 3.12 | FastAPI en local, scripts de campagne et d'analyse | **Oui** |
| Paquets Python : `pandas matplotlib scipy` | — | Analyse et graphiques | **Oui** |
| **Claude Code** | récente | Assistant de développement (à mentionner dans le rapport) | Recommandé |
| **VS Code** | récente | Éditeur | Recommandé |
| **PHP 8.3 + Composer** | 8.3 / 2.x | Créer le projet Laravel | Recommandé* |
| **JDK 21** (Temurin) | 21 LTS | Spring Boot (Maven passe par le wrapper `mvnw`) | Recommandé* |
| **Go** | 1.23+ | Gin | Recommandé* |
| **LaTeX** : compte **Overleaf** (le plus simple) ou MiKTeX/TeX Live | — | Rapport avec le modèle fourni | **Oui** (l'un des deux) |
| Postman ou Bruno | — | Tester les endpoints à la main | Facultatif |
| Grafana + Prometheus (+ exporters) | via Docker | Tableaux de bord pendant les tests | **Facultatif** (voir phase 5 bis) |

\* Facultatif si vous construisez tout uniquement via Docker, mais le développement est beaucoup plus
rapide avec les outils en local.

### Sous Windows (PowerShell en administrateur)

```powershell
wsl --install                                   # puis redémarrer
winget install Docker.DockerDesktop
winget install Git.Git
winget install GrafanaLabs.k6
winget install Python.Python.3.12
winget install EclipseAdoptium.Temurin.21.JDK
winget install GoLang.Go
winget install Microsoft.VisualStudioCode
# PHP + Composer : le plus simple est https://herd.laravel.com (Windows) ou php.new
pip install pandas matplotlib scipy
```
Claude Code : suivre la page d'installation officielle (docs.claude.com).

Sous Linux (Ubuntu) : `docker.io docker-compose-v2 git python3-pip golang` via apt, k6 depuis le dépôt
officiel de Grafana, JDK 21 via `openjdk-21-jdk`, PHP 8.3 via le PPA ondrej.

### Images Docker à télécharger (~1,5 Go, à lancer en premier)

```bash
docker pull mysql:8.4
docker pull php:8.3-fpm
docker pull nginx:alpine
docker pull python:3.12-slim
docker pull maven:3.9-eclipse-temurin-21
docker pull eclipse-temurin:21-jre
docker pull golang:1.23-alpine
docker pull alpine:3.20
```

### Réglages machine

- Docker Desktop → Settings → Resources (ou fichier `%UserProfile%\.wslconfig`) :
  au moins **4 CPU et 6 Go de RAM** pour Docker (les limites du `CLAUDE.md` supposent cela).
  Si le PC a moins de 8 Go de RAM : passer les API à `cpus: "1"` / `mem_limit: 768m` **pour toutes**.
- Noter dans `raw-results/environnement.txt` : modèle du PC, CPU, RAM, OS, versions
  (`docker --version`, `k6 version`, `python --version`, `java -version`, `go version`, `php -v`).

**Terminé quand :** `docker run hello-world`, `k6 version`, `python -c "import pandas"` fonctionnent.

---

## Phase 1 — Socle commun

Prompt de départ pour Claude Code (dans le dossier `benchmark-api/` avec `CLAUDE.md` à la racine) :
> « Lis CLAUDE.md. Réalise la phase 1 : arborescence, `.env`, `docker-compose.yml` avec MySQL seulement,
> `database/schema.sql`, `generate_seed.py`, `seed.sql` et `reset.sql`. »

- [ ] `git init`, arborescence du §3 de `CLAUDE.md`, `.gitignore`.
- [ ] `schema.sql` (5 tables, FK, UNIQUE, CHECK, index) — **figé ensuite**.
- [ ] `generate_seed.py` → `seed.sql` (1 000 users, 10 000 posts, 20 000 likes, 5 000 follows, 20 000 comments).
- [ ] `reset.sql` + vérification : après un reset, les comptages reviennent exactement aux valeurs du seed.
- [ ] `docker-compose.yml` : service `mysql` avec healthcheck, config du §5, scripts chargés au premier démarrage.

**Terminé quand :** `SELECT COUNT(*)` sur chaque table donne les valeurs attendues et `reset.sql` s'exécute en quelques secondes.

---

## Phase 2 — Les 4 API

Répartition conseillée (groupe de 3) : **A** = Laravel (la plus longue à configurer) ·
**B** = FastAPI puis Gin · **C** = Spring Boot. Chacun dans sa branche Git, puis fusion.
Si vous êtes plus nombreux, une personne prend en charge les phases 3 et 4 en parallèle.

Pour chaque API, même prompt de base :
> « Lis CLAUDE.md (§2, §4, §6). Implémente `<techno>-api/` : les 5 endpoints avec le SQL exact du §4,
> le format d'erreur unique, la configuration du §6, un Dockerfile de production, et le service
> correspondant dans docker-compose.yml. »

Points de vigilance par technologie :

| API | Pièges à éviter |
|---|---|
| Laravel | Remplacer la réponse 422 par `400 {"error":"validation_error"}` ; `DB::` et pas Eloquent ; php-fpm `pm=static` à 10 ; OPcache ; `config:cache` et `route:cache` dans le Dockerfile ; ne jamais utiliser `artisan serve`. |
| FastAPI | Remplacer le handler de `RequestValidationError` (422 → 400) ; refuser `"12"` pour un entier (`StrictInt`) ; pool aiomysql `maxsize=5` × 2 workers. |
| Spring Boot | `JdbcTemplate` (pas JPA) ; `@ControllerAdvice` pour toutes les erreurs, y compris JSON mal formé et 404 de route ; désactiver la coercition des chaînes en entiers dans Jackson ; Hikari à 10 ; `-Xmx512m`. |
| Gin | `ReleaseMode`, pas de `gin.Default()` (il ajoute le Logger) → `gin.New()` + `Recovery` ; `SetMaxOpenConns(10)` ; détecter l'erreur MySQL 1062. |

**Terminé quand :** chaque API répond sur son port (`curl http://localhost:800X/api/posts?limit=2`).

---

## Phase 3 — Équivalence fonctionnelle

- [ ] Fusionner les branches.
- [ ] Faire écrire `tests/contract_test.py` (§7) **par une personne qui n'a pas codé les API**, à partir de `CLAUDE.md` seulement.
- [ ] `python tests/contract_test.py --all` → corriger jusqu'à **100 % PASS sur les 4**.
- [ ] Garder la sortie du test (capture ou fichier) : c'est la preuve d'équivalence pour le rapport.

---

## Phase 4 — Outillage de mesure

- [ ] `load-tests/mixed.js` (§8) ; tester à la main sur une API : le JSON contient bien les sous-métriques par endpoint.
- [ ] `bench/run_campaign.py` (§9) avec l'option `--resume`.
- [ ] `bench/startup_time.py`.
- [ ] **Essai à blanc :** `python bench/run_campaign.py --essais 1 --duration 5s` → ouvrir `mesures.csv`,
      vérifier les colonnes, l'absence de cases vides, des erreurs ≈ 0 % et que CPU/RAM sont remplis.
- [ ] Volume prévu : 4 technologies × 3 niveaux de charge × 30 essais = **360 essais**.

---

## Phase 5 — Campagne complète

La campagne occupe la machine plusieurs heures : la lancer quand le PC peut tourner sans être utilisé.

Avant de lancer :
- [ ] PC **branché sur secteur** (ou onduleur), mise en veille **désactivée**,
      mode de performance maximale, mises à jour Windows suspendues.
- [ ] Fermer navigateur, VS Code, messageries, analyses antivirus… : rien d'autre ne doit tourner.
- [ ] `git commit` de tout le code (version exacte qui a été mesurée).

```bash
python bench/run_campaign.py --resume
python bench/startup_time.py
```
En cas de coupure (courant, plantage) : relancer la même commande, `--resume` reprend là où c'était arrêté.
Noter toute interruption : elle sera mentionnée dans les limites.

### Phase 5 bis — Grafana / Prometheus (facultatif)
Le guide les prévoit, mais **ils ne sont pas nécessaires pour les données brutes** (k6 + `docker stats`
fournissent tout). Si possible, lancer le profil `observability` (Prometheus, Grafana, cAdvisor,
mysqld-exporter) pendant **quelques** essais pour des captures d'écran illustratives. Le rapport
dit clairement d'où vient chaque chiffre.

---

## Phase 6 — Analyse

- [ ] Contrôle qualité : 30 essais présents partout ? essais aberrants (erreurs > 1 %) ? → les signaler, ne pas les supprimer en silence.
- [ ] `python analysis/analyse.py` → figures PDF + tables `.tex`.
- [ ] Pour chaque figure, écrire 3 phrases en brouillon : **ce qu'on observe → une explication possible → ce qu'on ne peut pas conclure**.

Métriques retenues (à justifier dans le rapport : définition, unité, outil, pourquoi) :

| Métrique | Unité | Outil | Ce qu'elle montre |
|---|---|---|---|
| Débit | req/s | k6 | Capacité de traitement soutenue |
| Latence médiane (p50) | ms | k6 | Expérience typique d'un utilisateur |
| Latence p95 / p99 | ms | k6 | Queue de distribution, ralentissements |
| Taux d'erreur | % | k6 | Un bon débit avec des erreurs n'est pas un bon résultat |
| CPU moyen de l'API | % (de 2 cœurs) | docker stats | Coût d'exécution, efficacité |
| RAM maximale de l'API | Mo | docker stats | Empreinte mémoire |
| CPU de MySQL | % | docker stats | Le goulot est-il la base ou l'API ? |
| Temps de démarrage à froid | ms | startup_time.py | Pertinent pour la mise à l'échelle |
| Efficacité (débit / CPU) | req/s par % CPU | calculée | Performance « à ressources égales » |

---

## Phase 7 — Rapport

Sur Overleaf avec le modèle fourni, travail en parallèle :
- **Personne 1 :** introduction (question de recherche + hypothèses H1–H3 formulées *avant* de regarder les résultats), méthodologie.
- **Personne 2 :** résultats et discussion (figures + tables générées).
- **Personne 3 :** développement (réalisation, équivalence, **usage de Claude Code mentionné**), limites, résumé, conclusion, références.

Limites à ne pas oublier : k6 sur la même machine que les serveurs ; Docker Desktop/WSL 2 ; une seule
machine ; configurations par défaut raisonnables mais non optimisées au maximum (ex. Laravel sans Octane,
JVM sans réglage fin) ; on compare des **implémentations**, pas des langages « en théorie ».

Exemples d'hypothèses : H1 — Gin et Spring Boot ont un débit supérieur à FastAPI et Laravel à 100 VU.
H2 — Sur les écritures, les écarts se réduisent car MySQL devient le facteur limitant.
H3 — Spring Boot consomme le plus de mémoire et met le plus de temps à démarrer.
(Elles peuvent être infirmées : c'est un résultat valable.)

---

## Phase 8 — Relecture et envoi

- [ ] Relecture croisée ; chaque figure numérotée, titrée, citée et commentée ; axes avec unités.
- [ ] Noms + e-mails de tous les membres dans l'en-tête.
- [ ] Archive : `rapport.pdf`, `final-report/` (sources LaTeX), code (zip ou lien GitHub **public**), `raw-results/`.
- [ ] Mail à **jacques.mumbere@upc.ac.cd**, objet **« MRI TP2, groupe N »**, envoyé par **un seul** membre.
- [ ] Vérifier la taille des pièces jointes ; si trop lourd, lien Drive/GitHub pour le code et les données.

---

## Plan B (en cas de difficulté)

| Si… | Alors… |
|---|---|
| Une API bloque | Ne pas retirer d'endpoint : les 4 versions doivent garder les 5. Simplifier plutôt la configuration (ex. Laravel dans un seul conteneur php-fpm + nginx) et mettre deux personnes dessus. |
| La campagne complète est trop longue | Garder **30 essais** (exigé : « plusieurs dizaines »), passer à **2 niveaux de charge (10 et 100 VU)** et `--duration 10s`. |
| Coupure de courant pendant la campagne | Relancer avec `--resume` et noter l'interruption dans les limites. |
| Grafana/Prometheus ne marchent pas | Les abandonner : ils ne sont pas demandés par le sujet. |
