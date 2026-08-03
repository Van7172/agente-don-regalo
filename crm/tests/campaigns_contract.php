<?php

declare(strict_types=1);

/**
 * El panel de campañas mide bien, o no sirve para decidir dónde gastar.
 *
 * Todo lo que se comprueba aquí es una forma de mentir con los mismos datos:
 * un JOIN que infla los chats, un rango que mezcla cohortes y da conversiones
 * imposibles, o un backfill que convierte "sin producto" en el producto 0. Son
 * errores silenciosos — la tabla se pinta igual y con números creíbles.
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

// 1. La migración del id de producto: SQL antes que PHP, y el backfill no puede
//    inventar el producto 0 a partir de un `null` del JSON.
$sql = source('sql/013_venta_producto_id.sql');
requiresText($sql, 'id_producto_venta_historial', 'Falta la columna del producto vendido');
requiresText(
    $sql,
    'JSON_TYPE',
    'El backfill necesita distinguir el JSON null de la clave ausente: sin eso, '
        . 'un id_producto null acaba en un CAST a 0 y se agrupa como producto real'
);
requiresText($sql, 'idx_ventas_historiales_producto', 'Sin índice, agrupar por producto escanea la tabla');

// 2. La tabla de demanda no cubierta. Existe antes que el panel que la lee a
//    propósito: la señal no es recuperable hacia atrás y empieza a contar desde
//    que se corre la migración.
$demanda = source('sql/014_demanda_no_cubierta.sql');
requiresText($demanda, 'crm_demanda_no_cubierta', 'Falta la tabla de demanda no cubierta');
requiresText($demanda, "ENUM('vacio','aproximado')", 'Hay que separar "no había nada" de "solo alternativas"');

// 3. El repositorio.
$repo = source('src/Repository.php');
requiresText($repo, 'function campaignPerformance', 'Falta campaignPerformance');
requiresText(
    $repo,
    'LEFT JOIN crm_ventas_historiales',
    'Con JOIN a secas desaparecen los anuncios que no cerraron ni una venta, '
        . 'que son justo los que hay que apagar'
);
requiresText(
    $repo,
    'COUNT(DISTINCT c.id_conversation)',
    'El LEFT JOIN duplica la conversación por cada venta: sin DISTINCT, un chat '
        . 'con dos compras cuenta como dos chats y hunde la conversión'
);
requiresText(
    $repo,
    'GROUP BY c.ad_source_id',
    'La agrupación es por anuncio'
);
requiresText(
    $repo,
    'AND c.fecha_creacion BETWEEN :f AND :to',
    'El rango va sobre la llegada del chat, no sobre el cierre de la venta: '
        . 'rangear las ventas por su fecha mezcla cohortes y puede dar más del 100%'
);

// 4. El controlador no puede dividir por cero ni llamar orgánico a lo que no sabe.
$controller = source('public/campaigns.php');
requiresText(
    $controller,
    '$leads > 0 ?',
    'Sin chats no hay conversión: un 0% se lee como "no cierra" en vez de "no trajo a nadie"'
);
requiresText(
    $controller,
    'Sin anuncio identificado',
    'Los chats sin referral no son tráfico orgánico: incluyen todo lo anterior '
        . 'a la captura del anuncio'
);

// 5. La vista advierte de lo que la tabla no puede decir.
$view = source('views/campaigns.php');
requiresText($view, 'reports-caveats', 'Falta el bloque de cómo leer la tabla');
requiresText($view, 'no por cierre de la venta', 'La vista debe explicar la cohorte');
requiresText($view, 'Falta el gasto', 'Sin gasto esto es conversión, no costo por venta; hay que decirlo');
requiresText(
    $view,
    'rel="noopener noreferrer"',
    'El enlace al anuncio sale a un dominio de terceros'
);

// 6. El estilo y la navegación: un módulo al que no se llega no existe.
requiresText(source('public/assets/app.css'), '.conv-bar', 'Falta el estilo de la barra de conversión');
requiresText(source('views/layout.php'), "url_to('campaigns.php')", 'Falta el enlace en la barra de navegación');

echo "campaigns contract: OK\n";
