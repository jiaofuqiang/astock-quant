#!/usr/bin/env python3
"""
24h闭环交易系统 — 主调度器
用法: python3 run.py [step_number]
  1=15:00盘后报告  2=17:35预测报告  3=08:00消息面+交易计划
  4=09:15竞价      5=09:30开盘执行  6=11:30早盘报告
  7=12:50中午消息   8=13:00下午监控  9=15:00获利报告  10=15:10升级报告
  或 run.py all 跑全部
"""
import sys, os, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))

STEPS = {
    '1': ('01_afternoon_report.py', '15:00 盘后报告'),
    '2': ('02_prediction_report.py', '17:35 预测报告'),
    '3': ('03_morning_news_and_plan.py', '08:00 消息面+交易计划'),
    '4': ('04_auction_monitor.py', '09:15 竞价分析'),
    '5': ('05_open_execute.py', '09:30 开盘执行'),
    '6': ('06_early_report.py', '11:30 早盘报告'),
    '7': ('07_noon_news.py', '12:50 中午消息面'),
    '8': ('08_afternoon_monitor.py', '13:00 下午监控'),
    '9': ('09_profit_report.py', '15:00 获利报告'),
    '10': ('10_upgrade_report.py', '15:10 升级报告'),
}

def run_step(key):
    if key not in STEPS:
        print(f"错误: 未知步骤 {key}, 可用: {','.join(STEPS.keys())}")
        return False
    script, name = STEPS[key]
    path = os.path.join(BASE, script)
    print(f"\n{'='*50}")
    print(f"执行步骤 {key}: {name}")
    print(f"{'='*50}")
    r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=120)
    if r.stdout: print(r.stdout)
    if r.stderr: print(f"[STDERR]\n{r.stderr[:500]}")
    if r.returncode != 0:
        print(f"❌ 步骤{key}失败 (exit={r.returncode})")
    else:
        print(f"✅ 步骤{key}完成")
    return r.returncode == 0

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print("用法: python3 run.py [步骤号|all]")
        print("  步骤: 1-10")
        sys.exit(1)

    if args[0] == 'all':
        for k in sorted(STEPS.keys()):
            run_step(k)
    else:
        for a in args:
            if a in STEPS:
                run_step(a)
            else:
                print(f"跳过未知步骤: {a}")
