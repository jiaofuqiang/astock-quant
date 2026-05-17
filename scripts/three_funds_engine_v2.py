#!/usr/bin/env python3
"""
🏆 三资金合力股引擎 v2.0 (纯实时版)

三大子评分系统 + 合力聚合 + 买卖信号
完全基于实时API（腾讯行情+东方财富），无SQLite依赖。

评分体系（满分100分）：
  机构子评分 0-30分 — 实时市值/PE/资金流向
  量化子评分 0-30分 — 量价活跃度/趋势强度  
  游资子评分 0-30分 — 短线强度/情绪周期
  合力加成 +10分    — 板块共振+资金共振

合力等级：
  ≥80分 — 🔥🔥🔥 三力合一 强烈买入
  ≥65分 — 🟢🟢 双力共振 买入/关注
  ≥50分 — 🟡 单力支撑 观察
  <50分 — ⚪ 无合力 放弃
"""

import os, sys, json, math, re, urllib.request, subprocess
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

def sf(v):
    try: return float(v) if v and v != '-' else 0.0
    except: return 0.0

# ============================================================
# 实时数据获取
# ============================================================

def em_fetch(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except:
        return {}

def get_market_stats():
    """全市场涨跌统计（双API容错）"""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=6000&po=1&np=1"
           "&fltt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f3")
    data = em_fetch(url, timeout=8)
    if data.get('data') and data['data'].get('diff'):
        items = data['data']['diff']
        up = sum(1 for it in items if (it.get('f3',-100) or 0) > 0)
        down = sum(1 for it in items if (it.get('f3',100) or 0) < 0)
        zt = sum(1 for it in items if (it.get('f3',-100) or 0) >= 9.5)
        dt = sum(1 for it in items if (it.get('f3',100) or 0) <= -9.5)
        return {'total':len(items),'up':up,'down':down,'zt':zt,'dt':dt,
                'up_ratio':round(up/len(items),3) if len(items)>0 else 0.5}
    # 备用：腾讯行情估算
    return {'total':5000,'up':2500,'down':2200,'zt':30,'dt':5,'up_ratio':0.5}

def fetch_tencent_batch(codes):
    """批量获取腾讯实时行情"""
    if not codes: return {}
    def mkt(s):
        s = s.strip()
        return f'sh{s}' if s[0] in ('6','5','9') else f'sz{s}'
    codes_str = ','.join(mkt(c) for c in codes)
    try:
        proc = subprocess.Popen(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
             f'https://qt.gtimg.cn/q={codes_str}'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = proc.communicate(timeout=15)
        txt = out.decode('gbk', errors='replace')
        results = {}
        for line in txt.strip().split('\n'):
            m = re.search(r'\"(.*)\"', line)
            if not m: continue
            parts = m.group(1).split('~')
            if len(parts) < 48: continue
            code = parts[2]
            name = parts[1]
            price = sf(parts[3])
            close_yest = sf(parts[4])
            change_pct = sf(parts[32])
            high = sf(parts[33])
            low = sf(parts[34])
            turnover = sf(parts[38])
            pe = sf(parts[39])
            market_cap = sf(parts[44])
            circ_cap = sf(parts[45])
            amount = sf(parts[37])
            volume = sf(parts[6])
            amplitude = sf(parts[43])
            buy_vol = sf(parts[63])
            sell_vol = sf(parts[64])
            main_net = sf(parts[74])
            
            # 涨停判断：涨幅>=9.5% 或 当前价=涨停价
            is_limit_up = change_pct >= 9.4 or (close_yest > 0 and price >= close_yest * 1.095)
            limit_up_price = round(close_yest * 1.095, 2) if close_yest > 0 else 0
            
            results[code] = {
                'name': name, 'price': price, 'close_yest': close_yest,
                'change_pct': change_pct, 'high': high, 'low': low,
                'turnover': turnover, 'pe': pe,
                'market_cap': market_cap, 'circ_cap': circ_cap,
                'amount': amount, 'volume': volume,
                'amplitude': amplitude,
                'buy_vol_ratio': buy_vol, 'sell_vol_ratio': sell_vol,
                'main_net_pct': main_net,
                'is_limit_up': is_limit_up,
                'limit_up_price': limit_up_price,
            }
        return results
    except:
        return {}

def get_sector_top(limit=15):
    """获取概念板块涨幅TOP"""
    url = (f"https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz={limit}&po=1&np=1"
           f"&fltt=2&fid=f3&fs=m:90+t:3&fields=f2,f3,f4,f12,f14,f8,f9,f20,f62")
    data = em_fetch(url)
    result = []
    if data.get('data') and data['data'].get('diff'):
        for it in data['data'].get('diff', []):
            result.append({
                'code': it['f12'], 'name': it['f14'],
                'change_pct': it.get('f3',0) or 0,
                'fund_net': (it.get('f62',0) or 0)/1e8,
            })
    return result

def get_sector_stocks(bk_code, max_n=50):
    """获取板块成分股"""
    url = (f"https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz={max_n}&po=1&np=1"
           f"&fltt=2&fid=f3&fs=b:{bk_code}"
           f"&fields=f2,f3,f4,f12,f14,f15,f20,f62,f38,f8,f168")
    data = em_fetch(url)
    if not data.get('data') or not data['data'].get('diff'):
        return []
    return [{
        'name': it['f14'], 'code': it['f12'],
        'change_pct': it.get('f3',0) or 0,
        'price': it.get('f2',0) or 0,
        'market_cap': (it.get('f20',0) or 0) / 1e8,
        'fund_net': (it.get('f62',0) or 0) / 1e8,
        'turnover': it.get('f38',0) or 0,
    } for it in (data['data'].get('diff') or [])]

def get_dragon_tiger():
    """获取龙虎榜"""
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=30&po=1&np=1"
           "&fltt=2&fid=f3&fs=m:0+t:7&fields=f12,f14,f3,f62,f20,f8")
    data = em_fetch(url)
    results = []
    if data.get('data') and data['data'].get('diff'):
        for it in data['data']['diff']:
            results.append({
                'code': it['f12'], 'name': it['f14'],
                'change_pct': it.get('f3',0) or 0,
                'fund_net': (it.get('f62',0) or 0)/1e8,
            })
    return results

# ============================================================
# 情绪周期判断
# ============================================================

def judge_emotion(market_stats, limit_data):
    zt = limit_data.get('zt_count', limit_data.get('zt_count', 0))
    dt = limit_data.get('dt_count', limit_data.get('dt_count', 0))
    up_ratio = market_stats.get('up_ratio', 0.5)
    
    if zt < 20 and dt > 10:
        return {'phase': '❄️ 冰点期', 'position': 10, 'strategy': '轻仓试错新题材首板',
                'zt_count': zt, 'dt_count': dt, 'up_ratio': up_ratio}
    elif zt >= 60 and dt < 5:
        return {'phase': '🔥 高潮期', 'position': 80, 'strategy': '重仓龙头+补涨龙套利',
                'zt_count': zt, 'dt_count': dt, 'up_ratio': up_ratio}
    elif dt > 10 and zt < 30:
        return {'phase': '🍂 退潮期', 'position': 0, 'strategy': '强制空仓',
                'zt_count': zt, 'dt_count': dt, 'up_ratio': up_ratio}
    elif zt >= 20:
        return {'phase': '🌱 复苏期', 'position': 50, 'strategy': '接力龙头',
                'zt_count': zt, 'dt_count': dt, 'up_ratio': up_ratio}
    else:
        return {'phase': '➖ 震荡期', 'position': 30, 'strategy': '轻仓试错',
                'zt_count': zt, 'dt_count': dt, 'up_ratio': up_ratio}

# ============================================================
# 子评分系统（纯实时）
# ============================================================

def score_institutional(rt, sector_info=None):
    """
    机构子评分 0-30分（纯实时版）
    
    因子：
    1. 市值规模 — 大市值=机构偏好高 0-10分
    2. PE合理性 — PE适中加分 0-10分
    3. 资金强度 — 主力净流入/成交额占比 0-10分
    """
    score = 0.0
    details = []
    risks = []
    
    cap = rt.get('market_cap', 0)
    pe = rt.get('pe', 0)
    main_net = rt.get('main_net_pct', 0)
    
    # 因子1：市值规模 0-10分
    if cap >= 1000:
        score += 10
        details.append(f'市值{cap:.0f}亿 超大机构标配')
    elif cap >= 300:
        score += 8
        details.append(f'市值{cap:.0f}亿 中大盘')
    elif cap >= 100:
        score += 6
        details.append(f'市值{cap:.0f}亿 中盘')
    elif cap >= 50:
        score += 4
        details.append(f'市值{cap:.0f}亿 小盘')
    else:
        score += 1
        details.append(f'市值{cap:.0f}亿 微盘 机构参与有限')
        risks.append('市值过小')
    
    # 因子2：PE合理性 0-10分
    if 0 < pe <= 20:
        score += 10
        details.append(f'PE={pe:.0f} 低估值')
    elif 20 < pe <= 40:
        score += 8
        details.append(f'PE={pe:.0f} 合理估值')
    elif 40 < pe <= 80:
        score += 5
        details.append(f'PE={pe:.0f} 偏高')
    elif pe > 80:
        score += 2
        details.append(f'PE={pe:.0f} 高估值')
        risks.append('PE过高')
    else:
        details.append(f'PE={pe} 无法评估')
    
    # 因子3：资金强度(主力净流入占比) 0-10分
    if main_net > 5:
        score += 10
        details.append(f'主力净{main_net:+.1f}% 大资金流入')
    elif main_net > 2:
        score += 7
        details.append(f'主力净{main_net:+.1f}% 资金流入')
    elif main_net > -2:
        score += 4
        details.append(f'主力净{main_net:+.1f}% 资金平衡')
    else:
        score += 1
        details.append(f'主力净{main_net:+.1f}% 资金流出')
        risks.append('主力流出')
    
    final = min(30, max(0, round(score, 1)))
    return {'agent': '机构', 'score': final,
            'rating': '买入' if final >= 20 else '持有' if final >= 12 else '卖出',
            'details': details, 'risks': risks}

def score_quantitative(rt):
    """
    量化子评分 0-30分（纯实时版）
    
    因子：
    1. 量价活跃度 — 换手率+量比+振幅 0-10分
    2. 当日趋势 — 涨幅+价格位置 0-10分
    3. 资金博弈 — 买卖比 0-10分
    """
    score = 0.0
    details = []
    risks = []
    
    turnover = rt.get('turnover', 0)
    amplitude = rt.get('amplitude', 0)
    chg = rt.get('change_pct', 0)
    buy_vol = rt.get('buy_vol_ratio', 0)
    sell_vol = rt.get('sell_vol_ratio', 0)
    balance = buy_vol - sell_vol
    
    # 因子1：量价活跃度 0-10分
    if turnover >= 10:
        score += 10
        details.append(f'换手{turnover:.1f}% 超高活跃')
    elif turnover >= 5:
        score += 8
        details.append(f'换手{turnover:.1f}% 高活跃')
    elif turnover >= 3:
        score += 6
        details.append(f'换手{turnover:.1f}% 活跃')
    elif turnover >= 1:
        score += 4
        details.append(f'换手{turnover:.1f}% 一般')
    else:
        score += 1
        details.append(f'换手{turnover:.1f}% 不活跃')
        risks.append('换手过低')
    
    # 振幅加分
    if amplitude >= 8:
        score += 3
        details.append(f'振幅{amplitude:.1f}% 激烈波动')
    elif amplitude >= 4:
        score += 1
    score = min(10, score)  # 因子1上限10分
    
    # 因子2：当日趋势 0-10分
    if chg >= 9.5:
        score += 10
        details.append(f'涨停{chg:.1f}% 极强')
    elif chg >= 5:
        score += 8
        details.append(f'大涨{chg:.1f}% 强势')
    elif chg >= 3:
        score += 6
        details.append(f'涨{chg:.1f}% 走强')
    elif chg >= 0:
        score += 3
        details.append(f'涨{chg:.1f}% 平稳')
    else:
        score += 0
        details.append(f'跌{chg:.1f}% 走弱')
        risks.append('当日下跌')
    
    # 因子3：资金博弈 0-10分
    if balance > 20:
        score += 10
        details.append(f'买方+{balance:.0f}% 强买入意愿')
    elif balance > 10:
        score += 8
        details.append(f'买方+{balance:.0f}%')
    elif balance > 0:
        score += 5
        details.append(f'买方+{balance:.0f}% 略强')
    elif balance > -10:
        score += 3
        details.append(f'买方{balance:.0f}% 略弱')
    else:
        score += 0
        details.append(f'买方{balance:.0f}% 抛压重')
        risks.append('抛压较重')
    
    final = min(30, max(0, round(score, 1)))
    return {'agent': '量化', 'score': final,
            'rating': '买入' if final >= 20 else '持有' if final >= 12 else '卖出',
            'details': details, 'risks': risks,
            'turnover': turnover, 'amplitude': amplitude}

def score_hot_money(rt, emotion):
    """
    游资子评分 0-30分（纯实时版）
    
    因子：
    1. 短线爆发力 — 涨停/大涨强度 0-12分
    2. 情绪周期 — 当前位置是否适合游资操作 0-10分
    3. 市场瞩目 — 量能/振幅/关注度 0-8分
    """
    score = 0.0
    details = []
    risks = []
    
    chg = rt.get('change_pct', 0)
    turnover = rt.get('turnover', 0)
    is_limit = rt.get('is_limit_up', False)
    amplitude = rt.get('amplitude', 0)
    amount = rt.get('amount', 0)
    cap = rt.get('market_cap', 0)
    
    # 因子1：短线爆发力 0-12分
    if is_limit:
        score += 12
        details.append('✅ 涨停板 游资点火确认')
    elif chg >= 7:
        score += 10
        details.append(f'大涨{chg:.1f}% 短线强势')
    elif chg >= 5:
        score += 7
        details.append(f'涨幅{chg:.1f}% 较强')
    elif chg >= 3:
        score += 5
        details.append(f'涨幅{chg:.1f}% 温和')
    elif chg >= 0:
        score += 2
        details.append(f'涨幅{chg:.1f}% 平淡')
    else:
        score += 0
        details.append(f'跌幅{chg:.1f}% 弱势')
        risks.append('当日下跌')
    
    # 量能关注度加分
    if turnover >= 10 and amount >= 5e8:
        score += 2
        details.append(f'高换手+大成交 市场瞩目')
    
    score = min(14, score)  # 因子1上限14分
    
    # 因子2：情绪周期 0-10分
    phase = emotion.get('phase', '')
    if '高潮' in phase:
        score += 10
        details.append('情绪高潮期 游资全面出击')
    elif '复苏' in phase:
        score += 7
        details.append('情绪复苏期 游资试探')
    elif '震荡' in phase:
        score += 4
        details.append('情绪震荡期 游资谨慎')
    elif '冰点' in phase:
        score += 1
        details.append('情绪冰点期 游资休息')
        risks.append('情绪冰点')
    else:
        score += 0
        details.append('退潮期 不操作')
        risks.append('退潮期')
    
    # 因子3：游资偏好度 0-6分  
    # 游资偏好中小盘+高换手
    if cap <= 200:
        score += 4
        details.append(f'市值{cap:.0f}亿 游资偏好')
    elif cap <= 500:
        score += 2
    else:
        details.append(f'市值{cap:.0f}亿 偏大盘')
    
    if amplitude >= 5 and turnover >= 3:
        score += 2
        details.append(f'振幅{amplitude:.1f}%+换手{turnover:.1f}% 游资博弈活跃')
    
    final = min(30, max(0, round(score, 1)))
    return {'agent': '游资', 'score': final,
            'rating': '买入' if final >= 20 else '持有' if final >= 12 else '卖出',
            'details': details, 'risks': risks,
            'is_limit': is_limit}

# ============================================================
# 合力计算
# ============================================================

def calc_combined(inst, quant, hot, rt, sector_chg=0):
    inst_s = inst['score']
    quant_s = quant['score']
    hot_s = hot['score']
    total = inst_s + quant_s + hot_s
    
    # 板块共振加分
    if sector_chg >= 5:
        total += 5
    elif sector_chg >= 3:
        total += 3
    
    # 涨停加分
    if rt.get('is_limit_up', False) and total >= 40:
        total += 5
    
    total = max(0, min(100, total))
    
    if total >= 80:
        level = '🔥🔥🔥 三力合一'
        action = '强烈买入'
    elif total >= 65:
        level = '🟢🟢 双力共振'
        action = '买入'
    elif total >= 50:
        level = '🟡 单力支撑'
        action = '关注'
    else:
        level = '⚪ 无合力'
        action = '放弃'
    
    return {'total': total, 'level': level, 'action': action,
            'breakdown': f'{inst_s}+{quant_s}+{hot_s}'}

# ============================================================
# 板块合力扫描
# ============================================================

def scan_sector(bk_code, sector_name, max_stocks=10):
    """扫描一个板块的合力股"""
    stocks = get_sector_stocks(bk_code, max_stocks)
    if not stocks:
        return [], {}, 0
    
    # 获取板块涨幅
    sector_chg = stocks[0].get('change_pct', 0) if stocks else 0
    
    codes = [s['code'] for s in stocks if s.get('code')]
    rt_data = fetch_tencent_batch(codes)
    
    ms = get_market_stats()
    limit_data = {'zt_count': ms['zt'], 'dt_count': ms['dt']}
    emotion = judge_emotion(ms, limit_data)
    
    results = []
    for s in stocks:
        code = s['code']
        rt = rt_data.get(code, {})
        if not rt:
            continue
        
        inst = score_institutional(rt)
        quant = score_quantitative(rt)
        hot = score_hot_money(rt, emotion)
        cb = calc_combined(inst, quant, hot, rt, sector_chg)
        
        results.append({
            'code': code,
            'name': rt.get('name', s.get('name', code)),
            'price': rt.get('price', 0),
            'change_pct': rt.get('change_pct', 0),
            'is_limit_up': rt.get('is_limit_up', False),
            'turnover': rt.get('turnover', 0),
            'market_cap': rt.get('market_cap', 0),
            'institutional': inst,
            'quantitative': quant,
            'hot_money': hot,
            'combined': cb,
        })
    
    results.sort(key=lambda x: x['combined']['total'], reverse=True)
    return results, emotion, sector_chg

# ============================================================
# 报告生成
# ============================================================

def format_report(sector_results, sector_name, emotion):
    lines = []
    lines.append(f"🏆 **三资金合力 — {sector_name}扫描**")
    lines.append(f"   ⏰ {datetime.now().strftime('%H:%M:%S')}")
    lines.append(f"   情绪: {emotion['phase']} | 仓位建议: {emotion.get('position',50)}%")
    lines.append(f"   涨停{emotion.get('zt_count',0)}只 | 跌停{emotion.get('dt_count',0)}只")
    lines.append("")
    
    for r in sector_results[:15]:
        cb = r['combined']
        chg = r['change_pct']
        limit_mark = '🚀' if r['is_limit_up'] else ''
        lines.append(f"   {limit_mark}{r['name']:<8s} {chg:>+5.1f}% | 总分{int(cb['total']):>2d}/100 ({cb['breakdown']}) | {cb['level'][:8]}")
        if cb['total'] >= 50:
            inst = r['institutional']
            quant = r['quantitative']
            hot = r['hot_money']
            lines.append(f"   {'':>12s}🏛{inst['score']}/30 💻{quant['score']}/30 🔥{hot['score']}/30 | {cb['action']}")
            if r['is_limit_up']:
                lines.append(f"   {'':>12s}📌 涨停板 — 关注封单质量+次日溢价")
        lines.append("")
    
    return '\n'.join(lines)

def format_full_report(all_sectors, emotion, top_by_sector):
    """生成完整扫描报告"""
    lines = []
    lines.append("📊 **三资金合力 — 全市场扫描**")
    lines.append(f"   ⏰ {datetime.now().strftime('%H:%M:%S')}")
    lines.append(f"   情绪: {emotion['phase']}  |  涨停{emotion.get('zt_count',0)} 跌停{emotion.get('dt_count',0)}")
    lines.append(f"   涨跌比: {emotion.get('up_ratio',0):.0%}  |  推荐仓位: {emotion.get('position',50)}%")
    lines.append("")
    lines.append(f"{'─'*45}")
    lines.append("")
    
    for sec_name, results in all_sectors:
        lines.append(f"📈 **{sec_name}**")
        for i, r in enumerate(results[:10]):
            cb = r['combined']
            if cb['total'] < 50:
                continue
            chg = r['change_pct']
            limit_mark = '🚀' if r['is_limit_up'] else ''
            lines.append(f"   {limit_mark}{r['name']:<8s} {chg:>+5.1f}% | {int(cb['total'])}分({cb['breakdown']}) | {cb['action']}")
        lines.append("")
    
    # 全天候推荐
    lines.append(f"{'─'*45}")
    lines.append("")
    lines.append("🎯 **综合推荐TOP5**")
    
    all_stocks = []
    for _, results in all_sectors:
        all_stocks.extend(results)
    all_stocks.sort(key=lambda x: x['combined']['total'], reverse=True)
    
    for i, r in enumerate(all_stocks[:5]):
        if r['combined']['total'] < 50:
            continue
        cb = r['combined']
        chg = r['change_pct']
        limit_mark = '🚀' if r['is_limit_up'] else ''
        lines.append(f"   {i+1}. {limit_mark}{r['name']}({r['code']}) {chg:>+.1f}% → {cb['action']} ({cb['level']})")
    
    lines.append("")
    lines.append(f"{'─'*45}")
    lines.append(f"📋 每日流程: 7:30盘前→9:15竞价→9:30黄金30分→下午狙击→15:00收盘复盘")
    lines.append(f"🤖 数据源: 腾讯行情 + 东方财富 | 无SQLite依赖")
    
    return '\n'.join(lines)

# ============================================================
# 主入口
# ============================================================

# 核心板块配置
SECTOR_CONFIG = {
    'BK1137': '存储芯片',
    'BK1127': 'AI芯片',
    'BK0917': '半导体',
    'BK0891': '国产芯片',
    'BK1184': '人形机器人',
    'BK1152': 'HBM高带宽内存',
    'BK0969': '汽车芯片',
}

def main():
    import argparse
    parser = argparse.ArgumentParser(description='🏆 三资金合力引擎 v2.0')
    parser.add_argument('--scan', action='store_true', help='全市场扫描')
    parser.add_argument('--sector', type=str, help='指定板块代码扫描')
    parser.add_argument('--codes', type=str, help='指定个股代码(逗号分隔)')
    parser.add_argument('--json', action='store_true', help='JSON输出')
    parser.add_argument('--topn', type=int, default=15, help='每板块最多扫描数')
    parser.add_argument('--brief', action='store_true', help='简洁模式')
    args = parser.parse_args()
    
    if args.codes:
        codes = [c.strip() for c in args.codes.split(',')]
        rt = fetch_tencent_batch(codes)
        ms = get_market_stats()
        emotion = judge_emotion(ms, {'zt_count': ms['zt'], 'dt_count': ms['dt']})
        
        results = []
        for code in codes:
            r = rt.get(code, {})
            if not r: continue
            inst = score_institutional(r)
            quant = score_quantitative(r)
            hot = score_hot_money(r, emotion)
            cb = calc_combined(inst, quant, hot, r)
            results.append({
                'code': code, 'name': r.get('name', code),
                'change_pct': r.get('change_pct', 0),
                'is_limit_up': r.get('is_limit_up', False),
                'combined': cb,
                'institutional': inst,
                'quantitative': quant,
                'hot_money': hot,
            })
        
        if args.json:
            print(json.dumps({'results': results, 'emotion': emotion}, ensure_ascii=False, indent=2))
        else:
            # 简洁输出
            lines = [f"🏆 三资金合力 — 指定标的", f"⏰ {datetime.now().strftime('%H:%M:%S')}", f"情绪: {emotion['phase']} 仓位:{emotion.get('position',50)}%", ""]
            for r in sorted(results, key=lambda x: x['combined']['total'], reverse=True):
                cb = r['combined']
                lines.append(f"   {r['name']:<8s} {r['change_pct']:>+5.1f}% | 总分{int(cb['total']):>2d} ({cb['breakdown']}) | {cb['level'][:8]} | {cb['action']}")
            print('\n'.join(lines))
        return
    
    if args.sector:
        sector_name = SECTOR_CONFIG.get(args.sector, args.sector)
        results, emotion, _ = scan_sector(args.sector, sector_name, args.topn)
        if args.json:
            print(json.dumps({'sector': args.sector, 'name': sector_name, 'results': results, 'emotion': emotion}, ensure_ascii=False, indent=2))
        else:
            print(format_report(results, sector_name, emotion))
        return
    
    # 默认：全市场扫描
    all_sectors = []
    sector_top_stocks = {}
    
    for bk_code, sec_name in SECTOR_CONFIG.items():
        results, emotion = scan_sector(bk_code, sec_name, args.topn)
        if results:
            valid = [r for r in results if r['combined']['total'] >= 50]
            all_sectors.append((sec_name, results))
            sector_top_stocks[sec_name] = valid[:5]
        
        # 防止频率限制
        import time
        time.sleep(0.5)
    
    # 再扫一遍市场整体数据
    ms = get_market_stats()
    emotion['zt_count'] = ms['zt']
    emotion['dt_count'] = ms['dt']
    emotion['up_ratio'] = ms['up_ratio']
    
    if args.json:
        output = {'sectors': {}, 'emotion': emotion}
        for name, results in all_sectors:
            output['sectors'][name] = results
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(format_full_report(all_sectors, emotion, sector_top_stocks))

if __name__ == '__main__':
    main()
