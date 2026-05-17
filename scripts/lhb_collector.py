#!/usr/bin/env python3
"""
📡 龙虎榜数据采集器 v3 — 新浪真实API

数据流:
  1. 龙虎榜列表页：获取当日所有上榜股票（含分类/涨跌幅/成交量）
  2. 详情页API：获取每只股票的买卖前五营业部详情
  3. SQLite持久化：lhb_cache.db

用法：
  python3 scripts/lhb_collector.py                    # 采集当日
  python3 scripts/lhb_collector.py 2026-05-07         # 指定日期
  python3 scripts/lhb_collector.py --backtest         # 批量回采近60天
  python3 scripts/lhb_collector.py --daily-cron       # 每日增量采集
"""
import os, sys, re, json, urllib.request, sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

BASE = os.path.expanduser("~/astock")
DB_PATH = os.path.join(BASE, "data", "lhb_cache.db")
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

# ══════════════════════════════════════════
# 一、列表页采集
# ══════════════════════════════════════════

def fetch_list_page(trade_date):
    """获取龙虎榜列表页，返回所有上榜股票的基本信息"""
    url = f"https://vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/lhb/index.phtml?tradedate={trade_date}"
    
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('gbk', errors='replace')
    except Exception as e:
        return None, str(e)
    
    if len(html) < 1000:
        return None, "页面太小"
    
    stocks = []
    
    # 找所有股票区块: 每个股票有代码、名称、涨跌幅、分类编码
    # 列表页HTML结构:
    # <tr class="head">
    #   <td>1</td>
    #   <td><a ...>000066</a></td>
    #   <td><a ...>中国长城</a></td>
    #   <td>23.98</td>
    #   <td>8.81</td>
    #   <td>50968.8218</td>
    #   <td>1199715.1490</td>
    #   <td><a href="javascript:void(0)" onclick="showDetail('01','000066','2026-05-07',this)">查看</a></td>
    # </tr>
    
    pattern = r"showDetail\('([^']+)','(\d{6})','(\d{4}-\d{2}-\d{2})'"
    matches = re.findall(pattern, html)
    
    if not matches:
        return None, "未找到股票列表"
    
    seen = set()
    for type_code, stock_code, date_str in matches:
        if stock_code not in seen:
            seen.add(stock_code)
            stocks.append({
                'code': stock_code,
                'type': type_code,
                'date': date_str,
            })
    
    # 补充名称和涨跌幅
    # 从HTML中提取完整表格行
    rows = re.findall(
        r'<tr[^>]*>.*?<td[^>]*>(\d+)</td>.*?'
        r'<td[^>]*>.*?(\d{6}).*?</td>.*?'
        r'<td[^>]*>.*?<a[^>]*>([^<]+)</a>.*?</td>.*?'
        r'<td[^>]*[^>]*>([\d.]+)</td>.*?'
        r'<td[^>]*[^>]*>([-\d.]+)</td>',
        html, re.DOTALL
    )
    
    code_map = {}
    for row in rows:
        if len(row) >= 5:
            seq, code, name, price, chg = row[:5]
            code_map[code] = {'name': name, 'chg': chg, 'price': price}
    
    for s in stocks:
        if s['code'] in code_map:
            s['name'] = code_map[s['code']]['name']
            s['chg'] = float(code_map[s['code']]['chg'])
            s['price'] = float(code_map[s['code']]['price'])
        else:
            s['name'] = ''
            s['chg'] = 0
            s['price'] = 0
    
    return stocks, None

# ══════════════════════════════════════════
# 二、详情页API采集
# ══════════════════════════════════════════

def fetch_detail(code, trade_date, type_code='01'):
    """获取单只股票的龙虎榜买卖详情"""
    url = (
        f"https://vip.stock.finance.sina.com.cn/q/api/jsonp.php/"
        f"var%20details=/InvestConsultService.getLHBComBSData?"
        f"symbol={code}&tradedate={trade_date}&type={type_code}"
    )
    
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode('gbk', errors='replace')
    except Exception as e:
        return None, str(e)
    
    # 解析JSONP
    # 去掉安全前缀 /*<script>...</script>*/
    raw = re.sub(r'^/\*.*?\*/', '', raw, flags=re.DOTALL)
    # 提取 details=( {...} )
    m = re.search(r'details=\(\s*(\{.*\})\s*\)', raw, re.DOTALL)
    if not m:
        return None, "JSONP格式异常: 无法定位JSON对象"
    
    json_str = m.group(1)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return None, f"JSON解析失败: {e}"
    
    result = {'buy': [], 'sell': []}
    
    # 买入方
    for item in data.get('buy', []):
        try:
            result['buy'].append({
                'dealer': item.get('comName', ''),
                'buy_amt': float(item.get('buyAmount', 0)),
                'sell_amt': float(item.get('sellAmount', 0)),
                'net': float(item.get('netAmount', 0)),
            })
        except: pass
    
    # 卖出方
    for item in data.get('sell', []):
        try:
            result['sell'].append({
                'dealer': item.get('comName', ''),
                'buy_amt': float(item.get('buyAmount', 0)),
                'sell_amt': float(item.get('sellAmount', 0)),
                'net': float(item.get('netAmount', 0)),
            })
        except: pass
    
    if not result['buy'] and not result['sell']:
        return None, "无买卖数据"
    
    return result, None

# ══════════════════════════════════════════
# 三、SQLite数据库
# ══════════════════════════════════════════

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS lhb_list (
            date TEXT, code TEXT, name TEXT, type TEXT,
            price REAL, chg REAL,
            PRIMARY KEY (date, code, type)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS lhb_detail (
            date TEXT, code TEXT, direction TEXT,
            seq INTEGER, dealer TEXT,
            buy_amt REAL, sell_amt REAL, net REAL,
            PRIMARY KEY (date, code, direction, seq)
        )
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_lhb_date ON lhb_list(date)
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_lhb_code ON lhb_list(code)
    ''')
    conn.commit()
    return conn

def save_stock(conn, stock):
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO lhb_list (date, code, name, type, price, chg)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (stock['date'], stock['code'], stock.get('name',''),
          stock['type'], stock.get('price',0), stock.get('chg',0)))

def save_detail(conn, date_str, code, detail):
    c = conn.cursor()
    for i, b in enumerate(detail.get('buy', [])):
        c.execute('''
            INSERT OR REPLACE INTO lhb_detail (date, code, direction, seq, dealer, buy_amt, sell_amt, net)
            VALUES (?, ?, 'buy', ?, ?, ?, ?, ?)
        ''', (date_str, code, i+1, b['dealer'], b['buy_amt'], b['sell_amt'], b['net']))
    for i, s in enumerate(detail.get('sell', [])):
        c.execute('''
            INSERT OR REPLACE INTO lhb_detail (date, code, direction, seq, dealer, buy_amt, sell_amt, net)
            VALUES (?, ?, 'sell', ?, ?, ?, ?, ?)
        ''', (date_str, code, i+1, s['dealer'], s['buy_amt'], s['sell_amt'], s['net']))

def get_saved_dates(conn):
    c = conn.cursor()
    c.execute('SELECT DISTINCT date FROM lhb_list ORDER BY date')
    return set(r[0] for r in c.fetchall())

# ══════════════════════════════════════════
# 四、分类辅助
# ══════════════════════════════════════════

def classify_dealer(dealer):
    """判断营业部类型：机构/游资/北上/未知"""
    if '机构专用' in dealer:
        return '机构'
    if '深股通' in dealer or '沪股通' in dealer:
        return '北上'
    # 知名游资席位
    known_youzi = ['国泰君安', '华泰证券', '中信证券', '招商证券', 
                   '银河证券', '广发证券', '海通证券', '中金财富',
                   '财通证券', '东方财富', '平安证券']
    for k in known_youzi:
        if k in dealer:
            return '游资'
    return '其他'

# ══════════════════════════════════════════
# 五、主力模式识别
# ══════════════════════════════════════════

def detect_pattern(stock, detail):
    """识别龙虎榜主力模式"""
    if not detail:
        return '未知'
    
    buys = detail.get('buy', [])
    sells = detail.get('sell', [])
    total_buy = sum(b['buy_amt'] for b in buys)
    total_sell = sum(s['sell_amt'] for s in sells)
    net = total_buy - total_sell
    
    # 机构净买入占比
    inst_buy = sum(b['buy_amt'] for b in buys if '机构专用' in b['dealer'])
    inst_sell = sum(s['sell_amt'] for s in sells if '机构专用' in s['dealer'])
    inst_net = inst_buy - inst_sell
    
    # 北上净买入
    north_buy = sum(b['buy_amt'] for b in buys if '股通' in b['dealer'])
    north_sell = sum(s['sell_amt'] for s in sells if '股通' in s['dealer'])
    north_net = north_buy - north_sell
    
    # 判断模式
    if net > 0 and inst_net > total_buy * 0.3:
        return '机构主导买入'
    elif net > 0 and north_net > total_buy * 0.3:
        return '北上资金买入'
    elif net > 0:
        return '游资主导买入'
    elif net < -total_buy * 0.3:
        return '资金大幅出逃'
    elif abs(net) / max(total_buy, 0.1) < 0.1:
        return '游资对倒'
    else:
        return '资金均衡'
    
# ══════════════════════════════════════════
# 六、采集主函数
# ══════════════════════════════════════════

def collect_date(conn, trade_date):
    """采集指定日期的完整龙虎榜数据"""
    print(f"  📥 {trade_date}: ", end='', flush=True)
    
    # 1. 获取列表
    stocks, err = fetch_list_page(trade_date)
    if not stocks:
        print(f"❌ {err}")
        return 0
    
    # 2. 保存列表
    for s in stocks:
        save_stock(conn, s)
    conn.commit()
    
    print(f"{len(stocks)}只上榜 ", end='', flush=True)
    
    # 3. 获取详情
    detail_count = 0
    for s in stocks:
        detail, err = fetch_detail(s['code'], trade_date, s['type'])
        if detail:
            save_detail(conn, trade_date, s['code'], detail)
            detail_count += 1
            # 模式识别
            pattern = detect_pattern(s, detail)
            s['pattern'] = pattern
    
    conn.commit()
    print(f"详{detail_count}家 ")
    
    # 汇总
    patterns = defaultdict(int)
    for s in stocks:
        p = s.get('pattern', '未知')
        patterns[p] += 1
    if patterns:
        summary = ' | '.join(f"{k}:{v}" for k, v in sorted(patterns.items(), key=lambda x:-x[1]))
        print(f"  → {summary}")
    
    return len(stocks)

def collect_range(start_date, end_date):
    """批量采集日期范围"""
    conn = init_db()
    saved = get_saved_dates(conn)
    total = 0
    
    current = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    while current <= end:
        date_str = current.strftime('%Y-%m-%d')
        if current.weekday() < 5:  # 工作日
            if date_str not in saved:
                n = collect_date(conn, date_str)
                total += n
            else:
                print(f"  📄 {date_str}: 已缓存")
        current += timedelta(days=1)
    
    conn.close()
    print(f"\n✅ 共采集 {total} 条龙虎榜记录")
    return total

def collect_today():
    """采集今天"""
    today = datetime.now().strftime('%Y-%m-%d')
    conn = init_db()
    n = collect_date(conn, today)
    conn.close()
    return n

# ══════════════════════════════════════════
# CLI入口
# ══════════════════════════════════════════

if __name__ == '__main__':
    args = sys.argv[1:]
    
    if '--backtest' in args:
        print("📡 批量回采龙虎榜...")
        collect_range('2026-03-01', datetime.now().strftime('%Y-%m-%d'))
    elif '--daily-cron' in args:
        print("📡 每日增量采集...")
        conn = init_db()
        collect_date(conn, datetime.now().strftime('%Y-%m-%d'))
        conn.close()
    elif args and not args[0].startswith('--'):
        # 指定日期
        print("📡 采集指定日期...")
        conn = init_db()
        collect_date(conn, args[0])
        conn.close()
    else:
        # 默认采集今天
        print("📡 采集今日龙虎榜...")
        collect_today()
    
    # 查看数据库统计
    conn = init_db()
    c = conn.cursor()
    c.execute('SELECT COUNT(DISTINCT date) FROM lhb_list')
    days = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM lhb_list')
    stocks = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM lhb_detail')
    details = c.fetchone()[0]
    print(f"\n📊 数据库: {days}天 | {stocks}只上榜 | {details}条营业部记录")
    conn.close()
