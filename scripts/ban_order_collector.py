#!/usr/bin/env python3
"""
📥 涨停封单数据采集器 v1.1

采集数据：
  每5分钟：封单额、成交量、成交额
  日统计：首次涨停时间、最大封单额、收盘封单额、封单比、连板数

数据来源：腾讯实时行情 qt.gtimg.cn
  parts[47]=涨停价, parts[48]=跌停价
  parts[10]=买一量(手), parts[9]=买一价
  封单额 = 买一量 * 涨停价（涨停时有效）
  
输出：data/limit_order_history.db
  表 limit_orders — 每5分钟快照
  表 limit_daily  — 每日涨停股日统计

用法：
  python3 scripts/ban_order_collector.py --once   # 立即采集一次（cron用）
"""

import os, sys, json, subprocess, sqlite3, time, re
from datetime import datetime

DB = os.path.expanduser("~/astock/data/limit_order_history.db")

def fetch_quotes(codes):
    """获取腾讯行情，返回 dict[code] = {name, price, ...}"""
    if not codes: return {}
    def mkt(code):
        code = code.strip()
        return f'sh{code}' if code[0] in ('6','5','9') else f'sz{code}'
    
    results = {}
    # 分批200只
    for i in range(0, len(codes), 200):
        batch = codes[i:i+200]
        codes_str = ','.join(mkt(c) for c in batch)
        try:
            proc = subprocess.Popen(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://qt.gtimg.cn/q={codes_str}'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, _ = proc.communicate(timeout=10)
            txt = out.decode('gbk', errors='replace')
        except:
            continue
        
        for line in txt.strip().split('\n'):
            if '~' not in line: continue
            parts = line.split('~')
            if len(parts) < 48: continue
            try:
                code = parts[2].strip()
                if not code.isdigit() or len(code) != 6: continue
                
                name = parts[1]
                price = float(parts[3]) if parts[3] else 0
                pre_close = float(parts[4]) if parts[4] else 0
                open_p = float(parts[5]) if parts[5] else 0
                volume = float(parts[6]) if parts[6] else 0  # 手
                amount = float(parts[37]) if parts[37] else 0  # 万元
                buy1 = float(parts[9]) if parts[9] else 0
                buy1_vol = float(parts[10]) if parts[10] else 0
                high_limit = float(parts[47]) if parts[47] else 0
                change_pct = (price - pre_close) / pre_close * 100 if pre_close else 0
                is_limit_up = change_pct >= 9.5 and price >= high_limit * 0.99
                seal_amount = buy1_vol * buy1 / 100 if is_limit_up else 0  # 万元 (1手=100股)
                
                results[code] = {
                    'name': name, 'price': price,
                    'pre_close': pre_close, 'open': open_p,
                    'volume': volume, 'amount': amount,
                    'buy1': buy1, 'buy1_vol': buy1_vol,
                    'high_limit': high_limit,
                    'change_pct': round(change_pct, 2),
                    'is_limit_up': is_limit_up,
                    'seal_amount': round(seal_amount, 2),
                }
            except:
                continue
        time.sleep(0.2)
    
    return results


def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS limit_orders (
            date TEXT, code TEXT,
            name TEXT,
            limit_price REAL,
            seal_amount REAL,      -- 封单额(万元) 注意：因1手=100股，公式为buy1_vol*buy1/100
            volume REAL,           -- 累计成交量(手)
            amount REAL,           -- 累计成交额(万元)
            snap_time TEXT,
            PRIMARY KEY (date, code, snap_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS limit_daily (
            date TEXT, code TEXT, name TEXT,
            limit_price REAL,
            first_limit_time TEXT,  -- 首次涨停时间
            max_seal_amount REAL,   -- 最大封单额
            end_seal_amount REAL,   -- 收盘封单额
            seal_ratio REAL,        -- 封单额/成交额
            total_volume REAL,
            total_amount REAL,
            PRIMARY KEY (date, code)
        )
    """)
    conn.commit()
    return conn


def save_snapshots(conn, date, snap_time, quotes):
    """保存一次快照"""
    cur = conn.cursor()
    saved = 0
    for code, q in quotes.items():
        if not q['is_limit_up']: continue
        cur.execute("""
            INSERT OR REPLACE INTO limit_orders 
            (date, code, name, limit_price, seal_amount, snap_time, volume, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (date, code, q['name'], q['high_limit'], q['seal_amount'],
              snap_time, q['volume'], q['amount']))
        saved += 1
    conn.commit()
    return saved


def compute_daily(conn, date):
    """从快照数据计算每日涨停股统计"""
    cur = conn.cursor()
    
    # 获取每只涨停股: 最早涨停时间、最大封单
    rows = cur.execute("""
        SELECT code, name, limit_price,
               MIN(snap_time) as first_limit,
               MAX(seal_amount) as max_seal
        FROM limit_orders
        WHERE date = ?
        GROUP BY code
    """, (date,)).fetchall()
    
    for row in rows:
        code, name, limit_price, first_time, max_seal = row
        
        # 收盘封单（最后一次快照）
        end = cur.execute("""
            SELECT seal_amount, volume, amount FROM limit_orders 
            WHERE date=? AND code=? ORDER BY snap_time DESC LIMIT 1
        """, (date, code)).fetchone()
        if not end: continue
        end_seal, total_vol, total_amt = end
        
        seal_r = end_seal / max(total_amt, 0.01)
        
        cur.execute("""
            INSERT OR REPLACE INTO limit_daily
            (date, code, name, limit_price, first_limit_time,
             max_seal_amount, end_seal_amount, seal_ratio,
             total_volume, total_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (date, code, name, limit_price, first_time,
              max_seal, end_seal, round(seal_r, 2),
              total_vol, total_amt))
    
    conn.commit()
    return len(rows)


def collect_all_stocks():
    """获取全市场主板行情"""
    with open(os.path.expanduser("~/astock/data/all_main_board.txt")) as f:
        codes = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    return fetch_quotes(codes)


def collect_one_shot():
    """立即采集一次（cron用）"""
    conn = init_db()
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M:%S")
    
    quotes = collect_all_stocks()
    limit_stocks = {c: q for c, q in quotes.items() if q['is_limit_up']}
    
    if limit_stocks:
        saved = save_snapshots(conn, today, now_str, limit_stocks)
        names = [q['name'] for q in list(limit_stocks.values())[:10]]
        print(f"[{now_str}] 涨停{len(limit_stocks)}只 保存{saved}条: {names}")
    else:
        print(f"[{now_str}] 无涨停")
    
    conn.close()


def compute_end():
    """收盘后计算日统计"""
    conn = init_db()
    today = datetime.now().strftime("%Y-%m-%d")
    cnt = compute_daily(conn, today)
    print(f"[15:05] {today} 日统计完成: {cnt}只涨停票")
    
    # 展示今日最强封单top5
    rows = conn.execute("""
        SELECT name, first_limit_time, max_seal_amount, end_seal_amount, seal_ratio
        FROM limit_daily WHERE date=?
        ORDER BY max_seal_amount DESC LIMIT 5
    """, (today,)).fetchall()
    print(f"  封单TOP5:")
    for r in rows:
        print(f"    {r[0]}: 首次{r[1] or '?'} 最大封单{int(r[2]):,}万 收盘{int(r[3]):,}万 封单比{r[4]:.2f}")
    conn.close()


if __name__ == '__main__':
    if '--once' in sys.argv:
        collect_one_shot()
    elif '--end' in sys.argv:
        compute_end()
    else:
        collect_one_shot()  # 默认once
