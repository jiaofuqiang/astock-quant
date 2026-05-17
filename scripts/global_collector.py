#!/usr/bin/env python3
"""
🌍 外围数据采集器 v1.0

采集来源：腾讯行情 API (qt.gtimg.cn) — 零成本零反爬

数据：
  1. 美股三大指数 — 道琼斯、纳斯达克、标普500
  2. 关键美股 — 苹果、英伟达、特斯拉、微软、谷歌、META、亚马逊、AMD
  3. 商品期货 — 原油(CL)、黄金(GC)、白银(SI)
  4. A股大盘 — 上证指数（已有但统一）

输出：data/global_market.db
  表 us_index     — 美股指数日数据
  表 us_stock     — 关键个股日数据  
  表 commodity    — 商品期货日数据

用法：
  python3 scripts/global_collector.py              # 采集今日
  python3 scripts/global_collector.py --date=2026-05-08  # 指定日期
  python3 scripts/global_collector.py --history    # 批量回采
  python3 scripts/global_collector.py --now        # 实时采集（盘前/盘中）
  python3 scripts/global_collector.py --install-cron  # 安装cron
"""

import os, sys, json, sqlite3, urllib.request, re
from datetime import datetime, timedelta

BASE = os.path.expanduser("~/astock")
DB_PATH = os.path.join(BASE, "data", "global_market.db")

# ══════════════════════════════════════════
# 配置
# ══════════════════════════════════════════

US_INDICES = {
    'usDJI':  '道琼斯',
    'usIXIC': '纳斯达克',
    'usINX':  '标普500',
}

US_STOCKS = {
    'usAAPL':  '苹果',
    'usNVDA':  '英伟达',
    'usTSLA':  '特斯拉',
    'usAMD':   '美国超威公司',
    'usMSFT':  '微软',
    'usGOOGL': '谷歌-A',
    'usMETA':  'Meta',
    'usAMZN':  '亚马逊',
}

COMMODITIES = {
    'hf_CL': '纽约原油',
    'hf_GC': '纽约黄金',
    'hf_SI': '纽约白银',
}

# 所有要采集的代码
ALL_CODES = list(US_INDICES.keys()) + list(US_STOCKS.keys()) + list(COMMODITIES.keys())


# ══════════════════════════════════════════
# 数据库
# ══════════════════════════════════════════

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 美股指数
    c.execute("""
        CREATE TABLE IF NOT EXISTS us_index (
            date TEXT, code TEXT, name TEXT,
            close_price REAL,
            change_pct REAL,
            high REAL,
            low REAL,
            volume REAL,
            create_time TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    
    # 关键美股
    c.execute("""
        CREATE TABLE IF NOT EXISTS us_stock (
            date TEXT, code TEXT, name TEXT, name_en TEXT,
            close_price REAL,
            change_pct REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL,
            pe REAL,
            market_cap REAL,
            create_time TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    
    # 商品期货
    c.execute("""
        CREATE TABLE IF NOT EXISTS commodity (
            date TEXT, code TEXT, name TEXT,
            close_price REAL,
            change_pct REAL,
            open_price REAL,
            pre_close REAL,
            high REAL,
            low REAL,
            create_time TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    
    conn.commit()
    return conn


# ══════════════════════════════════════════
# 采集
# ══════════════════════════════════════════

def fetch_tencent_quote(codes):
    """批量获取腾讯行情（美股/上证）"""
    if not codes:
        return {}
    
    q = ','.join(codes)
    url = f'http://qt.gtimg.cn/q={q}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode('gbk', errors='replace')
    except Exception as e:
        return None, str(e)
    
    return txt, None


def fetch_commodity(codes):
    """批量获取商品期货行情"""
    if not codes:
        return {}
    
    q = ','.join(codes)
    url = f'https://qt.gtimg.cn/q={q}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode('gbk', errors='replace')
    except Exception as e:
        return None, str(e)
    
    return txt, None


def parse_us_quote(txt, request_codes):
    """
    解析美股行情
    request_codes: 请求时的代码列表（用于映射结果）
    字段: 0=编码, 1=名称, 2=市场, 3=最新价, 30=时间, 31=涨跌额, 
          32=涨跌幅%, 33=最高, 34=最低, 36=成交量, 37=成交额,
          38=市盈率, 39=市值(亿), 46=英文名
    """
    results = {}
    for line in txt.strip().split(';'):
        if not line.strip():
            continue
        start = line.find('"')
        end = line.rfind('"')
        if start < 0 or end < start:
            continue
        
        content = line[start+1:end]
        parts = content.split('~')
        
        if len(parts) < 40:
            continue
        
        code = parts[0]  # 原始代码
        name = parts[1]
        price = safe_float(parts[3])
        trade_time = parts[30] if len(parts) > 30 else ''
        chg_pct = safe_float(parts[32]) if len(parts) > 32 else None
        high = safe_float(parts[33]) if len(parts) > 33 else None
        low = safe_float(parts[34]) if len(parts) > 34 else None
        volume = safe_float(parts[36]) if len(parts) > 36 else None
        amount = safe_float(parts[37]) if len(parts) > 37 else None
        pe = safe_float(parts[38]) if len(parts) > 38 else None
        mcap = safe_float(parts[39]) if len(parts) > 39 else None
        name_en = parts[46] if len(parts) > 46 else ''
        
        # 提取日期
        trade_date = ''
        if trade_time:
            m = re.match(r'(\d{4}-\d{2}-\d{2})', trade_time)
            if m:
                trade_date = m.group(1)
        
        # 从返回的parts[2]获取市场代码(.DJI)
        mkt_code = parts[2] if len(parts) > 2 else ''
        
        # 用名称匹配请求列表（最可靠）
        raw_code = ''
        for rc in request_codes:
            ref_name = US_INDICES.get(rc, '') or US_STOCKS.get(rc, '')
            if ref_name and ref_name in name:
                raw_code = rc
                break
        # 备选：代码匹配
        if not raw_code:
            for rc in request_codes:
                # parts[2]可能是.AAPL.OQ 或 .DJI
                rc_short = rc.replace('us', '').upper()
                if rc_short in mkt_code.upper():
                    raw_code = rc
                    break
        
        result_code = raw_code or code  # 找不到就用原始编码
        
        results[result_code] = {
            'name': name,
            'price': price,
            'chg_pct': chg_pct,
            'high': high,
            'low': low,
            'volume': volume,
            'amount': amount,
            'pe': pe,
            'mcap': mcap,
            'name_en': name_en,
            'trade_date': trade_date,
            'trade_time': trade_time,
        }
    
    return results


def parse_commodity(txt):
    """
    解析商品期货行情
    字段: 0=最新价, 1=涨跌幅%, 2=今开, 3=昨收, 4=最高, 5=最低, 6=时间, 7=昨收2, 12=日期, 13=名称
    """
    results = {}
    for line in txt.strip().split(';'):
        if not line.strip():
            continue
        if '="' not in line:
            continue
        
        code_key = line.split('=')[0].replace('v_', '')
        content = line[line.find('"')+1:line.rfind('"')]
        parts = content.split(',')
        
        if len(parts) < 8:
            continue
        
        price = safe_float(parts[0])
        chg_pct = safe_float(parts[1])
        open_p = safe_float(parts[2])
        pre_close = safe_float(parts[3])
        high = safe_float(parts[4])
        low = safe_float(parts[5])
        trade_time = parts[6]
        name = parts[13] if len(parts) > 13 else ''
        
        trade_date = ''
        if len(parts) > 12 and parts[12]:
            trade_date = parts[12]
        
        results[code_key] = {
            'name': name,
            'price': price,
            'chg_pct': chg_pct,
            'open': open_p,
            'pre_close': pre_close,
            'high': high,
            'low': low,
            'trade_date': trade_date,
        }
    
    return results


def safe_float(v, default=None):
    if v is None or v == '' or v == '-':
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ══════════════════════════════════════════
# 存入数据库
# ══════════════════════════════════════════

def save_index_data(conn, date_str, data):
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for code, info in data.items():
        # 确定代码对应的名称
        name = US_INDICES.get(code, info.get('name', code))
        
        c.execute("""
            INSERT OR REPLACE INTO us_index
            (date, code, name, close_price, change_pct, high, low, volume, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, code, name,
            info.get('price'),
            info.get('chg_pct'),
            info.get('high'),
            info.get('low'),
            info.get('volume'),
            now
        ))


def save_stock_data(conn, date_str, data):
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for code, info in data.items():
        name = US_STOCKS.get(code, info.get('name', code))
        
        c.execute("""
            INSERT OR REPLACE INTO us_stock
            (date, code, name, name_en, close_price, change_pct, 
             high, low, volume, amount, pe, market_cap, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, code, name, info.get('name_en', ''),
            info.get('price'),
            info.get('chg_pct'),
            info.get('high'),
            info.get('low'),
            info.get('volume'),
            info.get('amount'),
            info.get('pe'),
            info.get('mcap'),
            now
        ))


def save_commodity_data(conn, date_str, data):
    c = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for code, info in data.items():
        name = COMMODITIES.get(code, info.get('name', code))
        
        c.execute("""
            INSERT OR REPLACE INTO commodity
            (date, code, name, close_price, change_pct,
             open_price, pre_close, high, low, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, code, name,
            info.get('price'),
            info.get('chg_pct'),
            info.get('open'),
            info.get('pre_close'),
            info.get('high'),
            info.get('low'),
            now
        ))


# ══════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════

def collect_today():
    print("🌍 外围数据采集")
    print("=" * 50)
    
    today = datetime.now().strftime('%Y-%m-%d')
    conn = init_db()
    
    # 1. 美股指数 + 关键个股
    us_codes = list(US_INDICES.keys()) + list(US_STOCKS.keys())
    txt, err = fetch_tencent_quote(us_codes)
    if err:
        print(f"❌ 美股行情: {err}")
    else:
        data = parse_us_quote(txt, us_codes)
        # 分类
        index_data = {k: v for k, v in data.items() if k in US_INDICES}
        stock_data = {k: v for k, v in data.items() if k in US_STOCKS}
        
        save_index_data(conn, today, index_data)
        save_stock_data(conn, today, stock_data)
        
        print(f"✅ 美股指数:")
        for code, info in index_data.items():
            name = US_INDICES.get(code, '')
            p = info.get('price', '?')
            cp = info.get('chg_pct', '?')
            p_str = f"{p:.2f}" if isinstance(p, float) else str(p)
            cp_str = f"{cp:+.2f}%" if cp else '?'
            print(f"   {name:10s} {p_str:>10}  {cp_str}")
        
        print(f"\n✅ 关键美股:")
        for code, info in stock_data.items():
            name = US_STOCKS.get(code, '')
            p = info.get('price', '?')
            cp = info.get('chg_pct', '?')
            mcap = info.get('mcap', '?')
            p_str = f"{p:.2f}" if isinstance(p, float) else str(p)
            cp_str = f"{cp:+.2f}%" if cp else '?'
            mcap_str = f"{mcap:.0f}亿" if isinstance(mcap, float) else str(mcap)
            print(f"   {name:15s} ${p_str:>8}  {cp_str:>8}  市值{mcap_str}")
    
    # 2. 商品期货
    com_codes = list(COMMODITIES.keys())
    txt2, err2 = fetch_commodity(com_codes)
    if err2:
        print(f"\n❌ 商品期货: {err2}")
    else:
        data2 = parse_commodity(txt2)
        save_commodity_data(conn, today, data2)
        
        print(f"\n✅ 商品期货:")
        for code, info in data2.items():
            name = COMMODITIES.get(code, info.get('name', ''))
            p = info.get('price', '?')
            cp = info.get('chg_pct', '?')
            p_str = f"{p:.2f}" if isinstance(p, float) else str(p)
            cp_str = f"{cp:+.2f}%" if cp else '?'
            print(f"   {name:15s} ${p_str:>8}  {cp_str}")
    
    conn.close()
    print(f"\n📁 数据库: {DB_PATH}")
    return True


def install_cron():
    script_path = os.path.abspath(__file__)
    cron_line = f"40 16 * * 1-5 cd {BASE} && python3 {script_path} >> {BASE}/logs/global_collector.log 2>&1"
    
    import subprocess
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    existing = result.stdout
    
    if 'global_collector' in existing:
        print("⚠️ cron任务已存在")
        return
    
    new_cron = existing.strip() + '\n' + cron_line + '\n'
    subprocess.run(['crontab'], input=new_cron, text=True)
    print(f"✅ cron已添加: {cron_line}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='外围数据采集器')
    parser.add_argument('--now', action='store_true', help='实时采集')
    parser.add_argument('--install-cron', action='store_true')
    args = parser.parse_args()
    
    if args.install_cron:
        install_cron()
        return
    
    collect_today()


if __name__ == '__main__':
    main()
