<?php

declare(strict_types=1);

/**
 * Archivos de conversación (fotos, notas de voz, comprobantes).
 *
 * Viven FUERA del docroot (crm-php/storage/media) y solo se sirven vía
 * public/media.php, que exige sesión de asesor o el token interno del agente.
 * Son datos personales de clientes: nunca deben quedar colgando de una URL pública.
 */
final class Media
{
    /** Tope de WhatsApp para audio/doc/vídeo. Las imágenes las limita Meta a 5 MB. */
    public const MAX_BYTES = 16 * 1024 * 1024;

    /** @var array<string, string> extensión => mime */
    private const TYPES = [
        // imagen
        'jpg' => 'image/jpeg',
        'jpeg' => 'image/jpeg',
        'png' => 'image/png',
        'webp' => 'image/webp',
        'gif' => 'image/gif',
        // audio
        'ogg' => 'audio/ogg',
        'oga' => 'audio/ogg',
        'opus' => 'audio/ogg',
        'mp3' => 'audio/mpeg',
        'm4a' => 'audio/mp4',
        'aac' => 'audio/aac',
        'amr' => 'audio/amr',
        'wav' => 'audio/wav',
        // El navegador graba en webm/opus; el agente lo convierte antes de enviarlo.
        'webm' => 'audio/webm',
        // documento
        'pdf' => 'application/pdf',
        'doc' => 'application/msword',
        'docx' => 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xls' => 'application/vnd.ms-excel',
        'xlsx' => 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'ppt' => 'application/vnd.ms-powerpoint',
        'pptx' => 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'txt' => 'text/plain',
        'csv' => 'text/csv',
        'zip' => 'application/zip',
    ];

    private const IMAGE_EXT = ['jpg', 'jpeg', 'png', 'webp', 'gif'];
    private const AUDIO_EXT = ['ogg', 'oga', 'opus', 'mp3', 'm4a', 'aac', 'amr', 'wav', 'webm'];

    public static function root(): string
    {
        return dirname(__DIR__) . '/storage/media';
    }

    /**
     * Una clave válida es "YYYY-MM/<32 hex>.<ext>" con una extensión de la lista
     * blanca. Nada más entra: ni rutas relativas, ni .php, ni bytes nulos.
     */
    public static function isValidKey(string $key): bool
    {
        if (!preg_match('#^\d{4}-\d{2}/[a-f0-9]{32}\.([a-z0-9]{1,5})$#', $key, $m)) {
            return false;
        }
        return isset(self::TYPES[$m[1]]);
    }

    public static function pathFor(string $key): ?string
    {
        if (!self::isValidKey($key)) {
            return null;
        }
        $path = self::root() . '/' . $key;
        return is_file($path) ? $path : null;
    }

    public static function extOf(string $key): string
    {
        return strtolower((string) pathinfo($key, PATHINFO_EXTENSION));
    }

    public static function mimeFor(string $key): string
    {
        return self::TYPES[self::extOf($key)] ?? 'application/octet-stream';
    }

    /** image | audio | document — con qué widget lo pinta el inbox. */
    public static function kindFor(string $key): string
    {
        $ext = self::extOf($key);
        if (in_array($ext, self::IMAGE_EXT, true)) {
            return 'image';
        }
        if (in_array($ext, self::AUDIO_EXT, true)) {
            return 'audio';
        }
        return 'document';
    }

    /** Extensión permitida a partir del mime, con el nombre original como respaldo. */
    private static function resolveExt(string $mime, string $originalName): ?string
    {
        $mime = strtolower(trim(explode(';', $mime)[0]));
        foreach (self::TYPES as $ext => $knownMime) {
            if ($knownMime === $mime) {
                return $ext;
            }
        }
        $ext = strtolower((string) pathinfo($originalName, PATHINFO_EXTENSION));
        return isset(self::TYPES[$ext]) ? $ext : null;
    }

    /**
     * Guarda bytes y devuelve la clave de almacenamiento.
     *
     * @throws RuntimeException si el tipo no se admite, está vacío o pasa del tope.
     */
    public static function store(string $bytes, string $mime, string $originalName = ''): string
    {
        $size = strlen($bytes);
        if ($size === 0) {
            throw new RuntimeException('Archivo vacío');
        }
        if ($size > self::MAX_BYTES) {
            $mb = round(self::MAX_BYTES / 1048576);
            throw new RuntimeException("El archivo pasa del límite de {$mb} MB");
        }

        $ext = self::resolveExt($mime, $originalName);
        if ($ext === null) {
            throw new RuntimeException('Tipo de archivo no admitido');
        }

        $folder = date('Y-m');
        $dir = self::root() . '/' . $folder;
        if (!is_dir($dir) && !mkdir($dir, 0770, true) && !is_dir($dir)) {
            throw new RuntimeException('No se pudo crear el directorio de medios');
        }

        $key = $folder . '/' . bin2hex(random_bytes(16)) . '.' . $ext;
        if (file_put_contents(self::root() . '/' . $key, $bytes) === false) {
            throw new RuntimeException('No se pudo guardar el archivo');
        }

        return $key;
    }

    /** @param array $file entrada de $_FILES */
    public static function storeUploaded(array $file): string
    {
        $error = (int) ($file['error'] ?? UPLOAD_ERR_NO_FILE);
        if ($error !== UPLOAD_ERR_OK) {
            throw new RuntimeException(self::uploadErrorMessage($error));
        }

        $tmp = (string) ($file['tmp_name'] ?? '');
        if ($tmp === '' || !is_uploaded_file($tmp)) {
            throw new RuntimeException('Subida inválida');
        }

        $bytes = file_get_contents($tmp);
        if ($bytes === false) {
            throw new RuntimeException('No se pudo leer el archivo subido');
        }

        // El mime que declara el navegador no es de fiar: se comprueba contra el contenido.
        $detected = '';
        if (class_exists('finfo')) {
            $finfo = new finfo(FILEINFO_MIME_TYPE);
            $detected = (string) $finfo->buffer($bytes);
        }
        $mime = $detected !== '' ? $detected : (string) ($file['type'] ?? '');

        return self::store($bytes, $mime, (string) ($file['name'] ?? ''));
    }

    private static function uploadErrorMessage(int $error): string
    {
        switch ($error) {
            case UPLOAD_ERR_INI_SIZE:
            case UPLOAD_ERR_FORM_SIZE:
                return 'El archivo pasa del límite permitido';
            case UPLOAD_ERR_PARTIAL:
                return 'La subida se interrumpió';
            case UPLOAD_ERR_NO_FILE:
                return 'No se envió ningún archivo';
            default:
                return 'Error al subir el archivo';
        }
    }
}
