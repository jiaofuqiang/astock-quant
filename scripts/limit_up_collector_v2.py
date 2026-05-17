#!/usr/bin/env python3
"""
📈 涨跌停数据采集器 v2.0
基于东方财富涨跌停监控平台API：
  RPT_PCHOT_LIMITLIST_HSDETIAL → 涨停原因+涨跌幅+板块+封板时间
  RPT_CUSTOM_INTSELECTION_MONITOR → 全量涨停监控（盘中实时）

用法：
  python3 limit_up_collector_v2.py                     # 采集今天
  python3 limit_up_collector_v2.py --date 2026-05-15   # 采集指定日期
  python3 limit_up_collector_v2.py --backfill           # 补拉所有历史（从2022-07开始）
  python3 limit_up_collector_v2.py --cron               # cron模式（采集最近5个交易日）
"""

import subprocess, json, os, sys, time
from datetime import datetime, timedelta
from collections import defaultdict
import urllib.parse

BASE = os.path.expanduser('~/astock')
LIMIT_DB = os.path.join(BASE, 'data/daily_limit_data.db')

API_BASE = 'https://datacenter.eastmoney.com/securities/api/data/v1/get'
COLUMNS = 'SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME,BOARD_CODE,LIMIT_REASON,LIMIT_CONTENT,NLIMITUP,CLOSE,CHANGE_RATE,CZT_LIMITUP_TIME,LAST_LIMITUP_TIME,RANK_TIME,IS_ST'
PAGE_SIZE = 5000

def collect_date(trade_date):
    """采集指定日期的涨停数据"""
    dt_filter = f"(TRADE_DATE='{trade_date} 00:00:00')"
    encoded_filter = urllib.parse.quote(dt_filter, safe='()=')
    
    all_data = []
    page = 1
    while True:
        url = (f'{API_BASE}?source=SECURITIES&client=APP'
               f'&reportName=RPT_PCHOT_LIMITLIST_HSDETIAL'
               f'&columns={COLUMNS}'
               f'&filter={encoded_filter}'
               f'&pageNumber={page}&pageSize={PAGE_SIZE}'
               f'&sortColumns=RANK_TIME&sortTypes=-1')
        
        r = subprocess.run(['curl', '-s', '--connect-timeout', '5', '--max-time', '15', url],
            capture_output=True, timeout=20)
        raw = r.stdout.decode('utf-8', errors='replace')
        if not raw:
            break
        
        d = json.loads(raw)
        data = d.get('result', {}).get('data', [])
        if not data:
            break
        
        all_data.extend(data)
        pages = d.get('result', {}).get('pages', 0)
        if page >= pages:
            break
        page += 1
        time.sleep(0.2)
    
    if not all_data:
        print(f'  {trade_date}: 无数据')
        return 0
    
    # 排除ST
    non_st = [item for item in all_data if int(item.get('IS_ST', 0) or 0) == 0]
    
    # 解析板数
    vals = []
    for item in non_st:
        code = item.get('SECURITY_CODE', '')
        name = item.get('SECURITY_NAME_ABBR', '').replace("'", "''")
        
        nlimite = item.get('NLIMITUP', '今日首板')
        board_count = 1
        if nlimite and '天' in nlimite and '板' in nlimite:
            ps = nlimite.replace('板', '').split('天')
            if len(ps) == 2:
                board_count = int(ps[1])
        
        limit_time = (item.get('CZT_LIMITUP_TIME', '') or item.get('LAST_LIMITUP_TIME', '') or '').replace("'", "''")
        close_price = float(item.get('CLOSE', 0) or 0)
        change_pct = float(item.get('CHANGE_RATE', 0) or 0)
        ban_reason = (item.get('LIMIT_REASON', '') or '').replace("'", "''")
        ban_content = (item.get('LIMIT_CONTENT', '') or '').replace("'", "''").replace('\n', '\\n')[:500]
        board_name = (item.get('BOARD_NAME', '') or '').replace("'", "''")
        limit_label = '自然涨停'  # 默认
        if '一字' in str(item.get('LIMIT_CONTENT', '')):
            limit_label = '一字涨停'
        
        vals.append(f"('{trade_date}','{code}','{name}','{limit_time}',{board_count},'{limit_label}',{close_price},{change_pct},'{ban_reason}','{ban_content}','{board_name}')")
    
    # 批量写入
    if vals:
        for i in range(0, len(vals), 200):
            batch = vals[i:i+200]
            sql = f"INSERT OR REPLACE INTO limit_stocks_v2 VALUES {','.join(batch)};"
            subprocess.run(['sqlite3', LIMIT_DB], input=sql.encode(), capture_output=True, timeout=30)
    
    print(f'  {trade_date}: {len(vals)}只涨停 (排除ST后)')
    return len(vals)


def init_db():
    """初始化v2表"""
    sql = '''
CREATE TABLE IF NOT EXISTS limit_stocks_v2 (
    date TEXT, code TEXT, name TEXT,
    first_limit_time TEXT,
    board_count INTEGER DEFAULT 1,
    limit_stat TEXT DEFAULT '自然涨停',
    close_price REAL DEFAULT 0,
    change_pct REAL DEFAULT 0,
    ban_reason TEXT DEFAULT '',
    ban_content TEXT DEFAULT '',
    board_name TEXT DEFAULT '',
    PRIMARY KEY (date, code)
);
'''
    subprocess.run(['sqlite3', LIMIT_DB], input=sql.encode(), capture_output=True, timeout=10)
    print(f'数据库初始化完成: {LIMIT_DB}')


def verify_counts():
    """校验数据完整性"""
    r = subprocess.run(['sqlite3', '-noheader', LIMIT_DB,
        "SELECT date, COUNT(*) FROM limit_stocks_v2 GROUP BY date ORDER BY date DESC LIMIT 10;"],
        capture_output=True, text=True, timeout=10)
    print('\n最近10天数据量:')
    print(r.stdout)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='涨跌停数据采集器 v2.0')
    parser.add_argument('--date', help='指定日期 YYYY-MM-DD')
    parser.add_argument('--backfill', action='store_true', help='补拉全部历史')
    parser.add_argument('--cron', action='store_true', help='cron模式(最近5个交易日)')
    args = parser.parse_args()
    
    init_db()
    
    if args.date:
        collect_date(args.date)
    elif args.backfill:
        # 从2022-07-01开始补拉
        start = datetime(2022, 7, 1)
        end = datetime.now()
        total = 0
        d = start
        while d <= end:
            date_str = d.strftime('%Y-%m-%d')
            # 跳过周末
            if d.weekday() < 5:
                cnt = collect_date(date_str)
                total += cnt
                time.sleep(0.3)
            d += timedelta(days=1)
        print(f'\n补拉完成! 总计{total}条涨停记录')
    elif args.cron:
        # cron模式：采集最近5个交易日
        today = datetime.now()
        for i in range(10):
            d = today - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            date_str = d.strftime('%Y-%m-%d')
            collect_date(date_str)
            time.sleep(0.3)
    else:
        # 默认：采集今天
        collect_date(datetime.now().strftime('%Y-%m-%d'))
    
    verify_counts()
