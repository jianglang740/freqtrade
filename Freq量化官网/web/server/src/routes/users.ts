/*
 * 客户账号管理：仅 superadmin。
 *
 * 为什么不直接改数据库：密码是 bcrypt 哈希，手填明文不生效；
 * 停用、重置密码还要同时清掉失败次数与锁定状态。这里把这些动作收成接口，
 * 网页上点一下即可，并且全部写审计日志（绝不记录密码）。
 */
import { randomBytes } from 'node:crypto';
import { Router } from 'express';
import type { NextFunction, Request, Response } from 'express';
import { z } from 'zod';
import { requireAuth, requireSuperadmin } from '../middleware/auth.js';
import { keyedLimiter } from '../middleware/rateLimit.js';
import { ApiError } from '../middleware/error.js';
import { hashPassword } from '../services/auth.js';
import { apiUserLimiter } from '../services/rateLimit.js';
import { audit } from '../services/audit.js';
import {
  countActiveSuperadmins,
  countUserBots,
  deleteUser,
  getUserById,
  getUserByUsername,
  insertUser,
  listUsersWithBotCount,
  setUserPassword,
  updateUserProfile,
  type AdminUserRow,
} from '../services/users.js';

const USERNAME_RE = /^[A-Za-z0-9_.@-]{1,64}$/;

const usernameSchema = z
  .string()
  .trim()
  .regex(USERNAME_RE, '用户名只能包含字母、数字、_ . @ -，长度 1-64');
const passwordSchema = z.string().min(8, '密码至少 8 位').max(128, '密码最多 128 位');
const optionalPassword = z.union([z.literal(''), passwordSchema]).optional();
const optionalEmail = z
  .union([z.literal(''), z.string().trim().email('邮箱格式不正确').max(120)])
  .optional();
const optionalName = z.string().trim().max(64).optional();

const createSchema = z
  .object({
    username: usernameSchema,
    password: optionalPassword,
    name: optionalName,
    email: optionalEmail,
    role: z.enum(['user', 'superadmin']).default('user'),
  })
  .strict();

const patchSchema = z
  .object({
    name: optionalName,
    email: optionalEmail,
    isActive: z.boolean().optional(),
  })
  .strict();

const passwordBodySchema = z.object({ password: optionalPassword }).strict();

/** 生成 16 位随机密码，只在响应里出现一次 */
function generatePassword(): string {
  return randomBytes(8).toString('hex');
}

function toAdminUser(row: AdminUserRow) {
  return {
    id: row.id,
    username: row.username,
    name: row.name,
    email: row.email,
    role: row.role,
    isActive: row.is_active === 1,
    locked: row.account_locked_until !== null && row.account_locked_until > Date.now(),
    botCount: row.bot_count,
    lastLogin: row.last_login,
    createdAt: row.created_at,
  };
}

function mapUserById(id: string) {
  const row = getUserById(id);
  if (!row) throw new ApiError(404, '账号不存在');
  return toAdminUser({ ...row, bot_count: countUserBots(row.id) });
}

/** CSV 单元格转义：引号翻倍，含逗号/换行/引号时整体加引号 */
function csvCell(value: string | number | null | undefined): string {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function formatTime(ms: number | null): string {
  if (!ms) return '';
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 16);
}

const usersRouter = Router();

usersRouter.use(requireAuth);
usersRouter.use(requireSuperadmin);
usersRouter.use(keyedLimiter(apiUserLimiter, (req) => req.user?.id ?? null));

/* 导出 CSV：GET 不需要 CSRF；字段不含密码，可直接用 Excel 打开当台账 */
usersRouter.get('/export', (_req: Request, res: Response) => {
  const rows = listUsersWithBotCount().map(toAdminUser);
  const header = ['用户名', '姓名', '邮箱', '角色', '状态', '机器人数', '最近登录', '创建时间'];
  const lines = [header.join(',')];
  for (const row of rows) {
    lines.push(
      [
        csvCell(row.username),
        csvCell(row.name),
        csvCell(row.email),
        csvCell(row.role === 'superadmin' ? '管理员' : '客户'),
        csvCell(row.isActive ? '启用' : '停用'),
        csvCell(row.botCount),
        csvCell(formatTime(row.lastLogin)),
        csvCell(formatTime(row.createdAt)),
      ].join(','),
    );
  }
  // BOM 让 Excel 正确识别 UTF-8 中文
  res.setHeader('Content-Type', 'text/csv; charset=utf-8');
  res.setHeader('Content-Disposition', 'attachment; filename="freq-users.csv"');
  res.setHeader('Cache-Control', 'no-store');
  res.send(`﻿${lines.join('\r\n')}\r\n`);
});

usersRouter.get('/', (_req: Request, res: Response) => {
  res.setHeader('Cache-Control', 'no-store');
  res.json({ users: listUsersWithBotCount().map(toAdminUser) });
});

usersRouter.post('/', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const body = createSchema.parse(req.body ?? {});
    if (getUserByUsername(body.username)) {
      throw new ApiError(409, '用户名已存在');
    }
    const generatedPassword = body.password ? undefined : generatePassword();
    const password = body.password || generatedPassword!;
    const passwordHash = await hashPassword(password);
    const created = insertUser({
      username: body.username,
      name: body.name ? body.name : null,
      email: body.email ? body.email : null,
      password_hash: passwordHash,
      role: body.role,
    });
    audit({
      actorId: req.user!.id,
      action: 'user_created',
      target: created.id,
      detail: `${created.username} (${created.role})`,
      ip: req.ip,
    });
    res.status(201).json({
      user: mapUserById(created.id),
      generatedPassword,
    });
  } catch (err) {
    next(err);
  }
});

usersRouter.patch('/:id', (req: Request, res: Response, next: NextFunction) => {
  try {
    const id = req.params.id ?? '';
    const target = getUserById(id);
    if (!target) throw new ApiError(404, '账号不存在');

    const body = patchSchema.parse(req.body ?? {});
    if (body.isActive === false) {
      if (target.id === req.user!.id) {
        throw new ApiError(400, '不能停用当前登录的账号');
      }
      if (target.role === 'superadmin' && countActiveSuperadmins() <= 1) {
        throw new ApiError(400, '至少要保留一个启用的管理员账号');
      }
    }

    updateUserProfile(id, {
      name: body.name === undefined ? undefined : body.name || null,
      email: body.email === undefined ? undefined : body.email || null,
      isActive: body.isActive === undefined ? undefined : body.isActive ? 1 : 0,
    });

    if (body.isActive !== undefined) {
      audit({
        actorId: req.user!.id,
        action: 'user_status_changed',
        target: id,
        detail: `${target.username} ${body.isActive ? 'enabled' : 'disabled'}`,
        ip: req.ip,
      });
    } else {
      audit({
        actorId: req.user!.id,
        action: 'user_profile_updated',
        target: id,
        detail: target.username,
        ip: req.ip,
      });
    }

    res.json({ user: mapUserById(id) });
  } catch (err) {
    next(err);
  }
});

usersRouter.post('/:id/password', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const id = req.params.id ?? '';
    const target = getUserById(id);
    if (!target) throw new ApiError(404, '账号不存在');

    const body = passwordBodySchema.parse(req.body ?? {});
    const generatedPassword = body.password ? undefined : generatePassword();
    const password = body.password || generatedPassword!;
    setUserPassword(id, await hashPassword(password));

    audit({
      actorId: req.user!.id,
      action: 'user_password_reset',
      target: id,
      detail: target.username,
      ip: req.ip,
    });
    res.json({ generatedPassword });
  } catch (err) {
    next(err);
  }
});

usersRouter.delete('/:id', (req: Request, res: Response, next: NextFunction) => {
  try {
    const id = req.params.id ?? '';
    const target = getUserById(id);
    if (!target) throw new ApiError(404, '账号不存在');
    if (target.id === req.user!.id) {
      throw new ApiError(400, '不能删除当前登录的账号');
    }
    if (countUserBots(id) > 0) {
      throw new ApiError(409, '这个账号名下还有机器人，先解除归属再删除');
    }
    if (target.role === 'superadmin' && target.is_active === 1 && countActiveSuperadmins() <= 1) {
      throw new ApiError(400, '至少要保留一个启用的管理员账号');
    }

    deleteUser(id);
    audit({
      actorId: req.user!.id,
      action: 'user_deleted',
      target: id,
      detail: target.username,
      ip: req.ip,
    });
    res.status(204).end();
  } catch (err) {
    next(err);
  }
});

export { usersRouter };
