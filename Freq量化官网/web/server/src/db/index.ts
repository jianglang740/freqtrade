import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { DatabaseSync, type StatementSync } from 'node:sqlite';
import { env } from '../env.js';
import { SCHEMA_SQL } from './schema.js';

const dbPath = path.resolve(env.DATABASE_PATH);
mkdirSync(path.dirname(dbPath), { recursive: true });

export const db = new DatabaseSync(dbPath);
db.exec('PRAGMA journal_mode = WAL');
// WAL 下 NORMAL 已经足够安全（崩溃最多丢最后一个事务，不会损坏库），换掉每次提交的 fsync
db.exec('PRAGMA synchronous = NORMAL');
db.exec('PRAGMA foreign_keys = ON');
db.exec('PRAGMA busy_timeout = 5000');
db.exec(SCHEMA_SQL);

/** node:sqlite 的返回类型是宽泛的，这里统一收窄成普通对象 */
export type Row = Record<string, unknown>;

/**
 * 预编译语句缓存：每个请求都要回库查用户和归属，重复 prepare 是纯浪费。
 * SQL 是代码里的常量，键的数量有上限；真超了就整体清空重来。
 */
const MAX_PREPARED = 200;
const prepared = new Map<string, StatementSync>();

function prepare(sql: string): StatementSync {
  const cached = prepared.get(sql);
  if (cached) return cached;
  const stmt = db.prepare(sql);
  if (prepared.size >= MAX_PREPARED) prepared.clear();
  prepared.set(sql, stmt);
  return stmt;
}

export function all(sql: string, ...params: unknown[]): Row[] {
  return prepare(sql).all(...(params as never[])) as Row[];
}

export function get(sql: string, ...params: unknown[]): Row | undefined {
  return prepare(sql).get(...(params as never[])) as Row | undefined;
}

export function run(sql: string, ...params: unknown[]): void {
  prepare(sql).run(...(params as never[]));
}

export function transaction<T>(fn: () => T): T {
  db.exec('BEGIN');
  try {
    const result = fn();
    db.exec('COMMIT');
    return result;
  } catch (err) {
    db.exec('ROLLBACK');
    throw err;
  }
}
