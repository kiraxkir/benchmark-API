<?php

use App\Http\Controllers\ApiController;
use Illuminate\Support\Facades\Route;

// Préfixe /api ajouté par bootstrap/app.php. Aucune autre route (contrat §5).
Route::get('posts', [ApiController::class, 'listPosts']);
Route::post('posts', [ApiController::class, 'createPost']);
Route::post('posts/{id}/like', [ApiController::class, 'likePost']);
Route::post('users/{id}/follow', [ApiController::class, 'followUser']);
Route::post('posts/{id}/comments', [ApiController::class, 'createComment']);
