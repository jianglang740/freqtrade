import { readFileSync } from 'node:fs';

export type Flags = Record<string, string | true>;

/** 极简参数解析：--key value 或 --flag */
export function parseArgs(argv: string[]): Flags {
  const flags: Flags = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]!;
    if (!arg.startsWith('--')) continue;
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (next !== undefined && !next.startsWith('--')) {
      flags[key] = next;
      i += 1;
    } else {
      flags[key] = true;
    }
  }
  return flags;
}

export function str(flags: Flags, key: string, fallback?: string): string {
  const value = flags[key];
  if (typeof value === 'string' && value.trim()) return value.trim();
  if (fallback !== undefined) return fallback;
  fail(`缺少参数 --${key}`);
}

export function optStr(flags: Flags, key: string): string | undefined {
  const value = flags[key];
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

/**
 * 密码优先从 stdin 读，避免进 shell history：
 *   printf '%s' '密码' | npm run user:create -- --username demo --password-stdin
 */
export function readPassword(flags: Flags): string {
  if (flags['password-stdin']) {
    const value = readFileSync(0, 'utf8').trim();
    if (!value) fail('stdin 里没有读到密码');
    return value;
  }
  const value = optStr(flags, 'password');
  if (value) {
    console.warn('[warn] 用 --password 传密码会留在 shell history 里，生产请改用 --password-stdin');
    return value;
  }
  fail('需要 --password-stdin（推荐）或 --password');
}

/** YYYY-MM-DD → 当天 23:59:59.999（UTC）的毫秒时间戳 */
export function parseExpiry(value: string): number | null {
  if (value === 'never' || value === 'none') return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) fail('--expires 需要 YYYY-MM-DD 或 never');
  const ts = Date.parse(`${value}T23:59:59.999Z`);
  if (Number.isNaN(ts)) fail(`无法解析日期：${value}`);
  return ts;
}

export function fail(message: string): never {
  console.error(`[error] ${message}`);
  process.exit(1);
}

/** 打印对象时排除敏感字段（本应没有，留一道保险） */
export function safeJson(value: unknown): string {
  return JSON.stringify(value, (key, v) => {
    if (/password|secret|token|cipher|hash/i.test(key)) return '[redacted]';
    return v;
  });
}
