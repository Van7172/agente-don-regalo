<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

/**
 * Estudio de contenido: un post de redes a partir de un producto del catálogo.
 *
 * Tonos y formatos se declaran aquí porque son lo que ve el asesor, pero los
 * slugs tienen que coincidir con `app/services/social_copy.py` — si el panel
 * manda un tono que el agente no conoce, el agente cae al de por defecto sin
 * avisar y el asesor cree que eligió algo que no eligió. Lo vigila
 * `crm/tests/generador_contenido_contract.php`.
 */
$tonos = [
    'casual' => ['label' => 'Casual', 'hint' => 'Cercano, como hablarle a un amigo'],
    'elegante' => ['label' => 'Elegante', 'hint' => 'Sobrio, sin emojis chillones'],
    'divertido' => ['label' => 'Divertido', 'hint' => 'Humor ligero y juguetón'],
    'romantico' => ['label' => 'Romántico', 'hint' => 'Cálido, centrado en el gesto'],
    'urgente' => ['label' => 'Urgente', 'hint' => 'Oportunidad, decídete hoy'],
];

// 9:16 primero y marcado por defecto: es historia de Instagram y TikTok, de
// donde entra la mayoría de los leads.
$formatos = [
    '9:16' => ['label' => 'Historia 9:16', 'hint' => 'Instagram / TikTok · vertical completo'],
    '4:5' => ['label' => 'Feed 4:5', 'hint' => 'Publicación vertical de feed'],
    '1:1' => ['label' => 'Cuadrado 1:1', 'hint' => 'Publicación cuadrada'],
];

view('content', [
    'tonos' => $tonos,
    'formatos' => $formatos,
]);
