# Déroulement des tests — TP2 MRI

Ce document raconte comment les mesures ont été faites, dans l'ordre, et où trouver chaque
fichier. Les chiffres cités proviennent de `raw-results/` (aucune valeur estimée).
La spécification complète est dans `CLAUDE.md`, l'ordre des phases dans `FEUILLE_DE_ROUTE.md`.

---

## 1. Ce qu'on a testé

La **même API REST** (mini réseau social, 5 routes, base MySQL 8.4) écrite avec 4 technologies :

| Technologie | Langage | Port | Serveur |
|---|---|---|---|
| Laravel 13.42.0 | PHP 8.3 | 8001 | php-fpm (10 processus) + nginx |
| FastAPI 0.163.1 | Python 3.12 | 8002 | uvicorn (2 processus) |
| Spring Boot 4.1.2 | Java 25 | 8003 | Tomcat |
| Gin 1.12.0 | Go 1.27 | 8004 | serveur HTTP de Go |

Règles d'équité, identiques pour les 4 :
- chaque API dans un conteneur Docker limité à **2 cœurs et 1 Go** (Laravel : + 0,5 cœur / 128 Mo
  pour nginx) ;
- **MySQL** dans son propre conteneur (`mysql-dev`), limité à 2 cœurs et 2 Go ;
- **même SQL**, écrit à la main (pas d'ORM), **10 connexions MySQL maximum** par API ;
- pas de cache, journaux d'accès coupés, mode debug coupé.

Tout tourne sur **une seule machine** (portable HP Victus, i5-12450H, 16 Go, Windows 11,
Docker Desktop / WSL 2), y compris le générateur de charge k6.

---

## 2. Avant les mesures : vérifier que les 4 API font la même chose

**Test de contrat** (`tests/contract_test.py`, 102 cas) lancé sur les 4 API :
cas normaux, erreurs de validation, ressources absentes, doublons (409), auto-abonnement,
pagination, tri, espaces Unicode…

Résultat : **102/102 réussis pour chaque API**, et **corps de réponse identiques** entre les 4
dans les 102 cas (voir `raw-results/contract_test.txt`).
Règle suivie : aucune mesure tant que ce test n'était pas à 100 %.

---

## 3. Les essais à blanc (réglage du protocole)

Avant la vraie campagne, deux essais courts (1 essai de 5 s par charge) ont servi à tester les
scripts. Ils sont gardés à part dans `raw-results/essai-a-blanc/` et **ne comptent pas** dans
les résultats.

1. **`1-durabilite-complete/`** — MySQL avec sa configuration par défaut.
   Constat : chaque écriture coûtait environ **9 ms** de synchronisation disque (WSL 2).
   Les écritures mesuraient donc surtout le disque, pas les API.
2. **Décision du groupe (« option b »)** : MySQL avec durabilité réduite
   (`innodb_flush_log_at_trx_commit=2`, sans journal binaire). Après ce réglage, un INSERT
   coûte autant qu'un `SELECT 1`.
3. **`2-option-b/`** — nouvel essai à blanc : 0 % d'erreur, aucune case vide. On voyait déjà
   que MySQL saturait son CPU (≈ 150–200 %) à 50 et 100 utilisateurs.

Autre décision prise pendant la préparation (« option 2 ») : les 4 API envoient leurs
requêtes SQL de la même façon (paramètres insérés côté client, une requête = un aller-retour).

---

## 4. Le scénario de charge (k6)

Fichier : `load-tests/mixed.js` (k6 v2.4.0).

- Un nombre **constant** d'utilisateurs virtuels (VU) : **10, 50 ou 100**.
- Chaque VU envoie une requête, attend la réponse, et recommence **sans pause** pendant **15 s**.
- Chaque requête est tirée au sort :

| Requête | Part |
|---|---|
| `GET /api/posts` (liste paginée, page 1 à 50, 20 posts) | 50 % |
| `POST /api/posts` (créer un post) | 15 % |
| `POST /api/posts/{id}/like` | 15 % |
| `POST /api/users/{id}/follow` | 10 % |
| `POST /api/posts/{id}/comments` | 10 % |

- Un **409** (like ou follow déjà existant) est une réponse normale, **pas une erreur**.
- Utilisateurs et posts tirés dans le jeu de données initial (1 000 users, 10 000 posts).

---

## 5. La campagne complète

**Quand :** le 15 novembre 2026, de **22 h 57 à 0 h 43** (heure locale ; 21:57–23:43 UTC dans
les fichiers), soit **1 h 46**, **sans interruption**. Le PC n'était pas utilisé pendant ce temps.

**Commande :** `python bench/run_campaign.py --resume`

**Volume :** 4 technologies × 3 charges × 30 essais = **360 essais**.

### Organisation : 3 tours, ordre décalé à chaque tour

Pour ne pas avantager une technologie (chauffe de la machine, dérive au fil du temps), la
campagne est coupée en 3 tours de 10 essais, et l'ordre change d'un cran à chaque tour :

| Tour | Ordre des technologies |
|---|---|
| 1 | Laravel → FastAPI → Spring Boot → Gin |
| 2 | FastAPI → Spring Boot → Gin → Laravel |
| 3 | Spring Boot → Gin → Laravel → FastAPI |

### Ce qui se passe pour chaque technologie, dans un tour

1. Toutes les API sont arrêtées ; **seuls les conteneurs de la technologie testée** démarrent
   (MySQL reste allumé).
2. Le script attend la première réponse 200, puis fait un **échauffement de 10 s à 10 VU**
   (non mesuré).
3. Puis, 10 fois, pour chaque charge (10, puis 50, puis 100 VU) :
   1. **remise à zéro de la base** (`scripts/reset_db.py`) : suppression de tout ce qui a été
      créé, puis vérification des comptages exacts ;
   2. **pause de 2 s** ;
   3. **k6 pendant 15 s** ; en parallèle, un thread relève `docker stats` (CPU et mémoire de
      l'API et de MySQL) environ une fois par seconde ;
   4. écriture des résultats.
4. Les conteneurs de la technologie sont arrêtés.

### Fichiers produits

| Fichier | Contenu |
|---|---|
| `raw-results/mesures.csv` | 1 ligne par essai × route (+ 1 ligne « all ») : 2 160 lignes |
| `raw-results/k6/<tech>_<vus>vu_essaiNN.json` | résumé complet de k6 pour chaque essai (360 fichiers) |
| `raw-results/stats/<tech>_<vus>vu_essaiNN.csv` | relevés `docker stats` bruts (360 fichiers) |
| `raw-results/campagne_log.txt` | journal de la campagne, essai par essai |
| `raw-results/campagne.json` | paramètres de la campagne |

---

## 6. Le test de démarrage à froid

**Quand :** le 16 novembre 2026 au matin, PC sans autre activité.
**Commande :** `python bench/startup_time.py`

Pour chaque API, **30 fois** :
1. arrêt des conteneurs de l'API ;
2. chronomètre lancé, `docker compose up -d` ;
3. interrogation de `GET /api/posts?limit=1` toutes les 50 ms jusqu'à la **première réponse 200** →
   temps de démarrage ;
4. 5 s de repos, puis relevé de la **RAM au repos**.

Fichiers : `raw-results/demarrage.csv`, `ram_repos.csv`, `images.csv` (taille des images),
`demarrage_log.txt`.

---

## 7. Contrôle des données

Fait par `analysis/analyse.py` (résultat dans `analysis/controle_qualite.txt`) :

- les **360 essais** sont présents (30 par technologie et par charge), sans case vide ni doublon ;
- **0 % d'erreur** dans tous les essais ; aucun essai écarté ;
- **2 294 821 requêtes HTTP** mesurées au total ;
- **120 démarrages à froid** (30 par API) ;
- cohérence vérifiée par la **loi de Little** (nombre de VU = débit × latence moyenne) :
  accord à **1,1 % près** dans les 12 configurations.

---

## 8. Ce que les tests ont montré (résumé)

Médianes sur 30 essais (détails : `analysis/` et le rapport `final-report/rapport.tex`).

| | Laravel | FastAPI | Spring Boot | Gin |
|---|---|---|---|---|
| Débit à 100 VU (req/s) | 383,8 | 441,5 | 406,2 | 420,5 |
| Latence p95 à 50 VU (ms) | 184,0 | 290,3 | 292,8 | 287,1 |
| CPU de l'API à 50 VU (% d'un cœur) | 194 | 57 | 40 | 21 |
| RAM max de l'API à 50 VU (Mo) | 59 | 107 | 212 | 11 |
| Démarrage à froid (ms) | 902 | 1 733 | 3 346 | 445 |

- **MySQL est le goulot** : son CPU est au maximum (≈ 200 %) dès 10 VU, pour les 4 API.
  Le débit plafonne donc entre ≈ 380 et 485 req/s, et l'écart entre technologies reste ≤ 20 %.
- Les vraies différences sont dans le **coût** (CPU, mémoire, démarrage) et la **forme des
  latences** (Laravel : médiane plus haute mais queue plus courte).

---

## 9. Limites à garder en tête

- k6, les API et MySQL sur **la même machine** (un portable), sous Docker Desktop / WSL 2.
- Durabilité de MySQL **réduite** (option b) — sans effet sur les mesures.
- Laravel a **0,5 cœur de plus** pour nginx et envoie `USE` + `SET NAMES` à chaque requête.
- Essais de **15 s**, scénario **sans temps de pause**, un seul mélange de requêtes.
- CPU et mémoire relevés **≈ 1 fois par seconde**.
- Démarrage à froid : inclut le temps de la commande `docker compose` ; cache disque déjà chaud.

---

## 10. Refaire les tests

```powershell
docker compose up -d mysql                       # base seule
python scripts/init_db.py                        # schéma + jeu de données
docker compose up -d --build laravel fastapi spring gin
python tests/contract_test.py --all              # doit être à 100 % PASS
python bench/run_campaign.py --essais 1 --duration 5s --out-dir essai   # essai à blanc
python bench/run_campaign.py --resume            # campagne complète (~1 h 50)
python bench/startup_time.py                     # démarrage à froid
python analysis/analyse.py                       # analyse, figures, tables
```

`--resume` permet de reprendre une campagne interrompue sans refaire les essais déjà enregistrés.
