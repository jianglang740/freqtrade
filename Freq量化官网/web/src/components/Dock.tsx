/*
 * 顶部导航：一行硬边框格子。悬停时格子互相「抢宽度」，模拟 threeui 的 animated top dock。
 * 最后一个格子是入口：未登录显示「登录机器人」，登录后显示「我的机器人」。
 */

import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { site } from '../content/site';
import { getUser } from '../lib/api';

const NAV = [
  { to: '/', label: '首页' },
  { to: '/strategies', label: '策略' },
  { to: '/factors', label: '因子库' },
  { to: '/app', label: 'App' },
];

function PixelMark() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <rect x="0" y="0" width="4" height="4" />
      <rect x="6" y="0" width="4" height="4" />
      <rect x="12" y="0" width="4" height="4" />
      <rect x="0" y="6" width="4" height="4" />
      <rect x="12" y="6" width="4" height="4" />
      <rect x="0" y="12" width="4" height="4" />
      <rect x="6" y="12" width="4" height="4" />
      <rect x="12" y="12" width="4" height="4" />
    </svg>
  );
}

export function Dock() {
  const { pathname } = useLocation();
  // 登录/退出后立刻刷新入口文字，不用等下一次路由跳转
  const [, bump] = useState(0);
  useEffect(() => {
    const handler = () => bump((n) => n + 1);
    window.addEventListener('freq-session', handler);
    return () => window.removeEventListener('freq-session', handler);
  }, []);

  // 导航是 sticky 的，会一直盖住页面顶端；首页按「一屏一段」排版，需要知道它有多高
  const wrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const sync = () => {
      const h = Math.round(el.getBoundingClientRect().height);
      document.documentElement.style.setProperty('--dock-h', `${h}px`);
    };
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const loggedIn = !!getUser();

  const isActive = (to: string) =>
    to === '/' ? pathname === '/' : pathname === to || pathname.startsWith(`${to}/`);

  return (
    <div className="dock-wrap" ref={wrapRef}>
      <nav className="dock" aria-label="主导航">
        <Link to="/" className="dock-brand">
          <PixelMark />
          {site.latin}
        </Link>

        <div className="dock-cells">
          {NAV.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={`dock-cell${isActive(item.to) ? ' is-active' : ''}`}
            >
              {item.label}
            </Link>
          ))}

          <a
            className="dock-cell"
            href={site.blogUrl}
            target="_blank"
            rel="noreferrer"
          >
            博客 ↗
          </a>
        </div>

        <Link to="/robots" className="dock-cta">
          {loggedIn ? '我的机器人' : '登录机器人'}
        </Link>
      </nav>
    </div>
  );
}
