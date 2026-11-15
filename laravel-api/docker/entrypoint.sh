#!/bin/sh
# Met la configuration en cache au démarrage (les identifiants MySQL viennent de
# l'environnement du conteneur et ne sont donc jamais figés dans l'image).
set -e
php artisan config:cache --no-ansi --quiet
exec "$@"
