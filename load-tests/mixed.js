// Scénario de charge unique (CLAUDE.md §9), identique pour les 4 API.
//
// Usage : k6 run -e BASE_URL=http://localhost:8004 -e VUS=10 -e DURATION=15s -e OUT=resume.json load-tests/mixed.js
//
// Exécuteur constant-vus, sans temps de pause : chaque VU enchaîne les itérations (boucle fermée).
// À chaque itération, une requête tirée au sort :
//   GET posts 50 % · POST posts 15 % · like 15 % · follow 10 % · comment 10 %.
// Chaque requête est taguée par endpoint (tag `name`) ; le résumé complet est écrit dans OUT.
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8004';
const VUS = parseInt(__ENV.VUS || '10', 10);
const DURATION = __ENV.DURATION || '15s';
const OUT = __ENV.OUT || 'summary.json';

const ENDPOINTS = ['list_posts', 'create_post', 'like', 'follow', 'comment'];
const JSON_HEADERS = { headers: { 'Content-Type': 'application/json' } };

// 409 (doublon de like ou de follow) est une réponse métier normale, pas une erreur.
http.setResponseCallback(http.expectedStatuses(200, 201, 409));

// Seuils factices : ils forcent l'apparition des sous-métriques par endpoint dans le résumé.
const thresholds = {};
for (const name of ENDPOINTS) {
  thresholds[`http_req_duration{name:${name}}`] = ['p(95)<100000'];
  thresholds[`http_reqs{name:${name}}`] = ['count>=0'];
  thresholds[`http_req_failed{name:${name}}`] = ['rate<=1'];
}

export const options = {
  scenarios: {
    mixed: { executor: 'constant-vus', vus: VUS, duration: DURATION },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  thresholds,
};

function randInt(min, max) {
  return min + Math.floor(Math.random() * (max - min + 1));
}

function text() {
  return `Message de charge ${__VU}-${__ITER} ${randInt(1, 1000000)}`;
}

export default function () {
  const r = Math.random();
  if (r < 0.50) {
    const res = http.get(`${BASE_URL}/api/posts?page=${randInt(1, 50)}&limit=20`,
      { tags: { name: 'list_posts' } });
    check(res, { 'list_posts 200': (x) => x.status === 200 });
  } else if (r < 0.65) {
    const res = http.post(`${BASE_URL}/api/posts`,
      JSON.stringify({ user_id: randInt(1, 1000), content: text() }),
      { ...JSON_HEADERS, tags: { name: 'create_post' } });
    check(res, { 'create_post 201': (x) => x.status === 201 });
  } else if (r < 0.80) {
    const res = http.post(`${BASE_URL}/api/posts/${randInt(1, 10000)}/like`,
      JSON.stringify({ user_id: randInt(1, 1000) }),
      { ...JSON_HEADERS, tags: { name: 'like' } });
    check(res, { 'like 201/409': (x) => x.status === 201 || x.status === 409 });
  } else if (r < 0.90) {
    const followed = randInt(1, 1000);
    let follower = randInt(1, 999);
    if (follower >= followed) follower += 1; // jamais d'auto-follow
    const res = http.post(`${BASE_URL}/api/users/${followed}/follow`,
      JSON.stringify({ follower_id: follower }),
      { ...JSON_HEADERS, tags: { name: 'follow' } });
    check(res, { 'follow 201/409': (x) => x.status === 201 || x.status === 409 });
  } else {
    const res = http.post(`${BASE_URL}/api/posts/${randInt(1, 10000)}/comments`,
      JSON.stringify({ user_id: randInt(1, 1000), content: text() }),
      { ...JSON_HEADERS, tags: { name: 'comment' } });
    check(res, { 'comment 201': (x) => x.status === 201 });
  }
}

// Résumé JSON complet dans OUT (ne dépend pas de --summary-export) + une ligne sur la sortie standard.
export function handleSummary(data) {
  const reqs = data.metrics.http_reqs ? data.metrics.http_reqs.values : { count: 0, rate: 0 };
  const failed = data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate : 0;
  return {
    [OUT]: JSON.stringify(data, null, 1),
    stdout: `k6 : ${reqs.count} requêtes, ${reqs.rate.toFixed(1)} req/s, erreurs ${(100 * failed).toFixed(2)} %\n`,
  };
}
