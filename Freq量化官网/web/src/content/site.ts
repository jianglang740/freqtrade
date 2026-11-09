/*
 * 全站固定文案：站名、价格、联系方式、关于我们。
 * 策略与因子内容已经不在代码里了，改成 server/content/*.md，
 * 在网页的 /admin 里维护，保存即生效。
 *
 * 数据原则：只写我们真的跑出来过的数字。
 * 没有可引用的回测口径的策略，宁可写「模拟盘观察中」，也不编收益率。
 */

export const site = {
  name: 'Freq量化社区',
  latin: 'FREQ',
  slogan: '致力于加密领域量化交易，用机器和算法解决主观交易困境。',
  poweredBy: 'Powered by CCXT',
  footerLine: '基于工业级引擎 freqtrade 打造，生态成熟，功能强大，在这里开始你的量化之旅。',
  blogUrl: 'https://jianglang740.github.io/my-blog/',
  priceLabel: '35 USD/月',
  /**
   * 首页主视觉展示的「已有 xx 位用户」。
   * 每开通一位客户手动改这里；设为 null 时整行不显示。
   * 改完需要重新构建前端才会生效：cd web && npm run build
   */
  userCount: 32 as number | null,
};

/** 联系方式：把占位符换成你自己的即可 */
export const contact = {
  wechat: '15085111840',
  email: '2018036661@qq.com',
  github: 'github.com/jianglang740',
  githubUrl: 'https://github.com/jianglang740',
  note: '加微信请备注「量化订阅」，我们会先跟您确认交易所与资金规模，再决定是否接单。',
};

export const whoWeAre = {
  intro:
    'Freq量化社区由一群热爱量化交易的年轻人组成，这里有人爆过仓、有人曾露宿街头，也有人拿到大结果；有人是普通交易者、也有人是金融工程博士。尽管经历或身份不同，但我们的目标是一致的——用量化的方法解决交易困境。',
  /** 下面这排是散户最常挂在嘴边的借口，做成两排横向滚动的标签 */
  excuses: [
    '频繁交易',
    '又上头了',
    '杠杆太高了',
    '下次一定',
    '追高了',
    '仓位太重了',
    '行情不好',
    '满仓干',
    '要不贷点吧',
    '运气不好',
    '抄底抄在半山腰',
    '再梭哈一次',
    '扛单不认错',
    '听群里喊单',
    '补仓摊薄成本',
    '这次不一样',
    '回本就不玩了',
  ],
};
