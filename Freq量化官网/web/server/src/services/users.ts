import { randomUUID } from 'node:crypto';
import { all, get, run } from '../db/index.js';

export type Role = 'superadmin' | 'user';

export type DbUser = {
  id: string;
  username: string;
  name: string | null;
  email: string | null;
  password_hash: string;
  role: Role;
  is_active: number;
  failed_login_attempts: number;
  account_locked_until: number | null;
  last_login: number | null;
  last_login_ip: string | null;
  created_at: number;
  updated_at: number;
};

export type PublicUser = {
  id: string;
  username: string;
  name: string | null;
  email: string | null;
  role: Role;
};

export function toPublicUser(user: DbUser): PublicUser {
  return {
    id: user.id,
    username: user.username,
    name: user.name,
    email: user.email,
    role: user.role,
  };
}

export function getUserByUsername(username: string): DbUser | undefined {
  return get('SELECT * FROM users WHERE username = ? COLLATE NOCASE', username) as
    | DbUser
    | undefined;
}

export function getUserById(id: string): DbUser | undefined {
  return get('SELECT * FROM users WHERE id = ?', id) as DbUser | undefined;
}

export function listUsers(): DbUser[] {
  return all('SELECT * FROM users ORDER BY created_at ASC') as unknown as DbUser[];
}

/** 管理页用：不带密码哈希，多一列名下机器人数 */
export type AdminUserRow = Omit<DbUser, 'password_hash'> & { bot_count: number };

export function listUsersWithBotCount(): AdminUserRow[] {
  return all(
    `SELECT u.id, u.username, u.name, u.email, u.role, u.is_active,
            u.failed_login_attempts, u.account_locked_until,
            u.last_login, u.last_login_ip, u.created_at, u.updated_at,
            COUNT(o.id) AS bot_count
       FROM users u
       LEFT JOIN bot_ownership o ON o.user_id = u.id
      GROUP BY u.id
      ORDER BY u.created_at ASC`,
  ) as unknown as AdminUserRow[];
}

export function countUserBots(userId: string): number {
  const row = get('SELECT COUNT(*) AS n FROM bot_ownership WHERE user_id = ?', userId) as
    | { n: number }
    | undefined;
  return row?.n ?? 0;
}

export function countActiveSuperadmins(): number {
  const row = get(
    "SELECT COUNT(*) AS n FROM users WHERE role = 'superadmin' AND is_active = 1",
  ) as { n: number } | undefined;
  return row?.n ?? 0;
}

export function updateUserProfile(
  id: string,
  input: { name?: string | null; email?: string | null; isActive?: number },
): DbUser | undefined {
  const current = getUserById(id);
  if (!current) return undefined;
  run(
    'UPDATE users SET name = ?, email = ?, is_active = ?, updated_at = ? WHERE id = ?',
    input.name !== undefined ? input.name : current.name,
    input.email !== undefined ? input.email : current.email,
    input.isActive !== undefined ? input.isActive : current.is_active,
    Date.now(),
    id,
  );
  return getUserById(id);
}

/** 重置密码时顺手清掉失败次数与锁定，否则改了密码还是登不上 */
export function setUserPassword(id: string, passwordHash: string): boolean {
  const current = getUserById(id);
  if (!current) return false;
  run(
    `UPDATE users SET password_hash = ?, failed_login_attempts = 0,
       account_locked_until = NULL, updated_at = ? WHERE id = ?`,
    passwordHash,
    Date.now(),
    id,
  );
  return true;
}

export function deleteUser(id: string): boolean {
  if (!getUserById(id)) return false;
  run('DELETE FROM users WHERE id = ?', id);
  return true;
}

export function insertUser(input: {
  id?: string;
  username: string;
  name?: string | null;
  email?: string | null;
  password_hash: string;
  role?: Role;
  is_active?: number;
  failed_login_attempts?: number;
  account_locked_until?: number | null;
  last_login?: number | null;
  last_login_ip?: string | null;
  created_at?: number;
}): DbUser {
  const now = Date.now();
  const id = input.id ?? randomUUID();
  run(
    `INSERT INTO users (
      id, username, name, email, password_hash, role, is_active,
      failed_login_attempts, account_locked_until, last_login, last_login_ip,
      created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    id,
    input.username,
    input.name ?? null,
    input.email ?? null,
    input.password_hash,
    input.role ?? 'user',
    input.is_active ?? 1,
    input.failed_login_attempts ?? 0,
    input.account_locked_until ?? null,
    input.last_login ?? null,
    input.last_login_ip ?? null,
    input.created_at ?? now,
    now,
  );
  return getUserById(id)!;
}

export function recordLoginSuccess(userId: string, ip: string | null): void {
  const now = Date.now();
  run(
    `UPDATE users SET last_login = ?, last_login_ip = ?, failed_login_attempts = 0,
      account_locked_until = NULL, updated_at = ? WHERE id = ?`,
    now,
    ip,
    now,
    userId,
  );
}

/** 返回是否已触发锁定 */
export function recordLoginFailure(
  user: DbUser,
  threshold: number,
  lockMs: number,
): boolean {
  const now = Date.now();
  const attempts = user.failed_login_attempts + 1;
  if (attempts >= threshold) {
    run(
      `UPDATE users SET failed_login_attempts = 0, account_locked_until = ?, updated_at = ?
        WHERE id = ?`,
      now + lockMs,
      now,
      user.id,
    );
    return true;
  }
  run(
    'UPDATE users SET failed_login_attempts = ?, updated_at = ? WHERE id = ?',
    attempts,
    now,
    user.id,
  );
  return false;
}
