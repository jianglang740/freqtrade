import { env } from '../env.js';

/**
 * 机器人 API 地址校验：
 * - 只允许 http/https
 * - 必须是纯 origin（无 path/query/hash，也不能内嵌用户名密码）
 * - BOT_ALLOW_PRIVATE=false 时拒绝回环、私有网段与云元数据地址
 */
export function assertBotApiUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    throw new Error('Invalid API URL');
  }

  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('API URL must use http or https');
  }
  if (url.username || url.password) {
    throw new Error('API URL must not contain credentials');
  }
  if (url.pathname !== '/' && url.pathname !== '') {
    throw new Error('API URL must not contain a path');
  }
  if (url.search || url.hash) {
    throw new Error('API URL must not contain query or hash');
  }
  if (!env.BOT_ALLOW_PRIVATE && isPrivateHost(url.hostname)) {
    throw new Error('Private network addresses are not allowed for bot API URLs');
  }
  return url.origin;
}

export function isPrivateHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (host === 'localhost' || host === 'metadata' || host === 'metadata.google.internal') {
    return true;
  }
  if (host === '::1' || host === '::') return true;
  if (host.startsWith('fe80:')) return true;

  const ipv4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!ipv4) return false;
  const [a, b] = [Number(ipv4[1]), Number(ipv4[2])];
  if (a === 0 || a === 10 || a === 127) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a >= 224) return true;
  return false;
}

/** 代理路径校验：必须落在 freqtrade 的 /api/v1/ 下 */
export function normalizeProxyPath(raw: string): string {
  const path = raw.replace(/^\/+/, '');
  if (path.length === 0 || path.length > 512) {
    throw new Error('Invalid proxy path');
  }
  if (path.includes('..') || path.includes('\\') || path.includes('\0')) {
    throw new Error('Invalid proxy path');
  }
  if (!path.startsWith('api/v1/')) {
    throw new Error('Invalid proxy path');
  }
  return path;
}
