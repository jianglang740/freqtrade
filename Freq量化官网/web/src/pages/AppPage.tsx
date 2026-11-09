/*
 * App 页 = 产品介绍页：主视觉 / 三步上手 / 功能章节 / 获取方式。
 * 截图在 public/app/ 下，随官网项目一起托管。
 * 叙事从用户视角写：先说「你能看到什么、能做什么」，技术细节放到最后小字里。
 */

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useContact } from '../components/ContactModal';
import { usePageMeta } from '../lib/pageMeta';

const META = ['Android · 内测中', 'v3.2.2'];

/** 一张手机截图 + 图注。frame 用官网的像素切角，和卡片是同一套语言。 */
function Shot({
  src,
  alt,
  caption,
  small,
}: {
  src: string;
  alt: string;
  caption: string;
  small?: boolean;
}) {
  return (
    <figure className={`app-shot${small ? ' app-shot--sm' : ''}`}>
      <div className="app-phone">
        <img src={src} alt={alt} loading="lazy" />
      </div>
      <figcaption className="app-shot__caption">{caption}</figcaption>
    </figure>
  );
}

/** 功能章节：一段讲解 + 一或两张截图，左右交替 */
function Chapter({
  id,
  index,
  title,
  lead,
  bullets,
  shots,
  flip,
}: {
  id: string;
  index: string;
  title: string;
  lead: string;
  bullets: ReactNode[];
  shots: ReactNode;
  flip?: boolean;
}) {
  return (
    <article id={id} className={`app-chapter app-anchor${flip ? ' app-chapter--flip' : ''}`}>
      <div className="app-chapter__copy">
        <div className="app-chapter__num">FREQ / {index}</div>
        <h2>{title}</h2>
        <p className="app-chapter__lead">{lead}</p>
        <ul className="app-list">
          {bullets.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>
      </div>
      <div className="app-chapter__media">{shots}</div>
    </article>
  );
}

const STEPS = [
  {
    n: '01',
    title: '拿到机器人',
    body: '订阅之后我们会把机器人的地址和账号发给你。自己用 freqtrade 搭的机器人，也照样能连。',
  },
  {
    n: '02',
    title: '在 App 里加进来',
    body: '打开「添加机器人」，填地址、用户名和密码，保存。想连几台就连几台。',
  },
  {
    n: '03',
    title: '剩下的交给它',
    body: '机器人一直在你的服务器上跑，App 关掉也不影响。想看的时候掏出来看一眼就行。',
  },
];

const EXTRA = [
  '多机器人切换',
  '自定义图表指标',
  '平均 K 线',
  '持仓与平仓记录',
  '日 / 周 / 月盈亏',
  '累计盈亏与盈亏分布',
  '手动下单',
  '机器人日志',
  '通知推送',
  '浅色 / 深色',
  '自定义主色',
  '数据存在本机',
];

export function AppPage() {
  const contact = useContact();
  usePageMeta({
    title: 'FreqDroid',
    description:
      'FreqDroid：在手机上查看机器人总览、K 线图表、持仓与盈亏，随时手动下单、翻看日志。Android 内测中。',
  });

  return (
    <>
      {/* ── 01 主视觉 ── */}
      <section className="wrap section app-hero">
        <div className="app-hero__grid">
          <div>
            <div className="kicker">FREQ / App</div>
            <h1
              className="display"
              style={{ fontSize: 'clamp(40px, 6.4vw, 88px)', marginTop: 14 }}
            >
              FreqDroid
            </h1>
            <p className="lead" style={{ marginTop: 18 }}>
              在手机上看着你的交易机器人工作。
              <br />
              总览、图表、持仓、盈亏、下单和日志，装进一个 App。
            </p>

            <div className="app-meta">
              {META.map((m) => (
                <span key={m} className="app-meta__chip">
                  {m}
                </span>
              ))}
            </div>

            <div className="app-cta">
              <button className="btn btn--primary" onClick={contact.open}>
                获取安装包
              </button>
              <a className="btn" href="#tour">
                看看它能做什么 ↓
              </a>
            </div>

            <p className="app-hero__note">
              App 不碰你的资金。它只是连上你自己的机器人，替你把状态读出来、把指令发过去。
            </p>
          </div>

          <div className="app-hero__shot">
            <Shot
              src="/app/bot_comparison.png"
              alt="机器人总览：两台机器人的交易、持仓、胜负、盈亏与余额"
              caption="机器人总览 · 两台机器人，一屏看完"
            />
          </div>
        </div>
      </section>

      {/* ── 02 三步上手 ── */}
      <section className="wrap section app-anchor" id="start">
        <div className="kicker">FREQ / 上手</div>
        <h2 className="section-title" style={{ fontSize: 'clamp(30px, 4vw, 52px)' }}>
          三步开始
        </h2>
        <p className="lead" style={{ marginTop: 18 }}>
          没有复杂的配置向导，也不用懂代码。装好、连上、看数据。
        </p>

        <div className="app-steps">
          {STEPS.map((s) => (
            <div className="app-step" key={s.n}>
              <div className="app-step__num">{s.n}</div>
              <h3>{s.title}</h3>
              <p>{s.body}</p>
            </div>
          ))}
        </div>

        <p className="note">
          还没有机器人？先到<Link to="/strategies">策略</Link>页挑一个，订阅之后我们帮你部署。
          安装和使用上的问题，直接联系我们。App 目前只提供 Android 安装包，不经过应用商店。
        </p>
      </section>

      {/* ── 03 功能章节 ── */}
      <section className="wrap section" id="tour">
        <div className="kicker">FREQ / 功能</div>
        <h2 className="section-title" style={{ fontSize: 'clamp(30px, 4vw, 52px)' }}>
          它到底能做什么
        </h2>
        <p className="lead" style={{ marginTop: 18 }}>
          按你每天打开 App 的顺序，从总览一路看到下单。每一张都是真机截图，数据来自正在跑的模拟盘。
        </p>

        <Chapter
          id="overview"
          index="01"
          title="所有机器人，一屏看到底"
          lead="打开 App 第一眼就是它。不用挨个登录后台，哪台赚钱、哪台持仓、哪台掉线，一眼分清。"
          bullets={[
            <>
              每台机器人一张卡：<strong>全部交易、当前持仓、胜负笔数</strong>，三列对齐，方便对照。
            </>,
            <>
              下面一行是钱：<strong>持仓盈亏、已平仓盈亏、余额</strong>，赚了绿、亏了红。
            </>,
            <>右上角标着「模拟盘」还是实盘；底部还有一条汇总，把多台机器人合并成一份总账。</>,
          ]}
          shots={
            <Shot
              src="/app/bot_comparison.png"
              alt="机器人总览页面，Grid 与 Turtle 两张卡片及底部汇总"
              caption="机器人总览 · 汇总与单台并列"
            />
          }
        />

        <Chapter
          id="bots"
          index="02"
          title="加一台、停一台，随手的事"
          lead="机器人列表管的是「连哪些」。新买的策略、临时停掉的一台，都在这里处理。"
          bullets={[
            <>每台机器人的地址下面有在线状态，绿色对勾表示连得上。</>,
            <>左侧箭头调顺序，开关决定它出不出现在总览和图表里。</>,
            <>点铅笔改名字和账号，点垃圾桶删除；底部「添加机器人」随时再加一台。</>,
          ]}
          shots={
            <Shot
              src="/app/bots.png"
              alt="机器人管理页：Grid 与 Turtle 两个条目，带在线状态与开关"
              caption="机器人列表 · 在线状态与开关"
            />
          }
          flip
        />

        <Chapter
          id="chart"
          index="03"
          title="图表里，能看到策略在想什么"
          lead="同一套 K 线，接上机器人自己的指标。策略为什么在这里开仓，看图就明白。"
          bullets={[
            <>顶部切换机器人和交易对，点周期在 1m 到 1d 之间换，涨跌颜色与成交量都在。</>,
            <>
              图表配置可以<strong>直接从策略加载</strong>：策略里写了哪些指标，就画哪些，不用在手机上重算一遍。
            </>,
            <>主图放均线、通道这类价格线；子图放 ATR、成交量这类独立指标。</>,
            <>不习惯标准 K 线的话，设置里可以切成平均 K 线，趋势看着更顺。</>,
          ]}
          shots={
            <>
              <Shot
                src="/app/chart.png"
                alt="BTC/USDT 4h K 线图，上方可选机器人与交易对"
                caption="图表 · 交易对与周期"
                small
              />
              <Shot
                src="/app/plot_config.png"
                alt="图表配置页：主图四项唐奇安通道指标，子图 ATR"
                caption="图表配置 · 从策略加载"
                small
              />
            </>
          }
        />

        <Chapter
          id="trades"
          index="04"
          title="持仓和每一笔交易的来龙去脉"
          lead="开着的单子看得到，平掉的单子也查得到。赚了亏了，都能顺着记录找回原因。"
          bullets={[
            <>持仓卡写清方向、数量、投入、开仓价和浮盈亏，右下角是已经拿了多久。</>,
            <>
              点进交易详情，<strong>开仓标签、平仓原因、手续费、最高最低价、止损和风险敞口</strong>
              全都列出来，对着日志就能复盘。
            </>,
            <>已平仓记录按时间倒序，加仓单还会单独标出来，不会和首单混在一起。</>,
          ]}
          shots={
            <>
              <Shot
                src="/app/open_trades.png"
                alt="持仓页面：BTC/USDT 多单，数量、投入、开仓价与浮亏"
                caption="持仓 · 一单一行"
                small
              />
              <Shot
                src="/app/trade_details.png"
                alt="交易详情页：交易 ID、开平仓价格时间、手续费、止损与风险敞口"
                caption="交易详情 · 复盘用"
                small
              />
            </>
          }
          flip
        />

        <Chapter
          id="profit"
          index="05"
          title="把账户的账算明白"
          lead="不是只告诉你今天赚了多少，而是按日、周、月拆开，看这套策略到底稳不稳。"
          bullets={[
            <>
              盈亏走势可以在<strong>绝对值与相对百分比</strong>之间切换，柱子看金额，折线看收益率。
            </>,
            <>下面配一张按月表格：每月盈亏、交易笔数、收益率，哪个月不对劲一翻就知道。</>,
            <>同组的还有累计盈亏、盈亏分布和余额变化，钱包的每一笔出入也都有记录。</>,
          ]}
          shots={
            <Shot
              src="/app/profit_over_time.png"
              alt="盈亏走势页：按月柱状图与折线，以及每月盈亏、交易数、收益率表格"
              caption="盈亏走势 · 日 / 周 / 月"
            />
          }
        />

        <Chapter
          id="order"
          index="06"
          title="需要的时候，手动下一单"
          lead="策略没动作、但你想补一单或者平掉一个仓位时，不用再开电脑。"
          bullets={[
            <>选机器人和交易对，开仓或平仓、做多或做空、市价或限价，按需填写。</>,
            <>投入金额和杠杆都可以留空，用机器人默认值；开仓标签会自动带上来源，事后分得清是手动单。</>,
            <>机器人日志也能直接在手机上看，出问题不用再登录服务器翻文件。</>,
          ]}
          shots={
            <Shot
              src="/app/trade_form.png"
              alt="下单页：选择机器人、交易对、开仓方向、订单类型与投入金额"
              caption="手动下单 · 市价 / 限价"
            />
          }
          flip
        />

        <Chapter
          id="settings"
          index="07"
          title="装成你顺眼的样子"
          lead="该低调的地方低调，该显眼的地方显眼。外观、主色、通知，都在设置里。"
          bullets={[
            <>外观可以跟随系统，也能固定浅色或深色；主色默认青色，喜欢别的颜色可以自己调。</>,
            <>导航有「聚合」和「经典」两种：聚合把多台机器人放在一起看，经典回到单台机器人。</>,
            <>打开通知后，开仓、平仓、止损这类动作会推到手机；不用一直盯着页面。</>,
          ]}
          shots={
            <Shot
              src="/app/settings.png"
              alt="设置页：外观、导航、图表、通知与主色设置"
              caption="设置 · 深浅色与主色"
            />
          }
        />
      </section>

      {/* ── 04 还有这些 ── */}
      <section className="wrap section">
        <div className="kicker">FREQ / 细节</div>
        <h2 className="section-title" style={{ fontSize: 'clamp(30px, 4vw, 52px)' }}>
          还有一些小的
        </h2>
        <div className="app-tags">
          {EXTRA.map((t) => (
            <span className="app-tag" key={t}>
              {t}
            </span>
          ))}
        </div>
        <p className="app-fineprint">
          机器人地址、账号和设置都保存在手机本地，不上传到我们的服务器；交易数据则是每次打开时从你自己的机器人读取。
        </p>
      </section>

      {/* ── 05 获取 ── */}
      <section className="wrap section" id="get">
        <div className="kicker">FREQ / 获取</div>
        <h2 className="section-title" style={{ fontSize: 'clamp(30px, 4vw, 52px)' }}>
          现在就能用
        </h2>

        <div className="app-get">
          <div>
            <p className="app-get__lead">
              内测进行中，安装包直接发给你，不经过任何应用商店。装好之后，把机器人加进来就能用。
            </p>
            <dl className="app-facts">
              <div className="app-fact">
                <dt>平台</dt>
                <dd>Android（.apk 安装包）；iOS 版还在打磨，暂未发布。</dd>
              </div>
              <div className="app-fact">
                <dt>适用</dt>
                <dd>任何开启了 API 服务的 freqtrade 机器人；Freq 订阅用户的机器人已经开好权限。</dd>
              </div>
              <div className="app-fact">
                <dt>资金</dt>
                <dd>不托管资金、不代持密钥，App 只通过你的机器人发指令。</dd>
              </div>
              <div className="app-fact">
                <dt>版本</dt>
                <dd>v3.2.2，内测中，功能和界面仍可能调整。</dd>
              </div>
            </dl>
          </div>

          <div className="app-get__cta">
            <button className="btn btn--primary" onClick={contact.open}>
              获取安装包
            </button>
            <span className="app-get__hint">用微信或邮箱联系我们，说明来意即可。</span>
          </div>
        </div>
      </section>
    </>
  );
}
