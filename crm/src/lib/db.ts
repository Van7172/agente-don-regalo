import mysql, { Pool, RowDataPacket, ResultSetHeader } from "mysql2/promise";

let pool: Pool | null = null;

/** Valores que acepta mysql2 (named o posicionales). */
type ExecuteValues = NonNullable<Parameters<Pool["execute"]>[1]>;

export function getPool(): Pool {
  if (!pool) {
    pool = mysql.createPool({
      host: process.env.MYSQL_HOST || "127.0.0.1",
      port: Number(process.env.MYSQL_PORT || 3307),
      user: process.env.MYSQL_USER || "root",
      password: process.env.MYSQL_PASSWORD ?? "",
      database: process.env.MYSQL_DATABASE || "donregalo_bd",
      waitForConnections: true,
      connectionLimit: 10,
      namedPlaceholders: true,
      charset: "utf8mb4",
    });
  }
  return pool;
}

export type { RowDataPacket, ResultSetHeader };

export async function query<T extends RowDataPacket[]>(
  sql: string,
  params?: Record<string, unknown> | unknown[]
): Promise<T> {
  const [rows] = await getPool().execute<T>(sql, params as ExecuteValues | undefined);
  return rows;
}

export async function execute(
  sql: string,
  params?: Record<string, unknown> | unknown[]
): Promise<ResultSetHeader> {
  const [result] = await getPool().execute<ResultSetHeader>(
    sql,
    params as ExecuteValues | undefined
  );
  return result;
}
