<?php

use App\Http\Middleware\RejectTrailingSlash;
use App\Support\ApiError;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Foundation\Http\Middleware\ConvertEmptyStringsToNull;
use Illuminate\Foundation\Http\Middleware\TrimStrings;
use Illuminate\Http\Request;
use Symfony\Component\HttpKernel\Exception\MethodNotAllowedHttpException;
use Symfony\Component\HttpKernel\Exception\NotFoundHttpException;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        // Routes API uniquement (préfixe /api, groupe "api" sans session ni cookies).
        api: __DIR__.'/../routes/api.php',
    )
    ->withMiddleware(function (Middleware $middleware): void {
        // Le trim et la validation sont faits à la main, à l'identique des 3 autres versions.
        $middleware->remove([TrimStrings::class, ConvertEmptyStringsToNull::class]);
        $middleware->prepend(RejectTrailingSlash::class);
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        // Format d'erreur unique {"error": "..."} : route/méthode inconnue -> 404, le reste -> 500.
        $exceptions->render(function (Throwable $e, Request $request) {
            if ($e instanceof NotFoundHttpException || $e instanceof MethodNotAllowedHttpException) {
                return ApiError::response(404, 'not_found');
            }

            return ApiError::response(500, 'internal_error');
        });
    })->create();
