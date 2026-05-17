#!/usr/bin/env python3
"""
🏆 三资金合力股引擎 v1.0

三大子评分系统 + 合力聚合 + 买卖信号

评分体系（满分90分）：
  机构子评分 0-30分 — 基本面打底
  量化子评分 0-30分 — 数据活跃验证
  游资子评分 0-30分 — 情绪点火确认

合力等级：
  ≥80分 — 🔥🔥🔥 三力合一 强烈买入
  ≥65分 — 🟢🟢 双力共振 买入/关注
  ≥50分 — 🟡 单力支撑 观察
  <50分 — ⚪ 无合力 放弃

用法：
  python3 scripts/three_funds_engine.py --code 603986
  python3 scripts/three_funds_engine.py --scan
  python3 scripts/three_funds_engine.py --sector BK1127
  python3 scripts/three_funds_engine.py --full
"""

import os, sys, json, math, sqlite3
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from scripts.three_funds_data import (
    fetch_tencent_quote, get_index_snapshot, get_market_stats,
    get_limit_up_list, get_sector_stocks, get_sector_rank,
    get_concept_board_rank, get_dragon_tiger, load_klines,
    load_fundamentals,
)

# ============================================================
# 协同数据
# ============================================================

# 三面量化复用（从已有模块导入简化版）
def calc_momentum_indicators(code: str, klines: list) -> dict:
    """计算惯性面核心指标"""
    if not klines or len(klines) < 30:
        return {}
    closes = [k['close'] for k in klines]
    volumes = [k['volume'] for k in klines]
    n = len(closes)
    chg_60d = (closes[0] - closes[min(59, n-1)]) / closes[min(59, n-1)] * 100 if n >= 60 else 0
    chg_20d = (closes[0] - closes[min(19, n-1)]) / closes[min(19, n-1)] * 100 if n >= 20 else 0
    chg_5d = (closes[0] - closes[min(4, n-1)]) / closes[min(4, n-1)] * 100 if n >= 5 else 0
    ma20 = sum(closes[:min(20,n)]) / min(20,n)
    ma60 = sum(closes[:min(60,n)]) / min(60,n) if n >= 60 else ma20
    ma20_pct = (closes[0] - ma20) / ma20 * 100 if ma20 > 0 else 0
    ma60_pct = (closes[0] - ma60) / ma60 * 100 if ma60 > 0 else 0
    vol_ma5 = sum(volumes[:5]) / 5
    vol_ma20 = sum(volumes[:20]) / 20 if n >= 20 else vol_ma5
    vol_ratio = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
    # RSI
    if n >= 14:
        gains = losses = 0.0
        for i in range(14):
            c = closes[i] - closes[i+1] if i+1 < n else 0
            if c > 0: gains += c
            else: losses += abs(c)
        rsi = 100 - (100/(1+gains/losses)) if losses > 0 else 100
    else:
        rsi = 50
    return {'chg_60d': chg_60d, 'chg_20d': chg_20d, 'chg_5d': chg_5d,
            'ma20_pct': ma20_pct, 'ma60_pct': ma60_pct,
            'vol_ratio': vol_ratio, 'rsi': rsi}


# ============================================================
# 子评分系统
# ============================================================

def score_institutional(code: str, rt: dict, fundamentals: dict, klines: list) -> dict:
    """
    机构子评分 0-30分
    
    因子：
    1. 机构持仓占比(估) — 用基本面数据替代 0-10分
    2. 基本面质量 — ROE/营收增速 0-10分
    3. 估值合理性 — PE历史分位/市值 0-10分
    """
    score = 0.0
    details = []
    risk_factors = []

    # 因子1：基本面质量 0-10分
    roe = 0
    if fundamentals.get('profit'):
        p = fundamentals['profit']
        roe = p.get('roe', 0) or 0
        gross = p.get('gross_margin', 0) or 0
        eps = p.get('eps', 0) or 0
        if roe > 20:
            score += 10
            details.append(f'ROE={roe:.1f}% 优秀')
        elif roe > 10:
            score += 7
            details.append(f'ROE={roe:.1f}% 良好')
        elif roe > 5:
            score += 4
            details.append(f'ROE={roe:.1f}% 一般')
        else:
            details.append(f'ROE={roe:.1f}% 偏低')
            risk_factors.append('ROE过低')

    # 因子2：成长性 0-10分
    if fundamentals.get('growth'):
        g = fundamentals['growth']
        rev_yoy = g.get('revenue_yoy', 0) or 0
        profit_yoy = g.get('profit_yoy', 0) or 0
        if rev_yoy > 50 or profit_yoy > 50:
            score += 10
            details.append(f'营收{rev_yoy:.0f}%/利润{profit_yoy:.0f}% 高增长')
        elif rev_yoy > 20 or profit_yoy > 20:
            score += 7
            details.append(f'营收{rev_yoy:.0f}%/利润{profit_yoy:.0f}% 稳健增长')
        elif rev_yoy > 0:
            score += 4
            details.append(f'营收{rev_yoy:.0f}%/利润{profit_yoy:.0f}% 微增')
        else:
            risk_factors.append('营收/利润负增长')

    # 因子3：估值合理性 0-10分（用市值和PE近似）
    pe = rt.get('pe', 0)
    cap = rt.get('market_cap', 0)
    if 0 < pe <= 50 and cap > 100:
        if pe <= 20:
            score += 10
            details.append(f'PE={pe:.0f} 低估值')
        elif pe <= 40:
            score += 7
            details.append(f'PE={pe:.0f} 合理估值')
        else:
            score += 4
            details.append(f'PE={pe:.0f} 偏高')
    else:
        details.append(f'PE={pe:.0f},市值={cap:.0f}亿 无法评估')

    formatted_score = min(30, max(0, round(score, 1)))
    rating = '买入' if formatted_score >= 20 else '持有' if formatted_score >= 12 else '卖出'

    return {
        'agent': '机构',
        'score': formatted_score,
        'rating': rating,
        'details': details,
        'risk_factors': risk_factors,
        'roe': roe,
    }


def score_quantitative(code: str, rt: dict, klines: list) -> dict:
    """
    量化子评分 0-30分
    
    因子：
    1. 量价活跃度 — 换手率/量比 0-10分
    2. 趋势动量 — 短期涨幅/趋势强度 0-10分
    3. 资金流信号 — 主买/卖比 0-10分
    """
    score = 0.0
    details = []
    risk_factors = []

    turnover = rt.get('turnover', 0)
    mi = calc_momentum_indicators(code, klines) if klines else {}
    vol_ratio = mi.get('vol_ratio', 1.0)
    chg_5d = mi.get('chg_5d', 0)
    chg_20d = mi.get('chg_20d', 0)
    rsi = mi.get('rsi', 50)
    ma20_pct = mi.get('ma20_pct', 0)
    balance = rt.get('buy_vol_ratio', 0) - rt.get('sell_vol_ratio', 0)

    # 因子1：量价活跃度 0-10分
    if 3 <= turnover <= 15:
        t_score = 10
        details.append(f'换手率{turnover:.1f}% 活跃')
    elif 1 <= turnover < 3:
        t_score = 5
        details.append(f'换手率{turnover:.1f}% 一般')
    else:
        t_score = 2
        details.append(f'换手率{turnover:.1f}% 不活跃')
        risk_factors.append('换手率过低')
    score += t_score

    # 量比加分
    if vol_ratio > 1.5:
        score += 3
        details.append(f'量比{vol_ratio:.2f} 放量')
    elif vol_ratio > 0.8:
        score += 1
        details.append(f'量比{vol_ratio:.2f} 正常')
    elif vol_ratio <= 0.8:
        risk_factors.append('缩量')
    score = min(10, score)  # 因子1上限10分

    # 因子2：趋势动量 0-10分
    s2 = 0
    if chg_5d > 5 and chg_20d > 10:
        s2 = 10
        details.append(f'5日{chg_5d:.1f}% 20日{chg_20d:.1f}% 强势')
    elif chg_5d > 3 and chg_20d > 5:
        s2 = 7
        details.append(f'5日{chg_5d:.1f}% 20日{chg_20d:.1f}% 趋势向好')
    elif chg_5d > 0:
        s2 = 4
        details.append(f'5日{chg_5d:.1f}% 微涨')
    else:
        s2 = 1
        details.append(f'5日{chg_5d:.1f}% 走弱')
        risk_factors.append('短期趋势下行')
    if rsi > 80:
        risk_factors.append('RSI超买')
    score += s2

    # 因子3：资金流信号 0-10分
    s3 = 0
    if balance > 10:
        s3 = 10
        details.append(f'主买比偏多{balance:.0f}% 资金流入')
    elif balance > 3:
        s3 = 6
        details.append(f'主买略多{balance:.0f}%')
    elif balance > -3:
        s3 = 3
        details.append(f'资金平衡{balance:.0f}%')
    else:
        s3 = 1
        details.append(f'主卖偏多{balance:.0f}%')
        risk_factors.append('主力资金流出')
    score += s3

    formatted_score = min(30, max(0, round(score, 1)))
    rating = '买入' if formatted_score >= 20 else '持有' if formatted_score >= 12 else '卖出'

    return {
        'agent': '量化',
        'score': formatted_score,
        'rating': rating,
        'details': details,
        'risk_factors': risk_factors,
        'turnover': turnover,
        'vol_ratio': vol_ratio,
        'balance': balance,
    }


def score_hot_money(code: str, rt: dict, klines: list, limit_data: dict,
                    emotion_cycle: dict, sector_info: dict = None) -> dict:
    """
    游资子评分 0-30分
    
    因子：
    1. 短线强度 — 5日涨幅/是否涨停 0-10分
    2. 市场地位 — 是否主线/龙头 0-10分
    3. 情绪周期 — 当前情绪阶段适合操作 0-10分
    """
    score = 0.0
    details = []
    risk_factors = []

    chg = rt.get('change_pct', 0)
    mi = calc_momentum_indicators(code, klines) if klines else {}
    chg_5d = mi.get('chg_5d', 0)

    # 因子1：短线强度 0-10分
    if chg >= 9.5:
        s1 = 10
        details.append('今日涨停 短线最强')
    elif chg >= 7:
        s1 = 9
        details.append(f'大涨{chg:.1f}% 强势')
    elif chg >= 5:
        s1 = 7
        details.append(f'涨幅{chg:.1f}% 较强')
    elif chg >= 3:
        s1 = 5
        details.append(f'涨幅{chg:.1f}% 温和')
    elif chg >= 0:
        s1 = 3
        details.append(f'涨幅{chg:.1f}% 平淡')
    else:
        s1 = 0
        details.append(f'跌幅{chg:.1f}% 弱势')
        risk_factors.append('当日下跌')
    score += s1

    # 5日涨幅加分
    if chg_5d > 20:
        score += 4
        details.append(f'5日{chg_5d:.1f}% 持续拉升')
    elif chg_5d > 10:
        score += 2
        details.append(f'5日{chg_5d:.1f}% 短期强势')
    score = min(14, score)

    # 因子2：市场地位 0-10分（简化：用板块涨幅近似判断是否主线）
    s2 = 5  # 默认中性
    if sector_info:
        sector_chg = abs(sector_info.get('change_pct', 0))
        if sector_chg > 5:
            s2 = 10
            details.append('板块涨幅>5% 主线确认')
        elif sector_chg > 3:
            s2 = 8
            details.append('板块涨幅>3% 板块强势')
    # 涨停加分
    if chg >= 9.5:
        s2 = min(10, s2 + 3)
    score += s2

    # 因子3：情绪周期 0-10分
    phase = emotion_cycle.get('phase', '')
    if '高潮' in phase:
        s3 = 10
        details.append('情绪高潮期 适合操作')
    elif '复苏' in phase:
        s3 = 7
        details.append('情绪复苏期 可轻仓')
    elif '震荡' in phase:
        s3 = 5
        details.append('情绪震荡期 谨慎')
    else:
        s3 = 1
        details.append(f'{phase} 不操作')
        risk_factors.append('情绪冰点/退潮')
    score += s3

    formatted_score = min(30, max(0, round(score, 1)))
    rating = '买入' if formatted_score >= 20 else '持有' if formatted_score >= 12 else '卖出'

    return {
        'agent': '游资',
        'score': formatted_score,
        'rating': rating,
        'details': details,
        'risk_factors': risk_factors,
        'chg': chg,
        'chg_5d': chg_5d,
    }


# ============================================================
# 合力评分系统
# ============================================================

def calculate_combined(inst: dict, quant: dict, hot: dict, momentum: dict) -> dict:
    """计算合力综合评分"""
    inst_s = inst.get('score', 0)
    quant_s = quant.get('score', 0)
    hot_s = hot.get('score', 0)
    total = inst_s + quant_s + hot_s

    # 惯性面调节
    trend = momentum.get('trend', '')
    if '末期冲刺' in trend:
        total -= 10
    elif '趋势断裂' in trend:
        total -= 20
    elif '蓄力' in trend:
        total += 5

    total = max(0, min(100, total))

    if total >= 80:
        level = '🔥🔥🔥 三力合一'
    elif total >= 65:
        level = '🟢🟢 双力共振'
    elif total >= 50:
        level = '🟡 单力支撑'
    else:
        level = '⚪ 无合力'

    action = '强烈买入' if total >= 80 else '买入' if total >= 65 else '关注' if total >= 50 else '放弃'

    return {
        'total_score': total,
        'level': level,
        'action': action,
        'breakdown': {
            '机构': inst_s,
            '量化': quant_s,
            '游资': hot_s,
        },
        'trend_adjust': -10 if '末期冲刺' in trend else -20 if '趋势断裂' in trend else 5 if '蓄力' in trend else 0,
    }


# ============================================================
# 单只股票完整分析
# ============================================================

def analyze_stock_batch(codes: list) -> list:
    """批量分析多只股票的三资金合力"""
    rt_data = fetch_tencent_quote(codes)
    market_stats = get_market_stats()
    limit_data = get_limit_up_list()
    emotion = judge_emotion_cycle(market_stats, limit_data,
                                   limit_data.get('zt_count',0),
                                   limit_data.get('dt_count',0))

    results = []
    for code in codes:
        try:
            rt = rt_data.get(code, {})
            name = rt.get('name', code)
            klines = load_klines(code, 250)
            fundamentals = load_fundamentals(code)

            inst = score_institutional(code, rt, fundamentals, klines)
            quant = score_quantitative(code, rt, klines)
            hot = score_hot_money(code, rt, klines, limit_data, emotion)
            mi = calc_momentum_indicators(code, klines)
            combined = calculate_combined(inst, quant, hot, mi)

            results.append({
                'code': code, 'name': name,
                'price': rt.get('price', 0),
                'change_pct': rt.get('change_pct', 0),
                'institutional': inst,
                'quantitative': quant,
                'hot_money': hot,
                'combined': combined,
                'momentum': mi,
                'emotion': emotion,
                'market_cap': rt.get('market_cap', 0),
                'turnover': rt.get('turnover', 0),
                'timestamp': datetime.now().strftime('%H:%M:%S'),
            })
        except Exception:
            pass
    return results


def analyze_stock(code: str, sector_info: dict = None) -> dict:
    """单只股票分析（兼容单只调用）"""
    results = analyze_stock_batch([code])
    return results[0] if results else {}


def judge_emotion_cycle(market_stats, limit_data, zt_count, dt_count) -> dict:
    """判断情绪周期（包装成独立函数避免import冲突）"""
    zt = limit_data.get('zt_count', zt_count)
    dt = limit_data.get('dt_count', dt_count)
    up_ratio = market_stats.get('up_ratio', 0.5)

    if zt < 20 and dt > 10:
        return {'phase': '❄️ 冰点期', 'position_limit': 10, 'strategy': '轻仓试错'}
    elif zt >= 60 and dt < 5:
        return {'phase': '🔥 高潮期', 'position_limit': 80, 'strategy': '重仓龙头'}
    elif dt > 10 and zt < 30:
        return {'phase': '🍂 退潮期', 'position_limit': 0, 'strategy': '空仓'}
    elif zt >= 20:
        return {'phase': '🌱 复苏期', 'position_limit': 50, 'strategy': '接力龙头'}
    else:
        return {'phase': '➖ 震荡期', 'position_limit': 30, 'strategy': '轻仓试错'}


# ============================================================
# 报告生成
# ============================================================

def generate_report(result: dict) -> str:
    """生成微信推送格式报告"""
    lines = []
    cb = result['combined']
    inst = result['institutional']
    quant = result['quantitative']
    hot = result['hot_money']
    emo = result['emotion']

    lines.append(f"🏆 **三资金合力 — {result['name']} ({result['code']})**")
    lines.append(f"   ⏰ {result['timestamp']}  |  现价: {result['price']}")
    lines.append(f"   涨幅: {result['change_pct']:+.2f}%  |  换手: {result['turnover']:.1f}%")
    lines.append(f"   市值: {result['market_cap']:.0f}亿")
    lines.append("")

    lines.append(f"   📊 **{cb['level']}** 总分: {cb['total_score']}/100")
    lines.append(f"   🎯 {cb['action']}")
    lines.append("")

    lines.append(f"   📈 情绪周期: {emo['phase']} | 仓位上限: {emo['position_limit']}%")
    lines.append("")

    # 机构
    lines.append(f"   🏛 **机构: {inst['score']}/30** ({inst['rating']})")
    for d in inst['details']:
        lines.append(f"     {d}")
    if inst['risk_factors']:
        lines.append(f"     ⚠️ {' '.join(inst['risk_factors'])}")
    lines.append("")

    # 量化
    lines.append(f"   💻 **量化: {quant['score']}/30** ({quant['rating']})")
    for d in quant['details']:
        lines.append(f"     {d}")
    if quant['risk_factors']:
        lines.append(f"     ⚠️ {' '.join(quant['risk_factors'])}")
    lines.append("")

    # 游资
    lines.append(f"   🔥 **游资: {hot['score']}/30** ({hot['rating']})")
    for d in hot['details']:
        lines.append(f"     {d}")
    if hot['risk_factors']:
        lines.append(f"     ⚠️ {' '.join(hot['risk_factors'])}")
    lines.append("")

    # 惯性面
    mi = result.get('momentum', {})
    if mi:
        lines.append(f"   ⚡ **惯性参考**")
        lines.append(f"     5日: {mi.get('chg_5d',0):+.1f}%  20日: {mi.get('chg_20d',0):+.1f}%")
        lines.append(f"     60日: {mi.get('chg_60d',0):+.1f}%  RSI: {mi.get('rsi',0):.0f}")
        lines.append(f"     量比: {mi.get('vol_ratio',0):.2f}  距20日线: {mi.get('ma20_pct',0):+.1f}%")
        lines.append("")

    # 合力评分拆解
    lines.append(f"   💡 **合力拆解**: 机构{cb['breakdown']['机构']} + 量化{cb['breakdown']['量化']} + 游资{cb['breakdown']['游资']}")
    adj = cb.get('trend_adjust', 0)
    if adj != 0:
        lines.append(f"     惯性调节: {adj:+d}分")
    lines.append("")

    lines.append(f"   {'─'*40}")
    lines.append(f"   每日7:30-9:15盘前准备 | 9:15-9:30竞价 | 9:30-15:00交易")
    return '\n'.join(lines)


def generate_batch_report(results: list, emotion: dict) -> str:
    """批量报告"""
    lines = []
    lines.append(f"🏆 **三资金合力 — 批量扫描**")
    lines.append(f"   ⏰ {datetime.now().strftime('%H:%M:%S')}")
    lines.append(f"   情绪: {emotion['phase']} | 仓位上限: {emotion['position_limit']}%")
    lines.append("")

    for r in sorted(results, key=lambda x: x['combined']['total_score'], reverse=True):
        cb = r['combined']
        name = r.get('name', r['code'])
        chg = r.get('change_pct', 0)
        scores = f"{cb['breakdown']['机构']}+{cb['breakdown']['量化']}+{cb['breakdown']['游资']}"
        lines.append(f"   {name:<8s} {chg:>+5.1f}% | 总分{int(cb['total_score']):>2d} ({scores}) | {cb['level'][:8]} | {cb['action']}")
        if cb['total_score'] >= 50:
            lines.append(f"   {'':>12s}→ 建议{r['momentum'].get('action','') if r.get('momentum') else ''}")
        lines.append("")

    return '\n'.join(lines)


# ============================================================
# 主入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description='🏆 三资金合力股引擎')
    parser.add_argument('--code', type=str, help='个股代码')
    parser.add_argument('--scan', action='store_true', help='批量扫描核心标的')
    parser.add_argument('--sector', type=str, help='板块代码扫描')
    parser.add_argument('--full', action='store_true', help='输出完整因子')
    parser.add_argument('--json', action='store_true', help='JSON输出')
    args = parser.parse_args()

    # 核心标的池
    CORE_CODES = ['603986', '601138', '603019', '603893', '002281',
                  '600584', '603005', '603160', '600745', '600519']

    ms = get_market_stats()
    ld = get_limit_up_list()
    emo = judge_emotion_cycle(ms, ld, ld.get('zt_count',0), ld.get('dt_count',0))

    if args.code:
        result = analyze_stock(args.code)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(generate_report(result))

    elif args.sector:
        stocks = get_sector_stocks(args.sector, 10)
        codes = [s['code'] for s in stocks if s['code']]
        results = analyze_stock_batch(codes)
        print(generate_batch_report(results, emo))

    elif args.scan or args.full:
        results = analyze_stock_batch(CORE_CODES)
        print(generate_batch_report(results, emo))
        if args.full:
            print(f"\n{'='*50}\n详细报告:\n")
            for r in results:
                print(generate_report(r))
                print()

    else:
        results = analyze_stock_batch(CORE_CODES)
        print(generate_batch_report(results, emo))


if __name__ == '__main__':
    main()
