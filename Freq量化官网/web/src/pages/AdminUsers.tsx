/*
 * 用户管理：/admin/users，仅 superadmin。
 *
 * 常用动作都在这一页：新建客户、重置密码、停用/启用、删除、导出 CSV。
 * 密码只以哈希形式落库；自动生成的密码只在这里显示一次，关掉就没有了。
 */

import { useEffect, useState } from 'react';
import { AdminGate } from '../components/AdminGate';
import { usePageMeta } from '../lib/pageMeta';
import { UserBotsModal } from '../components/UserBotsModal';
import type { SiteUser } from '../lib/api';
import {
  USERS_EXPORT_URL,
  createUser,
  deleteAdminBot,
  deleteUser,
  listAdminBots,
  listUsers,
  resetUserPassword,
  updateUser,
  type AdminBot,
  type AdminUser,
} from '../lib/users';

function formatTime(ms: number | null): string {
  if (!ms) return '—';
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 后端把到期日存成 UTC 当天最后一毫秒，展示时也用 UTC，避免时区偏移一天 */
function formatExpiryDay(ms: number | null): string {
  if (!ms) return '永久';
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

function statusOf(u: AdminUser): { text: string; color: string } {
  if (!u.isActive) return { text: '已停用', color: 'var(--rh-coral)' };
  if (u.locked) return { text: '已锁定', color: 'var(--rh-gold)' };
  return { text: '正常', color: 'var(--rh-lav)' };
}

export function AdminUsers() {
  usePageMeta({ title: '用户管理', noindex: true });
  return (
    <AdminGate
      kicker="FREQ / 用户管理"
      title="管理员登录"
      lead="用户管理只允许管理员使用。登录后可以新建用户、重置密码、停用账号。"
      denied="只有管理员可以管理账号。"
    >
      {(user, logout) => <UsersConsole user={user} onLogout={logout} />}
    </AdminGate>
  );
}

function UsersConsole({ user, onLogout }: { user: SiteUser; onLogout: () => void }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [bots, setBots] = useState<AdminBot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [generated, setGenerated] = useState<{ user: AdminUser; password: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [botTarget, setBotTarget] = useState<AdminUser | null>(null);

  const [username, setUsername] = useState('');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<'user' | 'superadmin'>('user');
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [userList, botList] = await Promise.all([listUsers(), listAdminBots()]);
      setUsers(userList);
      setBots(botList);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取账号列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const submitCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    setNotice(null);
    setGenerated(null);
    try {
      const res = await createUser({
        username: username.trim(),
        password: password ? password : undefined,
        name: name.trim() || undefined,
        email: email.trim() || undefined,
        role,
      });
      setUsername('');
      setName('');
      setEmail('');
      setPassword('');
      setRole('user');
      if (res.generatedPassword) {
        setGenerated({ user: res.user, password: res.generatedPassword });
      } else {
        setNotice(`已创建用户 ${res.user.username}；点「机器人」即可绑定机器人`);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const toggleActive = async (u: AdminUser) => {
    if (u.isActive && !window.confirm(`确定停用「${u.username}」吗？停用后该账号立刻无法登录。`)) {
      return;
    }
    setBusyId(u.id);
    setError(null);
    setNotice(null);
    try {
      await updateUser(u.id, { isActive: !u.isActive });
      setNotice(`${u.username} 已${u.isActive ? '停用' : '启用'}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusyId(null);
    }
  };

  const resetPassword = async (u: AdminUser) => {
    const input = window.prompt(
      `为「${u.username}」设置新密码（至少 8 位）。留空则自动生成：`,
      '',
    );
    if (input === null) return;
    setBusyId(u.id);
    setError(null);
    setNotice(null);
    setGenerated(null);
    try {
      const res = await resetUserPassword(u.id, input.trim() || undefined);
      if (res.generatedPassword) {
        setGenerated({ user: u, password: res.generatedPassword });
      } else {
        setNotice(`已重置 ${u.username} 的密码`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '重置密码失败');
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (u: AdminUser) => {
    if (!window.confirm(`确定删除用户「${u.username}」吗？此操作不可撤销。`)) return;
    setBusyId(u.id);
    setError(null);
    setNotice(null);
    setGenerated(null);
    try {
      await deleteUser(u.id);
      setNotice(`已删除 ${u.username}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setBusyId(null);
    }
  };

  const removeBot = async (bot: AdminBot) => {
    if (
      !window.confirm(
        `确定删除机器人「${bot.name}」吗？删除后需要重新登记 API 地址和密码才能再使用。`,
      )
    ) {
      return;
    }
    setBusyId(bot.id);
    setError(null);
    setNotice(null);
    try {
      await deleteAdminBot(bot.id);
      setNotice(`已删除机器人 ${bot.name}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除机器人失败');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="wrap console">
      <aside className="sidebar">
        <div className="side-brand">
          <span className="side-brand__mark">＋</span>
          <span>
            FREQ<span className="side-brand__dim"> / 用户</span>
          </span>
        </div>

        <nav className="side-nav">
          <span className="side-btn is-active">
            <span className="side-btn__idx">01</span>
            <span>用户管理</span>
          </span>
        </nav>

        <div className="sidebar-foot">
          <div className="side-user side-login">已登录：{user.username}</div>
          <button type="button" className="btn btn--sm side-exit" onClick={onLogout}>
            退出登录
          </button>
        </div>
      </aside>

      <div className="console-main">
        <div className="hub-head">
          <div>
            <h1 className="hub-head__title">用户管理</h1>
            <p className="hub-head__sub">新建用户、重置密码、停用或删除账号；导出 CSV 可直接用 Excel 打开</p>
          </div>
          <div className="hub-head__tools">
            <a className="hub-refresh" href={USERS_EXPORT_URL}>
              导出 CSV ↓
            </a>
            <button type="button" className="hub-refresh" onClick={() => void load()}>
              ↻ 刷新
            </button>
          </div>
        </div>

        <details className="admin-create">
          <summary>＋ 新建用户</summary>
          <form className="admin-create__form" onSubmit={submitCreate}>
            <label>
              <span className="metric-label">用户名 *</span>
              <input
                className="input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="字母、数字、_ . @ -"
                required
              />
            </label>
            <label>
              <span className="metric-label">初始密码</span>
              <input
                className="input"
                type="text"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="留空自动生成（至少 8 位）"
                autoComplete="new-password"
              />
            </label>
            <label>
              <span className="metric-label">姓名</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              <span className="metric-label">邮箱</span>
              <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label>
              <span className="metric-label">角色</span>
              <select
                className="input"
                value={role}
                onChange={(e) => setRole(e.target.value as 'user' | 'superadmin')}
              >
                <option value="user">客户</option>
                <option value="superadmin">管理员</option>
              </select>
            </label>
            <button className="btn btn--primary" type="submit" disabled={creating}>
              {creating ? '创建中…' : '创建用户'}
            </button>
          </form>
        </details>

        {error && (
          <p className="admin-msg admin-msg--error" role="alert">
            {error}
          </p>
        )}
        {notice && !error && <p className="admin-msg">{notice}</p>}
        {generated && (
          <div className="admin-credential">
            <span>
              {generated.user.username} 的密码：
              <code>{generated.password}</code>
            </span>
            <span className="admin-credential__warn">只显示这一次，请立刻保存或发给客户</span>
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => setBotTarget(generated.user)}
            >
              绑定机器人
            </button>
          </div>
        )}

        {loading ? (
          <div className="empty">读取中…</div>
        ) : users.length === 0 ? (
          <div className="empty">还没有用户。</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>用户名</th>
                  <th>姓名 / 邮箱</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>机器人</th>
                  <th>最近登录</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const status = statusOf(u);
                  const self = u.id === user.id;
                  const busy = busyId === u.id;
                  return (
                    <tr key={u.id}>
                      <td>
                        {u.username}
                        {self && <span className="admin-badge">当前账号</span>}
                      </td>
                      <td>
                        {u.name || '—'}
                        <div className="small muted">{u.email || ''}</div>
                      </td>
                      <td>{u.role === 'superadmin' ? '管理员' : '客户'}</td>
                      <td style={{ color: status.color }}>{status.text}</td>
                      <td>{u.botCount}</td>
                      <td>{formatTime(u.lastLogin)}</td>
                      <td>
                        <div className="user-actions">
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => setBotTarget(u)}
                            disabled={busy}
                          >
                            机器人
                          </button>
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => void resetPassword(u)}
                            disabled={busy}
                          >
                            重置密码
                          </button>
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => void toggleActive(u)}
                            disabled={busy || self}
                          >
                            {u.isActive ? '停用' : '启用'}
                          </button>
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => void remove(u)}
                            disabled={busy || self || u.botCount > 0}
                            title={u.botCount > 0 ? '名下还有机器人，先解除归属再删除' : undefined}
                          >
                            删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <details className="admin-help">
          <summary>全部机器人（{bots.length}）</summary>
          <div className="bot-overview">
            {bots.length === 0 ? (
              <p className="form-hint">
                还没有登记机器人。在上面的账号行点「机器人」，可以新建一台并直接绑定给客户。
              </p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th>API 地址</th>
                      <th>登录名</th>
                      <th>归属</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bots.map((bot) => (
                      <tr key={bot.id}>
                        <td>{bot.name}</td>
                        <td className="small">{bot.apiUrl}</td>
                        <td className="small">{bot.username}</td>
                        <td className="small">
                          {bot.owners.length === 0
                            ? '未绑定'
                            : bot.owners
                                .map((o) =>
                                  o.expiresAt
                                    ? `${o.username}（${formatExpiryDay(o.expiresAt)} 到期）`
                                    : o.username,
                                )
                                .join('、')}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn--sm"
                            onClick={() => void removeBot(bot)}
                            disabled={busyId === bot.id || bot.owners.length > 0}
                            title={
                              bot.owners.length > 0
                                ? '还绑在账号上，先解绑再删除'
                                : '删除这台机器人'
                            }
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </details>
      </div>

      {botTarget && (
        <UserBotsModal
          user={botTarget}
          onClose={() => setBotTarget(null)}
          onChanged={() => void load()}
        />
      )}
    </section>
  );
}
