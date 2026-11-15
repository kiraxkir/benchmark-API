<?php

namespace App\Http\Middleware;

use App\Support\ApiError;
use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Laravel ignore la barre oblique finale lors du routage ("/api/posts/" == "/api/posts").
 * Les 3 autres versions la traitent comme une route inconnue : on aligne (404 not_found).
 */
class RejectTrailingSlash
{
    public function handle(Request $request, Closure $next): Response
    {
        $path = $request->getPathInfo();
        if ($path !== '/' && str_ends_with($path, '/')) {
            return ApiError::response(404, 'not_found');
        }

        return $next($request);
    }
}
