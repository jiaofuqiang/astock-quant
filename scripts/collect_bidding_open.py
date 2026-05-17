#!/usr/bin/env python3
"""
竞价开盘数据采集器 v1.0
========================
每日采集昨日涨停股的T+1日开盘数据。

采集时间:
  - 09:25  竞价结束数据
  - 09:30  正式开盘数据

采集字段:
  - 开盘涨跌幅 (%)
  - 量比
  - 换手率 (%)
  - 成交额 (万元)

数据来源: 腾讯行情 qt.gtimg.cn
存储: astock/data/bidding_open.db

用法:
  python3 scripts/collect_bidding_open.py --period=0925   # 09:25竞价采集
  python3 scripts/collect_bidding_open.py --period=0930   # 09:30开盘采集
  python3 scripts/collect_bidding_open.py --date=2026-05-15 --period=0925  # 指定日期
"""

import sqlite3, os, sys, subprocess
from datetime import datetime, date, timedelta

BASE = os.path.expanduser("~/astock")
DB = os.path.join(BASE, "data", "bidding_open.db")
KLINE_DB = os.path.join(BASE, "data", "lhb_cache.db")

# ===== 1. 参数解析 =====
query_date = date.today().strftime('%Y-%m-%d')
period = None  # '0925' 或 '0930'

for arg in sys.argv[1:]:
    if arg.startswith('--date='):
        query_date = arg.split('=')[1]
    elif arg.startswith('--period='):
        period = arg.split('=')[1]

if period not in ('0925', '0930'):
    print("❌ 请指定 --period=0925 或 --period=0930")
    sys.exit(1)

period_label = '竞价' if period == '0925' else '开盘'
print(f"📅 采集日期: {query_date} 时段: {period_label}({period})")

# ===== 2. 初始化数据库 =====
os.makedirs(os.path.dirname(DB), exist_ok=True)
conn = sqlite3.connect(DB)
c = conn.cursor()
c.execute("""
  CREATE TABLE IF NOT EXISTS bidding_open (
    date        TEXT NOT NULL,   -- T+1日（开盘日）
    period      TEXT NOT NULL,   -- 0925=竞价 或 0930=开盘
    code        TEXT NOT NULL,   -- 股票代码
    name        TEXT NOT NULL,   -- 股票名称
    open_chg    REAL,            -- 开盘涨跌幅(%)
    vol_ratio   REAL,            -- 量比
    turnover    REAL,            -- 换手率(%)
    amount_wan  REAL,            -- 成交额(万元)
    price       REAL,            -- 当前价
    create_time TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(date, period, code)
  )
""")
conn.commit()

# ===== 3. 获取昨日涨停股列表 =====
try:
    kconn = sqlite3.connect(KLINE_DB)
    kc = kconn.cursor()
    
    kc.execute("SELECT DISTINCT date FROM kline WHERE date<? ORDER BY date DESC LIMIT 1", (query_date,))
    row = kc.fetchone()
    if not row:
        print("❌ 找不到上一个交易日")
        sys.exit(1)
    prev_date = row[0]
    print(f"📊 涨停日(T-1): {prev_date}")
    
    kc.execute("""
      SELECT DISTINCT l.date, l.code, l.name
      FROM lhb_list l
      WHERE l.date=? AND l.chg>=9.5 AND l.name NOT LIKE '%ST%' AND l.name NOT LIKE '*ST%'
      AND (l.code LIKE '6%' OR l.code LIKE '000%' OR l.code LIKE '001%' OR l.code LIKE '002%' OR l.code LIKE '003%')
      ORDER BY l.code
    """, (prev_date,))
    stocks = kc.fetchall()
    kconn.close()
    
    if not stocks:
        print("❌ 昨日无符合条件的涨停股")
        sys.exit(0)
    
    print(f"🎯 昨日涨停股: {len(stocks)}只")
    
except Exception as e:
    print(f"❌ 读取数据库失败: {e}")
    sys.exit(1)

# ===== 4. 批量拉腾讯行情 =====
def add_market_prefix(code):
    if code.startswith(('sh', 'sz')):
        return code
    if code.startswith(('6', '5', '9')):
        return f"sh{code}"
    return f"sz{code}"

codes = [s[1] for s in stocks]
all_data = {}

batches = [codes[i:i+80] for i in range(0, len(codes), 80)]
for batch in batches:
    prefixed = [add_market_prefix(c) for c in batch]
    url = f"https://qt.gtimg.cn/q={','.join(prefixed)}"
    try:
        r = subprocess.run(
            ['curl', '-s', url, '--connect-timeout', '5', '--max-time', '8'],
            capture_output=True, timeout=10
        )
        raw = r.stdout.decode('gbk', errors='replace')
        for line in raw.strip().split('\n'):
            if '~' not in line: continue
            parts = line.split('"')
            if len(parts) < 2: continue
            f = parts[1].split('~')
            if len(f) < 50: continue
            
            code = f[2]
            name = f[1]
            open_p = float(f[5]) if f[5] else 0
            prev_close = float(f[4]) if f[4] else 0
            price = float(f[3]) if f[3] else 0
            vol_ratio = float(f[49]) if f[49] else 0
            turnover = float(f[38]) if len(f) > 38 and f[38] else 0
            amount_wan = float(f[37]) if f[37] else 0
            open_chg = round((open_p - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
            
            all_data[code] = {
                'name': name,
                'open_chg': open_chg,
                'vol_ratio': round(vol_ratio, 2),
                'turnover': round(turnover, 2),
                'amount_wan': round(amount_wan, 2),
                'price': price,
            }
    except Exception as e:
        print(f"  ⚠️ 批次拉取失败: {e}")

print(f"✅ 获取到 {len(all_data)} 只数据")

# ===== 5. 入库 =====
inserted = 0
skipped = 0

for s_date, code, name in stocks:
    q = all_data.get(code)
    if not q:
        skipped += 1
        continue
    
    try:
        c.execute("""
          INSERT OR REPLACE INTO bidding_open 
          (date, period, code, name, open_chg, vol_ratio, turnover, amount_wan, price)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            query_date, period, code, q.get('name', name),
            q.get('open_chg'), q.get('vol_ratio'),
            q.get('turnover'), q.get('amount_wan'), q.get('price')
        ))
        conn.commit()
        inserted += 1
        print(f"  ✅ {code} {q['name']:8s} 开盘{q['open_chg']:+.2f}% 量比{q['vol_ratio']:.2f} 换手{q['turnover']:.2f}% {q['amount_wan']:.0f}万")
    except Exception as e:
        print(f"  ❌ {code}: 入库失败 {e}")
        skipped += 1

# ===== 6. 概要 =====
print(f"\n{'='*50}")
print(f"📊 {period_label}采集完成")
print(f"   日期: {query_date} 涨停日: {prev_date}")
print(f"   涨停股: {len(stocks)}只 → 入库: {inserted}条 跳过: {skipped}条")

c.execute("SELECT COUNT(*) FROM bidding_open WHERE date=? AND period=?", (query_date, period))
total = c.fetchone()[0]
print(f"   本时段总量: {total}条")

c.execute("""
  SELECT ROUND(AVG(open_chg),2), ROUND(AVG(vol_ratio),2), 
         ROUND(AVG(turnover),2), ROUND(AVG(amount_wan),0)
  FROM bidding_open WHERE date=? AND period=?
""", (query_date, period))
avg = c.fetchone()
if avg and avg[0] is not None:
    print(f"\n📋 均值: 开盘{avg[0]:+.2f}% 量比{avg[1]:.2f} 换手{avg[2]:.2f}% 成交额{avg[3]:.0f}万")

conn.close()
