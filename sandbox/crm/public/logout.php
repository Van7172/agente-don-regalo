<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::logout();
header('Location: ' . url_to('login.php'));
exit;
