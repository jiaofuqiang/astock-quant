#!/usr/bin/env python3
# 【15:00 当日/隔日/多日获利报告】统计已执行交易的盈亏
import json, os, sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PROFIT_DIR, report_filename

TODAY = date.today().isoformat()

# 加载交易记录（从cloud_trading或持仓文件）
trade_log = os.path.expanduser('~/V2board/data/trade_records.json')

today_buy = []
today_sell = []
yest_sell = []
multiday_sell = []

if os.path.exists(trade_log):
    with open(trade_log) as f:
        records = json.load(f)
    
    for r in records:
        buy_date = r.get('buy_date', '')
        sell_date = r.get('sell_date', '')
        profit_pct = r.get('profit_pct', 0)
        
        if buy_date == TODAY:
            today_buy.append(r)
        if sell_date == TODAY:
            today_sell.append(r)
        if sell_date and buy_date < TODAY and sell_date == TODAY:
            yest_sell.append(r)
        if sell_date and (TODAY - buy_date) >= '3':
            multiday_sell.append(r)

report = {
    'type': 'profit_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'today_buy': {
        'count': len(today_buy),
        'stocks': [f"{r.get('name','')}({r.get('code','')})" for r in today_buy],
        'total_cost': sum(r.get('buy_price', 0) * r.get('shares', 0) for r in today_buy),
    },
    'today_sell': {
        'count': len(today_sell),
        'profit_avg': sum(r.get('profit_pct', 0) for r in today_sell) / len(today_sell) if today_sell else 0,
        'total_profit': sum(r.get('profit_pct', 0) * r.get('shares', 0) for r in today_sell),
    },
    'yesterday_buy_today_sell': {
        'count': len(yest_sell),
        'profit_avg': sum(r.get('profit_pct', 0) for r in yest_sell) / len(yest_sell) if yest_sell else 0,
    },
    'multiday': {
        'count': len(multiday_sell),
    }
}

profit_file = report_filename(PROFIT_DIR, 'profit')
with open(profit_file, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"  获利报告已保存: {profit_file}")

print(f"\n=== 今日获利报告 ===")
print(f"今日买入: {report['today_buy']['count']}只")
print(f"今日卖出: {report['today_sell']['count']}只, 均盈{report['today_sell']['profit_avg']:.1f}%")

print(f"\n[15:00 获利报告完成]")