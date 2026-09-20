<?php

declare(strict_types=1);

/**
 * Una URL externa no es sinónimo de imagen.
 *
 * Los productos usan fotos públicas, pero las campañas pueden usar PDFs
 * públicos. El inbox debe escoger el widget por la extensión real y mantener
 * el comportamiento histórico de imagen para CDNs cuya URL no tiene extensión.
 */

require_once dirname(__DIR__) . '/src/Media.php';

function expectKind(string $expected, string $url): void
{
    $actual = Media::kindForExternal($url);
    if ($actual !== $expected) {
        throw new RuntimeException(
            "Se esperaba {$expected} para {$url}; se obtuvo {$actual}"
        );
    }
}

expectKind(
    'document',
    'https://www.donregalo.pe/catalogo/catalogodepreventaFLORESAMARILLAS_.pdf'
);
expectKind('document', 'https://cdn.example.com/catalogo.pdf?version=2');
expectKind('image', 'https://cdn.example.com/producto.webp?size=large');
expectKind('audio', 'https://cdn.example.com/nota.ogg');
expectKind('image', 'https://cdn.example.com/media/abc123');

echo "external media kind contract: OK\n";
