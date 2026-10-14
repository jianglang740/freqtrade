#!/bin/bash
# 启动多个 freqtrade 模拟盘，用于在 FreqDroid 里测试「机器人总览」页。
# 每个实例：独立端口 / 钱包 / 策略 / 交易对 / 数据库 / 数据目录。
FT=/opt/anaconda3/envs/freqtrade/bin/freqtrade
ROOT=/Users/clinking/Documents/GitHub/freqtrade
PORTS=(8080 8081 8082 8083 8084)

cd "$ROOT" || exit 1

# 币安要走代理，隧道断了先补上
if ! nc -z 127.0.0.1 1080 2>/dev/null; then
    echo "socks5 1080 不通，尝试恢复 ssh 隧道…"
    ssh -fN -o ConnectTimeout=8 -o ServerAliveInterval=30 quant-proxy && sleep 2
    nc -z 127.0.0.1 1080 2>/dev/null || { echo "隧道恢复失败，币安连不上，退出。"; exit 1; }
fi

for port in "${PORTS[@]}"; do
    if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  $port 已在运行，跳过"
        continue
    fi
    cfg="user_data/config/config_multi_${port}.json"
    [ -f "$cfg" ] || { echo "  $port 缺配置 $cfg，跳过"; continue; }
    nohup "$FT" trade -c "$cfg" > "user_data/logs/bot_${port}.stdout.log" 2>&1 &
    echo "  $port 已启动 (pid $!)"
    sleep 20          # 错开，避免同时冲击币安接口
done

echo
echo "全部启动完毕。用 status.sh 看状态。"
