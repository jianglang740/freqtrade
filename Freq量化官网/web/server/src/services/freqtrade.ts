import type { DbBot } from './bots.js';
import { botCredentials } from './bots.js';
import { assertBotApiUrl, normalizeProxyPath } from './urlSecurity.js';

const CONNECT_TIMEOUT_MS = 5_000;
const PROXY_TIMEOUT_MS = 10_000;
const TOKEN_TTL_MS = 50 * 60 * 1000;
const MAX_RESPONSE_BYTES = 10 * 1024 * 1024;
const MAX_QUERY_LENGTH = 512;

type CachedToken = { token: string; expiresAt: number };
const tokenCache = new Map<string, CachedToken>();

/**
 * 代理响应缓存。
 * 50 个客户 × 15 秒轮询，同一台机器人会被反复拉取同一份数据；
 * 上游 freqtrade 的 K 线/成交表动辄几十 KB，缓存几秒既省上游也省带宽。
 * 只缓存成功结果，失败照常回源，保证故障可见。
 */
const CACHE_TTL_MS: Record<string, number> = {
  'api/v1/status': 3_000,
  'api/v1/profit': 5_000,
  'api/v1/trades': 5_000,
  'api/v1/balance': 10_000,
  'api/v1/ping': 10_000,
  'api/v1/show_config': 30_000,
  'api/v1/performance': 30_000,
  'api/v1/entries': 30_000,
  'api/v1/exits': 30_000,
  'api/v1/mix_tags': 30_000,
  'api/v1/historic_balance': 60_000,
};
const DEFAULT_TTL_MS = 5_000;
const MAX_CACHE_ENTRIES = 2_000;

type CacheEntry = { expiresAt: number; result: ProxyResult };
const responseCache = new Map<string, CacheEntry>();
/** 同一个 key 同时只回源一次，其余请求共享结果（single-flight） */
const inFlight = new Map<string, Promise<ProxyResult>>();

function ttlFor(path: string): number {
  const bare = path.split('?')[0] ?? path;
  return CACHE_TTL_MS[bare] ?? DEFAULT_TTL_MS;
}

function pruneCache(): void {
  if (responseCache.size <= MAX_CACHE_ENTRIES) return;
  const now = Date.now();
  for (const [key, entry] of responseCache) {
    if (entry.expiresAt <= now) responseCache.delete(key);
  }
  if (responseCache.size <= MAX_CACHE_ENTRIES) return;
  // 仍然超限：Map 按插入顺序迭代，从最旧的开始淘汰
  let overflow = responseCache.size - MAX_CACHE_ENTRIES;
  for (const key of responseCache.keys()) {
    responseCache.delete(key);
    overflow -= 1;
    if (overflow <= 0) break;
  }
}

export function invalidateProxyCache(botId: string): void {
  const prefix = `${botId}\0`;
  for (const key of responseCache.keys()) {
    if (key.startsWith(prefix)) responseCache.delete(key);
  }
}

function basicAuth(username: string, password: string): string {
  return `Basic ${Buffer.from(`${username}:${password}`, 'utf8').toString('base64')}`;
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function loginToken(bot: DbBot): Promise<string> {
  const { username, password } = botCredentials(bot);
  const res = await fetchWithTimeout(
    `${assertBotApiUrl(bot.api_url)}/api/v1/token/login`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: basicAuth(username, password),
      },
      body: '{}',
    },
    CONNECT_TIMEOUT_MS,
  );
  if (!res.ok) throw new Error('Authentication failed');
  const data = (await res.json()) as { access_token?: unknown };
  if (typeof data.access_token !== 'string' || !data.access_token) {
    throw new Error('Authentication failed');
  }
  return data.access_token;
}

async function getToken(bot: DbBot, forceRefresh = false): Promise<string> {
  const cached = tokenCache.get(bot.id);
  if (!forceRefresh && cached && cached.expiresAt > Date.now()) {
    return cached.token;
  }
  const token = await loginToken(bot);
  tokenCache.set(bot.id, { token, expiresAt: Date.now() + TOKEN_TTL_MS });
  return token;
}

export function invalidateToken(botId: string): void {
  tokenCache.delete(botId);
}

/** 试连：先 ping，再用 Basic 换 token。任何一步失败都返回 false。 */
export async function testBotConnection(
  apiUrl: string,
  username: string,
  password: string,
): Promise<boolean> {
  try {
    const origin = assertBotApiUrl(apiUrl);
    const ping = await fetchWithTimeout(`${origin}/api/v1/ping`, {}, CONNECT_TIMEOUT_MS);
    if (!ping.ok) return false;
    const pong = (await ping.json()) as { status?: unknown };
    if (pong.status !== 'pong') return false;

    const login = await fetchWithTimeout(
      `${origin}/api/v1/token/login`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: basicAuth(username, password),
        },
        body: '{}',
      },
      CONNECT_TIMEOUT_MS,
    );
    if (!login.ok) return false;
    const data = (await login.json()) as { access_token?: unknown };
    return typeof data.access_token === 'string' && data.access_token.length > 0;
  } catch {
    return false;
  }
}

export type ProxyResult =
  | { ok: true; contentType: string; body: Buffer }
  | { ok: false; status: number; message: string };

function extractUpstreamMessage(text: string, status: number): string {
  try {
    const parsed = JSON.parse(text) as {
      error?: unknown;
      detail?: unknown;
      message?: unknown;
    };
    for (const value of [parsed.error, parsed.detail, parsed.message]) {
      if (typeof value === 'string' && value.trim()) {
        return value.trim().slice(0, 300);
      }
    }
  } catch {
    // 不是 JSON，退回状态码
  }
  return `Freqtrade API error: ${status}`;
}

async function requestOnce(
  bot: DbBot,
  path: string,
  query: string,
  token: string,
): Promise<Response> {
  const origin = assertBotApiUrl(bot.api_url);
  return fetchWithTimeout(
    `${origin}/${path}${query}`,
    { method: 'GET', headers: { Authorization: `Bearer ${token}` } },
    PROXY_TIMEOUT_MS,
  );
}

/**
 * GET 代理：只读、限时、限大小。
 * 上游 401 时刷新一次 token 再重试；其余错误转成安全的中文/英文提示。
 * 带短 TTL 缓存与 single-flight，并发刷新不会把上游打穿。
 */
export async function proxyGet(bot: DbBot, rawPath: string, rawQuery: string): Promise<ProxyResult> {
  let path: string;
  try {
    path = normalizeProxyPath(rawPath);
  } catch {
    return { ok: false, status: 400, message: 'Invalid proxy path' };
  }

  const query = rawQuery.length > MAX_QUERY_LENGTH ? rawQuery.slice(0, MAX_QUERY_LENGTH) : rawQuery;
  const cacheKey = `${bot.id}\0${path}\0${query}`;

  const cached = responseCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) return cached.result;

  const pending = inFlight.get(cacheKey);
  if (pending) return pending;

  const task: Promise<ProxyResult> = fetchProxy(bot, path, query)
    .then((result) => {
      if (result.ok) {
        responseCache.set(cacheKey, {
          expiresAt: Date.now() + ttlFor(path),
          result,
        });
        pruneCache();
      }
      return result;
    })
    .finally(() => {
      inFlight.delete(cacheKey);
    });

  inFlight.set(cacheKey, task);
  return task;
}

async function fetchProxy(bot: DbBot, path: string, query: string): Promise<ProxyResult> {
  try {
    let token = await getToken(bot);
    let res = await requestOnce(bot, path, query, token);

    if (res.status === 401) {
      invalidateToken(bot.id);
      token = await getToken(bot, true);
      res = await requestOnce(bot, path, query, token);
    }

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      if (res.status === 401) {
        return { ok: false, status: 502, message: 'Authentication failed after token refresh' };
      }
      return { ok: false, status: 502, message: extractUpstreamMessage(text, res.status) };
    }

    const buffer = Buffer.from(await res.arrayBuffer());
    if (buffer.byteLength > MAX_RESPONSE_BYTES) {
      return { ok: false, status: 502, message: 'Freqtrade response is too large' };
    }
    return {
      ok: true,
      contentType: res.headers.get('content-type') || 'application/json; charset=utf-8',
      body: buffer,
    };
  } catch (err) {
    if (err instanceof Error && (err.name === 'AbortError' || err.name === 'TimeoutError')) {
      return { ok: false, status: 504, message: 'Freqtrade request timed out' };
    }
    const message = err instanceof Error ? err.message : 'unknown error';
    if (/authentication failed/i.test(message)) {
      return { ok: false, status: 502, message: 'Authentication failed' };
    }
    return {
      ok: false,
      status: 502,
      message: `Network error connecting to Freqtrade bot: ${message.slice(0, 200)}`,
    };
  }
}
