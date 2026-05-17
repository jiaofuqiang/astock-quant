#!/usr/bin/env python3
"""
📥 每日涨停聚合采集器 v1.0

采集来源：
  1. 东方财富涨停页 — 首次封板时间、连板数、涨停统计、换手率、成交额
  2. 大智慧涨停透视 — 封板率、炸板数、涨停梯队、赚钱效应
  3. 同花顺异动揭秘 — 涨停原因（行业原因+公司原因）

输出：data/daily_limit_data.db
  表 limit_strength   — 每日市场情绪（封板率/炸板数/赚钱效应）
  表 limit_stocks     — 每日涨停股明细（封板时间/连板数/涨停统计）

用法：
  python3 scripts/daily_limit_collector.py          # 全量采集
  python3 scripts/daily_limit_collector.py --east   # 仅东方财富
  python3 scripts/daily_limit_collector.py --dzh    # 仅大智慧
"""

import os, sys, json, subprocess, sqlite3, re, time
from datetime import datetime
from urllib.parse import urlencode

DB = os.path.expanduser("~/astock/data/daily_limit_data.db")

# ====== 数据库 ======

def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS limit_strength (
            date TEXT PRIMARY KEY,
            total_limit INTEGER,        -- 涨停数
            total_limit_yesterday INTEGER,
            seal_rate REAL,             -- 封板率(%)
            seal_rate_yesterday REAL,
            open_count INTEGER,         -- 炸板数
            open_count_yesterday INTEGER,
            total_down_limit INTEGER,   -- 跌停数
            max_board INTEGER,          -- 最高板数
            serial_limit_count INTEGER, -- 连板家数
            market_emotion REAL,        -- 全市场情绪(%)
            yesterday_yield REAL,       -- 昨日涨停今日表现
            ref_look INTEGER,           -- 赚钱效应(0/1)
            create_time TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS limit_stocks (
            date TEXT, code TEXT,
            name TEXT,
            first_limit_time TEXT,      -- 首次封板时间
            board_count INTEGER,        -- 连板数
            limit_stat TEXT,            -- 涨停统计(如"3/4")
            turnover REAL,              -- 换手率
            amount_wan REAL,            -- 成交额(万元)
            close_price REAL,           -- 收盘价
            change_pct REAL,            -- 涨幅
            seal_rate_real REAL,        -- 近一年封板率(大智慧)
            ban_reason TEXT,            -- 涨停原因(同花顺)
            PRIMARY KEY (date, code)
        )
    """)
    conn.commit()
    return conn


# ====== 东方财富涨停页采集 ======

def fetch_eastmoney_limit():
    """从东方财富涨停页采集涨停股明细"""
    url = "https://quote.eastmoney.com/ztb/detail#type=ztgc"
    
    # 东方财富的涨停股池有JSONP API
    # 先用直接请求JSON数据
    api_url = "https://push2ex.eastmoney.com/getStockFenShi?"
    params = {
        'cb': 'jQuery',
        'pageIndex': 1,
        'pageSize': 200,
        'dtype': 'gp',
        'sortType': 'ZDF',
        'sortOrder': 'desc',
        'extData': 'gp',
    }
    
    try:
        proc = subprocess.Popen(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
             '-H', 'User-Agent: Mozilla/5.0',
             f'https://push2ex.eastmoney.com/getStockFenShi?{urlencode(params)}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=12)
        txt = out.decode('utf-8', errors='replace')
    except:
        # fallback: 用浏览器解析HTML
        return _parse_eastmoney_html()
    
    # 尝试解析JSONP
    m = re.search(r'jQuery\((.*)\)', txt)
    if not m:
        return _parse_eastmoney_html()
    
    try:
        data = json.loads(m.group(1))
        stocks = data.get('data', []) or []
    except:
        return _parse_eastmoney_html()
    
    if not stocks:
        return _parse_eastmoney_html()
    
    result = []
    for s in stocks:
        try:
            result.append({
                'code': s.get('SC', ''),
                'name': s.get('N', ''),
                'first_limit_time': s.get('FBT', ''),  # 首次封板时间
                'board_count': s.get('BC', 0),  # 连板数
                'limit_stat': s.get('ZDL', ''),  # 涨停统计
                'turnover': s.get('HS', 0),
                'amount_wan': s.get('JE', 0) / 10000 if s.get('JE') else 0,
                'close_price': s.get('ZJ', 0),
                'change_pct': s.get('ZDF', 0),
            })
        except:
            continue
    
    return result


def _parse_eastmoney_html():
    """备用方案：从HTML解析"""
    print("  [备用] 从HTML解析东方财富涨停页...")
    try:
        proc = subprocess.Popen(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
             '-H', 'User-Agent: Mozilla/5.0',
             'https://quote.eastmoney.com/ztb/detail#type=ztgc'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=12)
        txt = out.decode('utf-8', errors='replace')
    except:
        return []
    
    # 尝试从HTML中提取JSON数据
    # 东方财富页面通常有window.zhTabsData = ...
    results = []
    
    # 提取表格行
    rows = re.findall(r'<tr[^>]*data-bind[^>]*>(.*?)</tr>', txt, re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) < 6:
            continue
        try:
            name_code = re.search(r'>([^<]+)<', cells[1])
            price_text = re.search(r'>([^<]+)<', cells[2])
            time_text = re.search(r'>([^<]+)<', cells[6]) if len(cells) > 6 else None
            
            results.append({
                'name': name_code.group(1).strip() if name_code else '',
                'first_limit_time': time_text.group(1).strip() if time_text else '',
            })
        except:
            continue
    
    return results


# ====== 大智慧涨停透视采集 ======

def fetch_dzh_limit():
    """从大智慧涨停透视页面采集"""
    url = "https://webrelease.dzh.com.cn/htmlweb/ztts/index.php"
    
    try:
        proc = subprocess.Popen(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
             '-L', url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=12)
        txt = out.decode('utf-8', errors='replace')
    except:
        return None
    
    data = {}
    
    # 涨停数
    m = re.search(r'涨停板[^<]*<span[^>]*>(\d+)</span>', txt)
    if m: data['total_limit'] = int(m.group(1))
    
    # 封板率
    m = re.search(r'封板率[^:]*:?\s*(\d+)%', txt)
    if m: data['seal_rate'] = float(m.group(1))
    
    # 涨停打开
    m = re.search(r'涨停打开[^<]*<span[^>]*>(\d+)</span>', txt)
    if m: data['open_count'] = int(m.group(1))
    
    # 跌停
    m = re.search(r'跌停板[^<]*<span[^>]*>(\d+)</span>', txt)
    if m: data['total_down_limit'] = int(m.group(1))
    
    # 跌停封板率
    m = re.search(r'跌停封板率[^:]*:?\s*(\d+)%', txt)
    if m: data['down_seal_rate'] = float(m.group(1))
    
    # 最高板数
    m = re.search(r'最高板数[^\d]*(\d+)', txt)
    if m: data['max_board'] = int(m.group(1))
    
    # 连板家数
    m = re.search(r'连板家数[^\d]*(\d+)', txt)
    if m: data['serial_limit_count'] = int(m.group(1))
    
    # 全市场情绪
    m = re.search(r'全市场情绪[^<]*<[^>]*>([\d.]+)%', txt)
    if m: data['market_emotion'] = float(m.group(1))
    
    # 涨停强度 — 近一年封板率
    stocks = []
    # 查找涨停股区域的每只股票
    strength_section = re.search(r'涨停强度(.*?)(?:最强风口|显示更多)', txt, re.DOTALL)
    if strength_section:
        section = strength_section.group(1)
        items = re.findall(r'<li[^>]*>(.*?)</li>', section, re.DOTALL)
        for item in items:
            # 提取: 名称 代码 涨幅 最新价 近一年封板率
            m2 = re.search(r'([^<]+)[\s\S]*?(\d{6})[\s\S]*?([\d.]+)%[\s\S]*?([\d.]+)[\s\S]*?(\d+)%', item)
            if m2:
                stocks.append({
                    'name': m2.group(1).strip(),
                    'code': m2.group(2),
                    'change_pct': float(m2.group(3)),
                    'price': float(m2.group(4)),
                    'seal_rate_real': float(m2.group(5)),
                })
    
    data['stocks'] = stocks
    
    # 昨日涨停今日表现
    m = re.search(r'昨日涨停今日表现[^<]*<[^>]*>([^<]+)', txt)
    if m: data['yesterday_yield'] = m.group(1).strip()
    
    return data


# ====== 同花顺涨停原因采集 ======

def fetch_ths_reasons():
    """从同花顺异动揭秘采集涨停原因（修复版 - 2026-05-11）"""
    url = "https://yuanchuang.10jqka.com.cn/mrnxgg_list/"
    try:
        proc = subprocess.Popen(
            ['curl', '-sL', '--connect-timeout', '5', '--max-time', '15',
             '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
             url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=18)
        html = out.decode('gbk', errors='replace')
        # 规范化空白
        html = re.sub(r'\s+', ' ', html)
    except:
        return []
    
    results = []
    # 提取涨停雷达条目 - 适配实际HTML结构
    items = re.findall(
        r'<li> <span class="arc-title"> <a target="_blank" title="涨停雷达[：:]([^"]+?)触及涨停" '
        r'href="([^"]+)" class="news-link" data-seq="(\d+)".*?</li>',
        html
    )
    
    for raw_title, href, seq in items:
        raw_title = raw_title.strip()
        # 解析 "tags name" -> name + tags
        m = re.match(r'(.+?)\s+(\S+)$', raw_title)
        if m:
            tags_str = m.group(1)
            name = m.group(2)
            tags = [t.strip() for t in tags_str.split('+') if t.strip()]
        else:
            name = raw_title
            tags = []
        
        results.append({
            'name': name,
            'tags': ' '.join(tags),
            'seq': seq,
            'href': href,
        })
    
    return results


# ====== 保存到数据库 ======

def save_strength(conn, data):
    """保存市场强度数据"""
    today = datetime.now().strftime("%Y-%m-%d")
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO limit_strength
        (date, total_limit, total_limit_yesterday, seal_rate, seal_rate_yesterday,
         open_count, open_count_yesterday, total_down_limit,
         max_board, serial_limit_count, market_emotion, yesterday_yield, create_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        today,
        data.get('total_limit'),
        None,  # 昨日数据需另外采集
        data.get('seal_rate'),
        None,
        data.get('open_count'),
        None,
        data.get('total_down_limit'),
        data.get('max_board'),
        data.get('serial_limit_count'),
        data.get('market_emotion'),
        str(data.get('yesterday_yield', '')),
        datetime.now().strftime('%H:%M:%S'),
    ))
    conn.commit()


def save_stocks_from_east(conn, stocks):
    """保存东方财富涨停股明细"""
    today = datetime.now().strftime("%Y-%m-%d")
    cur = conn.cursor()
    for s in stocks:
        if not s.get('code'):
            continue
        cur.execute("""
            INSERT OR REPLACE INTO limit_stocks
            (date, code, name, first_limit_time, board_count, limit_stat,
             turnover, amount_wan, close_price, change_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today, s['code'], s.get('name', ''),
            s.get('first_limit_time', ''), s.get('board_count', 0),
            s.get('limit_stat', ''),
            s.get('turnover', 0), s.get('amount_wan', 0),
            s.get('close_price', 0), s.get('change_pct', 0),
        ))
    conn.commit()


# ====== 主入口 ======

def main():
    args = sys.argv[1:]
    conn = init_db()
    today = datetime.now().strftime("%Y-%m-%d")
    
    print(f"📥 每日涨停数据采集 {today}")
    print("="*40)
    
    # 东方财富
    if not args or '--east' in args or '--all' in args:
        print("\n1️⃣ 东方财富涨停股池...")
        stocks = fetch_eastmoney_limit()
        if stocks:
            save_stocks_from_east(conn, stocks)
            board_dist = {}
            for s in stocks:
                bc = s.get('board_count', 0)
                board_dist[bc] = board_dist.get(bc, 0) + 1
            print(f"   采集{len(stocks)}只涨停股")
            print(f"   板数分布: {dict(sorted(board_dist.items()))}")
            # 显示封板时间分布
            time_dist = {}
            for s in stocks[:20]:
                t = s.get('first_limit_time', '')[:2]  # 取小时
                if t:
                    time_dist[t] = time_dist.get(t, 0) + 1
            if time_dist:
                print(f"   封板时间分布(前20): {dict(sorted(time_dist.items()))}")
        else:
            print(f"   采集失败（可能非交易时间或API限制）")
    
    # 大智慧
    if not args or '--dzh' in args:
        print("\n2️⃣ 大智慧涨停透视...")
        dzh_data = fetch_dzh_limit()
        if dzh_data and dzh_data.get('total_limit'):
            save_strength(conn, dzh_data)
            print(f"   涨停{dzh_data.get('total_limit')}只 封板率{dzh_data.get('seal_rate')}% "
                  f"炸板{dzh_data.get('open_count')}只 最高{dzh_data.get('max_board')}板")
            if dzh_data.get('stocks'):
                print(f"   近一年封板率采集{dzn_data.get('stocks', [])[:3]}")
        else:
            print(f"   采集失败")
    
    # 同花顺涨停原因
    if not args or '--ths' in args:
        print("\n3️⃣ 同花顺涨停原因...")
        reasons = fetch_ths_reasons()
        if reasons:
            # 保存到limit_stocks表
            saved = 0
            cur = conn.cursor()
            for r in reasons:
                try:
                    cur.execute("""INSERT OR REPLACE INTO limit_stocks
                        (date, code, name, ban_reason)
                        VALUES (?, ?, ?, ?)""",
                        (today, r.get('seq', ''), r['name'], r['tags']))
                    saved += 1
                except:
                    pass
            conn.commit()
            print(f"   采集{saved}条涨停原因（来自同花顺）")
            for r in reasons[:5]:
                print(f"   {r['name']}: {r['tags'][:50]}...")
        else:
            print(f"   采集失败")
    
    conn.close()
    print(f"\n✅ 采集完成")


if __name__ == '__main__':
    main()
