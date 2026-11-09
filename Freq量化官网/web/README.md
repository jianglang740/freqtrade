# Freq量化社区 · 官网

复古像素终端风格的营销站，外加一个「我的机器人」只读控制台。

> 第一次接触这个项目：先看下面的「日常使用手册（运营视角）」，里面是
> 开通客户、续费、发布内容的完整操作流程；接口、安全和部署细节在后面。

- 技术栈：Vite 6 + React 18 + TypeScript + react-router-dom 7
- 样式：手写 CSS（`src/styles/site.css`），没有 Tailwind、没有 UI 库
- 后端：官网自己的 `web/server/`（默认 `127.0.0.1:3002`），开发时由 Vite 把 `/api` 代理过去
- 参考项目：旧版 `FreqHub/` 已删除（本项目不再依赖它；它是公开项目
  `github.com/hrodrig/freqhub`，需要时重新克隆即可）

## 跑起来

先分清三种启动方式，按你要做什么选一种，不需要每次都全跑。

### 方式 A：只想打开网站 / 管内容 / 管用户（最短路径）

后端启动时如果发现 `web/dist/` 里有前端构建产物，会**顺手把整个网站一起托管**。
所以只起后端就能看到完整官网：

```bash
cd Freq量化官网/web/server
npm install          # 首次
npm run dev          # http://127.0.0.1:3002
```

浏览器打开 `http://127.0.0.1:3002`，首页、策略、因子、`/robots`、`/admin` 都在。
只跑这一条就能访问成功，是因为 `web/dist/` 已经构建好了。

前提是 `web/dist/index.html` 存在。刚克隆下来、或者删过 `dist/`，先构建一次：

```bash
cd Freq量化官网/web
npm install
npm run build
```

> 首次还要准备 `server/.env`（复制 `.env.example` 并生成两个密钥），
> 步骤见「日常使用手册 → 第一次上线」。

### 方式 B：要改前端代码（热更新）

Vite 开发服务器在 4000，保存代码后浏览器自动刷新：

```bash
# 终端 1：后端
cd Freq量化官网/web/server
npm run dev          # 3002

# 终端 2：前端
cd Freq量化官网/web
npm run dev          # 4000
```

开发时打开 `4000`（Vite 会把 `/api` 代理到 3002）。此时打开 3002 看到的仍是
上一次构建的 `dist`，改代码不会生效。

### 方式 C：生产 / 长期运行

```bash
cd Freq量化官网/web
npm run build                 # 前端产物在 web/dist/

cd Freq量化官网/web/server
npm run build                 # 后端产物在 server/dist/
npm start                     # 一个进程同时托管 web/dist/ 与 /api
```

`server/src/env.ts` 会固定读取 `server/.env`，与启动时所在目录无关；
默认静态目录是 `web/dist`，也可以用 `STATIC_DIR` 覆盖。

## 目录结构

```text
Freq量化官网/
├── web/                            # 网站本体（前端 + 后端 + 内容 + 数据）
│   ├── src/                        # 前端源码（React + TypeScript）
│   │   ├── pages/                  # 每个路由一个页面
│   │   │   ├── Home.tsx            #   首页
│   │   │   ├── Strategies.tsx      #   策略列表
│   │   │   ├── StrategyDetail.tsx  #   策略详情（回测口径、免责声明）
│   │   │   ├── Factors.tsx         #   因子库列表
│   │   │   ├── FactorDetail.tsx    #   因子详情（定义 / 公式 / 用法 / 限制）
│   │   │   ├── AppPage.tsx         #   安卓 App（FreqDroid）介绍
│   │   │   ├── Robots.tsx          #   「我的机器人」只读控制台
│   │   │   ├── Admin.tsx           #   内容管理：策略 / 因子 Markdown 编辑器
│   │   │   └── AdminUsers.tsx      #   用户管理：账号 + 机器人归属
│   │   ├── components/             # 可复用组件
│   │   │   ├── Dock.tsx            #   顶部导航与登录入口
│   │   │   ├── LoginPanel.tsx      #   登录面板
│   │   │   ├── AdminGate.tsx       #   管理页的登录 / 权限门
│   │   │   ├── UserBotsModal.tsx   #   客户 ↔ 机器人绑定弹窗
│   │   │   ├── ArticleMarkdown.tsx #   Markdown 渲染（策略 / 因子正文）
│   │   │   ├── EquityCurve.tsx     #   净值曲线
│   │   │   ├── ContactModal.tsx    #   联系 / 订阅弹窗
│   │   │   ├── DitherField.tsx     #   背景抖动效果
│   │   │   └── RobotMark.tsx       #   像素机器人图标
│   │   ├── lib/                    # 接口封装
│   │   │   ├── api.ts              #   fetch 封装、登录态、错误翻译
│   │   │   ├── users.ts            #   用户 / 机器人管理接口
│   │   │   ├── content.ts          #   策略 / 因子内容接口
│   │   │   └── useAsync.ts         #   通用异步 hook
│   │   ├── content/site.ts         # 站名、价格、联系方式、关于我们（静态文案）
│   │   ├── styles/site.css         # 全站样式（手写 CSS，无 UI 库）
│   │   ├── App.tsx                 # 路由、页脚、整体骨架
│   │   └── main.tsx                # 前端入口
│   ├── server/                     # Express 后端
│   │   ├── src/
│   │   │   ├── app.ts              #   中间件与路由装配、静态托管
│   │   │   ├── index.ts            #   启动入口、优雅退出
│   │   │   ├── env.ts              #   读取并校验 .env
│   │   │   ├── db/                 #   SQLite 连接与建表
│   │   │   ├── routes/             #   HTTP 路由
│   │   │   │   ├── auth.ts         #     登录 / 退出 / 当前用户
│   │   │   │   ├── users.ts        #     账号管理（superadmin）
│   │   │   │   ├── bots.ts         #     机器人登记与归属（superadmin）
│   │   │   │   ├── proxy.ts        #     透传 freqtrade /api/v1/*
│   │   │   │   └── content.ts      #     策略 / 因子内容与图片
│   │   │   ├── services/           #   业务逻辑
│   │   │   │   ├── auth.ts         #     密码校验、失败锁定
│   │   │   │   ├── users.ts        #     用户增删改查
│   │   │   │   ├── bots.ts         #     机器人、归属、到期判断
│   │   │   │   ├── freqtrade.ts    #     调上游 REST、token 缓存
│   │   │   │   ├── crypto.ts       #     AES-256-GCM 加解密机器人密码
│   │   │   │   ├── content.ts      #     Markdown 读写、校验、图片落盘
│   │   │   │   ├── rateLimit.ts    #     限流器
│   │   │   │   ├── urlSecurity.ts  #     机器人地址 / 代理路径校验（防 SSRF）
│   │   │   │   └── audit.ts        #     审计日志
│   │   │   ├── middleware/         #   auth / CSRF / 限流 / 写权限 / 错误处理
│   │   │   └── scripts/            #   命令行工具与压测脚本
│   │   ├── content/                # 运行期读取的内容（要提交、要备份）
│   │   │   ├── strategies/*.md     #   策略 Markdown
│   │   │   ├── factors/*.md        #   因子 Markdown
│   │   │   ├── assets/             #   上传的图片
│   │   │   └── _trash/             #   删除的内容先挪到这里，不物理删除
│   │   ├── data/freq.db            # SQLite：用户 / 机器人 / 归属 / 审计（要备份）
│   │   ├── dist/                   # 后端编译产物（npm run build 生成）
│   │   ├── .env                    # 本机配置与两个密钥（不提交，要备份）
│   │   ├── .env.example            # 配置模板
│   │   └── package.json
│   ├── dist/                       # 前端构建产物（npm run build 生成，后端托管它）
│   ├── index.html                  # Vite / 前端入口
│   ├── vite.config.ts              # 开发代理：/api → 127.0.0.1:3002
│   ├── package.json
│   └── README.md                   # 本文
└── 原始设想/                        # 产品设计资料：设计文档 + 参考截图，不参与运行
```

几个容易混淆的点：

| 路径                            | 作用                                       | 提交 git           | 备份        |
| ------------------------------- | ------------------------------------------ | ------------------ | ----------- |
| `web/src/`、`server/src/`   | 源码                                       | ✅                 | 按 git 即可 |
| `server/content/`             | 策略 / 因子正文与图片                      | ✅                 | ✅ 必须     |
| `server/data/freq.db`         | 账号、机器人凭据、归属、审计               | ❌（已 gitignore） | ✅ 必须     |
| `server/.env`                 | 配置 + `JWT_SECRET` + `ENCRYPTION_KEY` | ❌（已 gitignore） | ✅ 必须     |
| `web/dist/`、`server/dist/` | 构建产物                                   | ❌（已 gitignore） | 可随时重建  |
| `node_modules/`               | 依赖                                       | ❌                 | 不需要      |

所以：**源码和内容在 git 里；数据和密钥只在本机**。换机器或部署时，
除了克隆代码，还要单独带走 `server/data/freq.db`、`server/.env` 和 `server/content/`。

> 注意：`web/dist/` 是构建产物，不会跟着 git 走。新克隆的仓库里没有它，
> 第一次要么先 `cd web && npm run build`，要么用方式 B 起 Vite。

## 日常使用手册（运营视角）

这一节写给站长，按场景照做即可，不需要改代码。技术细节在后面几节。

### 先分清两个角色

| 角色                  | 能做什么                                  | 入口                                                               |
| --------------------- | ----------------------------------------- | ------------------------------------------------------------------ |
| 管理员 `superadmin` | 建客户、绑机器人、重置密码、发布策略/因子 | 登录后页脚「用户管理」`/admin/users`、页脚「内容管理」`/admin` |
| 客户 `user`         | 登录后看自己名下的机器人，只读            | 顶部导航「登录机器人」→ 登录后变「我的机器人」，即 `/robots`    |

客户**不需要也不应该**知道 freqtrade 的 API 地址和 REST 密码。你只把
「网站地址 + 用户名 + 初始密码」发给客户即可。

```
freqtrade 实例（8080 / 8082 / …）
        │  REST 账号密码：只在服务端解密使用
        ▼
官网后端 3002 ── bots（机器人凭据）
        │      └── bot_ownership（谁能看哪台、到期日）
        ▲
        │  客户登录后的 Cookie 会话
官网前端 4000 ── /robots 只读面板
```

### 客户登录后会看到什么

- 左侧是机器人切换，右侧是 KPI 卡片（总资产、可用、持仓盈亏等）和数据表。
- 持仓、已平仓、收益曲线都来自 freqtrade 的 REST 接口，页面每 15 秒自动刷新一次。
- 面板是**只读**的：没有开仓、平仓、启停、改配置的按钮；后端也会拦截非管理员的写请求。
- 一个客户被绑了多台机器人时，可以逐台切换查看。

客户自己的操作只有四步：

1. 打开网站 → 顶部导航点「登录机器人」。
2. 输入你发给他的账号和初始密码 → 登录。
3. 入口变成「我的机器人」，点进去就能看到分配给他的机器人。
4. 右上角「退出登录」。忘记密码只能找你重置。

### 第一次上线（只做一次）

1. 确认 Node.js ≥ 24（后端用的是内置 `node:sqlite`）。
2. 准备配置和密钥：

   ```bash
   cd Freq量化官网/web/server
   cp .env.example .env
   openssl rand -base64 48   # 填进 JWT_SECRET
   openssl rand -base64 32   # 填进 ENCRYPTION_KEY
   npm install
   ```
3. 建第一个管理员。网页登录本身要管理员，所以第一个超管只能用命令行建：

   ```bash
   printf '%s' '你的强密码' | npm run user:create -- \
     --username admin --role superadmin --name 站长 --password-stdin
   ```
4. 启动前后端（见上面「跑起来」），直接打开 `http://127.0.0.1:4000/admin/users`
   （生产环境换成你的域名），用 admin 登录。页脚的两个管理入口只对已登录的
   `superadmin` 显示，所以第一次要直接输地址，之后从页脚点进来即可。

> 本机当前这套：前端 4000、后端 3002；演示机器人
> 「网格马丁」→ `http://127.0.0.1:8080`、「海龟突破」→ `http://127.0.0.1:8082`，
> 都已绑定给演示账号 `demo`，可以直接拿来对照。

### 场景一：开通一个新客户（最常用）

目标：客户拿到账号后，登录 `/robots` 就能看到自己的机器人。

假设这次要给客户 `zhangsan` 开通一台跑在 8083 的网格策略，试用期到 2027-11-30。

**第 0 步：先让 freqtrade 跑起来并开 REST API。**
`config.json` 里必须有这一段：

```json
"api_server": {
  "enabled": true,
  "listen_ip_address": "127.0.0.1",
  "listen_port": 8083,
  "username": "freqtrader",
  "password": "换成一个强密码",
  "jwt_secret_key": "一串足够长的随机字符",
  "CORS_origins": [],
  "verbosity": "error"
}
```

先在**官网后端所在的机器**上验证能连上：

```bash
curl -u 'freqtrader:换成一个强密码' http://127.0.0.1:8083/api/v1/ping
# 期望：{"status":"pong"}
```

连不上就不要往下走，先解决网络或配置问题。

**第 1 步：建账号。**

1. 用超管登录 `/admin/users`。
2. 点「＋ 新建用户」，填：
   - 用户名：`zhangsan`（只能用字母、数字、`_ . @ -`，1–64 位）
   - 初始密码：**留空**，系统自动生成 16 位随机密码
   - 姓名 / 邮箱：选填
   - 角色：保持「客户」
3. 点「创建用户」。
4. 页面顶部会出现一次性密码。**它只显示这一次**，先复制到安全的地方。

**第 2 步：把机器人绑给他。**

1. 在 `zhangsan` 这一行点「机器人」。
2. 如果这台机器人还没登记过，在「新建机器人并绑定」里填：
   - 名称：`网格 8083`（自己看得懂即可，**全站不能重名**）
   - API 地址：`http://127.0.0.1:8083`（纯地址，**不要**带 `/api/v1`、路径或 `?`）
   - freqtrade 用户名 / 密码：就是上面 `api_server` 里的那一对
   - 到期日：`2027-11-30`（留空表示永久）
3. 点「创建并绑定」。后端会先试连，**连不上不会保存**，页面会直接报错。
4. 如果这台机器人之前已经登记过：在弹窗上半部分选机器人、填到期日，点「绑定」即可。
5. 一台机器人可以同时绑给多个客户；同一客户重复绑同一台会更新到期日。

**第 3 步：自己先验收。**

- 还在 `/admin/users`，展开底部「全部机器人」，确认 `网格 8083` 的「归属」列写着
  `zhangsan（2027-11-30 到期）`。
- 开一个无痕窗口，用 `zhangsan` 登录 `/robots`，能看到机器人名称、KPI 和数据表。
- 看不到就对照本节最后的「常见问题」。

**第 4 步：发给客户。** 话术模板（按需修改）：

```text
你好，你的量化看板已开通：
网址：https://你的域名/robots
账号：zhangsan
初始密码：<一次性密码>
登录后可以看到分配给你的机器人。面板是只读的，不会执行任何交易操作。
如有问题直接联系我。
```

（当前版本没有客户自助改密码，客户要改密码只能找你重置。）

**第 5 步：记一笔。**

- `/admin/users` 右上角「导出 CSV ↓」下载台账，存进你的客户表。
- 在日历上给到期日前 7 天设个提醒，续费见场景三。

### 场景二：客户已经在跑自己的 freqtrade

流程和场景一相同，区别只是第 2 步不新建机器人，直接「绑定已有机器人」。

API 地址必须从**官网后端所在机器**访问得到：

- 官网和 freqtrade 同机：`http://127.0.0.1:8080`
- 分开部署：填 freqtrade 机器的内网或公网地址，并确认防火墙放行
- `BOT_ALLOW_PRIVATE=false` 时后端会拒绝回环/内网地址（默认 `true`，保持不变即可）

### 场景三：续费 / 到期

- 到期日按 **UTC 当天 23:59:59** 存储；到点后客户立刻看不到这台机器人
  （列表不再返回，代理请求返回 403）。
- 续费：`/admin/users` → 该客户「机器人」→ 选中同一台，填新的到期日，点「绑定」。
  重复绑定会覆盖旧日期。
- 到期**不会删除数据**，客户账号仍能登录，只是机器人列表为空。
- 客户说「一台都看不到」时，先查绑定是否过期，再查账号是否被停用。

### 场景四：客户忘记密码 / 要改密码

- `/admin/users` → 该客户「重置密码」→ 输入新密码，或留空自动生成 →
  把一次性密码发给客户。重置会同时清掉登录失败计数和锁定。
- 重置**不会立刻踢掉已登录的会话**，旧会话最长再活 `SESSION_HOURS`（默认 12 小时）。
- 要立刻断开：点「停用」，再点「启用」。

### 场景五：给客户加一台机器人 / 换一台

- 加：重复场景一第 2 步，绑定新机器人。
- 换：先给新机器人绑定，再解绑旧机器人（解绑立即失去访问权）。
- 机器人本身要换地址或密码：先解绑 → 删除机器人 → 重新「新建并绑定」。
  `bots` 没有编辑接口，密码也从不回显，这是刻意设计。

### 场景六：客户暂停 / 退订

- 只是暂停：`/admin/users` 点「停用」，账号立刻不能登录，数据全部保留；
  恢复点「启用」。
- 彻底删除：先在该客户「机器人」里解绑全部机器人 → 再点「删除」。
  名下还有机器人时删除会被 409 挡下，这是保护，不是故障。
- 解绑后的机器人仍留在「全部机器人」里，可以再绑给别人；确认不再使用才删除机器人。

### 场景七：发布 / 更新策略或因子

见下面「策略 / 因子：Markdown 上传」的完整字段说明。最短流程：

1. `/admin` 新建或选中文件 → 编辑 Markdown。
2. 确认 front matter 里 `published: true`。
3. 点「保存」。
4. 刷新前台 `/strategies` 或 `/factors` 即可看到，**不需要重新构建或部署**。
5. 想先内部预览：设 `published: false`，前台 404，管理页会标「草稿」。

### 每天 5 分钟巡检

- [ ] `/admin/users`：有没有客户被锁定/停用，最近登录是否正常。
- [ ] `/admin/users` 底部「全部机器人」：有没有即将到期的绑定。
- [ ] 随便挑一个客户账号打开 `/robots`：KPI、持仓、已平仓是否有数据。
- [ ] 服务器：进程还在、磁盘够用；每周备份 `server/data/freq.db` 和 `server/content/`。

### 备份与恢复

每周（或每次大改动前）做一次，三个东西缺一不可：

```bash
cd Freq量化官网/web/server
mkdir -p backup

# 1. 数据库：用 SQLite 在线备份，不必停服
sqlite3 data/freq.db ".backup 'backup/freq-$(date +%F).db'"

# 2. 内容：策略 / 因子 Markdown 与图片
tar czf "backup/content-$(date +%F).tgz" content/

# 3. 配置：.env 里有 JWT_SECRET 和 ENCRYPTION_KEY
cp .env "backup/env-$(date +%F).bak"
```

恢复：停掉后端 → 把备份文件放回 `data/freq.db`、`content/`、`.env` →
重新启动。**`ENCRYPTION_KEY` 必须和备份时一致**，否则 `bots` 表里的机器人密码解不开，
所有机器人需要重新登记。备份文件不要提交到 git，也不要放进网站静态目录。

### 常见问题

| 现象                                         | 最可能的原因                                                          | 处理                                            |
| -------------------------------------------- | --------------------------------------------------------------------- | ----------------------------------------------- |
| 客户登录后看不到机器人                       | 没绑定 / 绑定已过期 / 账号被停用                                      | 查归属与到期日；重新绑定或启用账号              |
| 新建机器人提示连接失败                       | freqtrade 没开 `api_server`；地址多了路径；账号密码错；后端访问不到 | 先从后端机器 `curl -u ... /api/v1/ping` 验证  |
| 提示 `A bot with this name already exists` | 机器人名称全站唯一                                                    | 换名称，或直接绑定已有那台                      |
| 提示 `API URL must not contain a path`     | 地址填成了 `http://ip:8080/api/v1`                                  | 只填 `http://ip:8080`                         |
| 到期日显示差一天                             | 到期日按 UTC 存取，界面也按 UTC 显示                                  | 正常现象，不要改成本地时区                      |
| 客户说数据不刷新                             | 面板 15 秒轮询，后端还有 3–60 秒缓存                                 | 等 15 秒；仍不动就查 freqtrade 是否在跑         |
| 账号显示「已锁定」                           | 连续输错密码 10 次，锁 15 分钟                                        | 等 15 分钟，或直接「重置密码」立即解锁          |
| 我起了新的 freqtrade，网站没自动出现         | 网站不会扫描实例，必须显式登记                                        | 场景一第 2 步「新建机器人并绑定」               |
| 管理员自己进不去                             | 忘了密码，或唯一的超管被停用                                          | 用命令行再建一个超管（见「第一次上线」第 3 步） |

## 改内容只看这几个地方

| 想改什么                       | 改哪里                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------ |
| 策略、因子正文与卡片数据       | `/admin` 网页编辑器，或直接编辑 `server/content/{strategies,factors}/*.md` |
| 首页「已有 xx 位用户」         | `src/content/site.ts` 的 `userCount`（改完要 `npm run build`）           |
| 站名、价格、联系方式、关于我们 | `src/content/site.ts`                                                        |
| 颜色、字号、间距               | `src/styles/site.css` 顶部的 `:root` 变量                                  |

`userCount` 设为 `null` 时整行不显示；填数字后重新构建前端即可。
`src/content/site.ts` 里的 `contact` 是公开的联系方式，上线前确认微信号和邮箱
是你愿意对外展示的那一个。

### 策略 / 因子：Markdown 上传

内容以 Markdown 文件为唯一来源，运行时直接读文件，**保存后刷新前台即生效，不需要重新构建或部署**。

1. 用 `superadmin` 账号打开 `/admin`（页脚「内容管理」入口只对管理员显示）。
2. 左侧选择策略或因子，点击「新建」，或选中已有文件编辑；也可以把 `.md` 拖进编辑器。
3. 图片点「插入图片」或直接拖进编辑器，会存到 `server/content/assets/` 并自动插入链接。
4. 保存后写回 `server/content/`。这些文件在 git 工作区里，想留版本历史就正常提交。

front matter 字段（不认识的字段会被拒绝，避免拼写错误悄悄生效）：

- 策略：`slug`（必须与文件名一致）、`name`、`latin`、`tagline`、`desc`、`status`（`live` / `paper`）、
  `statusLabel`、`metrics`（最多 6 条 `label`/`value`）、`curve`（数字数组或 `null`）、
  `curveNote`、`disclaimer`、`published`、`order`。
- 因子：`slug`、`name`、`latin`、`oneLine`、`status`（`研究完成` / `研究中`）、`published`、`order`。

`published: false` 是草稿：前台列表与详情都返回 404，管理页会标「草稿」。`order` 越小越靠前。

正文就是普通 Markdown（GFM）：`##` 章节、代码块（渲染成等宽公式块）、表格、列表、图片。
渲染端不解析内联 HTML，`<script>` 会当纯文本丢掉，所以正文里不要依赖 HTML。

备份时除了数据库，也要复制整个 `server/content/`（含 `assets/`）；
删除内容只会移到 `server/content/_trash/`，不会物理删除。

## 页面结构

| 路由                  | 页面       | 说明                                                                     |
| --------------------- | ---------- | ------------------------------------------------------------------------ |
| `/`                 | 首页       | 主视觉 / 工作原理 / 我们是谁                                             |
| `/strategies`       | 策略列表   | 卡片：左文字右曲线+价格                                                  |
| `/strategies/:slug` | 策略详情   | 真实回测口径 + 免责声明 + 订阅按钮                                       |
| `/factors`          | 因子库     | 静态卡片                                                                 |
| `/factors/:slug`    | 因子详情   | 定义 / 公式 / 用法 / 限制                                                |
| `/app`              | FreqDroid  | 安卓 App 介绍                                                            |
| `/robots`           | 我的机器人 | 需要登录，只读控制台                                                     |
| `/admin`            | 内容管理   | 仅 `superadmin`：编辑策略 / 因子 Markdown                              |
| `/admin/users`      | 用户管理   | 仅 `superadmin`：新建 / 重置密码 / 停用 / 删除 / 机器人归属 / 导出 CSV |

博客是外链，直接跳到 `https://jianglang740.github.io/my-blog/`。

## 「我的机器人」怎么取数

登录走后端 `POST /api/auth/login`，会话放在 **httpOnly Cookie** `freq_session` 里：

- 前端拿不到 token，`localStorage` 只缓存一份 `user` 用于首屏显示。
- 所有请求同源 `fetch('/api/...', { credentials: 'same-origin' })`，浏览器自动带 Cookie。
- 页面加载时调 `GET /api/auth/me` 校验会话；失效则回到登录面板。
- 退出登录调 `POST /api/auth/logout`，由后端清 Cookie。

接口：

- `GET /api/bots` —— 后端已按归属过滤，客户只会看到分给自己的机器人
- `GET /api/bots/:id/proxy/api/v1/...` —— 透传到 freqtrade 的 REST 接口，**只开放 GET**
- `POST /api/bots/test`、`POST /api/bots` —— 仅 `superadmin`，且受 `CUSTOMER_READ_ONLY` 约束

面板是只读的：没有开仓、平仓、启停按钮。写权限在后端统一拦截，
`CUSTOMER_READ_ONLY=true` 时非 `superadmin` 一律返回：

> 只读面板：当前账号没有写权限，如需调整请联系服务方

> 已平仓列表用 `order_by_id=false` 取「最近优先」。
> 这个参数必须显式传：freqtrade 默认 `order_by_id=true`，也就是**最旧优先**。

### 内容接口

- 公开：`GET /api/content/{strategies,factors}` 与 `/:slug`，只返回 `published` 的内容
- 公开：`GET /api/content/assets/:name`，内容哈希命名，`immutable` 缓存
- 管理：`GET /api/content/admin`、`PUT|DELETE /api/content/admin/:kind/:slug`、`POST /api/content/assets`
  —— **只有 `superadmin`**（和 `CUSTOMER_READ_ONLY` 无关），写操作受 CSRF 与用户级限流约束

图片只接受 PNG / JPEG / WebP / GIF 的文件头，SVG 与伪装成图片的 HTML 会被拒绝；
文件扩展名由嗅探结果决定，不信任上传时声明的类型。

### 账号管理接口

`/api/users/*` 全部要求 `superadmin`（每次请求都重新读数据库角色），写操作受 CSRF 与用户级限流约束：

- `GET /api/users` —— 账号列表（含角色、状态、名下机器人数、最近登录），不返回密码哈希与 IP
- `POST /api/users` —— 新建；密码留空则生成 16 位随机密码，**只在响应里出现一次**
- `PATCH /api/users/:id` —— 改姓名 / 邮箱 / 启用状态
- `POST /api/users/:id/password` —— 重置密码，同样支持留空自动生成
- `DELETE /api/users/:id` —— 删除账号
- `GET /api/users/export` —— 导出 CSV（带 UTF-8 BOM，Excel 直接打开不乱码）

安全护栏：不能停用或删除当前登录的账号；至少保留一个启用的 `superadmin`；
名下还有机器人的账号不能直接删除（先解绑）。所有动作都写审计日志，但绝不记录密码。

**生效范围**：停用会立刻挡住新的登录与后续请求（`requireAuth` 每次都查数据库）；
重置密码不会踢掉已经登录的会话——旧会话最长可继续用 `SESSION_HOURS`（默认 12 小时），
要立刻断掉就同时「停用再启用」。

### 机器人归属接口

同样只给 `superadmin`，返回里永远不含机器人密码：

- `GET /api/bots/admin` —— 全部机器人 + 各自归属账号与到期日
- `POST /api/bots/:id/assign` —— body `{ userId, expiresAt }`；`expiresAt` 为 `YYYY-MM-DD` 或 `null`（永久）
- `DELETE /api/bots/:id/assign/:userId` —— 解绑，立即失去访问权
- `DELETE /api/bots/:id` —— 删除机器人；还绑在账号上时返回 409，要先解绑

登记机器人本身走已有的 `POST /api/bots`：后端会先 ping + token/login 试连，
连不上不会落库；密码用 AES-256-GCM 加密，只在服务端解密使用。

## 账号和机器人是两层

用户不需要自己「添加机器人」，也从不接触 freqtrade 的 REST 账号密码。

- **站点账号**：`web/server/data/freq.db` 的 `users` 表，管「谁能登录、能看到哪几台机器」。
- **机器人凭据**：`bots` 表里的 `api_url` + `username` + AES-256-GCM 加密后的密码，
  后端代替浏览器去调某台 freqtrade 的 `/api/v1/*`。
- 两者通过 **`bot_ownership` 关联表**绑定。超级管理员把机器人绑到账号上之后，
  用户登录后 `GET /api/bots` 只会返回分给自己的那几台（`expires_at` 到期即失去访问权）。
  演示账号 demo 能看到「海龟突破」和「网格马丁」两台，就是因为做了这条绑定——
  demo 从头到尾没有填过任何 API 地址或 freqtrade 账号密码。

正确做法是「**建账号 + 绑机器人**」，而不是把 freqtrade 的账号密码抄进站点账号里。
保持两层的好处：

1. **一个用户可能有多台机器人**，一台机器人也可能换着绑给不同账号；合并成一套密码就表达不了。
2. **解绑即断权**：不续费 / 到期，删一条 ownership（或设置 `expires_at`）就能立刻断掉访问，不用动 freqtrade 那边的 API 密码。
3. **改密码互不影响**：用户改站点密码不该影响机器人的 REST 认证；反过来轮换机器人 API 密码也不用通知用户重登。
4. **凭据不下发**：机器人密码只在服务端解密使用，接口与日志都不输出。
5. **客户是只读的**（`CUSTOMER_READ_ONLY=true`），界面上即便点「＋ 添加」也会被后端 403 挡掉，
   正好符合「登录就能看，不能乱加」。

## 账号运维：命令行与底层规则

网页操作步骤见前面的「日常使用手册」，这里补充命令行、批量场景和底层约束。
页面按钮全部调用 `/api/users/*`、`/api/bots/*`，后端每次都会重新校验 `superadmin`；
前端隐藏入口不算权限。

**命令行**适合批量操作、服务器上没有浏览器、或要写进脚本时使用：

```bash
cd Freq量化官网/web/server

# 建账号（密码从 stdin 读，避免留在 shell history）
printf '%s' '初始密码' | npm run user:create -- --username zhangsan --name 张三 --password-stdin

# 也可以建管理员；忘记超管密码时用这个再建一个
printf '%s' '强密码' | npm run user:create -- --username admin2 --role superadmin --password-stdin

# 登记机器人（会先 ping + token/login 试连，连不上不落库）
printf '%s' '<freqtrade 密码>' | npm run bot:add -- \
  --name 网格马丁 --api-url http://127.0.0.1:8080 --username '<freqtrade 用户名>' --password-stdin

# 绑定 / 解绑（机器人可以写 id，也可以写名称）
npm run bot:assign   -- --username demo --bot 网格马丁
npm run bot:assign   -- --username demo --bot 网格马丁 --expires 2027-12-31
npm run bot:assign   -- --username demo --bot 网格马丁 --expires never   # 改回永久
npm run bot:unassign -- --username demo --bot 网格马丁
```

> 历史说明：旧 `FreqHub/` 项目已删除，数据早已迁移完成。下面的脚本保留下来，
> 只有在拿到旧库备份（`backend/data/freqhub.db` 与 `.env` 里的
> `ENCRYPTION_KEY`）时才用得上。迁移会沿用原 bcrypt 哈希，客户原密码继续可用；
> 机器人密码从旧 CBC 解密后转成新 GCM 格式。

```bash
npm run migrate:freqhub -- --dry-run     # 先看数量与解密校验
npm run migrate:freqhub                  # 真正写入
```

## 安全设计

- **会话**：JWT HS256（`JWT_SECRET` ≥ 32 字符，默认 12 小时）只放在
  `freq_session` Cookie 里：`HttpOnly`、`SameSite=Lax`、生产 `Secure`、`Path=/`。
- **每次请求回库**：用户是否启用、角色、机器人归属都以数据库为准，不信任 token 里的权限。
- **CSRF**：SameSite=Lax + 非 GET 请求校验 `Origin`/`Referer` 白名单；
  有 body 的请求必须是 `application/json`。
- **密码**：bcrypt（新密码 cost 12；旧 cost 10 哈希可直接校验）。
  连续失败 10 次锁定 15 分钟，登录接口另有 10 次 / 15 分钟 / IP 限流。
- **机器人凭据**：AES-256-GCM（`v1:iv:tag:cipher`），密钥只存在于 `.env`；
  接口、响应、日志都不输出密码、密文或 token。
- **代理**：只开放 GET；上游 10 秒超时、响应上限 10MB；token 缓存 50 分钟，
  上游 401 时重新登录并重试一次。响应带 3–60 秒的短 TTL 缓存（按接口区分），
  同一份数据并发刷新只会回源一次；`gzip` 压缩 1KB 以上的响应。
- **SSRF**：机器人地址必须是纯 `http(s)://host:port`，不能带路径、查询串或内嵌凭据；
  代理路径必须以 `api/v1/` 开头，拒绝 `..`、反斜杠和空字节。
- **限流**：按**用户**计数（代理 300 次 / 分钟 / 用户、机器人 240 次 / 分钟），
  再加一个很高的 IP 兜底（3000 次 / 分钟）。客户常共用公司网络或运营商 NAT，
  按 IP 卡死会互相误伤；登录是 10 次 / 15 分钟 / IP。
- **登录闸门**：bcrypt 是纯 CPU 计算，密码校验同时最多跑 2 个，
  登录风暴不会把普通请求拖慢（压测里 p95 从 9.5 秒降到 1.4 秒）。
- **安全头**：helmet + CSP（`frame-ancestors 'none'`、`default-src 'self'` 等），关闭 `X-Powered-By`。
- **审计**：登录、登出、建档、登记机器人、绑定/解绑都写 `audit_logs`，只记动作与目标。

## 关于数字

策略页上的数据只有两种来源：

1. 真的跑出来过的回测结果（横截面动量：13–16% 年化、−35% 回撤、夏普 0.6–0.7，且已扣除幸存者偏差）
2. 「模拟盘观察中，不公开曲线」—— 没有可引用的口径就不写数字

不要为了页面好看给海龟突破、网格马丁编收益率。

## 容量与压测

目标场景：**50 个客户**，每人一个后台标签页，15 秒轮询一次，每轮 1–4 个请求
（约 8–13 请求/秒），偶尔同时刷新。

压测脚本在 `server/src/scripts/loadtest/`，本机回环跑，不经过公网：

```bash
# 终端 A：假的 freqtrade（延迟 8ms）
MOCK_PORT=18080 MOCK_DELAY_MS=8 npx tsx src/scripts/loadtest/mock-freqtrade.ts
# 终端 B：独立数据库的压测后端
DATABASE_PATH=./data/loadtest.db PORT=3102 npx tsx src/index.ts
# 终端 C：造数、压测（macOS 只能模拟同一 IP，用 --same-ip）
DATABASE_PATH=./data/loadtest.db npx tsx src/scripts/loadtest/seed.ts --users 100
DATABASE_PATH=./data/loadtest.db npx tsx src/scripts/loadtest/run.ts --port 3102 --users 50 --same-ip
```

优化前后（50 客户，同 IP/NAT 场景）：

| 阶段               | 优化前                 | 优化后                                               |
| ------------------ | ---------------------- | ---------------------------------------------------- |
| 稳态 60s           | 83.7% 被 429，p95 24ms | **0 错误**，10.4 req/s，p95 22–24ms，p99 28ms |
| 突发 10 轮同时刷新 | 87.6% 被 429           | **0 错误**，31.7 req/s，p95 52ms               |
| 50 人同时登录      | 全部 429               | 10 成功 + 40 个 429（符合预期），最慢 2.8s           |

容量余量：100 客户稳态 21 req/s、突发 64 req/s 也是 0 错误，p95 分别为 25ms / 70ms；
服务端 CPU 峰值 < 20%，常驻内存 100–160 MiB（测试用的 mock 延迟只有 8ms，
真实 freqtrade 更慢但会被缓存挡住，主要成本是等待上游，不是本进程）。

已知边界：

- 50 人**真正同时**登录时，bcrypt 会让登录排队约 13 秒（生产限流下同一 IP 最多 10 人，
  约 2.8 秒）；登录闸门保证这期间普通请求 p95 仍在 1.5 秒内。
- 改限流阈值前先想清楚：登录上限和每机器人上限是安全与体验的分界线。

## 部署

推荐一个 Node 进程同时托管前端构建产物与 `/api`（同源，Cookie 最省事）。
后端用到 Node 内置的 `node:sqlite`，要求 **Node.js ≥ 24**。
Nginx 可选，只做 TLS 终止与反向代理：

```nginx
server {
  listen 443 ssl;
  server_name example.com;

  location / {
    proxy_pass http://127.0.0.1:3002;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

后端会在 `STATIC_DIR`（默认 `web/dist`）存在时自动托管静态文件，
并对非 `/api` 路径回落到 `index.html`，所以不需要额外的 SPA 兜底配置。

环境变量全部在 `web/server/.env`（参考 `.env.example`）：

| 变量                              | 默认                     | 说明                                                           |
| --------------------------------- | ------------------------ | -------------------------------------------------------------- |
| `NODE_ENV`                      | `development`          | 生产设 `production`，Cookie 默认加 `Secure`                |
| `PORT` / `HOST`               | `3002` / `127.0.0.1` | 只监听本机，前面交给 Nginx                                     |
| `DATABASE_PATH`                 | `./data/freq.db`       | 相对路径以 `server/` 为基准                                  |
| `JWT_SECRET`                    | 必填                     | `openssl rand -base64 48`                                    |
| `ENCRYPTION_KEY`                | 必填                     | 32 字节 base64：`openssl rand -base64 32`                    |
| `SESSION_HOURS`                 | `12`                   | Cookie 与 JWT 有效期                                           |
| `COOKIE_SECURE`                 | 由 `NODE_ENV` 推导     | 本地 http 调试设 `false`；生产 HTTPS 设 `true`             |
| `CUSTOMER_READ_ONLY`            | `true`                 | 非 superadmin 写操作 403                                       |
| `PUBLIC_ORIGINS`                | 本地 4000                | CSRF 白名单，逗号分隔                                          |
| `BOT_ALLOW_PRIVATE`             | `true`                 | 允许机器人地址是回环/内网（freqtrade 常在本机）                |
| `TRUST_PROXY`                   | `false`                | 放在 Nginx 后面必须设 `true`，否则限流把所有人算成 127.0.0.1 |
| `STATIC_DIR`                    | `web/dist`             | 前端产物目录                                                   |
| `CONTENT_DIR`                   | `server/content`       | 策略 / 因子 Markdown 与图片目录；相对路径以 `server/` 为基准 |
| `LOG_REQUESTS`                  | `false`                | 默认只记错误和慢请求；排查问题时再打开                         |
| `RATE_LIMIT_USER_PER_MIN`       | `600`                  | 每登录用户的总 API 请求                                        |
| `RATE_LIMIT_PROXY_USER_PER_MIN` | `300`                  | 每登录用户的代理请求                                           |
| `RATE_LIMIT_PROXY_BOT_PER_MIN`  | `240`                  | 每台机器人的代理请求（多户共用一台机器人时共享额度）           |
| `RATE_LIMIT_API_IP_PER_MIN`     | `3000`                 | 每 IP 兜底，只防事故不误伤 NAT                                 |
| `RATE_LIMIT_LOGIN_PER_15MIN`    | `10`                   | 每 IP 登录尝试次数，安全相关，别随意放大                       |

注意：

- `.env` 已被 gitignore；部署时单独上传，`chmod 600`。
- 生产用 HTTPS；`COOKIE_SECURE=true` 时浏览器只在 HTTPS 下发送 Cookie。
- `TRUST_PROXY=true` 后后端才会采信 `X-Forwarded-For`。
- 备份：定期复制 `server/data/freq.db`（WAL 模式下同时带上 `-wal`/`-shm` 或先停进程），
  以及 `server/content/`（策略 / 因子 Markdown 与图片）。
- 换 `ENCRYPTION_KEY` 需要把 `bots.encrypted_password` 全部重新加密，当前没有在线轮换脚本，
  换密钥前先停服并备份。

### SEO（站点地图 / 分享卡片）

- `/robots.txt` 与 `/sitemap.xml` 由后端按请求域名动态生成（`server/src/routes/seo.ts`），策略 / 因子新增后自动出现，不用手工维护；`/robots`、`/admin`、`/api/` 对爬虫关闭。
- 分享卡片是 `web/public/og.png`。微信等抓取方要求绝对地址，正式构建时带上域名：
  `SITE_URL=https://你的域名 npm run build`（不传也能构建，只是分享图是相对路径）。

### 对路径的依赖

结论：**代码不依赖绝对路径**，部署到服务器后目录换成实际位置即可。

- 前端只有相对导入和 `fetch('/api/...')` 这种同源相对地址，没有写死任何本机绝对路径。
- `vite.config.ts` 里的 `http://127.0.0.1:3002` 只在 dev 模式生效，构建成 `dist/` 后不再出现。
- 后端固定从 `server/.env` 读取配置，`DATABASE_PATH` 的相对路径以 `server/` 为基准，
  不受启动目录影响；`STATIC_DIR` 默认解析到 `web/dist`，可用环境变量覆盖。
- Nginx 里 `root` / `proxy_pass` 指向的地址属于服务器配置，跟仓库放在哪个目录无关。

上服务器后真正要改的是**配置和数据**，不是代码：

1. `.env`：端口、数据库路径、两个密钥、`COOKIE_SECURE`、`TRUST_PROXY`。
2. Nginx：反代到后端端口；如果用 `base` 把站点挂在子路径（如 `https://xxx.com/freq/`），
   需要给 Vite 配置 `base: '/freq/'` 并同步改前端路由；挂根路径不用动。
3. `bots` 表里的 `api_url` 是绝对 URL（例如 `http://127.0.0.1:8080`）。
   服务器上 freqtrade 地址或端口变了，要用 `npm run bot:add` 重新登记——这是改数据，不是改代码。

> 开发时如果移动了 `server/` 目录，记得重启 `tsx watch`：
> watcher 会记住启动时的绝对路径，不重启可能报 `Cannot find module ...`。
> 生产环境跑编译产物，没有 watcher，不会遇到。
