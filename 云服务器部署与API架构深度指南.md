# 云服务器部署与 API 架构深度指南

> 从"端口是什么"一路讲到"一个面板管理多个策略"的底层原理。
> 适合已经把 freqtrade 跑通、想深入理解"为什么这么部署"的读者。
> 环境：腾讯云轻量服务器（Linux）+ 域名 jianglang.online + tmux + Nginx + freqtrade 2026.8-dev。

---

## 目录

- [〇、一条请求的完整旅程（先建立全局观）](#〇一条请求的完整旅程先建立全局观)
- [一、网络基础：IP、端口、进程与"谁在听"](#一网络基础ip端口进程与谁在听)
  - [1.1 IP 地址：网络世界的门牌](#11-ip-地址网络世界的门牌)
  - [1.2 端口：进程的"窗口"](#12-端口进程的窗口)
  - [1.3 什么是"监听"](#13-什么是监听)
  - [1.4 TCP 三次握手：建立可靠连接](#14-tcp-三次握手建立可靠连接)
  - [1.5 公网 IP 与私有 IP：为什么云服务器和别人不一样](#15-公网-ip-与私有-ip为什么云服务器和别人不一样)
- [二、为什么"开了端口"还要"转发端口"](#二为什么开了端口还要转发端口)
  - [2.1 防火墙：云服务器的第一道门](#21-防火墙云服务器的第一道门)
  - [2.2 端口转发 vs 反向代理：两个不同的概念](#22-端口转发-vs-反向代理两个不同的概念)
  - [2.3 Nginx 反向代理：你的"前台接待"](#23-nginx-反向代理你的前台接待)
  - [2.4 域名、DNS、子域名与 HTTPS](#24-域名dns子域名与-https)
- [三、freqtrade 到底是个什么东西：进程、模式与"两个面板"](#三freqtrade-到底是个什么东西进程模式与两个面板)
  - [3.1 freqtrade 是一个"常驻进程"](#31-freqtrade-是一个常驻进程)
  - [3.2 两个"面板"的真相：同一个 FreqUI，两种 RunMode](#32-两个面板的真相同一个-frequi两种-runmode)
  - [3.3 Trade 模式（trade 命令）](#33-trade-模式trade-命令)
  - [3.4 Webserver 模式（webserver 命令）](#34-webserver-模式webserver-命令)
  - [3.5 关键区别对照表](#35-关键区别对照表)
- [四、freqtrade API 设计：一个 FastAPI 进程怎么知道自己是哪种模式](#四freqtrade-api-设计一个-fastapi-进程怎么知道自己是哪种模式)
  - [4.1 API Server 的骨架](#41-api-server-的骨架)
  - [4.2 路由注册：哪些接口"只有交易模式有"](#42-路由注册哪些接口只有交易模式有)
  - [4.3 认证：HTTP Basic 与 JWT](#43-认证http-basic-与-jwt)
  - [4.4 WebSocket：实时推送，不是轮询](#44-websocket实时推送不是轮询)
  - [4.5 CORS：浏览器为什么拦你](#45-cors浏览器为什么拦你)
  - [4.6 一个完整的 API 调用长什么样](#46-一个完整的-api-调用长什么样)
- [五、一个面板管理多个策略：FreqUI 多 Bot 机制](#五一个面板管理多个策略frequi-多-bot-机制)
  - [5.1 核心思想：前端直连，面板只是"接线员"](#51-核心思想前端直连面板只是接线员)
  - [5.2 数据流全景图](#52-数据流全景图)
  - [5.3 Add Bot 到底发生了什么](#53-add-bot-到底发生了什么)
  - [5.4 为什么一定需要 CORS_origins](#54-为什么一定需要-cors_origins)
  - [5.5 bot 地址存哪：localStorage 的秘密](#55-bot-地址存哪localstorage-的秘密)
  - [5.6 一栏对比：你说的"一个实例一个面板" vs 多 Bot 方案](#56-一栏对比你说的一个实例一个面板-vs-多-bot-方案)
- [六、完整落地：云服务器上一个域名、一个面板、管理多个策略](#六完整落地云服务器上一个域名一个面板管理多个策略)
  - [6.1 目标架构](#61-目标架构)
  - [6.2 每个策略实例的配置](#62-每个策略实例的配置)
  - [6.3 Webserver 面板配置](#63-webserver-面板配置)
  - [6.4 tmux 与进程管理](#64-tmux-与进程管理)
  - [6.5 Nginx 配置（单域名多子路径）](#65-nginx-配置单域名多子路径)
  - [6.6 安全要点](#66-安全要点)
- [七、常见坑与排障](#七常见坑与排障)
- [八、进阶：还能怎么玩](#八进阶还能怎么玩)

---

## 〇、一条请求的完整旅程（先建立全局观）

你在浏览器里输入 `https://trade.jianglang.online`，回车。在你看不见的地方，发生了这些事情：

1. **浏览器问 DNS**："`trade.jianglang.online` 这个域名对应的 IP 是多少？"
2. **DNS 回答**：`110.42.xx.xx`（你的腾讯云公网 IP）。
3. **浏览器向 `110.42.xx.xx:443` 发起 TCP 连接**（443 是 HTTPS 默认端口，走加密）。
4. 你的云服务器上，**有一个进程在 443 端口"听"** —— 这不是 freqtrade，而是 **Nginx**。
5. Nginx 收到请求，查看 `server_name` 和路径，把请求**转发**给内网另一个端口上的 freqtrade（比如 `127.0.0.1:8080`）。
6. freqtrade 的 FastAPI 收到请求，检查认证（cookie 里的 JWT 令牌），查数据库，返回 JSON。
7. Nginx 把 JSON 原样送回浏览器。
8. 浏览器里的 Vue.js 前端（FreqUI）拿到 JSON，渲染成你看到的 K 线图、持仓列表、余额面板。

**你从头到尾只接触到一个端口（443）、一个 IP、一个域名。但背后可能有两个、三个、甚至更多 freqtrade 实例在各自的端口上默默工作。** 这篇文章就是要把每一环都拆开讲清楚。

---

## 一、网络基础：IP、端口、进程与"谁在听"

### 1.1 IP 地址：网络世界的门牌

IP 地址是网络里每台设备的唯一编号。它分两种：

- **IPv4**：`110.42.xx.xx` 这种，4 组数字，用 `.` 分隔。目前全球用的大头还是它。
- **IPv6**：更长一串十六进制，`2408:8000:...`，因为 IPv4 地址快用完了。

每个 IP 地址有两个视角：

- **公网 IP**：全世界都能访问。你的腾讯云服务器有一个公网 IP，所以全世界的电脑理论上都能"敲"它。
- **私有 IP**：只能在局域网内用，如 `192.168.x.x`、`10.x.x.x`、`172.16.x.x`。你家里路由器的内网、云服务器的内网，都是私有 IP。

> 关键直觉：**你的云服务器同时有两个"身份"** —— 对公网它是 `110.42.xx.xx`，对同一机房的另一台云服务器它是 `10.0.0.5`（私有 IP）。同一个服务，既可能被公网访问，也可能被内网的其他服务器访问，**取决于访问方在网络的哪一侧**。

### 1.2 端口：进程的"窗口"

一台服务器上跑着成百上千个服务：web、数据库、SSH、你的 freqtrade……它们都共享同一个 IP。**光有 IP 无法区分是哪个服务在收数据**，于是有了端口（Port）。

- 端口是一个 **0 ~ 65535 的整数**。
- 每个要对外提供服务的进程，都要"占用"一个端口，声明："从这个端口进来的数据，归我管。"
- 一个 IP + 一个端口，就能唯一定位一个服务。合起来叫 **`IP:端口`**，例如 `127.0.0.1:8080`。

常用端口速记：

| 端口 | 服务 | 说明 |
| ---- | ---- | ---- |
| 22 | SSH | 你连服务器改代码用的 |
| 80 | HTTP | 网页明文 |
| 443 | HTTPS | 网页加密 |
| 3306 | MySQL | 数据库 |
| 6379 | Redis | 缓存 |
| 8080 / 8081... | 应用自定义 | **你的 freqtrade 就在这类端口上** |

**端口是两个进程之间对话的"窗口"：一边是发送方的源端口，一边是接收方的目标端口。** 发送方也占用一个端口（通常是随机的、临时的），所以一次网络对话永远是"一对端口对"。

### 1.3 什么是"监听"

一个进程要让别人能连上它，必须做一件事：**监听（listen）某个端口**。

- `listen_ip_address = "127.0.0.1"` + `listen_port = 8080` → 这个服务**只监听本机回环地址**，只有服务器自己（或通过本机的其他进程）能访问 `127.0.0.1:8080`，**公网进不来**。
- `listen_ip_address = "0.0.0.0"` + `listen_port = 8080` → 监听**所有网卡**，公网 IP 也能直接敲 `110.42.xx.xx:8080`。

> 这就是 freqtrade 配置里 `api_server.listen_ip_address` 的含义。默认 `127.0.0.1` 是安全的 —— 服务只在本机开放，外面根本敲不到，必须靠 Nginx 在内网转发。这是两层安全，**Nginx 是第一道，监听地址是第二道**。

### 1.4 TCP 三次握手：建立可靠连接

HTTP/HTTPS 依赖 TCP 协议。TCP 建立连接需要"三次握手"，你可以理解成一次电话接通前的三次确认：

1. 客户端说："你好，我想连你。"（SYN）
2. 服务器说："好的，我听到了，我要连你。"（SYN-ACK）
3. 客户端说："好，连上了。"（ACK）

三次握手之后，双方才真正开始传数据。为什么一定要三次？因为要在"客户端确实在线"和"服务器确实能回"之间达成一致，缺一次都会导致一方误以为通道已建立。**你每次刷新面板，背后都是这么一次握手（如果连接是新建的话）。**

### 1.5 公网 IP 与私有 IP：为什么云服务器和别人不一样

你家里的电脑和你的云服务器，对公网的态度完全不同：

- **家里的电脑**：在路由器后面，没有公网 IP（除非运营商分配）。外界无法主动连你。这是 **NAT（网络地址转换）** 在起作用——你主动连别人可以，别人连你不行（除非你做了路由器端口映射）。
- **云服务器**：天生就有公网 IP。**任何人在任何地方都能主动连它的公网 IP**。这正是优点也是风险——优点是你随便部署，风险是别人也能来敲你的门。

所以云服务器的安全，全靠**防火墙（安全组）+ 服务自身只监听内网 + 认证**这三道防线。下面进入正题。

---

## 二、为什么"开了端口"还要"转发端口"

### 2.1 防火墙：云服务器的第一道门

你可能会想："既然服务器有公网 IP，我在 freqtrade 里把监听地址改成 `0.0.0.0:8080`，不就能直接访问了吗？"

**能，但你不该这么做。** 因为云服务商（腾讯云、阿里云）在物理网卡之外，还有一道**安全组（Security Group）/防火墙**。它独立于服务器内软件，规则默认是：**只放行 22（SSH）、80、443（网页），其余端口一律拒绝公网访问**。

所以哪怕 freqtrade 监听在 `0.0.0.0:8080`，公网仍然连不进来——安全组把 8080 挡在外面了。你要么去安全组控制台把 8080 放行（裸奔，不建议），要么走下面这条路。

### 2.2 端口转发 vs 反向代理：两个不同的概念

这两个词经常混用，但**原理不一样**：

- **端口转发（Port Forwarding）**：一个中转设备把某个端口的流量**原封不动**搬到另一个端口/主机。数据是"透传"的，中间人不知道也不关心里面是什么。家用路由器的端口映射、`iptables`、`socat` 都是这类。优点：透明、性能高。缺点：无法根据 URL/域名做精细分流，也没有加解密和认证的方便入口。
- **反向代理（Reverse Proxy）**：一个**前置服务**（最流行的是 Nginx）接收全部公网请求，然后**代表客户端去访问后端服务**，把结果转回来。它**能看到并能修改请求**——可以做域名分流（`trade.jianglang.online` → 8080，`backtest.jianglang.online` → 8081）、HTTPS 加密、压缩、缓存、限速、Basic Auth。freqtrade 官方文档推荐的正是这条路。

> 一句话：**端口转发 = 快递员把包裹原样送到；反向代理 = 前台接待，先验身份、再按你找的人带你进不同办公室。**

### 2.3 Nginx 反向代理：你的"前台接待"

Nginx 是你云服务器上**最常驻的、监听 80/443 的那个进程**。它一夫当关，所有公网流量先进它，再由它分流给后面的 freqtrade 们。配置文件长这样（核心片段）：

```nginx
# HTTP → 强制跳转 HTTPS（可选）
server {
    listen 80;
    server_name trade.jianglang.online;
    return 301 https://$host$request_uri;
}

# HTTPS 主入口
server {
    listen 443 ssl;
    server_name trade.jianglang.online;

    ssl_certificate     /etc/nginx/ssl/trade.jianglang.online.pem;   # SSL 证书
    ssl_certificate_key /etc/nginx/ssl/trade.jianglang.online.key;

    # ↓ 反向代理核心：把 / 开头的所有请求转给内网 8080 的 freqtrade
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket 必须的三行：freqtrade 面板实时推送全靠它
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

**为什么这样比直接开 8080 强？**

1. **端口不用暴露**：freqtrade 监听 `127.0.0.1:8080`，公网永远敲不到，只有本机的 Nginx 能转发给它。攻击面从"全网"缩小到"只有你的服务器"。
2. **HTTPS 加密**：浏览器 ↔ Nginx 之间是加密的，密码、JWT 令牌不会被嗅探。
3. **多实例分流**：加一个 `server { server_name backtest.jianglang.online; location / { proxy_pass http://127.0.0.1:8081; } }`，就是一个新面板，互不干扰。
4. **可以只暴露 443**：安全组只需要放行 443 和 22，其余端口全部关闭。

### 2.4 域名、DNS、子域名与 HTTPS

**域名**（`jianglang.online`）是人类可读的名字，**DNS** 是"名字 → IP"的电话簿。你在域名服务商后台加一条 A 记录。你有两个独立顶级域名，各加一条即可：

| 类型 | 主机记录 | 记录值 | 说明 |
| ---- | ---- | ---- | ---- |
| A | `@` | `110.42.xx.xx` | `jianglang.online` → 服务器公网 IP |
| A | `@` | `110.42.xx.xx` | `jianglangbacktest.online` → 同一个 IP |

（如果以后想用子域名扩展，比如 `trade.jianglang.online`，就在 `jianglang.online` 下加一条主机记录为 `trade` 的 A 记录。）

注意：**两个域名指向同一个公网 IP 完全没问题**，服务器上的 Nginx 靠 `server_name` 区分请求来自哪个域名，再分别转发给不同端口。这就是"多个域名 + 一个 Nginx"管理多实例的基础。

**HTTPS/SSL**：443 端口的流量是加密的。加密靠"证书"（SSL 证书）。免费方案有 Let's Encrypt（`certbot` 一键签发），或者腾讯云也有免费证书。证书必须绑定域名——所以要先有域名，再签证书，再配 Nginx。

> 小结一句话：**你的所有面板对外只暴露一个 443 端口；Nginx 是这个端口的唯一主人；它按域名/路径把请求分发给内网不同端口上的不同 freqtrade 实例。**

---

## 三、freqtrade 到底是个什么东西：进程、模式与"两个面板"

### 3.1 freqtrade 是一个"常驻进程"

先建立一个正确的模型：**每次 `freqtrade trade` 或 `freqtrade webserver`，都启动一个独立的 Python 进程。** 每个进程：

- 有自己的配置文件（`-c user_data/config/xxx.json`）
- 有自己的端口（`api_server.listen_port`）
- 有自己的数据库（默认 `user_data/tradesv3.sqlite`）
- 跑在独立的 tmux 会话里，谁都不影响谁

在云服务器上，这就是"一个策略一个实例"的样子：

```
tmux 会话: trade-fib   → freqtrade trade -s FibMartingaleStrategy -c config_fib.json
                          → 进程 A，监听 127.0.0.1:8080

tmux 会话: trade-smc   → freqtrade trade -s SMCTradingStrategy -c config_smc.json
                          → 进程 B，监听 127.0.0.1:8081

tmux 会话: webserver   → freqtrade webserver -c config_webserver.json
                          → 进程 C，监听 127.0.0.1:8082
```

**每个进程自带一个 FastAPI Web 服务**（就是 `api_server`）。这就是"面板"的物理存在——**面板不是独立软件，它是每个 freqtrade 进程自带的前端**。所以"每起一个实例就多一个面板"非常自然。

### 3.2 两个"面板"的真相：同一个 FreqUI，两种 RunMode

你前面困惑"为什么 Trade 面板和 webserver 面板看起来一样"，答案很关键：

**Trade 和 Webserver 用的是同一套前端（FreqUI，Vue.js 写的，打包后放在 `freqtrade/rpc/api_server/ui/installed/`）。** 面板外观一样，**但后端能用哪些 API 完全不同**——由进程的 `RunMode` 决定。

在 [webserver.py:218-281](freqtrade/rpc/api_server/webserver.py#L218-L281) 里，每个路由组都挂了一个"模式门卫"依赖：

```python
app.include_router(
    api_trading,
    prefix="/api/v1",
    dependencies=[Depends(http_basic_or_jwt_token), Depends(is_trading_mode)],
)
app.include_router(
    api_backtest,
    prefix="/api/v1",
    dependencies=[Depends(http_basic_or_jwt_token), Depends(is_webserver_mode)],
)
```

`is_trading_mode` 和 `is_webserver_mode`（定义在 [deps.py:68-75](freqtrade/rpc/api_server/deps.py#L68-L75)）会检查 `config["runmode"]`：

```python
def is_webserver_mode(config=Depends(get_config)):
    if config["runmode"] != RunMode.WEBSERVER:
        raise HTTPException(status_code=503, detail="Bot is not in the correct state.")

def is_trading_mode(config=Depends(get_config)):
    if config["runmode"] not in TRADE_MODES:
        raise HTTPException(status_code=503, detail="Bot is not in the correct state.")
```

**所以：一个 Trade 模式的进程，你请求它的 `/api/v1/backtest` 会得到 503（因为这个路由根本没对它开放）；一个 Webserver 模式的进程，你请求 `/api/v1/start`（交易控制）同样 503。** 同一个前端，在不同的进程上，展示出来的功能天然不同——前端拿到什么 API 就显示什么 UI。

### 3.3 Trade 模式（trade 命令）

```bash
freqtrade trade -s FibMartingaleStrategy -c user_data/config/config_fib.json --dry-run
```

- **RunMode**：`RunMode.TRADE`（以及 `LIVE`、`DRY_RUN`，都属于 `TRADE_MODES`）
- **职责**：真正跑策略——订阅行情、算指标、下单、DCA 加仓、止盈止损
- **面板能干什么**：看实时持仓、余额、K 线、强制开平仓、暂停/恢复交易、加黑名单
- **面板不能干什么**：不能跑回测（那是 webserver 专属 API）、不能下载数据、不能选策略切换

所以你在 `freqtrade trade` 的面板里找不到 Backtesting 选项卡，**不是因为缺功能，而是这个进程的模式不允许它响应回测请求**。

### 3.4 Webserver 模式（webserver 命令）

```bash
freqtrade webserver -c user_data/config/config_webserver.json
```

- **RunMode**：`RunMode.WEBSERVER`（入口在 [webserver_commands.py](freqtrade/commands/webserver_commands.py)）
- **职责**：**不带策略、不交易**，是一个纯粹的"控制台/工作站"。它负责：
  - 跑回测（`/api/v1/backtest` 相关路由只在 webserver 模式注册）
  - 下载/管理历史数据
  - 列策略、超参、FreqAI 模型
  - 串联起"一个面板管理多个 Trade 实例"（见第五、六章）
- **面板不能干什么**：不能看实时持仓、不能强制平仓（它自己都不交易）

### 3.5 关键区别对照表

| 维度 | Trade 模式 | Webserver 模式 |
| ---- | ---- | ---- |
| 命令 | `freqtrade trade` | `freqtrade webserver` |
| RunMode | TRADE / LIVE / DRY_RUN | WEBSERVER |
| 是否交易 | ✅ 是 | ❌ 否 |
| 是否跑策略 | ✅ 是 | ❌ 否 |
| 实时持仓/K线/余额 | ✅ 有 | ❌ 无 |
| 强制开平仓/暂停 | ✅ 有 | ❌ 无 |
| 回测 Backtesting | ❌ 无 | ✅ 有 |
| 下载数据 | ❌ 无 | ✅ 有 |
| 列策略/超参 | ❌ 无 | ✅ 有 |
| 管理多个 Trade 实例 | ❌ 不行 | ✅ 核心能力 |
| 典型端口 | 8080 / 8081… | 8082 |
| 典型用途 | 每个策略一个 | 唯一一个，做总控制台 |

**记忆口诀：Trade 模式 = 干活的工人；Webserver 模式 = 工地的调度室。工人各有各的工地，调度室只有一个，负责看所有工地并安排实验（回测）。**

---

## 四、freqtrade API 设计：一个 FastAPI 进程怎么知道自己是哪种模式

### 4.1 API Server 的骨架

freqtrade 的 API 是基于 **FastAPI**（一个 Python Web 框架）构建的，由 **uvicorn**（ASGI 服务器）跑起来。核心类 `ApiServer` 是一个**单例**（见 [webserver.py:117-135](freqtrade/rpc/api_server/webserver.py#L117-L135)），意思是一个进程内只有一个 API Server 实例。

```python
class ApiServer(RPCHandler):
    __instance = None  # 单例
    def __new__(cls, *args, **kwargs):
        if ApiServer.__instance is None:
            ApiServer.__instance = object.__new__(cls)
        return ApiServer.__instance
```

FastAPI 应用在 `__init__` 里创建，然后 `configure_app` 把所有路由挂进去，最后 `start_api` 用 uvicorn 在 `listen_ip_address:listen_port` 上监听。**整个过程就是这个进程的"面板"诞生的过程。**

### 4.2 路由注册：哪些接口"只有交易模式有"

`configure_app`（[webserver.py:202-289](freqtrade/rpc/api_server/webserver.py#L202-L289)）把路由分成几个组，每组有不同的"门卫"：

| 路由文件 | 前缀 | 模式门卫 | 干什么 |
| ---- | ---- | ---- | ---- |
| `api_v1_public` | `/api/v1` | 无 | **公开**接口：`/ping`、`/health` |
| `api_v1` | `/api/v1` | 仅认证 | 版本、日志、配置、市场数据 |
| `api_trading` | `/api/v1` | 认证 + **trade 模式** | 余额、持仓、K线、强制单、黑白名单、锁 |
| `api_webserver` | `/api/v1` | 认证 + **webserver 模式** | 策略列表、交易所列表、超参 |
| `api_backtest` | `/api/v1` | 认证 + **webserver 模式** | 回测的启动/轮询/历史/删除 |
| `api_bg_tasks` | `/api/v1` | 认证 + **webserver 模式** | 后台任务（数据下载、lookahead 分析） |
| `api_pair_history` | `/api/v1` | 认证 + **webserver 模式** | K 线历史查询 |
| `api_download_data` | `/api/v1` | 认证 + **webserver 模式** | 下载历史数据 |
| `api_ws` | `/api/v1` | WebSocket 专用 | 实时推送通道 |
| `router_ui` | `/` | 无 | 托管前端 HTML（必须最后注册） |

**这正是"同一个前端，不同功能"的底层机制。** 前端根据自己能成功调用哪些 API 来渲染哪些菜单。你 `freqtrade trade` 的面板请求 `/api/v1/backtest` 得到 503，前端就隐藏 Backtesting 选项卡。

### 4.3 认证：HTTP Basic 与 JWT

面板不是谁都能进的。认证机制在 [api_auth.py](freqtrade/rpc/api_server/api_auth.py)：

- **HTTP Basic Auth**：每次请求带上 `Authorization: Basic base64(username:password)`。简单但令牌每次都要带。
- **JWT（JSON Web Token）**：先 `POST /api/v1/token/login` 用用户名密码换两个令牌：
  - `access_token`：有效期 **15 分钟**（[api_auth.py:92](freqtrade/rpc/api_server/api_auth.py#L92)），用于访问具体接口
  - `refresh_token`：有效期 **30 天**，用于换新的 access_token
- **门卫函数** `http_basic_or_jwt_token`（[api_auth.py:108](freqtrade/rpc/api_server/api_auth.py#L108)）：二选一，带 JWT 就用 JWT，否则检查 Basic。

```python
def http_basic_or_jwt_token(form_data=..., token=..., api_config=...):
    if token:
        return get_user_from_token(token, api_config["jwt_secret_key"])
    elif form_data and verify_auth(api_config, form_data.username, form_data.password):
        return form_data.username
    raise HTTPException(status_code=401, detail="Unauthorized")
```

**注意 `jwt_secret_key`**：它用来签名 JWT。如果它是默认值（`"super-secret"` / `"somethingRandomSomethingRandom123"`），[webserver.py:314-322](freqtrade/rpc/api_server/webserver.py#L314-L322) 会在日志里警告"别人可能能登录你的 bot"。**生产环境务必改掉这个值。**

### 4.4 WebSocket：实时推送，不是轮询

K 线、持仓、新成交这些数据如果靠前端每秒轮询 HTTP，会非常费。freqtrade 用了 **WebSocket**（常驻双向连接）：

- 前端连 `ws://127.0.0.1:8080/api/v1/message/ws?token=xxx`
- 服务端通过 `MessageStream`（[ws/message_stream.py](freqtrade/rpc/api_server/ws/message_stream.py)）把行情、成交通知**主动推**给前端
- 连接是长连接，一次建立，持续收发

在 [api_ws.py:116](freqtrade/rpc/api_server/api_ws.py#L116)：

```python
@router.websocket("/message/ws")
async def message_endpoint(...):
    ...
```

WebSocket 的认证走 `validate_ws_token`（[api_auth.py:56](freqtrade/rpc/api_server/api_auth.py#L56)）：要么用配置的 `ws_token`，要么用 JWT。**这也解释了为什么 Nginx 里必须配那三行 `Upgrade` / `Connection: upgrade` 头**——没有它们，WebSocket 握手过不去，面板的实时部分会失灵（比如 K 线不更新、成交不出现）。

### 4.5 CORS：浏览器为什么拦你

**CORS（Cross-Origin Resource Sharing，跨域资源共享）** 是浏览器的安全策略。它的规则是：

> 网页里用 JS 去请求**另一个源**（协议/域名/端口任一不同就算跨域）的数据时，浏览器会先拦截，除非**被请求的那一方**明确说"我允许这个来源访问我"。

举例：你的面板开在 `http://127.0.0.1:8082`（webserver 模式），它去读 bot 的 `http://127.0.0.1:8081` 的数据。对浏览器来说，**8082 和 8081 是两个不同的源（端口不同）**，这就是跨域。浏览器会先发一个"预检请求"（OPTIONS），询问 8081："我（来自 8082）能读你吗？"8081 必须在响应头里带上：

```
Access-Control-Allow-Origin: http://127.0.0.1:8082
```

浏览器才会放行真正的请求。

**freqtrade 已经内置了 CORS 中间件**（[webserver.py:283-289](freqtrade/rpc/api_server/webserver.py#L283-L289)）：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=config["api_server"].get("CORS_origins", []),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

它读的就是配置文件里的 `api_server.CORS_origins`。**你要做的只是把"面板的源"填进去。**

### 4.6 一个完整的 API 调用长什么样

假设你在命令行手动调一个 Trade 模式的 bot（8080）：

```bash
# 1. 登录换 JWT
curl -s -u freqtrader:SuperSecurePassword \
  -X POST http://127.0.0.1:8080/api/v1/token/login
# → {"access_token":"eyJ...","refresh_token":"eyJ..."}

# 2. 带上 access_token 查余额
curl -s http://127.0.0.1:8080/api/v1/balance \
  -H "Authorization: Bearer eyJ..."
# → {"balances":[...],"total":"5000.0",...}

# 3. 公开接口不用令牌
curl -s http://127.0.0.1:8080/api/v1/ping
# → {"status":"pong"}
```

如果第 2 步你在一个 **webserver 模式的 8082** 上做，`/balance` 会返回 503，因为 `api_trading` 路由根本没在 webserver 模式注册。

---

## 五、一个面板管理多个策略：FreqUI 多 Bot 机制

### 5.1 核心思想：前端直连，面板只是"接线员"

很多人以为"webserver 面板会去后端汇总所有 bot 的数据"。**不是。** 真相是（从你本地 FreqUI 源码 [ftbotwrapper-*.js](freqtrade/rpc/api_server/ui/installed/assets/ftbotwrapper-D87AOxLc.js) 里可以确认）：

> **FreqUI 前端（跑在浏览器里）直接、单独地连接每一个 bot 的 API。webserver 面板本身不中转业务数据，它只负责"记住每个 bot 的地址和登录信息"，然后前端各自去连。**

证据就在打包的 JS 里：`addBot({botName, botId, botUrl, sortId})` 把每个 bot 的 URL 交给前端，前端给每个 bot 建立独立的连接对象（源码里的 `botStores`、`baseWsUrl`、`baseUrl`），并且**自动把 `http://` 转成 `ws://`、`https://` 转成 `wss://`** 用于 WebSocket。

所以 webserver 面板的定位是一个**"遥控器+地址簿"**：它告诉你有哪些 bot、谁在线、各自什么状态，并让你点一下就能切过去看那个 bot 自己的数据。

### 5.2 数据流全景图

```
浏览器（FreqUI 前端）
   │
   ├──① https://panel.jianglang.online  （webserver 面板，做回测/管理）
   │
   ├──② https://bot1.jianglang.online   （直连 Trade bot #1 的 API + WS）
   ├──③ https://bot2.jianglang.online   （直连 Trade bot #2 的 API + WS）
   │
   │   注意：②③ 是浏览器【绕过 webserver】直连各 bot 的，
   │   所以每个 bot 都必须允许"面板页面这个来源"访问自己 → CORS
   ▼
Nginx（443）—— 按子域名分流
   ├── ① → 127.0.0.1:8082  (webserver)
   ├── ② → 127.0.0.1:8080  (Trade bot #1)
   └── ③ → 127.0.0.1:8081  (Trade bot #2)
```

### 5.3 Add Bot 到底发生了什么

你在 webserver 面板点 "Add bot" 填 `http://127.0.0.1:8080` + 用户名/密码，实际发生：

1. 前端记录这个 `botUrl` 和凭据（存浏览器 localStorage）
2. 前端用这些凭据向 `http://127.0.0.1:8080/api/v1/token/login` 登录，拿到该 bot 自己的 JWT
3. 前端给这个 bot 建立独立的 API 客户端 + WebSocket 连接
4. 面板顶部出现一个新 bot 标签，点它就能看这个 bot 的持仓/余额/K线

**每个 bot 是一个独立登录、独立连接、独立刷新的对象。** 面板可以勾选多个 bot，在总览页把多个 bot 的余额、持仓、每日盈亏**聚合成一张表**（源码里 `allDailyStatsSelectedBots`、`allProfit`、`allBalance` 这些聚合器就是干这个的）。

### 5.4 为什么一定需要 CORS_origins

正因为前端是"浏览器直连各 bot"，而 bot 可能跑在**不同的端口甚至不同的域名**上，浏览器同源策略就介入了。所以：

- **每个 Trade bot 的 config 里**，`api_server.CORS_origins` 必须包含"面板页面所在的源"。
- 如果面板和 bot 都在 `127.0.0.1` 但端口不同，CORS_origins 就是 `["http://127.0.0.1:8082"]`。
- 如果走 Nginx + 域名，面板页面实际是 `https://panel.jianglang.online`，那 CORS_origins 就填 `["https://panel.jianglang.online"]`。

> 反过来想：**如果你把每个 bot 的监听地址和面板设成同一个 origin（同域名同端口），就不需要 CORS。** 但由于 freqtrade 每个进程只能占一个端口，同域名同端口做不到，所以 CORS 几乎是必配项。

### 5.5 bot 地址存哪：localStorage 的秘密

你换台电脑/换个浏览器打开面板，之前 Add 的 bot 全没了——因为**地址簿存在浏览器的 localStorage 里，不存服务器**。源码里能看到 `localStorage.setItem(..., ...)` 在读写当前选中的 bot。

这带来的实际影响：

- **不共享**：你和同事用不同电脑，各配各的地址簿。
- **不持久**：清浏览器数据就没了，需要重新 Add。
- **不影响 bot**：bot 本身在服务器上照跑，只是"遥控器上的按钮"没了。

如果你想让"地址簿"跟随服务器（团队共享），那就得靠**前端配置持久化**或自己维护一个清单——这是社区常讨论的进阶话题，freqtrade 本身目前以 localStorage 为主。

### 5.6 一栏对比：你说的"一个实例一个面板" vs 多 Bot 方案

| | 现在的方案（每实例一个 Trade 面板） | 多 Bot 方案（webserver + Add Bot） |
| ---- | ---- | ---- |
| 每个策略 | 各起一个 `freqtrade trade` | 一样，各起一个 `freqtrade trade` |
| 面板 | 每个 bot 自带一个，互相独立 | 一个 webserver 总面板，全部串起来 |
| 切换策略 | 换 URL / 换端口 | 面板顶部点一下标签 |
| 聚合看盈亏 | 不行 | ✅ 勾选多 bot 看总览 |
| 在面板跑回测 | ❌ | ✅ webserver 专属 |
| 需要额外 CORS | 不需要 | ✅ 每个 bot 配 CORS_origins |

**结论：多 Bot 方案并不改变"一策略一实例"的事实，它只是加了一个"总面板"把这些实例的管理入口收拢到一个地方。** 你的量化程序该怎么跑还是怎么跑。

---

## 六、完整落地：云服务器上两个域名、一个总面板、管理多个策略

> 你有两个独立域名，天然是"一个总面板 + 一个直达入口"的最佳布局，比子路径/子域名方案都简单：
> - **jianglangbacktest.online** → webserver 总面板（回测 + 多 bot 管理）——反正它本来就是回测面板
> - **jianglang.online** → 某一个 Trade 实例的直达面板（比如主力策略）

### 6.1 目标架构

```
公网（浏览器）
   │
   ▼
Nginx :443（对外只开 443 和 22，其余端口全关）
   ├── jianglang.online        → 127.0.0.1:8080   Trade bot #1 直达面板（Fib）
   └── jianglangbacktest.online → 127.0.0.1:8082  webserver 总面板
                                                    ├── 自带回测
                                                    └── Add bot 管理所有策略实例
```

浏览器里打开总面板 `https://jianglangbacktest.online`，Add 两个 bot：
`http://127.0.0.1:8080`（Fib）和 `http://127.0.0.1:8081`（SMC）。之后所有策略在总面板上切换查看，`jianglang.online` 作为 Fib 的快速直达入口（不打开也不影响，策略照跑）。

### 6.2 每个策略实例的配置

以 `config_fib.json` 为例：

```jsonc
{
    "api_server": {
        "enabled": true,
        "listen_ip_address": "127.0.0.1",   // 只监听本机，公网敲不到
        "listen_port": 8080,                // 每个实例不同：8080 / 8081
        "username": "freqtrader",
        "password": "换一个强密码",
        "jwt_secret_key": "换一个长随机串",   // 必须改默认值！
        "CORS_origins": [
            "https://jianglangbacktest.online",  // 总面板页面的源（跨域必须）
            "https://jianglang.online"            // 直达面板的源（可选）
        ]
    },
    "exchange": { /* 你自己的交易所配置 */ },
    "dry_run": true
}
```

`config_smc.json` 复制一份，端口改 8081，`CORS_origins` 同上面两个域名。

### 6.3 Webserver 总面板配置

`config_webserver.json`：

```jsonc
{
    "max_open_trades": 0,            // 面板不交易
    "api_server": {
        "enabled": true,
        "listen_ip_address": "127.0.0.1",
        "listen_port": 8082,
        "username": "panel",
        "password": "面板强密码",
        "jwt_secret_key": "又一个长随机串"
    }
}
```

面板本身的 CORS 不需要配（它是被浏览器直接打开的那个"源"，不是被访问方）。

### 6.4 tmux 与进程管理

每个实例一个 tmux 会话，互不影响，断连不挂：

```bash
# 一个命令起一个会话
tmux new-session -d -s fib "cd ~/code/freqtrade && conda activate freqtrade && freqtrade trade -s FibMartingaleStrategy -c user_data/config/config_fib.json --dry-run 2>&1 | tee -a ~/logs/fib.log"
tmux new-session -d -s smc "cd ~/code/freqtrade && conda activate freqtrade && freqtrade trade -s SMCTradingStrategy -c user_data/config/config_smc.json --dry-run 2>&1 | tee -a ~/logs/smc.log"
tmux new-session -d -s panel "cd ~/code/freqtrade && conda activate freqtrade && freqtrade webserver -c user_data/config/config_webserver.json 2>&1 | tee -a ~/logs/panel.log"

# 查看
tmux ls
tmux attach -t fib
# 重启单个（杀会话再起）
tmux kill-session -t fib && tmux new-session -d -s fib "..."
```

`tee -a` 把日志落盘，方便排障时看 `tail -f`。

### 6.5 Nginx 配置（双域名）

两个域名各自一个 `server` 块，互不干扰。**别忘记 WebSocket 那三行 `Upgrade` 头，否则面板实时数据会断。**

```nginx
# ── jianglang.online → Fib 直达面板 ──
server {
    listen 443 ssl;
    server_name jianglang.online;
    ssl_certificate     /etc/nginx/ssl/jianglang.online.pem;
    ssl_certificate_key /etc/nginx/ssl/jianglang.online.key;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

# ── jianglangbacktest.online → webserver 总面板（回测 + 多 bot）──
server {
    listen 443 ssl;
    server_name jianglangbacktest.online;
    ssl_certificate     /etc/nginx/ssl/jianglangbacktest.online.pem;
    ssl_certificate_key /etc/nginx/ssl/jianglangbacktest.online.key;

    location / {
        proxy_pass http://127.0.0.1:8082;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

# ── HTTP 全部跳 HTTPS（可选）──
server {
    listen 80;
    server_name jianglang.online jianglangbacktest.online;
    return 301 https://$host$request_uri;
}
```

> 如果你以后要加第三个策略实例，无需再动 Nginx —— 直接在总面板里 Add 一个 bot 就行（前提是那个实例的端口能被本机访问到，且 `CORS_origins` 里有 `jianglangbacktest.online`）。

### 6.6 安全要点

1. **改 `jwt_secret_key`**：默认值等于把大门钥匙挂在门上。
2. **改用户名密码**：不要用 `freqtrader`/`SuperSecurePassword` 默认组合。
3. **只放行 22 / 443**：安全组里其他端口全关，freqtrade 都监听 `127.0.0.1`。
4. **面板不开公网也行**：如果只在服务器本机用 tmux，可以不开公网，直接在服务器上用 curl 测。但浏览器是在你自己电脑上，所以要经 Nginx 转发。
5. **数据库备份**：`user_data/tradesv3.sqlite` 定期备份，尤其是实盘前。
6. **HTTPS 不要跳过**：明文 HTTP 下，Basic Auth 的密码和 JWT 都会被嗅探。

---

## 七、常见坑与排障

| 症状 | 原因 | 解法 |
| ---- | ---- | ---- |
| 浏览器打开面板一直转圈，K 线不更新 | Nginx 没配 `Upgrade`/`Connection`，WebSocket 断了 | 补上 [6.5](#65-nginx-配置单域名多子路径) 那三行 |
| 面板能开，但某个 bot 标签显示 "Login info expired" | 该 bot 的 JWT 过期（15 分钟），且 refresh 失败 | 重新 Add bot；或检查 bot 的 `jwt_secret_key` 与面板端缓存是否一致 |
| Add bot 后数据加载不出来，控制台报 CORS 错误 | bot 的 `CORS_origins` 没配面板的来源 | 在 bot 的 config 里补 `CORS_origins` |
| 请求 `/api/v1/backtest` 返回 503 | 你在 Trade 模式进程上调回测 API | 回测 API 只在 webserver 模式注册 |
| 请求 `/api/v1/start` 返回 503 | 你在 webserver 进程上调交易控制 | 交易控制只在 trade 模式注册 |
| 日志警告 "jwt_secret_key seems to be default" | 没改默认密钥 | 改成随机长串 |
| 公网还是连不上 8080 | 安全组没放行，或 freqtrade 只监听 127.0.0.1 | 走 Nginx 转发，别直接暴露 8080 |
| 换浏览器后 Add 的 bot 全没了 | 地址簿在 localStorage | 重新 Add（bot 本身没挂） |

**排障三板斧**：

```bash
# 1. 确认进程活着、端口在听
ss -tlnp | grep -E "8080|8081|8082"

# 2. 直接本机测 API（绕过 Nginx）
curl -s http://127.0.0.1:8080/api/v1/ping

# 3. 看日志
tail -f ~/logs/fib.log
```

---

## 八、进阶：还能怎么玩

学完这套，你已经理解了 freqtrade 的 API 骨架。往上延伸的空间很大：

- **REST API 编程**：完全可以在脚本里 `curl` 或写 Python 调 freqtrade API，做自动化运维（定时拉余额、异常时强制平仓、推送到自己的监控面板）。
- **WebSocket 实时消费**：自己写个客户端连 `/api/v1/message/ws`，把成交、新 K 线实时接进自建系统（比如 Grafana）。
- **多机部署**：把不同策略分散到不同服务器，webserver 面板 Add 各服务器的 bot（CORS 填各自来源），实现全局总览。
- **给面板加一层自己的封装**：Nginx 上再加 Basic Auth 或 IP 白名单，双保险。
- **理解并复用这套 FastAPI 模式**：FreqUI 前端 + FastAPI 后端 + JWT + WebSocket 这套组合，是你自己开发小工具时的现成模板。

---

> **一句话总结**：端口是进程的门，监听地址决定谁敲得开门，Nginx 是唯一的公共前台，RunMode 决定每个进程对外能干什么，FreqUI 前端直连每个 bot 而 webserver 只当接线员，CORS 就是浏览器问"这个来源能不能进"的那句许可。把这五层串起来，你就能自如地在云服务器上管理任意数量的策略实例了。
