<?php

declare(strict_types=1);

final class Auth
{
    /** @var array */
    private static $config = array();

    public static function init(array $config)
    {
        self::$config = $config;
        if (session_status() !== PHP_SESSION_ACTIVE) {
            $base = rtrim((string) (isset($config['base_path']) ? $config['base_path'] : ''), '/');
            $cookiePath = $base === '' ? '/' : $base;
            // Misma ruta para login e /api/*; si no, el fetch del inbox va sin sesión → vacío/401.
            if (PHP_VERSION_ID >= 70300) {
                session_set_cookie_params(array(
                    'lifetime' => 0,
                    'path' => $cookiePath,
                    'secure' => (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off'),
                    'httponly' => true,
                    'samesite' => 'Lax',
                ));
            } else {
                session_set_cookie_params(0, $cookiePath);
            }
            session_name(isset($config['session_name']) ? $config['session_name'] : 'dr_crm_php');
            session_start();
        }
    }

    /** @return array */
    public static function config()
    {
        return self::$config;
    }

    /** @return array|null */
    public static function user()
    {
        return isset($_SESSION['crm_user']) ? $_SESSION['crm_user'] : null;
    }

    public static function requireLogin()
    {
        if (!self::user()) {
            $base = rtrim((string) (isset(self::$config['base_path']) ? self::$config['base_path'] : ''), '/');
            header('Location: ' . ($base === '' ? '/login.php' : $base . '/login.php'));
            exit;
        }
    }

    /**
     * Login con login_usuario + password_usuario (tabla usuarios).
     *
     * @param string $login
     * @param string $password
     * @return bool
     */
    public static function login($login, $password)
    {
        $loginClean = trim($login);
        if ($loginClean === '' || $password === '') {
            return false;
        }

        $row = Database::fetchOne(
            'SELECT u.id_usuario, u.login_usuario, u.password_usuario,
                    u.nombre_usuario, u.apellidos_usuario, u.email_usuario,
                    u.id_rol
             FROM usuarios u
             WHERE u.login_usuario = :login
             LIMIT 1',
            array(':login' => $loginClean)
        );

        if (!$row) {
            return false;
        }

        $stored = (string) (isset($row['password_usuario']) ? $row['password_usuario'] : '');
        if (!hash_equals($stored, (string) $password)) {
            return false;
        }

        $roleName = 'rol_' . (isset($row['id_rol']) ? $row['id_rol'] : '0');
        try {
            $role = Database::fetchOne(
                'SELECT nombre_rol FROM roles WHERE id_rol = :id LIMIT 1',
                array(':id' => (int) $row['id_rol'])
            );
            if ($role && !empty($role['nombre_rol'])) {
                $roleName = $role['nombre_rol'];
            }
        } catch (Exception $e) {
            // Tabla roles opcional
        }

        $_SESSION['crm_user'] = array(
            'id' => (int) $row['id_usuario'],
            'login' => $row['login_usuario'],
            'name' => trim((isset($row['nombre_usuario']) ? $row['nombre_usuario'] : '') . ' ' . (isset($row['apellidos_usuario']) ? $row['apellidos_usuario'] : '')),
            'email' => isset($row['email_usuario']) ? $row['email_usuario'] : '',
            'role' => $roleName,
        );
        return true;
    }

    public static function logout()
    {
        $_SESSION = array();
        if (ini_get('session.use_cookies')) {
            $p = session_get_cookie_params();
            setcookie(session_name(), '', time() - 42000, $p['path'], $p['domain'], (bool) $p['secure'], (bool) $p['httponly']);
        }
        session_destroy();
    }

    /**
     * Lee el token del agente desde headers (Apache/CGI a veces renombra).
     * @return string
     */
    private static function requestToken()
    {
        $candidates = array();
        if (!empty($_SERVER['HTTP_X_CRM_TOKEN'])) {
            $candidates[] = (string) $_SERVER['HTTP_X_CRM_TOKEN'];
        }
        if (!empty($_SERVER['REDIRECT_HTTP_X_CRM_TOKEN'])) {
            $candidates[] = (string) $_SERVER['REDIRECT_HTTP_X_CRM_TOKEN'];
        }
        if (!empty($_SERVER['HTTP_AUTHORIZATION'])) {
            $auth = (string) $_SERVER['HTTP_AUTHORIZATION'];
            if (stripos($auth, 'Bearer ') === 0) {
                $candidates[] = trim(substr($auth, 7));
            }
        }
        if (!empty($_SERVER['REDIRECT_HTTP_AUTHORIZATION'])) {
            $auth = (string) $_SERVER['REDIRECT_HTTP_AUTHORIZATION'];
            if (stripos($auth, 'Bearer ') === 0) {
                $candidates[] = trim(substr($auth, 7));
            }
        }
        if (function_exists('getallheaders')) {
            $headers = getallheaders();
            if (is_array($headers)) {
                foreach ($headers as $k => $v) {
                    $lk = strtolower((string) $k);
                    if ($lk === 'x-crm-token') {
                        $candidates[] = (string) $v;
                    }
                    if ($lk === 'authorization' && stripos((string) $v, 'Bearer ') === 0) {
                        $candidates[] = trim(substr((string) $v, 7));
                    }
                }
            }
        }
        foreach ($candidates as $c) {
            $c = trim($c);
            if ($c !== '') {
                return $c;
            }
        }
        return '';
    }

    public static function assertInternalToken()
    {
        $expected = trim((string) (isset(self::$config['crm_internal_token']) ? self::$config['crm_internal_token'] : ''));
        if ($expected === '' || $expected === 'cambia-este-token-seguro') {
            Http::jsonError('CRM_INTERNAL_TOKEN not configured on CRM PHP', 500);
        }
        $got = self::requestToken();
        if ($got === '' || !hash_equals($expected, $got)) {
            Http::jsonError('Unauthorized', 401);
        }
    }

    /** @return bool */
    public static function hasValidInternalToken()
    {
        $expected = trim((string) (isset(self::$config['crm_internal_token']) ? self::$config['crm_internal_token'] : ''));
        if ($expected === '' || $expected === 'cambia-este-token-seguro') {
            return false;
        }
        $got = self::requestToken();
        return $got !== '' && hash_equals($expected, $got);
    }
}
