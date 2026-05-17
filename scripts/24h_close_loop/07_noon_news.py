#!/usr/bin/env python3
"""
【12:50 中午消息面+下午交易计划】采集午间消息 → 更新交易计划

包含下午打板、低位潜伏策略
"""
import sys, os, json
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 12:50 中午消息面 {TODAY}")

# 加载bundle中的最新news
bundle = load_json_or_empty(BUNDLE_JSON)
news = bundle.get('news', {}) if bundle else {}
articles = news.get('articles', []) if isinstance(news, dict) else []

# 筛选今日中午前后新增的消息（简化处理）
noontime = datetime.now().strftime('%H:%M')
print(f"  中午 {noontime} | bundle中有{len(articles)}条消息")

# 加载早盘报告
early = load_json_or_empty(os.path.join(REPORTS_DIR, 'early_report', f'early_{TODAY}.json'))
afternoon_adjust = early.get('afternoon_adjustment', '震荡延续') if early else '震荡延续'
print(f"  下午预测: {afternoon_adjust}")

# 下午交易计划
afternoon_plan = {
    'type': 'noon_plan',
    'date': TODAY,
    'time': noontime,
    'prediction': afternoon_adjust,
    'actions': [
        {'time': '13:00-14:00', 'strategy': '监控板块联动，板块爆发≥3涨停跟风买入'},
        {'time': '14:00-14:30', 'strategy': '尾盘潜伏，缩量<0.7+ST信号优先'},
        {'time': '14:30-14:55', 'strategy': '盘尾决策，轻仓买入明日预期'},
    ],
}

os.makedirs(os.path.join(REPORTS_DIR, 'noon_plan'), exist_ok=True)
with open(os.path.join(REPORTS_DIR, 'noon_plan', f'noon_{TODAY}.json'), 'w', encoding='utf-8') as f:
    json.dump(afternoon_plan, f, ensure_ascii=False, indent=2)
print(f"[24h] 12:50 中午消息面完成 ✅")
