import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { env } from '../env.js';
import { recordLoginFailure, recordLoginSuccess, type DbUser, type PublicUser } from './users.js';

export const COOKIE_NAME = 'freq_session';
export const BCRYPT_ROUNDS = 12;
export const MAX_FAILED_ATTEMPTS = 10;
export const LOCKOUT_MS = 15 * 60 * 1000;

/** 用户不存在时也跑一次 bcrypt，避免通过响应时间枚举用户名 */
const DUMMY_HASH = bcrypt.hashSync('invalid-placeholder-password', BCRYPT_ROUNDS);

export class AuthError extends Error {}

/**
 * bcrypt 是纯 CPU 计算，50 个人同时登录就能把一个核心跑满十几秒，
 * 期间正常的面板请求会被拖慢到接近 10 秒。这里给密码校验加一个并发闸门：
 * 排队本身不减少总耗时，但能把事件循环留给普通请求。
 */
const MAX_CONCURRENT_VERIFY = 2;
let activeVerifies = 0;
const verifyWaiters: (() => void)[] = [];

function acquireVerifySlot(): Promise<void> {
  if (activeVerifies < MAX_CONCURRENT_VERIFY) {
    activeVerifies += 1;
    return Promise.resolve();
  }
  // 名额在 release() 里直接转交给下一个等待者，activeVerifies 不变
  return new Promise<void>((resolve) => {
    verifyWaiters.push(resolve);
  });
}

function releaseVerifySlot(): void {
  const next = verifyWaiters.shift();
  if (next) {
    next();
  } else {
    activeVerifies -= 1;
  }
}

export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, BCRYPT_ROUNDS);
}

export async function verifyPassword(password: string, hash: string): Promise<boolean> {
  await acquireVerifySlot();
  try {
    return await bcrypt.compare(password, hash);
  } finally {
    releaseVerifySlot();
  }
}

export function signSession(userId: string): string {
  return jwt.sign({ sub: userId }, env.JWT_SECRET, {
    algorithm: 'HS256',
    expiresIn: `${env.SESSION_HOURS}h`,
  });
}

export function verifySession(token: string): string {
  const payload = jwt.verify(token, env.JWT_SECRET, { algorithms: ['HS256'] });
  if (typeof payload !== 'object' || typeof payload.sub !== 'string') {
    throw new AuthError('Invalid token');
  }
  return payload.sub;
}

/**
 * 登录：锁定检查 → 密码校验 → 成功清零 / 失败累计。
 * 登录失败统一返回同一句英文，前端会翻译成中文。
 */
export async function login(
  user: DbUser | undefined,
  password: string,
  ip: string | null,
): Promise<PublicUser> {
  if (!user) {
    await verifyPassword(password, DUMMY_HASH);
    throw new AuthError('Invalid username or password');
  }

  if (user.account_locked_until && user.account_locked_until > Date.now()) {
    throw new AuthError('Account temporarily locked. Try again later.');
  }

  const ok = await verifyPassword(password, user.password_hash);
  if (!ok) {
    recordLoginFailure(user, MAX_FAILED_ATTEMPTS, LOCKOUT_MS);
    throw new AuthError('Invalid username or password');
  }

  if (user.is_active !== 1) {
    throw new AuthError('User account is inactive');
  }

  recordLoginSuccess(user.id, ip);
  return {
    id: user.id,
    username: user.username,
    name: user.name,
    email: user.email,
    role: user.role,
  };
}
