#!/usr/bin/env python3
"""每分钟采集一次涨停封单数据（交易时段内持续运行）
写入日志文件而非stdout，避免后台进程缓冲问题"""
import subprocess, sys, time, os
from datetime import datetime

BASE = os.path.expanduser("~/astock")
LOG = os.path.join(BASE, "logs/ban_collector_loop.log")
os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {msg}\n")

log("=== 涨停封单循环采集启动 ===")

while True:
    now = datetime.now()
    hms = now.strftime("%H:%M:%S")
    hm = now.strftime("%H:%M")
    
    # 交易时段 9:30-15:00
    if "09:30" <= hm <= "15:00":
        try:
            proc = subprocess.Popen(
                ["python3", "scripts/ban_order_collector.py", "--once"],
                cwd=BASE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            out, _ = proc.communicate(timeout=30)
            output = out.decode().strip()
            if output:
                log(f"{output}")
                print(f"[{hms}] {output}", flush=True)
            else:
                log(f"采集完成(无输出)")
        except Exception as e:
            log(f"采集异常: {e}")
        
        time.sleep(300)
    else:
        time.sleep(300)  # 非交易时段每5分钟检查一次
