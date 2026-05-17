#!/usr/bin/env python3
"""
龙虎榜策略共享数据层 — 供三个策略引擎复用
"""
import sqlite3, os, json
from collections import defaultdict, Counter
from datetime import datetime, timedelta

DB = os.path.expanduser("~/astock/data/kline_cache.db")
LHB_DB = os.path.expanduser("~/astock/data/lhb_cache.db")

QUANT_KW = ['东方财富证券股份有限公司拉萨','中国国际金融股份有限公司上海分公司','中国国际金融股份有限公司北京建国门外大街','中信证券股份有限公司总部','瑞银证券有限责任公司','摩根大通证券有限责任公司','高盛(中国)证券有限责任公司','华泰证券股份有限公司总部']
YOUZI_KW = ['东方财富','华泰证券','中信证券','国泰君安','招商证券','银河证券','广发证券','国泰海通','中金财富','财通证券','平安证券']
LASA_KW = ['拉萨','东环路','团结路','金融城南环路','香曲东路','昌都','江苏大道']

FAMOUS_MAP = {
    '国泰海通证券股份有限公司武汉紫阳东路证券营业部': ('武汉紫阳', 'S'),
    '国泰海通证券股份有限公司成都北一环路证券营业部': ('成都北一环', 'S'),
    '中信证券股份有限公司上海分公司': ('中信上海', 'A'),
    '国泰海通证券股份有限公司总部': ('国泰海通总部', 'A'),
    '华泰证券股份有限公司总部': ('华泰总部', 'A'),
    '华鑫证券有限责任公司上海宛平南路证券营业部': ('炒股养家', 'B'),
    '华鑫证券有限责任公司上海分公司': ('养家分仓', 'B'),
    '国泰海通证券股份有限公司南京太平南路证券营业部': ('南京太平南', 'B'),
    '国泰海通证券股份有限公司上海长宁区江苏路证券营业部': ('章盟主', 'B'),
    '中国银河证券股份有限公司绍兴证券营业部': ('绍兴', 'B'),
    '开源证券股份有限公司西安太华路证券营业部': ('开源太华路', 'B'),
    '开源证券股份有限公司西安西大街证券营业部': ('开源西大街', 'B'),
    '中泰证券股份有限公司常州惠国路证券营业部': ('中泰常州', 'B'),
    '平安证券股份有限公司杭州曙光路证券营业部': ('平安曙光路', 'C'),
}

def classify(dealer):
    if '机构专用' in dealer: return '机构'
    if '股通' in dealer: return '北上'
    for q in QUANT_KW:
        if q in dealer: return '量化'
    for l in LASA_KW:
        if l in dealer: return '散户'
    for y in YOUZI_KW:
        if y in dealer: return '游资'
    return '其他'

def is_valid(code, name=''):
    if not (code.startswith('60') or code.startswith('00')): return False
    if 'ST' in name or '*ST' in name: return False
    return True

def detect_board(arr, idx):
    cnt = 0
    for i in range(idx, -1, -1):
        k = arr[i]; kp = arr[i-1] if i > 0 else arr[0]
        chg = (k['close'] - kp['close']) / max(kp['close'], 0.01) * 100
        if chg >= 9.8: cnt += 1
        else: break
    return cnt

def get_recent_trade_dates(days=5):
    """获取最近几个有龙虎榜数据的交易日"""
    conn = sqlite3.connect(LHB_DB)
    c = conn.cursor()
    c.execute("SELECT DISTINCT date FROM lhb_list WHERE date <= ? ORDER BY date DESC LIMIT ?",
              (datetime.now().strftime('%Y-%m-%d'), days))
    dates = [r[0] for r in c.fetchall()]
    conn.close()
    return dates

def load_kline_data():
    """加载K线数据"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    krows = conn.execute("SELECT code, date, open, close, high, low, volume FROM kline ORDER BY code, date").fetchall()
    conn.close()
    kb = defaultdict(list)
    for r in krows:
        if is_valid(r['code']): kb[r['code']].append(r)
    ki = {}
    for code, arr in kb.items():
        ki[code] = {arr[i]['date']: i for i in range(len(arr))}
    return kb, ki

def load_lhb_data(target_date=None):
    """
    加载指定日期的龙虎榜数据
    返回: { (date, code): { dealers, types, name } }
    """
    conn = sqlite3.connect(LHB_DB)
    conn.row_factory = sqlite3.Row
    if target_date:
        lrows = conn.execute("""
            SELECT d.date, d.code, d.direction, d.dealer, d.net, l.name as sname
            FROM lhb_detail d JOIN lhb_list l ON d.date=l.date AND d.code=l.code
            WHERE d.date = ?
            ORDER BY d.date, d.code
        """, (target_date,)).fetchall()
    else:
        lrows = conn.execute("""
            SELECT d.date, d.code, d.direction, d.dealer, d.net, l.name as sname
            FROM lhb_detail d JOIN lhb_list l ON d.date=l.date AND d.code=l.code
            ORDER BY d.date, d.code
        """).fetchall()
    conn.close()
    
    filtered = [r for r in lrows if is_valid(r['code'], r['sname'] or '')]
    
    stock_signals = defaultdict(lambda: {'dealers': [], 'types': Counter(), 'name': ''})
    for r in filtered:
        if r['direction'] != 'buy': continue
        key = (r['date'], r['code'])
        stock_signals[key]['dealers'].append(r['dealer'])
        stock_signals[key]['types'][classify(r['dealer'])] += 1
        stock_signals[key]['name'] = r['sname']
    
    return stock_signals

def get_tencent_quote(code):
    """获取腾讯实时行情"""
    import subprocess
    url = f"http://qt.gtimg.cn/q={code}"
    try:
        r = subprocess.run(['curl', '-s', url], capture_output=True, timeout=5)
        text = r.stdout.decode('gbk', errors='replace')
        parts = text.split('~')
        if len(parts) > 32:
            name = parts[1]
            price = float(parts[3]) if parts[3] else 0
            open_p = float(parts[5]) if parts[5] else 0
            high = float(parts[33]) if parts[33] else 0
            low = float(parts[34]) if parts[34] else 0
            prev_close = float(parts[4]) if parts[4] else 0
            volume = int(parts[6]) if parts[6] else 0
            amount = float(parts[37]) if parts[37] else 0
            if prev_close > 0:
                chg_pct = (price - prev_close) / prev_close * 100
                open_chg = (open_p - prev_close) / prev_close * 100 if open_p else 0
            else:
                chg_pct = 0; open_chg = 0
            return {
                'code': code, 'name': name, 'price': price,
                'open': open_p, 'high': high, 'low': low,
                'prev_close': prev_close, 'chg_pct': round(chg_pct, 2),
                'open_chg': round(open_chg, 2),
                'volume': volume, 'amount': amount,
            }
    except: pass
    return None
