<?php

declare(strict_types=1);

final class Database
{
    /** @var PDO|null */
    private static $pdo = null;

    public static function init(array $db)
    {
        if (self::$pdo) {
            return;
        }
        $dsn = sprintf(
            'mysql:host=%s;port=%d;dbname=%s;charset=%s',
            $db['host'],
            (int) (isset($db['port']) ? $db['port'] : 3306),
            $db['name'],
            isset($db['charset']) ? $db['charset'] : 'utf8mb4'
        );
        self::$pdo = new PDO($dsn, $db['user'], $db['pass'], array(
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ));

        // MySQL y PHP deben coincidir. Sin esto, NOW() escribe la hora del servidor
        // de BD y PHP la interpreta en SU zona: el inbox mostraba las horas
        // desfasadas respecto a lo que el cliente ve en WhatsApp.
        $offset = (new DateTimeImmutable('now'))->format('P'); // ej. "-05:00"
        $stmt = self::$pdo->prepare('SET time_zone = :tz');
        $stmt->execute(array('tz' => $offset));
    }

    /** @return PDO */
    public static function pdo()
    {
        if (!self::$pdo) {
            throw new RuntimeException('Database not initialized');
        }
        return self::$pdo;
    }

    /**
     * @param array $params
     * @return array
     */
    public static function fetchAll($sql, array $params = array())
    {
        $stmt = self::pdo()->prepare($sql);
        $stmt->execute($params);
        return $stmt->fetchAll();
    }

    /**
     * @param array $params
     * @return array|null
     */
    public static function fetchOne($sql, array $params = array())
    {
        $stmt = self::pdo()->prepare($sql);
        $stmt->execute($params);
        $row = $stmt->fetch();
        return $row === false ? null : $row;
    }

    /**
     * @param array $params
     * @return int
     */
    public static function execute($sql, array $params = array())
    {
        $stmt = self::pdo()->prepare($sql);
        $stmt->execute($params);
        return (int) self::pdo()->lastInsertId();
    }

    /**
     * @param array $params
     */
    public static function exec($sql, array $params = array())
    {
        $stmt = self::pdo()->prepare($sql);
        $stmt->execute($params);
    }
}
