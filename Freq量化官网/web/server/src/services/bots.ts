import { randomUUID } from 'node:crypto';
import { all, get, run } from '../db/index.js';
import type { PublicUser } from './users.js';
import { decryptSecret, encryptSecret } from './crypto.js';

export type DbBot = {
  id: string;
  name: string;
  api_url: string;
  username: string;
  encrypted_password: string;
  is_enabled: number;
  notes: string | null;
  created_at: number;
  updated_at: number;
};

export type PublicBot = {
  id: string;
  name: string;
  apiUrl: string;
  username: string;
  isEnabled: boolean;
  notes: string | null;
  createdAt: number;
  updatedAt: number;
};

export function toPublicBot(bot: DbBot): PublicBot {
  return {
    id: bot.id,
    name: bot.name,
    apiUrl: bot.api_url,
    username: bot.username,
    isEnabled: bot.is_enabled === 1,
    notes: bot.notes,
    createdAt: bot.created_at,
    updatedAt: bot.updated_at,
  };
}

export function getBotById(id: string): DbBot | undefined {
  return get('SELECT * FROM bots WHERE id = ?', id) as DbBot | undefined;
}

export function getBotByName(name: string): DbBot | undefined {
  return get('SELECT * FROM bots WHERE name = ?', name) as DbBot | undefined;
}

export function listAllBots(): DbBot[] {
  return all('SELECT * FROM bots ORDER BY created_at ASC') as unknown as DbBot[];
}

/** 普通用户只看未过期的绑定；superadmin 看全部 */
export function listBotsForUser(user: PublicUser): DbBot[] {
  if (user.role === 'superadmin') return listAllBots();
  return all(
    `SELECT b.* FROM bots b
       JOIN bot_ownership o ON o.bot_id = b.id
      WHERE o.user_id = ?
        AND (o.expires_at IS NULL OR o.expires_at > ?)
      ORDER BY b.created_at ASC`,
    user.id,
    Date.now(),
  ) as unknown as DbBot[];
}

export type AccessResult =
  | { ok: true; bot: DbBot }
  | { ok: false; reason: 'not_found' | 'forbidden' | 'expired' | 'disabled' };

export function getBotForUser(user: PublicUser, botId: string): AccessResult {
  const bot = getBotById(botId);
  if (!bot) return { ok: false, reason: 'not_found' };
  if (user.role !== 'superadmin') {
    const ownership = get(
      'SELECT expires_at FROM bot_ownership WHERE bot_id = ? AND user_id = ?',
      botId,
      user.id,
    ) as { expires_at: number | null } | undefined;
    if (!ownership) return { ok: false, reason: 'forbidden' };
    if (ownership.expires_at !== null && ownership.expires_at <= Date.now()) {
      return { ok: false, reason: 'expired' };
    }
  }
  if (bot.is_enabled !== 1) return { ok: false, reason: 'disabled' };
  return { ok: true, bot };
}

export function createBot(input: {
  id?: string;
  name: string;
  apiUrl: string;
  username: string;
  password: string;
  notes?: string | null;
}): DbBot {
  const now = Date.now();
  const id = input.id ?? randomUUID();
  run(
    `INSERT INTO bots (id, name, api_url, username, encrypted_password, is_enabled, notes,
      created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)`,
    id,
    input.name,
    input.apiUrl,
    input.username,
    encryptSecret(input.password),
    input.notes ?? null,
    now,
    now,
  );
  return getBotById(id)!;
}

export function botCredentials(bot: DbBot): { username: string; password: string } {
  return { username: bot.username, password: decryptSecret(bot.encrypted_password) };
}

export function assignBot(input: {
  botId: string;
  userId: string;
  createdBy?: string | null;
  expiresAt?: number | null;
}): void {
  const existing = get(
    'SELECT id FROM bot_ownership WHERE bot_id = ? AND user_id = ?',
    input.botId,
    input.userId,
  ) as { id: string } | undefined;

  if (existing) {
    run(
      'UPDATE bot_ownership SET expires_at = ? WHERE id = ?',
      input.expiresAt ?? null,
      existing.id,
    );
    return;
  }

  run(
    `INSERT INTO bot_ownership (id, bot_id, user_id, expires_at, created_at, created_by)
     VALUES (?, ?, ?, ?, ?, ?)`,
    randomUUID(),
    input.botId,
    input.userId,
    input.expiresAt ?? null,
    Date.now(),
    input.createdBy ?? null,
  );
}

export function unassignBot(botId: string, userId: string): boolean {
  const existing = get(
    'SELECT id FROM bot_ownership WHERE bot_id = ? AND user_id = ?',
    botId,
    userId,
  );
  if (!existing) return false;
  run('DELETE FROM bot_ownership WHERE bot_id = ? AND user_id = ?', botId, userId);
  return true;
}

/** 管理页展示的归属信息：不含任何机器人凭据 */
export type BotOwnerView = {
  userId: string;
  username: string;
  name: string | null;
  expiresAt: number | null;
  createdAt: number;
};

type BotOwnerRow = {
  bot_id: string;
  user_id: string;
  username: string;
  name: string | null;
  expires_at: number | null;
  created_at: number;
};

const OWNER_SQL = `SELECT o.bot_id, o.user_id, u.username, u.name,
       o.expires_at, o.created_at
  FROM bot_ownership o
  JOIN users u ON u.id = o.user_id`;

function toOwnerView(row: BotOwnerRow): BotOwnerView {
  return {
    userId: row.user_id,
    username: row.username,
    name: row.name,
    expiresAt: row.expires_at,
    createdAt: row.created_at,
  };
}

export function listOwnersForBot(botId: string): BotOwnerView[] {
  const rows = all(`${OWNER_SQL} WHERE o.bot_id = ? ORDER BY u.username ASC`, botId) as unknown as BotOwnerRow[];
  return rows.map(toOwnerView);
}

/** 一次取全部归属，管理页按 bot_id 分组，避免每台机器人各查一次 */
export function listAllOwners(): Array<BotOwnerView & { botId: string }> {
  const rows = all(`${OWNER_SQL} ORDER BY u.username ASC`) as unknown as BotOwnerRow[];
  return rows.map((row) => ({ ...toOwnerView(row), botId: row.bot_id }));
}

export function deleteBot(id: string): boolean {
  if (!getBotById(id)) return false;
  run('DELETE FROM bots WHERE id = ?', id);
  return true;
}
