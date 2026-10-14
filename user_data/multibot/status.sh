#!/bin/bash
# 查看 5 个模拟盘的运行状态（机器人 / 策略 / 状态 / 持仓数 / 余额 / 累计盈亏）
U=freqtrader; P=SuperSecurePassword
printf "  %-6s %-7s %-16s %-19s %-8s %-5s %-11s %-9s %s\n" \
       端口 PID 机器人 策略 状态 持仓 余额 累计盈亏 胜负
printf "  %s\n" "────────────────────────────────────────────────────────────────────────────────────────────────"
for port in 8080 8081 8082 8083 8084; do
    pid=$(lsof -nP -iTCP:$port -sTCP:LISTEN -t 2>/dev/null | head -1)
    [ -z "$pid" ] && { printf "  %-6s 没在跑\n" "$port"; continue; }

    tok=$(curl -s -m 5 -X POST "http://127.0.0.1:$port/api/v1/token/login" -u "$U:$P" \
          | jq -r '.access_token // empty')
    [ -z "$tok" ] && { printf "  %-6s %-7s  API 无应答\n" "$port" "$pid"; continue; }
    A="Authorization: Bearer $tok"

    cfg=$(curl -s -m 8 "http://127.0.0.1:$port/api/v1/show_config" -H "$A")
    st=$( curl -s -m 8 "http://127.0.0.1:$port/api/v1/status"      -H "$A")
    bal=$(curl -s -m 8 "http://127.0.0.1:$port/api/v1/balance"     -H "$A")
    pf=$( curl -s -m 8 "http://127.0.0.1:$port/api/v1/profit"      -H "$A")

    printf "  %-6s %-7s %-16s %-19s %-8s %-5s %-11s %-9s %s\n" \
        "$port" "$pid" \
        "$(jq -r '.bot_name // "?"' <<<"$cfg")" \
        "$(jq -r '.strategy // "?"' <<<"$cfg")" \
        "$(jq -r '.state // "?"'    <<<"$cfg")" \
        "$(jq -r 'if type=="array" then length else "?" end' <<<"$st")" \
        "$(jq -r '.total // 0 | .*100|round/100' <<<"$bal")" \
        "$(jq -r '(.profit_closed_percent // 0) | .*100|round/100 | if .>=0 then "+\(.)%" else "\(.)%" end' <<<"$pf")" \
        "$(jq -r '.winrate // 0 | .*10|round/10 | "\(.)%"' <<<"$pf")"
done
