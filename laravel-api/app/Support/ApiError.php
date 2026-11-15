<?php

namespace App\Support;

use Illuminate\Http\JsonResponse;

/** Format d'erreur unique du contrat : {"error": "<code>"}. */
final class ApiError
{
    public static function response(int $status, string $code): JsonResponse
    {
        return new JsonResponse(['error' => $code], $status, [], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }
}
