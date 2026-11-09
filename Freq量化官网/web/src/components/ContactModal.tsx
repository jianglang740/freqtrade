/*
 * 「获取联系方式」弹窗。全站复用同一个实例：任何按钮调 useContact().open() 即可唤出。
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { contact } from '../content/site';

type ContactCtx = { open: () => void; close: () => void };

const Ctx = createContext<ContactCtx>({ open: () => {}, close: () => {} });

export function useContact() {
  return useContext(Ctx);
}

export function ContactProvider({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState(false);

  const open = useCallback(() => setVisible(true), []);
  const close = useCallback(() => setVisible(false), []);

  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [visible, close]);

  return (
    <Ctx.Provider value={{ open, close }}>
      {children}
      {visible && (
        <div className="modal-backdrop" onClick={close} role="presentation">
          <div
            className="modal modal--brick"
            role="dialog"
            aria-modal="true"
            aria-label="获取联系方式"
            onClick={(e) => e.stopPropagation()}
          >
            <button className="modal-close" onClick={close} aria-label="关闭">
              ✕
            </button>
            <h3>// 获取联系方式</h3>
            <p>通过下面任一方式联系我，说明你想订阅的策略和资金规模。</p>

            <div className="contact-row">
              <span className="k">微信</span>
              <span className="v">{contact.wechat}</span>
            </div>
            <div className="contact-row">
              <span className="k">邮箱</span>
              <span className="v">{contact.email}</span>
            </div>
            <a
              className="contact-row"
              href={contact.githubUrl}
              target="_blank"
              rel="noreferrer"
              style={{ display: 'flex' }}
            >
              <span className="k">GitHub</span>
              <span className="v">{contact.github} ↗</span>
            </a>

            <p className="small muted" style={{ marginTop: 16, marginBottom: 0 }}>
              {contact.note}
            </p>
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
}
