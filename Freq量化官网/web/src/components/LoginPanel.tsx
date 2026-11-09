/*
 * 登录面板：/robots 与 /admin 共用。
 * 账号由服务方开通；登录成功后把用户对象交回调用方。
 */

import { useState } from 'react';
import { login, type SiteUser } from '../lib/api';

type Props = {
  onDone: (user: SiteUser) => void;
  kicker?: string;
  title?: string;
  lead?: string;
};

export function LoginPanel({
  onDone,
  kicker = 'FREQ / 我的机器人',
  title = '登录机器人',
  lead = '用订阅时给你开通的账号登录。你只会看到自己名下的机器人。',
}: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await login(username.trim(), password);
      onDone(user);
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="wrap section">
      <div style={{ maxWidth: 460, margin: '0 auto' }}>
        <div className="kicker">{kicker}</div>
        <h1 className="display" style={{ fontSize: 'clamp(28px, 4vw, 44px)', marginTop: 14 }}>
          {title}
        </h1>
        <p className="lead" style={{ marginTop: 14, fontSize: 14 }}>
          {lead}
        </p>

        <form className="card" style={{ marginTop: 26, display: 'grid', gap: 14 }} onSubmit={submit}>
          <label style={{ display: 'grid', gap: 6 }}>
            <span className="metric-label">账号</span>
            <input
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </label>

          <label style={{ display: 'grid', gap: 6 }}>
            <span className="metric-label">密码</span>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>

          {error && (
            <p className="small" style={{ color: 'var(--rh-coral)', margin: 0 }}>
              {error}
            </p>
          )}

          <button className="btn btn--primary" type="submit" disabled={busy} style={{ justifyContent: 'center' }}>
            {busy ? '登录中…' : '登录'}
          </button>

          <p className="small muted" style={{ margin: 0, lineHeight: 1.8 }}>
            还没有账号？通过首页的「获取联系方式」找我开通。
          </p>
        </form>
      </div>
    </section>
  );
}
