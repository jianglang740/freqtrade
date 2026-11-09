import { Router } from 'express';
import { z } from 'zod';
import { requireAuth, requireSuperadmin } from '../middleware/auth.js';
import { requireWriteAccess } from '../middleware/writeAccess.js';
import { keyedLimiter } from '../middleware/rateLimit.js';
import { ApiError } from '../middleware/error.js';
import { apiUserLimiter, testConnectionLimiter } from '../services/rateLimit.js';
import {
  assignBot,
  createBot,
  deleteBot,
  getBotById,
  getBotByName,
  listAllBots,
  listAllOwners,
  listBotsForUser,
  listOwnersForBot,
  toPublicBot,
  unassignBot,
} from '../services/bots.js';
import { getUserById } from '../services/users.js';
import { invalidateToken, testBotConnection } from '../services/freqtrade.js';
import { assertBotApiUrl } from '../services/urlSecurity.js';
import { audit } from '../services/audit.js';

const testSchema = z
  .object({
    apiUrl: z.string().min(1).max(2048),
    username: z.string().min(1).max(128),
    password: z.string().min(1).max(256),
  })
  .strict();

const createSchema = testSchema
  .extend({
    name: z.string().trim().min(1).max(100),
    notes: z.string().max(1000).nullish(),
  })
  .strict();

const testPerUser = keyedLimiter(testConnectionLimiter, (req) => req.user?.id ?? null);

function safeApiUrl(raw: string): string {
  try {
    return assertBotApiUrl(raw);
  } catch (err) {
    throw new ApiError(400, err instanceof Error ? err.message : 'Invalid API URL');
  }
}

const CONNECT_FAILED = 'Failed to connect to Freqtrade bot';

export const botsRouter = Router();

botsRouter.use(requireAuth);
// 用户级限流：按账号计数，不误伤共用出口 IP 的客户
botsRouter.use(keyedLimiter(apiUserLimiter, (req) => req.user?.id ?? null));

botsRouter.get('/', (req, res) => {
  const bots = listBotsForUser(req.user!).map(toPublicBot);
  res.json({ status: 'success', data: bots });
});

botsRouter.post('/test', requireWriteAccess, testPerUser, async (req, res, next) => {
  try {
    const body = testSchema.parse(req.body ?? {});
    const apiUrl = safeApiUrl(body.apiUrl);

    const ok = await testBotConnection(apiUrl, body.username, body.password);
    if (!ok) throw new ApiError(400, CONNECT_FAILED);

    res.json({ status: 'success', message: 'Connection successful' });
  } catch (err) {
    next(err);
  }
});

botsRouter.post('/', requireWriteAccess, testPerUser, async (req, res, next) => {
  try {
    const body = createSchema.parse(req.body ?? {});
    const apiUrl = safeApiUrl(body.apiUrl);

    if (getBotByName(body.name)) {
      throw new ApiError(400, 'A bot with this name already exists');
    }

    const ok = await testBotConnection(apiUrl, body.username, body.password);
    if (!ok) throw new ApiError(400, CONNECT_FAILED);

    const bot = createBot({
      name: body.name,
      apiUrl,
      username: body.username,
      password: body.password,
      notes: body.notes ?? null,
    });
    invalidateToken(bot.id);

    audit({
      actorId: req.user!.id,
      action: 'bot_created',
      target: bot.id,
      detail: bot.name,
      ip: req.ip,
    });

    res.status(201).json({ status: 'success', data: toPublicBot(bot) });
  } catch (err) {
    next(err);
  }
});

/* ───────────────── 管理：机器人清单与归属 ───────────────── */

const assignSchema = z
  .object({
    userId: z.string().min(1).max(64),
    /** YYYY-MM-DD；不传或 null 表示永久 */
    expiresAt: z.string().nullable().optional(),
  })
  .strict();

/** 与命令行 --expires 口径一致：取当天 UTC 23:59:59.999，避免时区导致提前一天失效 */
function parseExpiryDate(value: string | null | undefined): number | null {
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new ApiError(400, '到期日期需要 YYYY-MM-DD 格式');
  }
  const ts = Date.parse(`${value}T23:59:59.999Z`);
  if (Number.isNaN(ts)) throw new ApiError(400, '到期日期无法解析');
  return ts;
}

/** 全部机器人 + 各自归属；只给 superadmin，响应不含密码 */
botsRouter.get('/admin', requireSuperadmin, (_req, res) => {
  const owners = listAllOwners();
  const byBot = new Map<string, ReturnType<typeof listOwnersForBot>>();
  for (const owner of owners) {
    const list = byBot.get(owner.botId) ?? [];
    list.push({
      userId: owner.userId,
      username: owner.username,
      name: owner.name,
      expiresAt: owner.expiresAt,
      createdAt: owner.createdAt,
    });
    byBot.set(owner.botId, list);
  }

  const bots = listAllBots().map((bot) => ({
    ...toPublicBot(bot),
    owners: byBot.get(bot.id) ?? [],
  }));

  res.setHeader('Cache-Control', 'no-store');
  res.json({ status: 'success', bots });
});

botsRouter.post('/:id/assign', requireSuperadmin, (req, res, next) => {
  try {
    const bot = getBotById(req.params.id ?? '');
    if (!bot) throw new ApiError(404, '找不到这台机器人');

    const body = assignSchema.parse(req.body ?? {});
    const user = getUserById(body.userId);
    if (!user) throw new ApiError(404, '找不到这个账号');

    assignBot({
      botId: bot.id,
      userId: user.id,
      createdBy: req.user!.id,
      expiresAt: parseExpiryDate(body.expiresAt),
    });

    audit({
      actorId: req.user!.id,
      action: 'bot_assigned',
      target: bot.id,
      detail: `${bot.name} → ${user.username}`,
      ip: req.ip,
    });

    res.json({ status: 'success' });
  } catch (err) {
    next(err);
  }
});

botsRouter.delete('/:id/assign/:userId', requireSuperadmin, (req, res, next) => {
  try {
    const bot = getBotById(req.params.id ?? '');
    if (!bot) throw new ApiError(404, '找不到这台机器人');

    const userId = req.params.userId ?? '';
    const user = getUserById(userId);
    if (!user) throw new ApiError(404, '找不到这个账号');

    if (!unassignBot(bot.id, userId)) {
      throw new ApiError(404, '这个账号没有绑定这台机器人');
    }

    audit({
      actorId: req.user!.id,
      action: 'bot_unassigned',
      target: bot.id,
      detail: `${bot.name} ✕ ${user.username}`,
      ip: req.ip,
    });

    res.status(204).end();
  } catch (err) {
    next(err);
  }
});

botsRouter.delete('/:id', requireSuperadmin, (req, res, next) => {
  try {
    const bot = getBotById(req.params.id ?? '');
    if (!bot) throw new ApiError(404, '找不到这台机器人');

    if (listOwnersForBot(bot.id).length > 0) {
      throw new ApiError(409, '这台机器人还绑在账号上，先解绑再删除');
    }

    deleteBot(bot.id);
    invalidateToken(bot.id);
    audit({
      actorId: req.user!.id,
      action: 'bot_deleted',
      target: bot.id,
      detail: bot.name,
      ip: req.ip,
    });

    res.status(204).end();
  } catch (err) {
    next(err);
  }
});
