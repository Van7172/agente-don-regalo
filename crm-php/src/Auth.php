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

    public static function assertInternalToken()
    {
        $expected = (string) (isset(self::$config['crm_internal_token']) ? self::$config['crm_internal_token'] : '');
        if ($expected === '') {
            Http::jsonError('CRM_INTERNAL_TOKEN not configured', 500);
        }
        $got = isset($_SERVER['HTTP_X_CRM_TOKEN']) ? $_SERVER['HTTP_X_CRM_TOKEN'] : '';
        if (!hash_equals($expected, (string) $got)) {
            Http::jsonError('Unauthorized', 401);
        }
    }

    /** @return bool */
    public static function hasValidInternalToken()
    {
        $expected = (string) (isset(self::$config['crm_internal_token']) ? self::$config['crm_internal_token'] : '');
        if ($expected === '') {
            return false;
        }
        $got = isset($_SERVER['HTTP_X_CRM_TOKEN']) ? $_SERVER['HTTP_X_CRM_TOKEN'] : '';
        return hash_equals($expected, (string) $got);
    }
}
