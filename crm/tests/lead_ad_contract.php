<?php

declare(strict_types=1);

/**
 * El anuncio que trajo el lead llega hasta la pantalla del asesor.
 *
 * La cadena es larga y cruza tres despliegues (SQL → CRM → agente): si se cae
 * un eslabón, el asesor sigue viendo "¡Hola! Quiero más información." a secas y
 * nadie se entera de que el dato se perdió por el camino. Esto comprueba que
 * los cinco tramos existen.
 */

function source(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

function requiresText(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

// 1. La migración: sin las columnas, todo lo demás peta en producción.
$sql = source('sql/007_lead_anuncio.sql');
foreach (['ad_source_type', 'ad_source_id', 'ad_headline', 'ad_body', 'ad_source_url', 'ad_ctwa_clid'] as $col) {
    requiresText($sql, $col, 'Falta la columna del anuncio');
}

// 2. La ingesta: el agente manda `referral` y el CRM lo guarda.
$api = source('public/api/index.php');
requiresText($api, "\$body['referral']", 'La ingesta no lee el referral que manda el agente');
requiresText($api, 'setConversationAd', 'La ingesta no guarda el anuncio');
requiresText($api, "'ad' =>", 'El endpoint del chat no devuelve el anuncio al panel');

// 3. El repositorio: se fija una vez y no se pisa.
$repo = source('src/Repository.php');
requiresText($repo, 'function setConversationAd', 'Falta setConversationAd');
requiresText(
    $repo,
    'AND ad_source_id IS NULL',
    'La atribución debe ser del primer anuncio: sin esta guarda, una segunda '
        . 'visita desde otro anuncio le reescribe el origen al asesor'
);
requiresText($repo, 'c.ad_source_id', 'getConversation no trae las columnas del anuncio');

// 4. La vista y el pintado.
$view = source('views/inbox.php');
requiresText($view, 'id="ad-card"', 'Falta el contenedor de la tarjeta del anuncio');

$js = source('public/assets/inbox.js');
requiresText($js, 'function adCard', 'Falta el render de la tarjeta');
requiresText($js, 'repaintAdCard(conv.ad)', 'La tarjeta no se pinta al abrir el chat');
requiresText($js, 'esc(String(ad.headline))', 'El copy del anuncio debe ir escapado: es texto de terceros');

// 5. El estilo.
$css = source('public/assets/app.css');
requiresText($css, '.ad-card', 'Falta el estilo de la tarjeta del anuncio');

echo "lead ad contract: OK\n";
