#!/usr/bin/env python3
"""
📡 龙虎榜策略研究框架 — 真实龙虎榜数据驱动

核心认知：
  1. T-1日龙虎榜 → T日开盘前看到 → 选股+板块信号（买入信号）
  2. T日龙虎榜 → T日收盘后出 → 次日卖出/持有决策（卖出信号）

数据来源：
  - 新浪龙虎榜页面（477KB，GB2312编码）
  - kline_cache.db（T+N收益验证）

时间链：
  T-2: 龙虎榜出现         ← 盘前选股信号
  T-1: 龙虎榜后第一个交易日 ← 板块+个股择时
  T:   交易当天           ← 建仓/追涨
  T+1: 次交易日          ← 卖出/持有决策
  
研究维度：
  1. 板块维度：龙虎榜集中出现在哪些板块？板块联动效应
  2. 选股维度：龙虎榜个股 → T日打板胜率
  3. 买入信号：龙虎榜机构净买 vs 游资净买 → 次日收益率
  4. 卖出信号：T日龙虎榜前五买入 vs 卖出 → T+1开盘/收盘表现
"""
import os, sys, re, json, subprocess
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timedelta, date
from urllib.parse import quote

BASE = os.path.expanduser("~/astock")
DB_PATH = os.path.join(BASE, "data", "kline_cache.db")
LHB_CACHE_DIR = os.path.join(BASE, "data", "lhb_cache")

os.makedirs(LHB_CACHE_DIR, exist_ok=True)

# ══════════════════════════════════════════
# 龙虎榜数据采集
# ══════════════════════════════════════════

def fetch_lhb_page(trade_date):
    """从新浪获取指定日期的龙虎榜页面"""
    url = f"https://vip.stock.finance.sina.com.cn/q/go.php/vInvestConsult/kind/lhb/index.phtml?tradedate={trade_date}"
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', '8', '--max-time', '15', url],
            capture_output=True, text=True, timeout=20
        )
        if len(r.stdout) < 1000:
            return None
        # GB2312转UTF-8
        try:
            text = r.stdout.encode('latin1').decode('gbk')
        except:
            text = r.stdout
        return text
    except:
        return None

def parse_lhb_page(html):
    """
    解析新浪龙虎榜HTML，提取每只上榜个股的龙虎榜详情
    
    返回: {
        'date': '2026-05-07',
        'stocks': [
            {
                'code': '000066',
                'name': '中国长城',
                'reason': '日涨幅偏离值达7%',
                'buy_top5': [
                    {'dealer': '中信证券', 'buy_amt': 5000, 'sell_amt': 100, 'net': 4900},
                    ...
                ],
                'sell_top5': [
                    {'dealer': '国泰君安', 'buy_amt': 200, 'sell_amt': 3000, 'net': -2800},
                    ...
                ],
                'total_buy': 12000,
                'total_sell': 8000,
                'net_buy': 4000,
                'buy_concentration': 0.6,  # 买入集中度
            }
        ]
    }
    """
    stocks = []
    # 找所有龙虎榜个股区块
    # 每个个股区块包含：股票名称、代码、买入前五、卖出前五
    
    # 提取所有股票区块（用股票代码和表格结构定位）
    # 新浪龙虎榜页面结构：
    # <tr><td>股票代码</td><td>股票名称</td><td>收盘价</td><td>涨跌幅</td>...</tr>
    # 然后是买入前五的表格和卖出前五的表格
    
    # 先提取所有股票代码和名称
    code_pattern = re.compile(r'<a\s+href="[^"]*code=([a-z]{2}|)(\d{6})"[^>]*>([^<]+)</a>')
    code_matches = code_pattern.findall(html)
    
    if not code_matches:
        # 备选：找td里的股票代码
        code_pattern2 = re.compile(r'<td>\s*(\d{6})\s*</td>')
        code_matches2 = code_pattern2.findall(html)
        # 找股票名称
        name_pattern = re.compile(r'<td><a\s+href="[^"]*">([^<]+)</a></td>')
        name_matches = name_pattern.findall(html)
        
        if code_matches2:
            # 按龙虎榜页面结构，每个股票区块之间有分隔
            # 用股票代码位置分割
            pass
    
    if not code_matches:
        # 用更简单的方法：直接按换行解析文本表格
        text = re.sub(r'<[^>]+>', '\n', html)
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        
        # 查找股票代码（6位数字）
        codes_found = []
        for line in lines:
            m = re.match(r'^(\d{6})$', line)
            if m:
                codes_found.append(m.group(1))
        
        # 找名字和买入/卖出金额
        i = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r'^\d{6}$', line):
                code = line
                # 名字通常在下一行
                name = lines[i+1] if i+1 < len(lines) else ''
                # 跳过一段直到找到买入前五
                stock = {
                    'code': code,
                    'name': name,
                    'buy_top5': [],
                    'sell_top5': [],
                    'total_buy': 0,
                    'total_sell': 0,
                    'net_buy': 0,
                }
                
                # 找前五买入/卖出表格
                # 格式：
                # 买入金额最大前5名
                # 营业部名称 买入金额 卖出金额 净额
                # ...
                # 卖出金额最大前5名
                # ...
                
                j = i
                buy_count = 0
                sell_count = 0
                in_buy_section = False
                in_sell_section = False
                
                while j < min(i + 60, len(lines)):
                    l = lines[j]
                    
                    if '买入' in l and '前5' in l:
                        in_buy_section = True
                        in_sell_section = False
                    elif '卖出' in l and '前5' in l:
                        in_sell_section = True
                        in_buy_section = False
                    elif l.startswith('交易营业所') or l.startswith('买入金额'):
                        pass
                    elif in_buy_section and buy_count < 5:
                        # 尝试解析: 营业部名 买入额 卖出额 净额
                        parts = l.split()
                        if len(parts) >= 4 and re.match(r'^[\d.]+$', parts[-3].replace(',', '')):
                            dealer = ' '.join(parts[:-3])
                            buy_amt = float(parts[-3].replace(',', ''))
                            sell_amt = float(parts[-2].replace(',', ''))
                            net = float(parts[-1].replace(',', ''))
                            stock['buy_top5'].append({
                                'dealer': dealer, 'buy_amt': buy_amt,
                                'sell_amt': sell_amt, 'net': net
                            })
                            buy_count += 1
                    elif in_sell_section and sell_count < 5:
                        parts = l.split()
                        if len(parts) >= 4 and re.match(r'^[\d.]+$', parts[-3].replace(',', '')):
                            dealer = ' '.join(parts[:-3])
                            buy_amt = float(parts[-3].replace(',', ''))
                            sell_amt = float(parts[-2].replace(',', ''))
                            net = float(parts[-1].replace(',', ''))
                            stock['sell_top5'].append({
                                'dealer': dealer, 'buy_amt': buy_amt,
                                'sell_amt': sell_amt, 'net': net
                            })
                            sell_count += 1
                    elif '前5名' in l and ('买入' in l or '卖出' in l):
                        # 新股票开始
                        break
                    
                    j += 1
                
                stock['total_buy'] = sum(x['buy_amt'] for x in stock['buy_top5'])
                stock['total_sell'] = sum(x['sell_amt'] for x in stock['sell_top5'])
                stock['net_buy'] = stock['total_buy'] - stock['total_sell']
                
                if stock['buy_top5']:
                    stocks.append(stock)
                
                i = j
            else:
                i += 1
    
    return stocks

def parse_lhb_simple(html):
    """
    简化版解析：直接从HTML文本块中提取龙虎榜数据
    新浪龙虎榜页面的数据是表格形式的，但结构不太规整
    用正则解析关键数据
    """
    stocks = []
    
    # 第一步：找出所有"买入金额最大前5名"和"卖出金额最大前5名"区块
    # 每个区块前有股票代码
    
    # 删除所有HTML标签，保留文本内容
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '\n', text)
    # 清理多余空格和空行
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if line:
            lines.append(line)
    
    # 找出"龙虎榜"股票区块
    # 格式模式: 股票代码 → 股票名称 → 收益说明 → "买入金额最大前5名"
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 找股票代码行（恰好是6位数字）
        if re.match(r'^(\d{6})$', line):
            code = line
            name = ''
            reason = ''
            
            # 找名字（通常是下几行，且不是数字）
            for j in range(i+1, min(i+10, len(lines))):
                l = lines[j]
                # 跳过纯数字和常见表格头
                if re.match(r'^\d+\.?\d*$', l):
                    continue
                if l in ['买入金额最大前5名', '卖出金额最大前5名', '交易营业所',
                         '买入金额(万元)', '卖出金额(万元)', '净买入(万元)']:
                    continue
                if not name:
                    name = l
                elif l and not reason:
                    reason = l
                    break
            
            # 找到买入前五和卖出前五
            stock = {
                'code': code, 'name': name, 'reason': reason,
                'buy_top5': [], 'sell_top5': [],
                'total_buy': 0, 'total_sell': 0, 'net_buy': 0,
            }
            
            # 从当前位置往后找数据
            in_buy = False
            in_sell = False
            buy_count = 0
            sell_count = 0
            
            j = i + 2
            while j < len(lines) and (buy_count < 5 or sell_count < 5 or (not in_buy and not in_sell)):
                l = lines[j]
                
                if '买入金额最大前5名' in l:
                    in_buy = True; in_sell = False
                    j += 1; continue
                elif '卖出金额最大前5名' in l:
                    in_sell = True; in_buy = False
                    j += 1; continue
                
                # 跳过表头
                if l in ['交易营业所', '买入金额(万元)', '卖出金额(万元)', '净买入(万元)', 
                         '买入金额（万元）', '卖出金额（万元）', '净买入（万元）']:
                    j += 1; continue
                
                if in_buy and buy_count < 5:
                    # 解析数据行: 营业部名 + 金额 + 金额 + 金额
                    parts = l.split()
                    # 过滤掉纯数字的干扰行
                    if len(parts) >= 4:
                        try:
                            # 最后三个应该都是数字
                            buy_amt = float(parts[-3].replace(',', ''))
                            sell_amt = float(parts[-2].replace(',', ''))
                            net = float(parts[-1].replace(',', ''))
                            dealer = ' '.join(parts[:-3])
                            stock['buy_top5'].append({
                                'dealer': dealer, 'buy_amt': buy_amt,
                                'sell_amt': sell_amt, 'net': net
                            })
                            buy_count += 1
                        except ValueError:
                            pass
                
                elif in_sell and sell_count < 5:
                    parts = l.split()
                    if len(parts) >= 4:
                        try:
                            buy_amt = float(parts[-3].replace(',', ''))
                            sell_amt = float(parts[-2].replace(',', ''))
                            net = float(parts[-1].replace(',', ''))
                            dealer = ' '.join(parts[:-3])
                            stock['sell_top5'].append({
                                'dealer': dealer, 'buy_amt': buy_amt,
                                'sell_amt': sell_amt, 'net': net
                            })
                            sell_count += 1
                        except ValueError:
                            pass
                
                # 下一个股票区块开始
                if re.match(r'^\d{6}$', l) and j > i + 5:
                    break
                
                j += 1
            
            stock['total_buy'] = sum(x['buy_amt'] for x in stock['buy_top5'])
            stock['total_sell'] = sum(x['sell_amt'] for x in stock['sell_top5'])
            stock['net_buy'] = stock['total_buy'] - stock['total_sell']
            
            if stock['buy_top5']:
                stocks.append(stock)
            
            i = j
        else:
            i += 1
    
    return stocks

# ══════════════════════════════════════════
# 龙虎榜 → K线验证
# ══════════════════════════════════════════

def verify_with_kline(stock, trade_date_str, kline_data):
    """
    用K线数据验证龙虎榜信号 → 次日收益
    
    返回每个股票的T+1/T+3收益
    """
    code = stock['code']
    klines = kline_data.get(code, [])
    if not klines:
        return None
    
    # 找到trade_date在K线中的位置
    idx = -1
    for i, k in enumerate(klines):
        if k['date'] == trade_date_str:
            idx = i
            break
    
    if idx < 0 or idx + 6 >= len(klines):
        return None
    
    d = klines[idx]
    prev_close = klines[idx-1]['close'] if idx > 0 else d['open']
    
    # T日涨幅
    chg = (d['close'] - prev_close) / prev_close * 100
    
    # T+1 各卖出方式
    n1 = klines[idx+1]
    t1_open_ret = (n1['open'] - d['close']) / d['close'] * 100
    t1_close_ret = (n1['close'] - d['close']) / d['close'] * 100
    t1_high_ret = (n1['high'] - d['close']) / d['close'] * 100
    is_t1_limit = n1['close'] >= d['close'] * 1.095
    
    # T+3
    n3 = klines[idx+3]
    t3_close_ret = (n3['close'] - d['close']) / d['close'] * 100
    
    # T+5
    n5 = klines[idx+5]
    t5_close_ret = (n5['close'] - d['close']) / d['close'] * 100
    
    return {
        'code': code,
        'name': stock['name'],
        'date': trade_date_str,
        'chg': round(chg, 2),
        'net_buy': stock['net_buy'],
        'total_buy': stock['total_buy'],
        'num_buyers': len(stock['buy_top5']),
        'buy_concentration': stock['buy_top5'][0]['buy_amt'] / max(stock['total_buy'], 1) if stock['buy_top5'] else 0,
        't1_open_ret': round(t1_open_ret, 2),
        't1_close_ret': round(t1_close_ret, 2),
        't1_high_ret': round(t1_high_ret, 2),
        't3_close_ret': round(t3_close_ret, 2),
        't5_close_ret': round(t5_close_ret, 2),
        'is_t1_limit': is_t1_limit,
        # 龙虎榜特征
        'buy_dealers': [b['dealer'] for b in stock['buy_top5']],
        'sell_dealers': [s['dealer'] for s in stock['sell_top5']],
    }

# ══════════════════════════════════════════
# K线数据加载
# ══════════════════════════════════════════

def load_kline_data():
    """加载K线数据"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT code, date, open, close, high, low, volume FROM kline ORDER BY code, date").fetchall()
    conn.close()
    
    data = defaultdict(list)
    for r in rows:
        data[r[0]].append({'date': r[1], 'open': r[2], 'close': r[3], 'high': r[4], 'low': r[5], 'volume': r[6]})
    return data

# ══════════════════════════════════════════
# 批量分析
# ══════════════════════════════════════════

def scan_lhb_dates(start_date='2026-01-01', end_date=None, max_days=60):
    """扫描指定日期范围的龙虎榜数据"""
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')
    
    # 生成交易日历（周一到周五）
    all_lhb_data = {}  # {date: [stocks]}
    
    current = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    days_scanned = 0
    
    while current <= end and days_scanned < max_days:
        if current.weekday() < 5:  # 周一到周五
            date_str = current.strftime('%Y-%m-%d')
            print(f"  扫描 {date_str}...", end=' ')
            
            html = fetch_lhb_page(date_str)
            if html:
                stocks = parse_lhb_simple(html)
                if stocks:
                    all_lhb_data[date_str] = stocks
                    print(f"{len(stocks)}只")
                else:
                    print("解析失败或为空")
            else:
                print("无数据")
        
        current += timedelta(days=1)
        days_scanned += 1
    
    return all_lhb_data

def main():
    print("=" * 70)
    print("📡 龙虎榜策略研究框架")
    print("=" * 70)
    
    args = sys.argv[1:]
    
    # 先扫描近期数据
    print("\n📥 扫描龙虎榜数据...")
    lhb_data = scan_lhb_dates('2026-04-20', max_days=60)
    
    if not lhb_data:
        print("❌ 无法获取龙虎榜数据，检查网络或尝试不同日期")
        return
    
    print(f"\n📊 获取到 {len(lhb_data)} 天龙虎榜数据")
    total_stocks = sum(len(v) for v in lhb_data.values())
    print(f"   共 {total_stocks} 条龙虎榜记录")
    
    # 加载K线数据
    print("\n📊 加载K线数据...")
    kline_data = load_kline_data()
    print(f"   已加载 {len(kline_data)} 只股票K线")
    
    # ══════════════════════════════════════
    # 维度1: 板块分析 — 龙虎榜集中度
    # ══════════════════════════════════════
    print("\n" + "═" * 70)
    print("📊 维度1: 龙虎榜板块集中度分析（选板块策略）")
    print("═" * 70)
    
    # 统计每个板块的上榜次数
    sector_stocks = {}  # 自动从板块成分判断
    from collections import defaultdict as dd
    sector_counts = Counter()
    
    for date_str, stocks in lhb_data.items():
        for s in stocks:
            sector_counts[s['code']] += 1
    
    # 按板块判断
    sector_map = {
        'chip': ['603986','603019','600584','603005','603160','002049','600171','603893','002185'],
        'gpu': ['601138','603019','000977','000063','002916'],
        'robot': ['002472','002896','300124','601689','603662'],
        'ai': ['002230','300418','603533','002555','002517'],
        'battery': ['002074','300014','002460','002709','300750'],
        'low_alt': ['002085','600580','603885','000099'],
    }
    sector_stock_counts = dd(int)
    sector_stock_details = dd(list)
    
    for code, cnt in sector_counts.most_common(100):
        for sector_name, sector_codes in sector_map.items():
            if code in sector_codes:
                sector_stock_counts[sector_name] += cnt
                sector_stock_details[sector_name].append((code, cnt))
    
    if sector_stock_counts:
        print(f"{'板块':<12s} {'上榜次数':>8s} {'个股明细':<30s}")
        print("-" * 55)
        for sector, cnt in sorted(sector_stock_counts.items(), key=lambda x: -x[1]):
            detail = ','.join(f"{code}({c})" for code, c in sector_stock_details[sector][:3])
            print(f"{sector:<12s} {cnt:>8d} {detail:<30s}")
    else:
        print("   无板块关联数据（龙虎榜标的多为非热点板块）")
    
    # ══════════════════════════════════════
    # 维度2: 选股策略 — 龙虎榜买入信号
    # ══════════════════════════════════════
    print("\n" + "═" * 70)
    print("📊 维度2: 龙虎榜买入信号分析（选股策略）")
    print("═" * 70)
    
    # 按净买入分档统计T+1收益
    all_verified = []
    for date_str, stocks in lhb_data.items():
        for s in stocks:
            result = verify_with_kline(s, date_str, kline_data)
            if result:
                all_verified.append(result)
    
    print(f"   总验证样本: {len(all_verified)}条\n")
    
    # 按净买入分档
    for threshold, label in [
        (-10000, '净买<-1亿(大卖出)'), (0, '净买-1亿~0(卖出)'),
        (10000, '净买0~1亿(小幅买入)'), (30000, '净买1~3亿(中幅买入)'),
        (999999, '净买>3亿(大规模买入)'),
    ]:
        if isinstance(threshold, int):
            continue
        
    # 分档展示
    net_cats = [
        ('净买入>3亿', lambda r: r['net_buy'] >= 30000),
        ('净买入1~3亿', lambda r: 10000 <= r['net_buy'] < 30000),
        ('净买入0~1亿', lambda r: 0 <= r['net_buy'] < 10000),
        ('净卖出0~1亿', lambda r: -10000 <= r['net_buy'] < 0),
        ('净卖出>1亿', lambda r: r['net_buy'] < -10000),
    ]
    
    for name, check in net_cats:
        sub = [r for r in all_verified if check(r)]
        if len(sub) < 3: continue
        t1c = [r['t1_close_ret'] for r in sub]
        avg = sum(t1c) / len(t1c)
        win = sum(1 for v in t1c if v > 0) / len(t1c) * 100
        t1h = [r['t1_high_ret'] for r in sub]
        avg_h = sum(t1h) / len(t1h)
        print(f"  {name:<20s}({len(sub):>3d}次): T+1收{avg:+.2f}% 胜{win:.1f}% T+1高{avg_h:+.2f}%")
    
    # ══════════════════════════════════════
    # 维度3: 龙虎榜机构vs游资
    # ══════════════════════════════════════
    print("\n" + "═" * 70)
    print("📊 维度3: 机构席位移踪 — 机构净买 vs 游资净买")
    print("═" * 70)
    
    # 机构席位的常见名称关键词
    inst_keywords = ['机构专用', '深股通', '沪股通', '中信证券', '国泰君安']
    
    for name, inst_check, label in [
        ('机构专用', lambda d: any('机构专用' in b['dealer'] for b in d['buy_top5']), '机构席位买入'),
        ('深/沪股通', lambda d: any('股通' in b['dealer'] for b in d['buy_top5']), '北上资金买入'),
        ('知名游资', lambda d: any('国泰' in b['dealer'] or '中信证券' in b['dealer'] or '华泰' in b['dealer'] or '招商' in b['dealer'] for b in d['buy_top5']), '知名营业部买入'),
    ]:
        sub = []
        for s in all_verified:
            # 找到原始stock数据
            for date_str, stocks in lhb_data.items():
                for st in stocks:
                    if st['code'] == s['code'] and inst_check(st):
                        sub.append(s)
                        break
                if sub and sub[-1] == s:
                    break
        
        if len(sub) < 3: continue
        t1c = [r['t1_close_ret'] for r in sub]
        avg = sum(t1c) / len(t1c)
        win = sum(1 for v in t1c if v > 0) / len(t1c) * 100
        t1h = [r['t1_high_ret'] for r in sub]
        avg_h = sum(t1h) / len(t1h)
        print(f"  {label:<20s}({len(sub):>3d}次): T+1收{avg:+.2f}% 胜{win:.1f}% T+1高{avg_h:+.2f}%")
    
    print(f"\n{'='*70}")
    print("📝 龙虎榜策略总结")
    print(f"{'='*70}")
    print("""
T-1龙虎榜（盘前可看到的信号）:
  → 板块策略: 看哪个板块上榜最多，选板块
  → 选股策略: 净买入最大的个股是首选
  → 买入时机: T日开盘追入，非盘尾

T日龙虎榜（收盘后才能看到）:
  → 卖出信号: 机构净买多→持有，游资对倒→卖出
  → 板块信号: 板块多只上榜→持续性看
  → 次日策略: 龙虎榜净买>3亿 → T+1冲高卖
""")
    
    if all_verified:
        # 找出最优的几个样本
        print(f"\n🏆 最佳龙虎榜买入信号:")
        sorted_by_t1 = sorted(all_verified, key=lambda x: -x['t1_close_ret'])[:5]
        for s in sorted_by_t1:
            dealers = ', '.join(s['buy_dealers'][:2])
            print(f"  {s['name']}({s['code']}) 净买{s['net_buy']/10000:.1f}亿 {dealers}")
            print(f"    T日+{s['chg']:.1f}% → T+1开{s['t1_open_ret']:+.1f}% 收{s['t1_close_ret']:+.1f}% 高{s['t1_high_ret']:+.1f}%")

if __name__ == '__main__':
    main()
