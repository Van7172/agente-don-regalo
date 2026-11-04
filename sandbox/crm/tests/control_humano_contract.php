<?php

declare(strict_types=1);

$repository = (string) file_get_contents(dirname(__DIR__) . '/src/Repository.php');
$api = (string) file_get_contents(dirname(__DIR__) . '/public/api/index.php');
$javascript = (string) file_get_contents(dirname(__DIR__) . '/public/assets/inbox.js');

$start = strpos($repository, 'public static function claimConversation');
$end = strpos($repository, 'public static function releaseConversation', $start ?: 0);
if ($start === false || $end === false) {
    throw new RuntimeException('No se pudo aislar claimConversation');
}
$claim = substr($repository, $start, $end - $start);

// Reclamar y apagar el bot son una sola escritura. Si el navegador tuviera que
// hacer un segundo PATCH, ese request podría fallar mientras el asesor ya cree
// que tiene el control y la IA seguiría contestando.
foreach ([
    "mode_conversation = \\'HUMAN\\'",
    'human_support = 0',
    'bot_active = 0',
] as $required) {
    if (strpos($claim, $required) === false) {
        throw new RuntimeException(
            "El claim no toma control humano atómicamente: falta {$required}"
        );
    }
}

// El check no puede ser solo memoria del DOM: al abrir o refrescar el chat debe
// mostrar el pin que realmente está en crm_settings y que lee el releaser.
foreach ([
    [$repository, "AS keep_human", 'getConversation no trae keep_human'],
    [$api, "'keep_human' =>", 'la API no expone keep_human'],
    [
        $javascript,
        'el.keepHuman.checked = !!conv.keep_human',
        'el checkbox no refleja el valor persistido',
    ],
] as [$source, $needle, $message]) {
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message);
    }
}

echo "control humano contract: OK\n";
