#!/usr/bin/env python3
"""
采集涨停封单数据 — 交易时段每5分钟运行一次
输出到 /tmp/ban_order.log
工作时间：周一至周五 9:30-11:30, 13:00-15:00
"""
import subprocess, time, datetime, os, sys

LOG_FILE = "/tmp/ban_order.log"
SCRIPT_DIR = os.path.expanduser("~/astock/scripts/ban_order_collector.py")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    # 追加到日志文件 (unbuffered)
    with open(LOG_FILE, "a", buffering=1) as f:
        f.write(line + "\n")
    # 也打印到stdout (给hermes看)
    print(line, flush=True)

def is_trading_time():
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)

def run_once():
    try:
        result = subprocess.run(
            ["python3", SCRIPT_DIR, "--once"],
            capture_output=True, text=True, timeout=60
        )
        output = (result.stdout or "").strip() + (" " + result.stderr.strip() if result.stderr.strip() else "")
        log(output)
        return output
    except subprocess.TimeoutExpired:
        log("⚠️ 采集超时")
    except Exception as e:
        log(f"❌ 错误: {e}")

def main():
    log("🚀 涨停封单采集守护进程启动")
    log(f"   脚本: {SCRIPT_DIR}")
    log(f"   间隔: 5分钟")
    
    while True:
        try:
            if is_trading_time():
                run_once()
            else:
                log("非交易时段，跳过")
        except Exception as e:
            log(f"❌ 循环错误: {e}")
        
        time.sleep(300)  # 5分钟

if __name__ == "__main__":
    main()
