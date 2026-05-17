#!/usr/bin/env python3
"""每分钟采集一次涨停封单数据并保存到数据库"""
import subprocess, sys, time
from datetime import datetime

while True:
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # 判断是否在交易时段 (9:25-15:00)
    h, m = now.hour, now.minute
    if h < 9 or (h == 9 and m < 25) or h >= 15:
        print(f"[{ts}] 非交易时段，休眠60秒")
        time.sleep(60)
        continue
    
    try:
        result = subprocess.run(
            [sys.executable, "-u", "scripts/ban_order_collector.py", "--once"],
            capture_output=True, text=True, timeout=120, cwd="/home/ubuntu/astock"
        )
        output = result.stdout.strip()
        if result.stderr:
            output += f" ERR:{result.stderr.strip()}"
        print(f"[{ts}] {output}")
    except subprocess.TimeoutExpired:
        print(f"[{ts}] ⚠ 采集超时")
    except Exception as e:
        print(f"[{ts}] ⚠ 采集异常: {e}")
    
    sys.stdout.flush()
    time.sleep(60)
