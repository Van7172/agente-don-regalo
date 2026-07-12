<?php

declare(strict_types=1);

final class Http
{
    public static function jsonOk(array $data, $status = 200)
    {
        http_response_code($status);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode($data, JSON_UNESCAPED_UNICODE);
        exit;
    }

    public static function jsonError($message, $status = 400)
    {
        http_response_code($status);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(array('error' => $message), JSON_UNESCAPED_UNICODE);
        exit;
    }

    /** @return array */
    public static function readJson()
    {
        $raw = file_get_contents('php://input');
        if ($raw === false || $raw === '') {
            return array();
        }
        $data = json_decode($raw, true);
        return is_array($data) ? $data : array();
    }

    /** @return string */
    public static function method()
    {
        return strtoupper(isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET');
    }

    public static function cors()
    {
        header('Access-Control-Allow-Origin: *');
        header('Access-Control-Allow-Headers: Content-Type, X-CRM-Token');
        header('Access-Control-Allow-Methods: GET, POST, PUT, PATCH, OPTIONS');
        if (self::method() === 'OPTIONS') {
            http_response_code(204);
            exit;
        }
    }
}
