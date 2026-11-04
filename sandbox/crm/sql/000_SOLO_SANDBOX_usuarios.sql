-- SOLO PARA EL SANDBOX. NUNCA se corre en el hosting ni se sube a crm/sql.
--
-- `usuarios` y `roles` son tablas del e-commerce de Don Regalo: ninguna migración
-- del CRM las crea. Esto da una versión mínima con las columnas que lee
-- `Auth::login`, con asesores ficticios y contraseñas de prueba.

CREATE TABLE IF NOT EXISTS roles (
  id_rol INT(11) NOT NULL AUTO_INCREMENT,
  nombre_rol VARCHAR(64) NOT NULL,
  PRIMARY KEY (id_rol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS usuarios (
  id_usuario INT(11) NOT NULL AUTO_INCREMENT,
  login_usuario VARCHAR(64) NOT NULL,
  password_usuario VARCHAR(190) NOT NULL,
  nombre_usuario VARCHAR(120) NULL,
  apellidos_usuario VARCHAR(120) NULL,
  email_usuario VARCHAR(190) NULL,
  id_rol INT(11) NULL,
  PRIMARY KEY (id_usuario),
  UNIQUE KEY uq_sandbox_login (login_usuario)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO roles (id_rol, nombre_rol) VALUES (1, 'Asesor de prueba');

INSERT IGNORE INTO usuarios
  (id_usuario, login_usuario, password_usuario, nombre_usuario, apellidos_usuario, email_usuario, id_rol)
VALUES
  (1, 'asesor1', 'sandbox', 'Ana', 'Prueba', 'ana@example.invalid', 1),
  (2, 'asesor2', 'sandbox', 'Beto', 'Prueba', 'beto@example.invalid', 1);
