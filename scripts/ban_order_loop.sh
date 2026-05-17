#!/bin/bash
# 盘中每5分钟采集涨停封单数据
# 运行方式: nohup bash scripts/ban_order_loop.sh &
# 运行时间: 交易日 09:30 - 15:00

cd ~/astock
while true; do
    NOW=$(date +%H%M)
    # 交易时段检查: 09:30-11:30, 13:00-15:00
    if [ "$NOW" -ge "0930" ] && [ "$NOW" -le "1130" ] || [ "$NOW" -ge "1300" ] && [ "$NOW" -le "1500" ]; then
        python3 scripts/ban_order_collector.py --once 2>&1 | ts '[%H:%M:%S]'
    fi
    sleep 300  # 5分钟
done
