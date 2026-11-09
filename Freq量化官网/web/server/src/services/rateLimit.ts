import { env } from '../env.js';

/**
 * 进程内固定窗口限流。
 * 单实例部署足够用；将来多实例再换 Redis。
 *
 * 为什么代理和 API 限流按「用户」而不是只按 IP：
 * 客户可能共用公司网络或运营商 NAT，一个出口 IP 背后有多个客户。
 * 按 IP 卡死会把他们一起误伤；按用户卡，再留一个很高的 IP 上限防滥用。
 */
export type RateLimitResult = { allowed: true } | { allowed: false; retryAfterMs: number };

type Bucket = { count: number; resetAt: number };

const MAX_BUCKETS = 10_000;

export class FixedWindowLimiter {
  private buckets = new Map<string, Bucket>();

  constructor(
    private readonly limit: number,
    private readonly windowMs: number,
  ) {}

  check(key: string): RateLimitResult {
    const now = Date.now();
    let bucket = this.buckets.get(key);

    if (!bucket || bucket.resetAt <= now) {
      bucket = { count: 0, resetAt: now + this.windowMs };
      this.buckets.set(key, bucket);
      this.prune(now);
    }

    if (bucket.count >= this.limit) {
      return { allowed: false, retryAfterMs: bucket.resetAt - now };
    }

    bucket.count += 1;
    return { allowed: true };
  }

  /** 定期清掉过期桶，避免内存无限增长 */
  private prune(now: number): void {
    if (this.buckets.size <= MAX_BUCKETS) return;
    for (const [key, bucket] of this.buckets) {
      if (bucket.resetAt <= now) this.buckets.delete(key);
    }
  }
}

const MINUTE = 60 * 1000;

/** 未登录/异常流量兜底：每 IP 很高上限，只防事故不误伤 NAT */
export const apiIpLimiter = new FixedWindowLimiter(env.RATE_LIMIT_API_IP_PER_MIN, MINUTE);
/** 已登录用户的总 API 调用（含机器人列表等） */
export const apiUserLimiter = new FixedWindowLimiter(env.RATE_LIMIT_USER_PER_MIN, MINUTE);
/** 代理：每用户每分钟 */
export const proxyUserLimiter = new FixedWindowLimiter(env.RATE_LIMIT_PROXY_USER_PER_MIN, MINUTE);
/** 代理：每机器人每分钟 */
export const proxyBotLimiter = new FixedWindowLimiter(env.RATE_LIMIT_PROXY_BOT_PER_MIN, MINUTE);
/** 登录：每 IP / 15 分钟 */
export const loginLimiter = new FixedWindowLimiter(env.RATE_LIMIT_LOGIN_PER_15MIN, 15 * MINUTE);
/** 测试连接 / 添加机器人：每用户 10 次 / 分钟 */
export const testConnectionLimiter = new FixedWindowLimiter(10, MINUTE);
/** 上传图片：每用户 30 次 / 分钟，防止管理页被刷成图床 */
export const assetUploadLimiter = new FixedWindowLimiter(30, MINUTE);
