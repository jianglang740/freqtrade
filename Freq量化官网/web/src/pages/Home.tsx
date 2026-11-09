/*
 * 首页 = 三段：主视觉 / 工作原理 / 我们是谁。
 * 文案全部来自 content/site.ts。
 */

import type { CSSProperties } from 'react';
import { site, whoWeAre } from '../content/site';
import { useContact } from '../components/ContactModal';
import { RobotMark } from '../components/RobotMark';
import { usePageMeta } from '../lib/pageMeta';

/** 从「工作原理」右下角弯出去，指向右侧那段说明 */
function HowArrow() {
  return (
    <svg
      className="how-arrow"
      viewBox="0 0 120 240"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinecap="square"
        strokeLinejoin="miter"
      >
        <path d="M2 96 C14 140 48 168 82 172 L104 174" />
        <path d="M96 168 L104 174 L96 181" />
      </g>
    </svg>
  );
}

/*
 * 直角分叉箭头：上横线从标题右侧水平指进按钮，中点垂下一道竖线，
 * 再从竖线底部引一条横线指进正文。
 * viewBox 高 260 对应标题行 + 正文行的总高，97 处是按钮中线，200 处是正文第三行。
 */
function ForkArrow() {
  return (
    <svg
      className="who-fork"
      viewBox="0 0 120 260"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinecap="square"
        strokeLinejoin="miter"
      >
        {/* 上横线 + 箭头，指向按钮 */}
        <path d="M2 98 L104 95" />
        <path d="M95 87 L104 95 L94 101" />
        {/* 竖线：从上横线的中点垂到正文那一行 */}
        <path d="M46 97 L45 200" />
        {/* 下横线 + 箭头，指向正文 */}
        <path d="M45 199 L104 201" />
        <path d="M95 192 L104 201 L94 207" />
      </g>
    </svg>
  );
}

/**
 * 借口标签的角度和上下偏移。
 * 现在是滚动的一排，歪得太厉害会显得乱，所以幅度收小，只留一点点手写感。
 */
const CHIP_STYLE = [
  { rot: -2.5, dy: -3, size: 19 },
  { rot: 1.8, dy: 3, size: 18 },
  { rot: -1.4, dy: -1, size: 17 },
  { rot: 2.6, dy: 4, size: 19 },
  { rot: -2, dy: 0, size: 18 },
  { rot: 1.2, dy: -4, size: 19 },
  { rot: -3, dy: 2, size: 17 },
  { rot: 2.2, dy: -2, size: 18 },
  { rot: -1, dy: 4, size: 19 },
];

/** 摆成两排滚动：上排 9 个、下排 8 个 */
const EXCUSE_ROWS = [whoWeAre.excuses.slice(0, 9), whoWeAre.excuses.slice(9)];

/*
 * 一排从右往左无限滚动的标签。
 * 轨道里把同一组标签渲染两遍、整体平移 -50%，第二遍正好接上第一遍的尾巴，看不出接缝。
 * 两排给不同的时长和相位，看起来不像同一条传送带。
 */
function ChipRow({
  items,
  seed,
  duration,
  delay,
}: {
  items: string[];
  seed: number;
  duration: number;
  delay: number;
}) {
  return (
    <div
      className="who-marquee"
      style={{ '--dur': `${duration}s`, '--delay': `${delay}s` } as CSSProperties}
    >
      <div className="who-track">
        {[0, 1].map((copy) =>
          items.map((text, i) => {
            const s = CHIP_STYLE[(seed + i) % CHIP_STYLE.length];
            return (
              <span
                key={`${copy}-${text}`}
                className="chip"
                /* 第二遍只是为了让循环接得上，读屏软件不用念两遍 */
                aria-hidden={copy === 1 || undefined}
                style={
                  {
                    '--rot': `${s.rot}deg`,
                    '--dy': `${s.dy}px`,
                    '--fs': `${s.size}px`,
                  } as CSSProperties
                }
              >
                {text}
              </span>
            );
          }),
        )}
      </div>
    </div>
  );
}

export function Home() {
  const contact = useContact();
  usePageMeta();

  return (
    <>
      {/* ── 01 主视觉 ── */}
      <section className="wrap hero section home-sec" style={{ borderBottom: 0 }}>
        <div className="kicker">FREQ / 01 — 加密量化交易</div>
        <h1 className="display hero-title" style={{ marginTop: 'clamp(26px, 5vh, 58px)' }}>
          {site.name}
          {/* 机器人当成标题里的最后一个「字」，字面高度和「区」齐平 */}
          <span className="hero-mark" aria-hidden="true">
            <RobotMark className="hero-mark__svg" />
          </span>
        </h1>

        <p className="hero-sub">
          <span className="hero-sub__mark" aria-hidden="true">
            ——
          </span>
          <span className="hero-sub__text">{site.slogan}</span>
        </p>

        <div className="hero-foot">
          {site.userCount !== null && (
            <span className="hero-proof">
              <span className="hero-proof__dot" aria-hidden="true" />
              已有 <strong>{site.userCount}</strong> 位用户
            </span>
          )}
          <span className="hero-powered">{site.poweredBy}</span>
          {site.footerLine}
          <div className="scroll-hint">
            <i />
            向下滚动
          </div>
        </div>
      </section>

      {/* ── 02 工作原理 ── */}
      <section className="wrap section home-sec" id="how">
        <div className="how-top">
          <div className="how-head">
            <div className="kicker">FREQ / 02</div>
            <h2 className="section-title">工作原理</h2>
          </div>

          <div className="how-arrow-slot">
            <HowArrow />
          </div>

          <p className="how-lead">
            仅通过用户提供的交易所 API 来执行交易、接管账户，
            <strong>绝不托管资金</strong>。保证每一位用户的财产安全，是我们的底线。
          </p>
        </div>

        <div className="how-flow">
          <div className="how-node">
            <h3 className="how-node__title">交易所</h3>
            <span className="how-badge">用户</span>
          </div>

          <div className="how-link">
            <span className="how-link__label">API</span>
            <span className="how-link__bar" />
            {/* 第一行末尾的「开」要和第二行开头的「不」上下对正，所以断行位置写死 */}
            <span className="how-link__note">
              只开交易权限，
              <br />
              <span className="how-link__note-2">不开提现权限</span>
            </span>
          </div>

          <div className="how-node how-node--engine">
            <div className="how-sub">
              机器人在 freqtrade 交易引擎下，根据量化策略计算信号，并通过 API
              操控你的账户，以此来执行信号。
            </div>
            <div className="how-sub">
              API 由交易所官方提供，在这个过程中我们也将保证你的隐私，不必担心安全泄露问题。
            </div>
          </div>

          <div className="how-link">
            <span className="how-link__label">机器人账号</span>
            <span className="how-link__bar" />
            <span className="how-link__note">本网站为你提供仪表盘</span>
          </div>

          <div className="how-node how-node--dash">
            <p>
              我们将为你提供机器人的登录信息；获取信息后，只需点击网站右上角的「登录机器人」按钮，
              即可在后台监控机器人的运行状态、绩效等，并对其进行操控。
            </p>
          </div>
        </div>

        <p className="how-foot">
          在策略页浏览策略，找到你喜欢的风格，联系我们获取订阅后即可获得机器人权限，开启你的量化之旅。
        </p>
      </section>

      {/* ── 03 我们是谁 ── */}
      <section className="who-section home-sec" id="who">
        <div className="wrap who-top">
          <div className="who-head">
            <div className="kicker">FREQ / 03</div>
            <h2 className="section-title">我们是谁？</h2>
          </div>

          <div className="who-fork-slot">
            <ForkArrow />
          </div>

          <button className="btn btn--primary who-cta" onClick={contact.open}>
            Get 联系方式.
          </button>
          <p className="who-intro">{whoWeAre.intro}</p>
        </div>

        <div className="who-band">
          <ChipRow items={EXCUSE_ROWS[0]} seed={0} duration={46} delay={0} />
          <ChipRow items={EXCUSE_ROWS[1]} seed={5} duration={64} delay={-21} />
        </div>
      </section>
    </>
  );
}
