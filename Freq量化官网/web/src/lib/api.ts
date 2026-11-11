/*
 * 后端是官网自己的 server/（默认 127.0.0.1:3002），开发时由 vite 代理 /api 转发。
 * 登录态放在 httpOnly Cookie 里：前端拿不到 token，只缓存一份 user 供界面显示。
 * 这里只放「我的机器人」用得到的几个只读调用。
 */

const USER_KEY = 'freq_site_user';

export type SiteUser = {
  id: string;
  username: string;
  name?: string | null;
  email?: string | null;
  role: string;
  [k: string]: unknown;
};

export function getUser(): SiteUser | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SiteUser;
  } catch {
    return null;
  }
}

export function clearSession() {
  localStorage.removeItem(USER_KEY);
  notifySessionChange();
}

/** 登录状态变化时通知导航等组件，省得整页刷新 */
function notifySessionChange() {
  window.dispatchEvent(new Event('freq-session'));
}

/** 后端有几处固定英文提示，界面上统一换成中文 */
function translateError(msg: string, status: number, path: string): string {
  if (/too many requests/i.test(msg)) return '请求过于频繁，请稍后再试';
  if (/invalid username or password/i.test(msg)) return '账号或密码不正确';
  if (/account temporarily locked/i.test(msg)) return '账号已临时锁定，请稍后再试';
  if (/user account is inactive/i.test(msg)) return '账号已停用，请联系服务方';
  if (/request rejected \(csrf\)/i.test(msg)) return '页面已过期，请刷新重试';
  if (/failed to connect to freqtrade bot/i.test(msg)) return '连不上机器人：请检查 API 地址、用户名与密码';
  if (/authentication failed/i.test(msg)) return '机器人认证失败，请联系服务方';
  if (/a bot with this name already exists/i.test(msg)) return '已有同名机器人，请换一个名字';
  if (/you do not have access to this bot/i.test(msg)) return '没有权限访问这台机器人';
  if (/access to this bot has expired/i.test(msg)) return '这台机器人的授权已过期，请联系服务方';
  if (/this bot is disabled/i.test(msg)) return '这台机器人已停用';
  if (/method not allowed/i.test(msg)) return '不支持该操作';
  if (/api url/i.test(msg)) return 'API 地址不合法：请填写 http(s)://主机:端口，不要带路径';
  if (/internal server error/i.test(msg)) return '服务暂时不可用，请稍后再试';
  // 登录接口的 401 是账号密码不对，不能提示「登录已过期」
  if (status === 401 && !path.startsWith('/auth/')) return '登录已过期，请重新登录';
  if (status === 403) {
    // 后端的 403 通常带具体原因（例如「只读面板……」），有就原样透出
    return /^请求失败/.test(msg) ? '没有权限执行这个操作' : msg;
  }
  if (/not found/i.test(msg)) return '找不到这个机器人';
  return msg;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) || {}),
  };

  const res = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: 'same-origin',
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;

  if (!res.ok) {
    // 会话失效：清掉本地缓存的用户，界面会回到登录面板
    if (res.status === 401 && !path.startsWith('/auth/')) clearSession();
    const raw = (body && (body.message || body.error)) || '';
    const msg = typeof raw === 'string' && raw ? raw : `请求失败（${res.status}）`;
    throw new Error(translateError(msg, res.status, path));
  }
  return body as T;
}

export async function login(username: string, password: string) {
  const data = await request<{ user: SiteUser }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  localStorage.setItem(USER_KEY, JSON.stringify(data.user));
  notifySessionChange();
  return data.user;
}

/** 页面加载时用 Cookie 校验会话；失败返回 null（不回登录面板前会先清缓存） */
export async function restoreSession(): Promise<SiteUser | null> {
  try {
    const user = await request<SiteUser>('/auth/me');
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    notifySessionChange();
    return user;
  } catch {
    clearSession();
    return null;
  }
}

/** 让后端清掉 httpOnly Cookie，再清本地缓存 */
export async function logout(): Promise<void> {
  try {
    await request<null>('/auth/logout', { method: 'POST' });
  } finally {
    clearSession();
  }
}

export type Bot = {
  id: string;
  name: string;
  apiUrl: string;
  isEnabled: boolean;
  [k: string]: unknown;
};

export async function listBots(): Promise<Bot[]> {
  const res = await request<{ status: string; data: Bot[] }>('/bots');
  return res.data;
}

export type CreateBotInput = {
  name: string;
  apiUrl: string;
  username: string;
  password: string;
};

/** 登记一台新的 freqtrade 机器人；后端会先试连，连不上不会落库 */
export async function createBot(input: CreateBotInput): Promise<Bot> {
  const res = await request<{ status: string; data: Bot }>('/bots', {
    method: 'POST',
    body: JSON.stringify(input),
  });
  return res.data;
}

/** 表单里的「测试连接」：只试连，不落库 */
export async function testBotConnection(input: Omit<CreateBotInput, 'name'>): Promise<void> {
  await request<{ status: string; message: string }>('/bots/test', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

/** 走官网后端的代理，直接读 freqtrade 的 REST 接口（只读 GET） */
export async function botGet<T>(botId: string, path: string): Promise<T> {
  return request<T>(`/bots/${botId}/proxy/${path}`);
}

export type OpenTrade = {
  trade_id: number;
  pair: string;
  amount: number;
  open_rate: number;
  current_rate?: number;
  open_date: string;
  stake_amount: number;
  /** status 接口里是「浮动盈亏」，字段名沿用交易的 profit_abs */
  profit_abs?: number;
  profit_ratio?: number;
  /** 持仓时长，单位分钟 */
  trade_duration?: number;
  is_open: boolean;
  enter_tag?: string;
  [k: string]: unknown;
};

export type ClosedTrade = {
  trade_id: number;
  pair: string;
  open_date: string;
  close_date: string;
  close_rate: number;
  profit_abs: number;
  profit_ratio: number;
  trade_duration?: number;
  exit_reason?: string;
  is_open: boolean;
  [k: string]: unknown;
};

export type ProfitSummary = {
  profit_all_percent: number;
  profit_all_coin: number;
  closed_trade_count: number;
  trade_count: number;
  winning_trades?: number;
  losing_trades?: number;
  winrate?: number;
  profit_factor?: number;
  avg_duration?: string;
  [k: string]: unknown;
};

export type BalanceItem = {
  currency: string;
  free: number;
  used: number;
  /** 现货账户里合计放在 balance 字段，total 只有部分接口给 */
  total?: number;
  balance?: number;
  est_stake?: number;
  /** 合约持仓占位行，不是真正的币种 */
  is_position?: boolean;
};
export type BalanceSummary = {
  stake?: string;
  total?: number;
  value?: number;
  note?: string;
  starting_capital?: number;
  starting_capital_pct?: number;
  currencies?: BalanceItem[];
  [k: string]: unknown;
};

/** freqtrade 统计接口的公共字段；profit_pct 是百分数，例如 0.52 表示 0.52% */
export type StatRow = {
  profit_ratio: number;
  profit_pct: number;
  profit_abs: number;
  count: number;
};

export type PairPerformance = StatRow & { pair: string; profit?: number };
export type EnterTagPerformance = StatRow & { enter_tag: string };
export type ExitReasonPerformance = StatRow & { exit_reason: string };
export type MixTagPerformance = StatRow & { mix_tag: string };

/** freqtrade 的 historic_balance：钱包历史快照，每日一条，可用于画净值曲线 */
export type HistoricBalance = {
  columns: string[];
  length: number;
  /** 每行按 columns 顺序排列，按当前版本是 [date, __date_ts, total_quote] */
  data: (string | number)[][];
  /** 真实采集开始的时间戳；更早的数据是从交易记录反推的 */
  capture_start_ts?: number | null;
};

export type ShowConfig = {
  state?: string;
  dry_run?: boolean;
  strategy?: string;
  exchange?: string;
  trading_mode?: string;
  margin_mode?: string;
  stake_currency?: string;
  stake_amount?: string | number;
  max_open_trades?: number;
  timeframe?: string;
  /** 比率，不是百分数：-0.25 表示 -25% */
  stoploss?: number;
  position_adjustment_enable?: boolean;
  [k: string]: unknown;
};
