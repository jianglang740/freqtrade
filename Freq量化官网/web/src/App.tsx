/*
 * 路由与页面骨架。
 * 视觉三层（抖动背景 / 暗角 / 扫描线）固定在最底下，内容在 .rh-shell 里。
 */

import { useEffect, useState } from 'react';
import { Link, Route, Routes, useLocation } from 'react-router-dom';
import { DitherField } from './components/DitherField';
import { Dock } from './components/Dock';
import { ContactProvider } from './components/ContactModal';
import { Home } from './pages/Home';
import { Strategies } from './pages/Strategies';
import { StrategyDetail } from './pages/StrategyDetail';
import { Factors } from './pages/Factors';
import { FactorDetail } from './pages/FactorDetail';
import { AppPage } from './pages/AppPage';
import { Robots } from './pages/Robots';
import { Admin } from './pages/Admin';
import { AdminUsers } from './pages/AdminUsers';
import { getUser, restoreSession } from './lib/api';
import { site } from './content/site';

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

function Footer() {
  // 登录/退出后同步一次，管理入口只给 superadmin 看（真正的权限门在后端）
  const [, bump] = useState(0);
  useEffect(() => {
    const handler = () => bump((n) => n + 1);
    window.addEventListener('freq-session', handler);
    // 刷新页面后 localStorage 可能是空的，用 Cookie 校验一次，页脚入口才稳定
    void restoreSession();
    return () => window.removeEventListener('freq-session', handler);
  }, []);

  const isAdmin = getUser()?.role === 'superadmin';

  return (
    <footer className="readout">
      <span>© 2026 {site.name}</span>
      <span>基于 freqtrade 引擎</span>
      <span>不托管资金</span>
      <Link to="/strategies">策略</Link>
      <Link to="/factors">因子库</Link>
      <a href={site.blogUrl} target="_blank" rel="noreferrer">
        博客 ↗
      </a>
      {isAdmin && <Link to="/admin">内容管理</Link>}
      {isAdmin && <Link to="/admin/users">用户管理</Link>}
    </footer>
  );
}

export default function App() {
  return (
    <ContactProvider>
      <DitherField />
      <div className="rh-vignette" aria-hidden="true" />
      <div className="rh-scanlines" aria-hidden="true" />

      <div className="rh-shell">
        <ScrollToTop />
        <Dock />

        <main style={{ flex: 1 }}>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/strategies" element={<Strategies />} />
            <Route path="/strategies/:slug" element={<StrategyDetail />} />
            <Route path="/factors" element={<Factors />} />
            <Route path="/factors/:slug" element={<FactorDetail />} />
            <Route path="/app" element={<AppPage />} />
            <Route path="/robots" element={<Robots />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="/admin/users" element={<AdminUsers />} />
            <Route path="*" element={<Home />} />
          </Routes>
        </main>

        <Footer />
      </div>
    </ContactProvider>
  );
}
