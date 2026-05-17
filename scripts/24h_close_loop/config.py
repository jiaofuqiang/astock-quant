#!/usr/bin/env python3
"""24h闭环系统配置文件"""
import os
from datetime import date, datetime, timedelta

HOME = os.path.expanduser('~')
BASE = os.path.join(HOME, 'astock', 'scripts', '24h_close_loop')
REPORTS_DIR = os.path.join(BASE, 'reports')
AFTERNOON_DIR = os.path.join(REPORTS_DIR, 'afternoon_report')
PREDICTION_DIR = os.path.join(REPORTS_DIR, 'prediction_report')
NEWS_DIR = os.path.join(REPORTS_DIR, 'news_report')
PLAN_DIR = os.path.join(REPORTS_DIR, 'trade_plan')
PROFIT_DIR = os.path.join(REPORTS_DIR, 'profit_reports')
UPGRADE_DIR = os.path.join(REPORTS_DIR, 'upgrade_report')
EXPERIENCE_DIR = os.path.join(REPORTS_DIR, 'experience')

# 数据库
KLINE_DB = os.path.join(HOME, 'astock', 'data', 'kline_cache.db')
SECTOR_DB = os.path.join(HOME, 'astock', 'data', 'sector_indexes.db')
LIMIT_DB = os.path.join(HOME, 'astock', 'data', 'daily_limit_data.db')
LHB_DB = os.path.join(HOME, 'astock', 'data', 'lhb.db')

# 作战面板数据
V2BOARD = os.path.join(HOME, 'V2board')
BUNDLE_JSON = os.path.join(V2BOARD, 'dashboard_bundle.json')

# 三资金合力最新扫描
SCAN_JSON = '/dev/shm/three_funds_latest.json'
if not os.path.exists(SCAN_JSON):
    SCAN_JSON = os.path.join(V2BOARD, 'scan_data.txt')

# 十大流通股数据
HOLDER_NEW = os.path.join(HOME, 'astock', 'data', 'holder_new.db')

def today():
    return date.today().isoformat()

def yesterday():
    return (date.today() - timedelta(days=1)).isoformat()

def report_filename(directory, prefix):
    d = today()
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, f'{prefix}_{d}.json')

def yest_report_filename(directory, prefix):
    d = yesterday()
    return os.path.join(directory, f'{prefix}_{d}.json')

def load_json_or_empty(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return __import__('json').load(f)
        except:
            pass
    return {}

# 买入优先级（基于11289次修正回测）
BUY_PRIORITY = [
    {'name': '龙≥5+开≥3%', 'dragon_min': 5, 'open_min': 3.0, 'score': 100, 'expected': '+7.6%', 'profitable': '70%'},
    {'name': '开≥5%+龙≥3', 'dragon_min': 3, 'open_min': 5.0, 'score': 90, 'expected': '+7.0%', 'profitable': '62%'},
    {'name': '开≥3%+龙≥3', 'dragon_min': 3, 'open_min': 3.0, 'score': 80, 'expected': '+6.9%', 'profitable': '61%'},
    {'name': '板块爆发≥3涨停', 'dragon_min': 0, 'open_min': 0, 'score': 75, 'expected': '+9.9%', 'profitable': '94%'},
    {'name': '隔夜溢价缩量<0.7', 'dragon_min': 0, 'open_min': 0, 'score': 60, 'expected': '+3.5%', 'profitable': '78%'},
]

# 卖出策略
SELL_RULES = {
    'open_ge7': {'action': '竞价卖', 'reason': '开≥7%仅剩0.4%空间'},
    'open_ge3': {'action': '等冲高', 'reason': '76%还有+2.9%+空间'},
    'open_lt0': {'action': '等冲高', 'reason': '70%翻红概率'},
    'rush_ge7_drop2': {'action': '锁肉卖', 'reason': '冲高≥7%+回落>2%'},
    'stop_loss': {'action': '止损卖', 'reason': '跌≥-8%'},
}

# cron时间定义
CRON_TIMES = {
    'afternoon_report': '0 15 * * 1-5',     # 15:00
    'prediction_report': '35 17 * * 1-5',    # 17:35
    'morning_news': '0 8 * * 1-5',           # 08:00
    'auction': '15 9 * * 1-5',               # 09:15
    'open_execute': '30 9 * * 1-5',          # 09:30
    'early_report': '30 11 * * 1-5',         # 11:30
    'noon_news': '50 12 * * 1-5',            # 12:50
    'afternoon_monitor': '0 13 * * 1-5',     # 13:00
    'profit_report': '5 15 * * 1-5',         # 15:05
    'upgrade_report': '10 15 * * 1-5',       # 15:10
}

if __name__ == '__main__':
    print(f'BASE: {BASE}')
    print(f'KLINE_DB exists: {os.path.exists(KLINE_DB)}')
    print(f'SECTOR_DB exists: {os.path.exists(SECTOR_DB)}')
    print(f'LIMIT_DB exists: {os.path.exists(LIMIT_DB)}')
    print(f'BUNDLE_JSON exists: {os.path.exists(BUNDLE_JSON)}')
    print(f'REPORTS_DIR: {REPORTS_DIR}')
