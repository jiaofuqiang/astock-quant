#!/usr/bin/env python3
"""
📡 三资金合力股共享数据层 v1.0

提供统一的数据接口供三资金合力引擎使用：
- 机构数据：基本面、估值、研报覆盖
- 量化数据：因子、资金流、换手率
- 游资数据：龙虎榜、情绪周期、涨停数据

所有数据优先走东方财富API（国内通畅），腾讯行情做备选。
"""

import os, sys, json, sqlite3, subprocess, re, urllib.request, math
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, 'data')
DB_PATH = os.path.join(DATA_DIR, 'kline_cache.db')
FUND_DB_PATH = os.path.join(DATA_DIR, 'fundamental.db')

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


# ============================================================
# 工具函数
# ============================================================

def prepend_market(code: str) -> str:
    if not code: return ''
    if code.startswith(('sh','sz','us','hk')): return code
    return f'sh{code}' if code[0] in ('6','5','9') else f'sz{code}'

def sf(v):
    try: return float(v) if v and v != '-' else 0.0
    except: return 0.0


# ============================================================
# 1. 腾讯实时行情
# ============================================================

def fetch_tencent_quote(codes: list) -> dict:
    """批量获取腾讯实时行情"""
    if not codes: return {}
    codes_str = ','.join(prepend_market(c) for c in codes)
    try:
        proc = subprocess.Popen(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
             f'https://qt.gtimg.cn/q={codes_str}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=12)
        if proc.returncode != 0: return {}
        txt = out.decode('gbk', errors='replace')
        results = {}
        for line in txt.strip().split('\n'):
            m = re.search(r'\"(.*)\"', line)
            if not m: continue
            parts = m.group(1).split('~')
            if len(parts) < 48: continue
            code = parts[2]
            results[code] = {
                'name': parts[1], 'price': sf(parts[3]),
                'change_pct': sf(parts[32]), 'turnover': sf(parts[38]),
                'pe': sf(parts[39]), 'amplitude': sf(parts[43]),
                'market_cap': sf(parts[44]), 'circulating_cap': sf(parts[45]),
                'high': sf(parts[33]), 'low': sf(parts[34]),
                'volume': sf(parts[6]), 'amount': sf(parts[37]),
                'high_52w': sf(parts[46]), 'low_52w': sf(parts[47]),
                'buy_vol_ratio': sf(parts[63]), 'sell_vol_ratio': sf(parts[64]),
                'main_net_pct': sf(parts[74]),
            }
        return results
    except Exception:
        return {}


# ============================================================
# 2. 东方财富API
# ============================================================

def em_fetch(url: str, timeout: int = 10) -> dict:
    """获取东方财富API数据"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except:
        return {}


def get_index_snapshot() -> dict:
    """获取主要指数快照"""
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2"
           "&secids=1.000001,0.399001,0.399006,1.000688,1.000016,1.000300,1.000905"
           "&fields=f2,f3,f4,f12,f14,f43,f44,f45,f46,f57,f58,f167,f170")
    data = em_fetch(url)
    result = {}
    if data.get('data') and data['data'].get('diff'):
        for it in data['data']['diff']:
            result[it['f12']] = {
                'name': it['f14'], 'price': it.get('f2',0),
                'change_pct': it.get('f3',0),
                'high': it.get('f43',0), 'low': it.get('f44',0),
            }
    return result


def get_market_stats() -> dict:
    """获取全市场涨跌统计（精确）"""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=6000&po=1&np=1"
           "&fltt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f3")
    data = em_fetch(url, timeout=15)
    if not data.get('data') or not data['data'].get('diff'):
        return {'total':0,'up':0,'down':0,'zt':0,'dt':0}
    items = data['data']['diff']
    up = sum(1 for it in items if (it.get('f3',-100) or 0) > 0)
    down = sum(1 for it in items if (it.get('f3',100) or 0) < 0)
    zt = sum(1 for it in items if (it.get('f3',-100) or 0) >= 9.5)
    dt = sum(1 for it in items if (it.get('f3',100) or 0) <= -9.5)
    return {'total': len(items), 'up': up, 'down': down, 'zt': zt, 'dt': dt,
            'up_ratio': round(up/len(items),3) if len(items)>0 else 0.5}


def get_sector_rank(top_n: int = 20) -> list:
    """获取行业板块涨幅排行"""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=80&po=1&np=1&fltt=2"
           "&fid=f3&fs=m:90+t:2&fields=f2,f3,f4,f12,f14,f8,f9,f20,f62")
    data = em_fetch(url)
    result = []
    if data.get('data') and data['data'].get('diff'):
        for it in data['data']['diff'][:top_n]:
            result.append({
                'code': it['f12'], 'name': it['f14'],
                'change_pct': it.get('f3',0),
                'fund_net': (it.get('f62',0) or 0) / 1e8,
            })
    return result


def get_sector_stocks(bk_code: str, max_n: int = 50) -> list:
    """获取板块成分股"""
    url = (f"https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz={max_n}&po=1&np=1"
           f"&fltt=2&fid=f3&fs=b:{bk_code}"
           f"&fields=f2,f3,f4,f12,f14,f15,f20,f62,f8,f9,f38,f10,f168,f169")
    data = em_fetch(url)
    if not data.get('data') or not data['data'].get('diff'):
        return []
    return [{
        'name': it['f14'], 'code': it['f12'],
        'change_pct': it.get('f3',0) or 0,
        'market_cap': (it.get('f20',0) or 0) / 1e8,
        'fund_net': (it.get('f62',0) or 0) / 1e8,
        'turnover': it.get('f38',0) or 0,
        'amount': (it.get('f10',0) or 0) / 1e4,
    } for it in (data['data'].get('diff') or [])]


def get_limit_up_list() -> list:
    """获取今日涨停股列表（含封单/炸板信息）"""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1&fltt=2"
           "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
           "&fields=f3,f12,f14,f2,f15,f20,f62,f8,f168,f169,f170")
    data = em_fetch(url, timeout=15)
    result = {'zt': [], 'dt': [], 'zt_count': 0, 'dt_count': 0}
    if not data.get('data') or not data['data'].get('diff'):
        return result
    for it in data['data']['diff']:
        chg = it.get('f3',0) or 0
        item = {'code': it['f12'], 'name': it['f14'], 'change_pct': chg,
                'price': it.get('f2',0), 'market_cap': (it.get('f20',0) or 0)/1e8}
        if chg >= 9.5:
            result['zt'].append(item)
        elif chg <= -9.5:
            result['dt'].append(item)
    result['zt_count'] = len(result['zt'])
    result['dt_count'] = len(result['dt'])
    return result


# ============================================================
# 3. K线数据加载（复用已有数据库）
# ============================================================

def load_klines(code: str, max_days: int = 250) -> list:
    """加载单只股票K线（最新在前）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT date, open, close, high, low, volume FROM kline WHERE code=? ORDER BY date DESC LIMIT ?",
        (code, max_days))
    rows = [{'date': r[0], 'open': r[1], 'close': r[2],
             'high': r[3], 'low': r[4], 'volume': r[5]} for r in cur.fetchall()]
    conn.close()
    return rows


def load_fundamentals(code: str) -> dict:
    """加载基本面数据"""
    conn = sqlite3.connect(FUND_DB_PATH)
    cur = conn.cursor()
    result = {}
    try:
        cur.execute("SELECT roe, roa, gross_profit_margin, net_profit_margin, eps FROM profit_data WHERE code=? ORDER BY statDate DESC LIMIT 1", (code,))
        row = cur.fetchone()
        if row: result['profit'] = {'roe': row[0], 'roa': row[1], 'gross_margin': row[2], 'net_margin': row[3], 'eps': row[4]}
    except: pass
    try:
        cur.execute("SELECT yoy_operate, yoy_net_profit FROM growth_data WHERE code=? ORDER BY statDate DESC LIMIT 1", (code,))
        row = cur.fetchone()
        if row: result['growth'] = {'revenue_yoy': row[0], 'profit_yoy': row[1]}
    except: pass
    try:
        cur.execute("SELECT total_assets, total_liab, current_assets, current_liab FROM balance_data WHERE code=? ORDER BY statDate DESC LIMIT 1", (code,))
        row = cur.fetchone()
        if row: result['balance'] = {'assets': row[0], 'liab': row[1], 'current_assets': row[2], 'current_liab': row[3]}
    except: pass
    conn.close()
    return result


# ============================================================
# 4. 龙虎榜数据（东方财富）
# ============================================================

def get_dragon_tiger() -> list:
    """获取昨日龙虎榜数据"""
    url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&fltt=2&fid=f3&fs=m:0+t:7&fields=f12,f14,f3,f62,f20,f8"
    data = em_fetch(url)
    results = []
    if data.get('data') and data['data'].get('diff'):
        for it in data['data']['diff']:
            results.append({
                'code': it['f12'], 'name': it['f14'],
                'change_pct': it.get('f3',0),
                'fund_net': (it.get('f62',0) or 0)/1e8,
            })
    return results


# ============================================================
# 5. 情绪周期判断
# ============================================================

def judge_emotion_cycle(market_stats: dict, limit_data: dict, zt_count: int, dt_count: int,
                        prev_limit_up: int = 0) -> dict:
    """判断情绪周期阶段"""
    zt = limit_data.get('zt_count', 0)
    dt = limit_data.get('dt_count', 0)
    up_ratio = market_stats.get('up_ratio', 0.5)
    total_up = market_stats.get('up', 0)

    # 冰点期特征
    if zt < 20 and dt > 10:
        phase = '❄️ 冰点期'
        position = 10
        strategy = '轻仓试错新题材首板'
    # 复苏期特征
    elif zt >= 20 and zt < 60 and dt < 10:
        phase = '🌱 复苏期'
        position = 50
        strategy = '主线龙头2进3/3进4接力'
    # 高潮期特征
    elif zt >= 60 and dt < 5:
        phase = '🔥 高潮期'
        position = 80
        strategy = '重仓龙头+补涨龙套利'
    # 退潮期特征
    elif dt > 10 and zt < 30:
        phase = '🍂 退潮期'
        position = 0
        strategy = '强制空仓，不抢反弹'
    else:
        phase = '➖ 震荡期'
        position = 30
        strategy = '轻仓试错'

    return {
        'phase': phase,
        'position_limit': position,
        'strategy': strategy,
        'zt_count': zt,
        'dt_count': dt,
        'up_ratio': up_ratio,
    }


def get_concept_board_rank(top_n: int = 15) -> list:
    """获取概念板块涨幅排行"""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=80&po=1&np=1&fltt=2"
           "&fid=f3&fs=m:90+t:3&fields=f2,f3,f4,f12,f14,f8,f9,f20,f62")
    data = em_fetch(url)
    result = []
    if data.get('data') and data['data'].get('diff'):
        for it in data['data'].get('diff', [])[:top_n]:
            result.append({
                'code': it['f12'], 'name': it['f14'],
                'change_pct': it.get('f3',0) or 0,
                'fund_net': (it.get('f62',0) or 0)/1e8,
            })
    return result


# ============================================================
# 6. 主力资金流
# ============================================================

def get_money_flow(code: str) -> dict:
    """获取个股资金流"""
    prefix = '1' if code.startswith(('6','000')) else '0'
    url = (f"https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get?"
           f"secid={prefix}.{code}&fields=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f14,f15,f16,f17,f18,f19,f20,f21,f22,f23,f24,f25,f26,f27,f28,f29,f30,f31,f32,f33,f34,f35,f36,f37,f38,f39,f40,f41,f42,f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65,f66,f67,f68,f69,f70,f71,f72,f73,f74,f75,f76,f77,f78,f79,f80")
    data = em_fetch(url)
    return data
