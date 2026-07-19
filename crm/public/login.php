<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';

if (Auth::user()) {
    header('Location: ' . url_to('/'));
    exit;
}

$error = '';
$login = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $login = (string) ($_POST['login'] ?? '');
    $password = (string) ($_POST['password'] ?? '');
    if (Auth::login($login, $password)) {
        header('Location: ' . url_to('/'));
        exit;
    }
    $error = 'Usuario o contraseña incorrectos';
}

view('login', ['error' => $error, 'login' => $login]);
