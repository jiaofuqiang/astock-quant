#!/usr/bin/env python3
"""盘中辅助数据：板块分组 + 连板数计算"""
import os, sqlite3, json
from datetime import datetime, date
from collections import defaultdict

BASE = os.path.expanduser("~/astock")
DATA = os.path.join(BASE, "data")

# 板块→股票映射缓存
_sector_cache = None
def load_sector_mapping():
    """加载板块→股票映射（从sector_indexes.db）"""
    global _sector_cache
    if _sector_cache is not None:
        return _sector_cache
    
    db = os.path.join(DATA, 'sector_indexes.db')
    if not os.path.exists(db):
        return {}
    
    conn = sqlite3.connect(db)
    
    # 取最近有数据的日期
    latest = conn.execute("SELECT MAX(date) FROM sector_stock_daily").fetchone()[0]
    if not latest:
        conn.close()
        return {}
    
    cur = conn.execute("""
        SELECT sector_name, code FROM sector_stock_daily 
        WHERE date=? AND code IS NOT NULL
    """, (latest,))
    
    code_to_sectors = defaultdict(list)   # code -> [(sector_name,)]
    sector_to_stocks = defaultdict(list)   # sector_name -> [code, ...]
    for row in cur.fetchall():
        sn, code = row
        if code:
            code_to_sectors[code].append(sn)
            sector_to_stocks[sn].append(code)
    
    conn.close()
    
    _sector_cache = {
        'code_to_sectors': dict(code_to_sectors),
        'sector_to_stocks': dict(sector_to_stocks),
        'loaded_date': latest,
    }
    return _sector_cache

def get_sectors_for_code(code):
    """获取股票所属板块列表"""
    cache = load_sector_mapping()
    return cache.get('code_to_sectors', {}).get(code, [])

def get_top_sectors(quotes, top_n=5):
    """从实时行情中找出涨停最多的板块"""
    cache = load_sector_mapping()
    sector_to_stocks = cache.get('sector_to_stocks', {})
    code_to_sectors = cache.get('code_to_sectors', {})
    
    # 统计每个板块的涨停数
    sector_limit_counts = defaultdict(int)
    sector_limit_stocks = defaultdict(list)
    
    for code, q in quotes.items():
        if q.get('is_limit_up') or q.get('chg_pct', 0) >= 9.5:
            sectors = code_to_sectors.get(code, [])
            for sn in sectors:
                sector_limit_counts[sn] += 1
                sector_limit_stocks[sn].append((code, q.get('name', '')))
    
    # 排序
    ranked = sorted(sector_limit_counts.items(), key=lambda x: -x[1])
    
    result = []
    for sn, cnt in ranked[:top_n]:
        stocks = sector_limit_stocks.get(sn, [])[:5]
        result.append({
            'sector': sn,
            'limit_up_count': cnt,
            'stocks': [{'code': c, 'name': n} for c, n in stocks],
        })
    
    return result

def calculate_board_counts(codes, today=None):
    """从kline_cache.db计算连续涨停天数（盘中可用，基于历史K线）"""
    if today is None:
        today = str(date.today())
    
    db = os.path.join(DATA, 'kline_cache.db')
    if not os.path.exists(db):
        return {}
    
    conn = sqlite3.connect(db)
    board_counts = {}
    
    for code in codes:
        # 查该股票最近30天的K线
        cur = conn.execute("""
            SELECT date, open, close FROM kline 
            WHERE code=? AND date <= ?
            ORDER BY date DESC LIMIT 30
        """, (code, today))
        
        rows = cur.fetchall()
        
        # 从最近一天往前数连续涨停的天数
        # 涨停判断：(close - 前一天的close) / 前一天的close >= 9.5%
        count = 0
        prev_row = None
        for i, row in enumerate(rows):
            if i == 0:
                prev_row = row
                continue
            # 用前一天的close作为基准
            prev_close = row[2]  # close of previous day (row is date, open, close)
            # 今天的数据是rows[i-1]
            today_close = prev_row[2]
            chg = (today_close - prev_close) / prev_close * 100 if prev_close > 0 else 0
            
            if chg >= 9.5:
                count += 1
                prev_row = row
            else:
                break
        
        if count >= 1:
            board_counts[code] = count
    
    conn.close()
    return board_counts

def is_new_stock(code, today=None):
    """判断是否为次新股（上市<30天或K线不足30条）"""
    if today is None:
        today = str(date.today())
    db = os.path.join(DATA, 'kline_cache.db')
    if not os.path.exists(db):
        return True
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT MIN(date), COUNT(*) FROM kline WHERE code=?", (code,)).fetchone()
    conn.close()
    if not row or not row[0]:
        return True  # 无K线数据，视为次新
    min_date, count = row
    try:
        dt = datetime.strptime(min_date, '%Y-%m-%d')
        days_since_list = (datetime.strptime(today, '%Y-%m-%d') - dt).days
        return days_since_list < 30 or count < 30
    except:
        return True


def calculate_m20_bias(codes, today=None):
    """计算MA20乖离率"""
    if today is None:
        today = str(date.today())
    
    db = os.path.join(DATA, 'kline_cache.db')
    if not os.path.exists(db):
        return {}
    
    conn = sqlite3.connect(db)
    bias_results = {}
    
    for code in codes:
        cur = conn.execute("""
            SELECT date, close FROM kline 
            WHERE code=? AND date <= ?
            ORDER BY date DESC LIMIT 25
        """, (code, today))
        
        rows = [r[1] for r in cur.fetchall()]  # 收盘价
        if len(rows) >= 21:
            today_close = rows[0]
            ma20 = sum(rows[:20]) / 20
            bias = (today_close - ma20) / ma20 * 100
            if bias <= -15:  # 只返回超卖的
                bias_results[code] = round(bias, 2)
    
    conn.close()
    return bias_results

def refresh():
    """刷新缓存"""
    global _sector_cache
    _sector_cache = None
    return load_sector_mapping()

if __name__ == '__main__':
    # 测试
    print("=== 板块缓存加载测试 ===")
    cache = load_sector_mapping()
    print(f"  板块数: {len(cache.get('sector_to_stocks', {}))}")
    print(f"  股票数: {len(cache.get('code_to_sectors', {}))}")
    
    # 测试工业富联
    sectors = get_sectors_for_code('601138')
    print(f"  工业富联板块: {sectors}")
    
    print("\n=== 连板计算测试 ===")
    bc = calculate_board_counts(['600156', '600172', '601138', '600396'])
    for code, cnt in bc.items():
        print(f"  {code}: {cnt}连板")
    
    print("\n=== MA20乖离测试 ===")
    bias = calculate_m20_bias(['600156', '600172'])
    for code, b in bias.items():
        print(f"  {code}: MA20乖离{b}%")
