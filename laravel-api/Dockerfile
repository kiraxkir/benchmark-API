# Image de production Laravel (php-fpm). nginx tourne dans un conteneur séparé.

# --- Étape 1 : dépendances Composer (versions figées par composer.lock) ---
FROM composer:2 AS vendor
WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-autoloader --prefer-dist \
        --no-interaction --ignore-platform-reqs
COPY . .
RUN composer dump-autoload --no-dev --classmap-authoritative --no-scripts

# --- Étape 2 : exécution ---
FROM php:8.3-fpm
RUN docker-php-ext-install pdo_mysql opcache \
    # Logs d'accès php-fpm désactivés (activés par défaut dans l'image officielle)
    && sed -i '/^access.log/d' /usr/local/etc/php-fpm.d/docker.conf
COPY docker/php.ini /usr/local/etc/php/conf.d/zz-benchmark.ini
COPY docker/www.conf /usr/local/etc/php-fpm.d/zz-benchmark.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
WORKDIR /var/www/html
COPY --from=vendor /app /var/www/html
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && php artisan package:discover --no-ansi \
    && php artisan route:cache --no-ansi \
    && chown -R www-data:www-data storage bootstrap/cache
ENV APP_ENV=production APP_DEBUG=false
ENTRYPOINT ["entrypoint.sh"]
CMD ["php-fpm"]
