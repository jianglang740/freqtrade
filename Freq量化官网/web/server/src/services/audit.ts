import { run } from '../db/index.js';

/**
 * 审计日志：只记动作与目标，绝不写入密码、token、密文。
 */
export function audit(input: {
  actorId?: string | null;
  action: string;
  target?: string | null;
  detail?: string | null;
  ip?: string | null;
}): void {
  try {
    run(
      `INSERT INTO audit_logs (actor_id, action, target, detail, ip, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
      input.actorId ?? null,
      input.action,
      input.target ?? null,
      input.detail ?? null,
      input.ip ?? null,
      Date.now(),
    );
  } catch (err) {
    // 审计失败不影响主流程，但要在服务端日志里留痕（不含敏感数据）
    console.error('[audit] write failed:', err instanceof Error ? err.message : 'unknown');
  }
}
