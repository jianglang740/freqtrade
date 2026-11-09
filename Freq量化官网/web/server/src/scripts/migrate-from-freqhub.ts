/**
 * 一次性迁移：把 FreqHub 的 users / bots / bot_ownership 搬进官网自己的库。
 * - 用户密码哈希原样保留（bcrypt cost 10，可直接校验），原密码继续可用
 * - 机器人密码从旧 AES-256-CBC 解密，再用新 AES-256-GCM 重新加密
 * - 全程不打印密钥、明文、密文
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { createDecipheriv } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { parse as parseEnv } from 'dotenv';
import { get, run, transaction } from '../db/index.js';
import { decryptSecret, encryptSecret } from '../services/crypto.js';
import { insertUser, getUserById, getUserByUsername } from '../services/users.js';
import { getBotById } from '../services/bots.js';
import { fail, parseArgs, str } from './lib.js';

type SourceUser = {
  id: string;
  username: string;
  name: string | null;
  email: string | null;
  password_hash: string;
  role: string;
  is_active: number;
  failed_login_attempts: number;
  account_locked_until: number | null;
  last_login: number | null;
  last_login_ip: string | null;
  created_at: number;
};

type SourceBot = {
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

type SourceOwnership = {
  bot_id: string;
  user_id: string;
  created_at: number;
  created_by: string | null;
};

/** 旧格式：ivHex:cipherHex，AES-256-CBC，密钥为 UTF-8 前 32 字节（不足补零） */
function decryptLegacyCbc(secret: string, stored: string): string {
  const parts = stored.split(':');
  if (parts.length !== 2) throw new Error('旧密文格式不正确');

  const iv = Buffer.from(parts[0]!, 'hex');
  const cipher = Buffer.from(parts[1]!, 'hex');
  if (iv.length !== 16 || cipher.length === 0) throw new Error('旧密文格式不正确');

  const key = Buffer.alloc(32);
  Buffer.from(secret, 'utf8').copy(key, 0, 0, 32);
  try {
    const decipher = createDecipheriv('aes-256-cbc', key, iv);
    return Buffer.concat([decipher.update(cipher), decipher.final()]).toString('utf8');
  } finally {
    key.fill(0);
  }
}

function main(): void {
  const flags = parseArgs(process.argv.slice(2));
  const dryRun = Boolean(flags['dry-run']);

  const sourceDbPath = path.resolve(
    str(flags, 'source-db', '../../../FreqHub/backend/data/freqhub.db'),
  );
  const sourceEnvPath = path.resolve(str(flags, 'source-env', '../../../FreqHub/backend/.env'));

  const sourceEnv = parseEnv(readFileSync(sourceEnvPath, 'utf8'));
  const legacyKey = sourceEnv.ENCRYPTION_KEY;
  if (!legacyKey) fail(`源 .env 里没有 ENCRYPTION_KEY：${sourceEnvPath}`);

  const source = new DatabaseSync(sourceDbPath, { readOnly: true });
  let users: SourceUser[];
  let bots: SourceBot[];
  let ownership: SourceOwnership[];
  try {
    users = source.prepare('SELECT * FROM users').all() as unknown as SourceUser[];
    bots = source.prepare('SELECT * FROM bots').all() as unknown as SourceBot[];
    ownership = source
      .prepare('SELECT bot_id, user_id, created_at, created_by FROM bot_ownership')
      .all() as unknown as SourceOwnership[];
  } finally {
    source.close();
  }

  console.log(`源库：users=${users.length} bots=${bots.length} ownership=${ownership.length}`);

  // 先在内存里全部解密验证一遍，避免写一半才发现密钥不对
  const botPlain = new Map<string, string>();
  for (const bot of bots) {
    try {
      botPlain.set(bot.id, decryptLegacyCbc(legacyKey, bot.encrypted_password));
    } catch {
      fail(`机器人「${bot.name}」的旧密码解密失败，请确认 --source-env 指向正确的 .env`);
    }
  }

  if (dryRun) {
    console.log('[dry-run] 校验通过，未写入任何数据');
    for (const user of users) console.log(`  用户 ${user.username} (${user.role})`);
    for (const bot of bots) console.log(`  机器人 ${bot.name} → ${bot.api_url}`);
    return;
  }

  const targetUserId = new Map<string, string>();
  const targetBotId = new Map<string, string>();
  let skipped = 0;

  transaction(() => {
    for (const user of users) {
      const existing = getUserById(user.id) ?? getUserByUsername(user.username);
      if (existing) {
        targetUserId.set(user.id, existing.id);
        skipped += 1;
        continue;
      }
      insertUser({
        id: user.id,
        username: user.username,
        name: user.name,
        email: user.email,
        password_hash: user.password_hash,
        role: user.role === 'superadmin' ? 'superadmin' : 'user',
        is_active: user.is_active,
        failed_login_attempts: user.failed_login_attempts ?? 0,
        account_locked_until: user.account_locked_until,
        last_login: user.last_login,
        last_login_ip: user.last_login_ip,
        created_at: user.created_at,
      });
      targetUserId.set(user.id, user.id);
    }

    for (const bot of bots) {
      if (getBotById(bot.id)) {
        targetBotId.set(bot.id, bot.id);
        skipped += 1;
        continue;
      }
      const plain = botPlain.get(bot.id)!;
      const encrypted = encryptSecret(plain);
      run(
        `INSERT INTO bots (id, name, api_url, username, encrypted_password, is_enabled,
          notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        bot.id,
        bot.name,
        bot.api_url,
        bot.username,
        encrypted,
        bot.is_enabled ?? 1,
        bot.notes,
        bot.created_at,
        bot.updated_at,
      );
      targetBotId.set(bot.id, bot.id);

      // 立刻回读校验新密文能否解回原文（只在内存里比较）
      const stored = get('SELECT encrypted_password FROM bots WHERE id = ?', bot.id) as unknown as {
        encrypted_password: string;
      };
      if (decryptSecret(stored.encrypted_password) !== plain) {
        throw new Error(`机器人「${bot.name}」新密文回读校验失败`);
      }
    }

    for (const row of ownership) {
      const botId = targetBotId.get(row.bot_id);
      const userId = targetUserId.get(row.user_id);
      if (!botId || !userId) continue;
      const existing = get(
        'SELECT id FROM bot_ownership WHERE bot_id = ? AND user_id = ?',
        botId,
        userId,
      );
      if (existing) continue;
      run(
        `INSERT INTO bot_ownership (id, bot_id, user_id, expires_at, created_at, created_by)
         VALUES (?, ?, ?, NULL, ?, ?)`,
        `own-${botId}-${userId}`,
        botId,
        userId,
        row.created_at,
        row.created_by ? (targetUserId.get(row.created_by) ?? null) : null,
      );
    }
  });

  const count = (table: string): number =>
    (get(`SELECT count(*) AS n FROM ${table}`) as unknown as { n: number }).n;
  const targetCounts = {
    users: count('users'),
    bots: count('bots'),
    ownership: count('bot_ownership'),
  };

  console.log('迁移完成');
  console.log(`  现有：users=${targetCounts.users} bots=${targetCounts.bots} ownership=${targetCounts.ownership}`);
  console.log(`  跳过（已存在）条目：${skipped}`);
  console.log('  旧密码可直接登录；机器人凭据已转为新加密格式');
}

main();
