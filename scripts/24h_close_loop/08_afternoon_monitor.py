#!/usr/bin/env python3
"""
【13:00-15:00 下午监控】监控板块联动 + 个股买卖信号

板块爆发优先（同板块≥3涨停跟风）+ 缩量隔夜溢价
"""
import sys, os, json, urllib.request
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 13:00 下午监控 {TODAY}")
print("="*60)

# 实际监控由 cloud_trading.py 和 three_funds_scan.py 实现
# 本脚本只做状态汇总

bundle = load_json_or_empty(BUNDLE_JSON)
scan_text = bundle.get('scan_data', '') if bundle else ''
buy_signal = bundle.get('buy_signal', '') if bundle else ''

# 解析scan_data中的涨停数
limit_count = 0
if isinstance(scan_text, str):
    import re
    # 统计🔥涨停标记
    limit_count = len(re.findall(r'🔥涨停板', scan_text))

print(f"  目前涨停: {limit_count}只")
print(f"  buy_signal存在: {'✅' if buy_signal else '❌'}")

# 下午交易建议
if limit_count >= 3:
    suggestion = "板块已爆发，关注跟风买入机会（龙≥3板+开≥3%）"
elif limit_count >= 1:
    suggestion = "少量涨停，等待板块确认后再跟"
else:
    suggestion = "无涨停，观望为主，可埋伏尾盘"

print(f"  建议: {suggestion}")

report = {
    'type': 'afternoon_monitor',
    'date': TODAY,
    'time': datetime.now().strftime('%H:%M'),
    'limit_count': limit_count,
    'has_buy_signal': bool(buy_signal),
    'suggestion': suggestion,
}

os.makedirs(os.path.join(REPORTS_DIR, 'afternoon_monitor'), exist_ok=True)
with open(os.path.join(REPORTS_DIR, 'afternoon_monitor', f'monitor_{TODAY}.json'), 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"[24h] 13:00 下午监控完成 ✅")
