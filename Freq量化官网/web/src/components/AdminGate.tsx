/*
 * 管理页的统一门禁：校验会话 → 必须是 superadmin → 渲染子内容。
 *
 * 本地缓存的角色只用于首屏，真正判断以 /api/auth/me 返回的数据库角色为准。
 * 前端门禁只是体验，后端每个管理接口都会再校验一次。
 */

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { getUser, logout as apiLogout, restoreSession, type SiteUser } from '../lib/api';
import { LoginPanel } from './LoginPanel';

type Props = {
  children: (user: SiteUser, logout: () => Promise<void>) => ReactNode;
  kicker?: string;
  title?: string;
  lead?: string;
  denied?: string;
};

export function AdminGate({
  children,
  kicker = 'FREQ / 管理',
  title = '管理员登录',
  lead = '这个页面只允许管理员使用。',
  denied = '只有管理员可以进入。',
}: Props) {
  const [user, setUser] = useState<SiteUser | null>(() => getUser());
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let alive = true;
    restoreSession().then((u) => {
      if (!alive) return;
      setUser(u);
      setChecking(false);
    });
    return () => {
      alive = false;
    };
  }, []);

  const logout = async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
    }
  };

  if (checking) {
    return (
      <section className="wrap section">
        <div className="empty">校验会话…</div>
      </section>
    );
  }

  if (!user) {
    return <LoginPanel onDone={setUser} kicker={kicker} title={title} lead={lead} />;
  }

  if (user.role !== 'superadmin') {
    return (
      <section className="wrap section">
        <div className="empty">
          {denied}
          <div style={{ marginTop: 18, display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
            <Link className="btn" to="/">
              回到首页
            </Link>
            <button className="btn" type="button" onClick={logout}>
              退出登录
            </button>
          </div>
        </div>
      </section>
    );
  }

  return <>{children(user, logout)}</>;
}
