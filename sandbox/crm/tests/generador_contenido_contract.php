<?php

declare(strict_types=1);

/**
 * Estudio de contenido: el panel y el agente tienen que hablar del mismo idioma.
 *
 * La sección vive en dos repos a la vez —PHP en el hosting, Python en el VPS— y
 * los desplieguan personas distintas en momentos distintos. Un tono que el
 * panel ofrece y el agente no conoce NO da error: el agente cae al de por
 * defecto en silencio y el asesor cree que eligió algo que no eligió.
 */

$raiz = dirname(__DIR__, 2);
$vista = (string) file_get_contents(dirname(__DIR__) . '/public/content.php');
$markup = (string) file_get_contents(dirname(__DIR__) . '/views/content.php');
$javascript = (string) file_get_contents(dirname(__DIR__) . '/public/assets/content.js');
$api = (string) file_get_contents(dirname(__DIR__) . '/public/api/index.php');
$layout = (string) file_get_contents(dirname(__DIR__) . '/views/layout.php');
$servicio = (string) file_get_contents($raiz . '/app/services/social_copy.py');

// 1. Los tonos del selector existen en el agente.
if (!preg_match_all("/^\s{4}'([a-z]+)' => \['label'/m", $vista, $m)) {
    throw new RuntimeException('No se pudieron leer los tonos del panel');
}
foreach ($m[1] as $tono) {
    if (strpos($servicio, "\"{$tono}\":") === false) {
        throw new RuntimeException("El tono «{$tono}» del panel no existe en social_copy.TONOS");
    }
}

// 2. 9:16 por defecto — es historia de Instagram/TikTok, de donde vienen los leads.
if (strpos($vista, "'9:16' => ['label'") === false) {
    throw new RuntimeException('Falta el formato 9:16');
}
if (!preg_match("/\\\$formatos = \[\s*'9:16'/", $vista)) {
    throw new RuntimeException('9:16 dejó de ser el primero, o sea el marcado por defecto');
}
if (strpos($servicio, 'FORMATO_POR_DEFECTO = "9:16"') === false) {
    throw new RuntimeException('El agente ya no tiene 9:16 como formato por defecto');
}

// 3. El selector se habilita a los 3 caracteres, y el corte se repite en el
//    servidor: el navegador no decide cuánto se le pregunta al agente.
if (strpos($javascript, 'const MIN_CHARS = 3;') === false) {
    throw new RuntimeException('El selector perdió el mínimo de 3 caracteres');
}
if (strpos($api, "mb_strlen(\$q) < 3") === false) {
    throw new RuntimeException('El CRM ya no corta la búsqueda por debajo de 3 caracteres');
}
if (strpos($servicio, 'MAX_VARIANTES') === false) {
    throw new RuntimeException('Desapareció el tope de variantes');
}

// 4. El token del agente NO baja al navegador: el panel va con cookie de sesión
//    y es el CRM quien presenta el token.
if (strpos($api, "strpos(\$path, '/content/') === 0") === false) {
    throw new RuntimeException('Las rutas de contenido ya no aceptan sesión');
}
foreach (["'/content/products'", "'/content/draft'"] as $ruta) {
    if (strpos($api, $ruta) === false) {
        throw new RuntimeException("Falta la ruta {$ruta}");
    }
}
if (strpos($api, 'AgentClient::get(') === false || strpos($api, 'AgentClient::post(') === false) {
    throw new RuntimeException('El CRM dejó de pasar por AgentClient');
}
if (strpos($javascript, 'X-Agent-Token') !== false) {
    throw new RuntimeException('El token interno NUNCA puede estar en el JavaScript del panel');
}

// 5. El precio es del catálogo. Es la regla que convierte un post en un
//    problema legal si se rompe.
if (strpos($servicio, 'def menciona_dinero') === false) {
    throw new RuntimeException('Se perdió el guardia de precios inventados');
}
if (strpos($markup, 'sale del catálogo') === false) {
    throw new RuntimeException('El panel dejó de decir de dónde sale el precio');
}

// 6. La sección es alcanzable. Una página sin enlace no la usa nadie.
if (strpos($layout, 'content.php') === false) {
    throw new RuntimeException('Falta el enlace «Crear contenido» en la navegación');
}

// 7. Los cuatro pasos que pidió el equipo, en orden.
$pasos = ['step-product', 'step-format', 'step-brief', 'step-result'];
$anterior = -1;
foreach ($pasos as $paso) {
    $pos = strpos($markup, 'id="' . $paso . '"');
    if ($pos === false) {
        throw new RuntimeException("Falta el paso {$paso}");
    }
    if ($pos < $anterior) {
        throw new RuntimeException("El paso {$paso} está fuera de orden");
    }
    $anterior = $pos;
}

echo "generador contenido contract: OK\n";
