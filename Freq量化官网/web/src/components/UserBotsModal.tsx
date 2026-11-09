/*
 * 管理员的「客户 ↔ 机器人」绑定面板。
 *
 * 可以：绑定已有机器人、新建机器人并直接绑定、解绑、设置到期日。
 * 机器人密码只在新建时提交给后端，任何接口都不会把它返回前端。
 */

import { useEffect, useMemo, useState } from 'react';
import { createBot } from '../lib/api';
import type { AdminUser } from '../lib/users';
import {
  assignBotToUser,
  listAdminBots,
  unassignBotFromUser,
  type AdminBot,
} from '../lib/users';

type Props = {
  user: AdminUser;
  onClose: () => void;
  /** 绑定或解绑后通知父组件刷新（机器人数会变） */
  onChanged: () => void;
};

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/** 后端把「当天」存成 UTC 23:59:59.999，这里也用 UTC 取日期，避免东八区显示成第二天 */
function formatDay(ms: number | null): string {
  if (!ms) return '永久';
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

function isExpired(ms: number | null): boolean {
  return ms !== null && ms <= Date.now();
}

/** 输入框里的 YYYY-MM-DD；空字符串表示永久 */
function expiryToApi(value: string): string | null {
  return value.trim() ? value.trim() : null;
}

export function UserBotsModal({ user, onClose, onChanged }: Props) {
  const [bots, setBots] = useState<AdminBot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [assignId, setAssignId] = useState('');
  const [assignExpiry, setAssignExpiry] = useState('');

  const [name, setName] = useState('');
  const [apiUrl, setApiUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [newExpiry, setNewExpiry] = useState('');
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const list = await listAdminBots();
      setBots(list);
      setError(null);
      return list;
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取机器人列表失败');
      return [];
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  // Esc 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const isMine = (bot: AdminBot) => bot.owners.some((o) => o.userId === user.id);

  const assigned = useMemo(() => bots.filter(isMine), [bots, user.id]);
  const available = useMemo(() => bots.filter((bot) => !isMine(bot)), [bots, user.id]);

  // 可选列表变了就重置选择，避免指向已经不存在的机器人
  useEffect(() => {
    if (!available.some((bot) => bot.id === assignId)) {
      setAssignId(available[0]?.id ?? '');
    }
  }, [available, assignId]);

  const assign = async () => {
    if (!assignId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const target = bots.find((bot) => bot.id === assignId);
      await assignBotToUser(assignId, user.id, expiryToApi(assignExpiry));
      setNotice(`已绑定 ${target?.name ?? '机器人'}`);
      setAssignExpiry('');
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : '绑定失败');
    } finally {
      setBusy(false);
    }
  };

  const unassign = async (bot: AdminBot) => {
    if (!window.confirm(`确定解绑「${bot.name}」吗？解绑后 ${user.username} 立刻看不到这台机器人。`)) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await unassignBotFromUser(bot.id, user.id);
      setNotice(`已解绑 ${bot.name}`);
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : '解绑失败');
    } finally {
      setBusy(false);
    }
  };

  const createAndAssign = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      // 后端会先试连 freqtrade，连不上不会落库
      const bot = await createBot({
        name: name.trim(),
        apiUrl: apiUrl.trim().replace(/\/+$/, ''),
        username: username.trim(),
        password,
      });
      await assignBotToUser(bot.id, user.id, expiryToApi(newExpiry));
      setName('');
      setApiUrl('');
      setUsername('');
      setPassword('');
      setNewExpiry('');
      setNotice(`已创建并绑定 ${bot.name}`);
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="user-bots-title"
      onClick={onClose}
    >
      <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="关闭">
          ×
        </button>
        <h3 id="user-bots-title">机器人 · {user.username}</h3>
        <p>
          绑定后，客户登录「我的机器人」只会看到分给自己的机器；解绑立即断权。
          到期日留空表示永久，格式 YYYY-MM-DD。
        </p>

        {error && <p className="form-error">{error}</p>}
        {notice && !error && <p className="form-ok">{notice}</p>}

        <h4 className="bot-block__title">已绑定（{assigned.length}）</h4>
        {loading ? (
          <div className="empty">读取中…</div>
        ) : assigned.length === 0 ? (
          <div className="empty">还没有给这个客户绑定机器人。</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>API 地址</th>
                  <th>登录名</th>
                  <th>到期</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {assigned.map((bot) => {
                  const owner = bot.owners.find((o) => o.userId === user.id);
                  const expired = isExpired(owner?.expiresAt ?? null);
                  return (
                    <tr key={bot.id}>
                      <td>{bot.name}</td>
                      <td className="small">{bot.apiUrl}</td>
                      <td className="small">{bot.username}</td>
                      <td style={{ color: expired ? 'var(--rh-coral)' : undefined }}>
                        {formatDay(owner?.expiresAt ?? null)}
                        {expired ? '（已过期）' : ''}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn--sm"
                          onClick={() => void unassign(bot)}
                          disabled={busy}
                        >
                          解绑
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="bot-block">
          <h4 className="bot-block__title">绑定已有机器人</h4>
          {loading ? null : available.length === 0 ? (
            <p className="form-hint">没有可绑定的机器人；用下面的表单新建一台。</p>
          ) : (
            <div className="bot-assign-row">
              <select
                className="input"
                value={assignId}
                onChange={(e) => setAssignId(e.target.value)}
              >
                {available.map((bot) => (
                  <option key={bot.id} value={bot.id}>
                    {bot.name}（{bot.username}@{bot.apiUrl}）
                    {bot.owners.length > 0
                      ? ` · 已绑 ${bot.owners.map((o) => o.username).join('、')}`
                      : ' · 未绑定'}
                  </option>
                ))}
              </select>
              <input
                className="input"
                type="date"
                value={assignExpiry}
                onChange={(e) => setAssignExpiry(e.target.value)}
                aria-label="到期日"
              />
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void assign()}
                disabled={busy || !assignId}
              >
                绑定
              </button>
            </div>
          )}
          {available.length > 0 && (
            <p className="form-hint">一台机器人可以同时绑给多个账号；到期日留空即永久。</p>
          )}
        </div>

        <div className="bot-block">
          <h4 className="bot-block__title">新建机器人并绑定</h4>
          <form className="add-bot-form" onSubmit={createAndAssign}>
            <label>
              <span className="metric-label">名称</span>
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如 网格 1h"
                required
              />
            </label>
            <label>
              <span className="metric-label">API 地址</span>
              <input
                className="input"
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                placeholder="http://127.0.0.1:8080"
                required
              />
            </label>
            <div className="add-bot-row">
              <label>
                <span className="metric-label">freqtrade 用户名</span>
                <input
                  className="input"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="off"
                  required
                />
              </label>
              <label>
                <span className="metric-label">freqtrade 密码</span>
                <input
                  className="input"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                  required
                />
              </label>
            </div>
            <label>
              <span className="metric-label">到期日（可留空）</span>
              <input
                className="input"
                type="date"
                value={newExpiry}
                onChange={(e) => setNewExpiry(e.target.value)}
              />
            </label>
            <p className="form-hint">
              提交时会先连一次这台 freqtrade，连不上不会保存；密码加密后落库，之后不再显示。
            </p>
            <div className="form-actions">
              <button type="submit" className="btn btn--primary btn--sm" disabled={creating}>
                {creating ? '连接中…' : '创建并绑定'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
