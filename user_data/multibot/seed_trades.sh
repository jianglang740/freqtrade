#!/bin/bash
# 给每个机器人手动开几笔仓，让「总览」页上各张卡片的数据区分开。
# 干跑盘 + 市价单，没有真实风险。格式：端口|交易对|方向
U=freqtrader; P=SuperSecurePassword
SEEDS=(
  "8080|BTC/USDT:USDT|long"   "8080|ETH/USDT:USDT|long"
  "8081|BTC/USDT:USDT|short"  "8081|ETH/USDT:USDT|long"   "8081|SOL/USDT:USDT|long"
  "8082|BTC/USDT:USDT|long"   "8082|ETH/USDT:USDT|long"   "8082|BNB/USDT:USDT|short"
  "8083|ETH/USDT:USDT|long"   "8083|SOL/USDT:USDT|short"  "8083|XRP/USDT:USDT|long"
  "8084|BTC/USDT:USDT|long"   "8084|ETH/USDT:USDT|short"  "8084|ADA/USDT:USDT|long"
)
for s in "${SEEDS[@]}"; do
    IFS='|' read -r port pair side <<< "$s"
    tok=$(curl -s -m 5 -X POST "http://127.0.0.1:$port/api/v1/token/login" -u "$U:$P" \
          | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
    [ -z "$tok" ] && { echo "  $port 登录失败"; continue; }
    r=$(curl -s -m 15 -X POST "http://127.0.0.1:$port/api/v1/forceenter" \
         -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' \
         -d "{\"pair\":\"$pair\",\"side\":\"$side\"}")
    if echo "$r" | grep -q '"trade_id"'; then
        echo "  $port  $pair  $side  已开仓"
    else
        echo "  $port  $pair  $side  失败: $(echo "$r" | head -c 150)"
    fi
    sleep 2
done
