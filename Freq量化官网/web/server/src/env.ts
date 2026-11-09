/*
 * 官网后端的环境变量：全部在这里校验，缺失或过弱直接启动失败。
 * 不读 .env 里的任何值进日志。
 */
import dotenv from 'dotenv';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { z } from 'zod';

const here = path.dirname(fileURLToPath(import.meta.url));
// server/src/env.ts 与编译后的 server/dist/env.js 都定位到 server/
const SERVER_ROOT = path.resolve(here, '..');
// 固定从 server/.env 读取，避免依赖启动时的 cwd
dotenv.config({ path: path.resolve(SERVER_ROOT, '.env') });
// server/ 与编译后的 dist 都会指向 web/dist
const DEFAULT_STATIC_DIR = path.resolve(SERVER_ROOT, '../dist');

/** 只有字符串 "false" 才算关闭，避免误配 */
function booleanish(def: boolean) {
  return z
    .string()
    .optional()
    .transform((v) => (v === undefined ? def : v.trim() !== 'false'));
}

const schema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  HOST: z.string().default('127.0.0.1'),
  PORT: z.coerce.number().int().positive().default(3002),
  DATABASE_PATH: z.string().default('./data/freq.db'),
  JWT_SECRET: z.string().min(32, 'JWT_SECRET 至少需要 32 个字符'),
  ENCRYPTION_KEY: z
    .string()
    .refine((v) => {
      try {
        return Buffer.from(v, 'base64').length === 32;
      } catch {
        return false;
      }
    }, 'ENCRYPTION_KEY 必须是 32 字节的 base64（生成方式见 .env.example）'),
  CUSTOMER_READ_ONLY: booleanish(true),
  COOKIE_SECURE: z
    .string()
    .optional()
    .transform((v) =>
      v === undefined || v.trim() === ''
        ? process.env.NODE_ENV === 'production'
        : v.trim() === 'true',
    ),
  SESSION_HOURS: z.coerce.number().int().min(1).max(72).default(12),
  PUBLIC_ORIGINS: z.string().default('http://127.0.0.1:4000,http://localhost:4000'),
  BOT_ALLOW_PRIVATE: booleanish(true),
  TRUST_PROXY: booleanish(false),
  STATIC_DIR: z.string().optional(),
  /** 策略/因子 Markdown 与图片目录；相对路径以 server/ 为基准 */
  CONTENT_DIR: z.string().optional(),
  /** 是否把每个请求都打进日志；默认只记错误和慢请求，避免高并发时 stdout 拖慢事件循环 */
  LOG_REQUESTS: booleanish(false),
  // 限流阈值（次/分钟，登录除外）；按容量压测结果调过，允许生产用环境变量覆盖
  RATE_LIMIT_API_IP_PER_MIN: z.coerce.number().int().positive().default(3000),
  RATE_LIMIT_USER_PER_MIN: z.coerce.number().int().positive().default(600),
  RATE_LIMIT_PROXY_USER_PER_MIN: z.coerce.number().int().positive().default(300),
  // 多户可能共用一台机器人；上游有缓存挡着，这里放宽到够十几个人同时看
  RATE_LIMIT_PROXY_BOT_PER_MIN: z.coerce.number().int().positive().default(240),
  RATE_LIMIT_LOGIN_PER_15MIN: z.coerce.number().int().positive().default(10),
});

const parsed = schema.safeParse(process.env);

if (!parsed.success) {
  for (const issue of parsed.error.issues) {
    console.error(`[env] ${issue.path.join('.')}: ${issue.message}`);
  }
  process.exit(1);
}

const databasePath = path.isAbsolute(parsed.data.DATABASE_PATH)
  ? parsed.data.DATABASE_PATH
  : path.resolve(SERVER_ROOT, parsed.data.DATABASE_PATH);

const contentDir = path.resolve(SERVER_ROOT, parsed.data.CONTENT_DIR ?? 'content');

export const env = {
  ...parsed.data,
  DATABASE_PATH: databasePath,
  CONTENT_DIR: contentDir,
  SERVER_ROOT,
  STATIC_DIR: parsed.data.STATIC_DIR
    ? path.resolve(parsed.data.STATIC_DIR)
    : DEFAULT_STATIC_DIR,
  ORIGINS: parsed.data.PUBLIC_ORIGINS.split(',')
    .map((s) => s.trim())
    .filter(Boolean),
};
