#!/usr/bin/env python3
"""
穷举式多层级多维度市场行情穿透动态评分系统 v2
===========================================
从bundle 50+keys中穷举所有可用数据，每个维度独立量化评分，6层25维合成汇总。

== 重大精简（v1 48维→v2 25维）==
移除冗余/高度相关的维度，合并为合成指标：
  🌐全球层 16→5维：美股三指合成|科技锚7只合成|趋势逆映射合成|贵金属↘合成
|  📈大盘层 12→6维：A股四指数|涨停链条合成(涨停+炸板+连板+溢价)|涨跌比|温度|量能|情绪(恐慌+活跃)|昨涨停持续性
  💰资金层  5→4维：龙虎榜合成(活跃+质量)|北上|散户↘|游资
  📡板块层  7→5维：板块强度合成(领涨+周期+密度)|主线|微观|预警↘|美股映射
  🔍个股层  4→4维：不变(已足够精简)
  ⚡时序层  4→2维：竞价合成(状态+决策)|分时信号

输出：penetration_score + 6层25维明细
"""

import os, json, sys
from datetime import datetime

BASE = os.path.expanduser('~/V2board')

def safe_float(v, default=0):
    try: return float(v) if v is not None else default
    except: return default

def safe_int(v, default=0):
    try: return int(v) if v is not None else default
    except: return default

def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))

# ================================================================
# 全局工具函数
# ================================================================
def trend_score(t):
    """趋势文字→分数映射（正向：持续强=85，持续弱=10）"""
    if '持续强' in t or '强转强' in t: return 85
    if '转强' in t: return 65
    if '弱转强' in t: return 55
    if '中性' in t: return 45
    if '强转弱' in t: return 30
    if '转弱' in t: return 20
    if '持续弱' in t: return 10
    return 45


# ================================================================
# 🌐 全球层 (5维) — 美股指数·原油·贵金属·A50/恒生/日经·全球方向
# ================================================================
def dimension_global(bundle):
    """全球市场：美股三指合成+原油+贵金属合成+亚太综合+全球方向"""
    scores = {}

    # === 1. 美股指数合成（纳斯达克+标普500+道琼斯） ===
    us_map = bundle.get('us_market_map', {})
    indices = us_map.get('indices', [])
    idx_map = {}
    for idx in indices:
        name = idx.get('name', '')
        chg = safe_float(idx.get('change_pct'))
        idx_map[name] = chg

    nasdaq = idx_map.get('纳斯达克', 0)
    sp500 = idx_map.get('标普500', 0)
    dow = idx_map.get('道琼斯', 0)
    us_avg = (nasdaq + sp500 + dow) / 3
    # 用连续梯度替代分段跳跃：得分=50+us_avg*20，上限85下限10
    us_score = clamp(50 + us_avg * 20, 10, 85)
    scores['美股指数(合成)'] = round(us_score)

    # === 2. WTI原油（↘双向风险） ===
    oil = bundle.get('brent_oil', {})
    oil_chg = safe_float(oil.get('change_pct', 0))
    # 油价偏离0越远分越低，0附近最高
    oil_score = clamp(70 - abs(oil_chg) * 15, 5, 80)
    scores['WTI原油↘'] = round(oil_score)

    # === 3. 贵金属↘合成（黄金+白银反向映射） ===
    us_dual = bundle.get('us_dual', {})
    overview = us_dual.get('overview', {})
    trends = overview.get('us_trends', {})
    if not isinstance(trends, dict): trends = {}

    gold_rev = {'持续强': 20, '转强': 35, '弱转强': 40, '中性': 50, '强转弱': 65, '转弱': 75, '持续弱': 85}
    gold_t = trends.get('COMEX黄金', '中性')
    slv_t = trends.get('COMEX白银', '中性')
    gold_raw = gold_rev.get(gold_t, 50)
    slv_raw = gold_rev.get(slv_t, 50)
    scores['贵金属↘(合成)'] = round(gold_raw * 0.6 + slv_raw * 0.4)

    # === 4. 亚太综合（A50/恒生/日经） — 替代之前7只科技锚（周日全50分） ===
    # 从us_market_map获取亚太指数
    apac_indices = us_map.get('apac_indices', [])
    apac_map = {}
    for idx in apac_indices:
        name = idx.get('name', '')
        chg = safe_float(idx.get('change_pct'))
        apac_map[name] = chg
    
    a50 = apac_map.get('A50', 0)
    hsi = apac_map.get('恒生指数', 0)
    nikkei = apac_map.get('日经225', 0)
    
    # 如果亚太数据不存在，回退到纳指/恒生联动
    if a50 == 0 and hsi == 0 and nikkei == 0:
        # 从us_market_map的extra_indices或old_data取
        ext = us_map.get('extra', [])
        for e in ext:
            en = e.get('name', '')
            ec = safe_float(e.get('change_pct'))
            if 'A50' in en: a50 = ec
            elif '恒生' in en: hsi = ec
            elif '日经' in en: nikkei = ec
    
    apac_avg = (a50 + hsi + nikkei) / 3 if (a50 != 0 or hsi != 0 or nikkei != 0) else 0
    if apac_avg == 0:
        # 完全无数据时用科技锚回退（避免周日50分扎堆）
        tech_trends = [trends.get(a, '中性') for a in ['英伟达', '台积电', '博通', 'AMD', '微软', '特斯拉', '苹果']]
        tech_avg = sum(trend_score(t) for t in tech_trends) / len(tech_trends) if tech_trends else 45
        # 纳指交叉验证
        if nasdaq < -1 and tech_avg > 60:
            tech_avg -= 20
        scores['亚太综合(科技锚回退)'] = round(clamp(tech_avg))
    else:
        apac_score = clamp(50 + apac_avg * 15, 5, 85)
        scores['亚太综合'] = round(apac_score)

    # === 5. 全球方向（buy/sell信号比） ===
    buy_sig = safe_int(overview.get('buy_signals', 5))
    sell_sig = safe_int(overview.get('sell_signals', 2))
    ratio = buy_sig / max(sell_sig, 1)
    if buy_sig >= 5 and sell_sig <= 2: scores['全球方向'] = 85
    elif ratio >= 2 and buy_sig >= 3: scores['全球方向'] = 75
    elif ratio >= 1: scores['全球方向'] = 65
    elif sell_sig > buy_sig: scores['全球方向'] = 30
    else: scores['全球方向'] = 45

    return scores


# ================================================================
# 📈 大盘层 (4维，精简) — A股指数·涨跌比·涨停链条合成·量价温度综合
# ================================================================
def dimension_market(bundle):
    """大盘(精简版)：涨跌比+涨停链条+指数+温度-已合并量价"""
    scores = {}
    md = bundle.get('market_daily', {})
    idx_list = bundle.get('market_index', [])
    env = bundle.get('market_env', {})

    # === 1. 涨跌比（最能反映大盘冷暖的核心指标） ===
    zh = safe_float(md.get('zh_ratio', md.get('up_ratio', 50)))
    if zh >= 70: scores['涨跌比'] = 90
    elif zh >= 55: scores['涨跌比'] = 70
    elif zh >= 45: scores['涨跌比'] = 50
    elif zh >= 30: scores['涨跌比'] = 30
    else: scores['涨跌比'] = 10

    # === 2. 涨停链条合成（涨停强度×0.4+连板高度×0.25+炸板率逆向×0.2+隔日溢价×0.15） ===
    lu = safe_int(md.get('limit_up', 0))
    ld = safe_int(md.get('limit_down', 0))
    net = lu - ld
    ld_penalty = ld >= 30 and ld >= lu * 0.4
    
    zt_raw = clamp(net * 0.8, 0, 100)  # 连续梯度：净涨停每只+0.8分
    if lu >= 80 and net >= 50: zt_score = 95
    elif lu >= 50 and net >= 25: zt_score = 75
    elif lu >= 25 and net >= 10: zt_score = 55
    elif lu >= 10 and net >= 0: zt_score = 35
    elif net >= -10: zt_score = 15
    else: zt_score = 5
    
    if ld_penalty: zt_score = min(zt_score, 25)

    max_b = safe_int(md.get('max_board', 0))
    if max_b >= 7: lb_score = 95
    elif max_b >= 5: lb_score = 80
    elif max_b >= 3: lb_score = 60
    elif max_b >= 1: lb_score = 40
    else: lb_score = 10

    zr = safe_float(md.get('zhaban_rate', 50))
    if zr < 15: zb_score = 90
    elif zr < 25: zb_score = 70
    elif zr < 35: zb_score = 50
    elif zr < 45: zb_score = 30
    else: zb_score = 10

    pa = safe_float(md.get('pretoday_avg_change', 0))
    if pa > 3: yy_score = 90
    elif pa > 1.5: yy_score = 70
    elif pa > 0: yy_score = 50
    elif pa > -1: yy_score = 30
    else: yy_score = 10

    chain = round(zt_score * 0.40 + lb_score * 0.25 + zb_score * 0.20 + yy_score * 0.15)
    scores['涨停链条(合成)'] = chain

    # === 3. 环境温度+量价综合（合并之前冗余的A股指数+量能+温度三个维度） ===
    env_key = env.get('env_key', env.get('env', '震荡'))
    temp_map = {'高潮': 95, '活跃': 80, '发酵': 60, '震荡': 45, '冰点': 15, '恐慌': 5}
    temp_score = temp_map.get(env_key, 45)
    
    # 成交量修正：放量加分，缩量减分
    vol_ratio = safe_float(env.get('vol_ratio', 1.0))
    if vol_ratio >= 1.3: temp_score = min(100, temp_score + 10)
    elif vol_ratio <= 0.7: temp_score = max(5, temp_score - 10)
    
    # 指数修正（市场_daily的change_pct）
    sh_chg = safe_float(md.get('sh_change', md.get('上证涨幅', 0)))
    if abs(sh_chg) > 1: temp_score = clamp(temp_score + sh_chg * 5)  # 大波动加/减分
    
    scores['环境量价综合'] = clamp(round(temp_score))

    # === 4. A股指数合成（保留但改为连续梯度） ===
    idx_map = {}
    if isinstance(idx_list, list):
        for item in idx_list:
            name = item.get('name', '')
            chg = safe_float(item.get('change_pct'))
            idx_map[name] = chg

    sh = idx_map.get('上证指数', 0)
    cy = idx_map.get('创业板指', 0)
    sz = idx_map.get('深证成指', 0)
    kc = idx_map.get('科创50', 0)
    a_avg = sh * 0.35 + cy * 0.25 + sz * 0.25 + kc * 0.15
    a_score = clamp(50 + a_avg * 20, 5, 90)
    scores['A股指数(合成)'] = round(a_score)

    # === 5. 市场情绪(恐慌↘vs活跃↗) ===
    panic = safe_int(md.get('panic_score', 0))
    boom = safe_int(md.get('boom_score', 0))
    mood = md.get('market_mood', '')
    panic_rev = max(0, 100 - panic * 20) if panic > 0 else 50
    boom_score = min(100, boom * 20) if boom > 0 else 50
    mood_avg = (panic_rev + boom_score) / 2
    if '恐慌' in str(mood): mood_avg = min(mood_avg, 30)
    elif '活跃' in str(mood): mood_avg = max(mood_avg, 60)
    scores['市场情绪(恐慌↘)'] = round(clamp(mood_avg))

    # === 6. 昨涨停持续性(连板率+隔日溢价) ===
    lb_rate = safe_float(md.get('pretoday_lianban_rate', 50))
    die_gt5_rate = safe_float(md.get('pretoday_die_gt_5_rate', 20))
    pa2 = safe_float(md.get('pretoday_avg_change', 0))
    if lb_rate >= 70 and pa2 > 0: sustain = 80
    elif lb_rate >= 50 and pa2 > 0: sustain = 60
    elif lb_rate >= 30: sustain = 40
    else: sustain = 20
    if die_gt5_rate >= 25: sustain = min(sustain, 35)
    elif die_gt5_rate >= 15: sustain = min(sustain, 50)
    scores['昨涨停持续性'] = round(clamp(sustain))

    return scores


# ================================================================
# 💰 资金层 (6维) — 龙虎榜合成·北上·散户↘·游资·V7因子·大单流向
# ================================================================
def dimension_funds(bundle):
    """资金：龙虎榜合成+北上+散户+游资+V7因子+大单流向"""
    scores = {}
    lhb = bundle.get('lhb_scoring', {})

    # === 1. 龙虎榜合成（活跃×0.4+质量×0.6） ===
    actionable = safe_int(lhb.get('actionable', 0))
    if actionable >= 5: active = 85
    elif actionable >= 3: active = 70
    elif actionable >= 1: active = 55
    else: active = 30

    tier_stats = lhb.get('tier_stats', {})
    s_count = safe_int(tier_stats.get('S', {}).get('count', 0))
    a_count = safe_int(tier_stats.get('A', {}).get('count', 0))
    high_quality = s_count + a_count
    if high_quality >= 3: quality = 85
    elif high_quality >= 1: quality = 65
    else: quality = 40

    scores['龙虎榜(合成)'] = round(active * 0.35 + quality * 0.65)

    # === 2. 北上资金 ===
    lhb_detail = bundle.get('lhb_selected_detail', {}) or bundle.get('lhb_selected_features', {})
    north_score = safe_float(lhb_detail.get('north_score', 50))
    scores['北上资金'] = clamp(north_score)

    # === 3. 散户情绪↘ ===
    rs = bundle.get('retail_sentiment', {})
    if isinstance(rs, dict):
        retail_score = safe_float(rs.get('index', rs.get('sentiment', 50)))
    else:
        retail_score = 50
    if retail_score < 20: scores['散户↘'] = 85  # 恐慌=机会
    elif retail_score < 35: scores['散户↘'] = 70
    elif retail_score < 50: scores['散户↘'] = 50
    elif retail_score < 65: scores['散户↘'] = 35
    else: scores['散户↘'] = 20  # 狂热=风险

    # === 4. 游资活跃度 ===
    yz = bundle.get('youzi_signal', {})
    yz_score = safe_float(yz.get('score', yz.get('total', 50)))
    scores['游资活跃度'] = clamp(yz_score)

    # === 5. V7因子综合评分（从lhb_v7_decision注入bundle） ===
    v7 = bundle.get('lhb_v7_report', {})
    if v7:
        v7_score = safe_float(v7.get('total_score', v7.get('env_score', 50)))
        scores['V7因子评分'] = clamp(v7_score)
    else:
        # 无V7数据时，从lhb_scoring的s/a级回测得分推算资金信心
        lhb_details = bundle.get('lhb_list', [])
        if lhb_details:
            avg_signal = sum(
                safe_float(d.get('score', 50)) for d in 
                (lhb_details if isinstance(lhb_details, list) else [lhb_details])
            ) / max(len(lhb_details), 1)
            scores['V7因子评分'] = clamp(avg_signal)
        else:
            scores['V7因子评分'] = 40  # 保守默认值

    # === 6. 大单资金流向（从内部数据读取，纯主板主力资金净占比） ===
    funds_data = bundle.get('fund_flow', bundle.get('market_daily', {}))
    main_force = safe_float(funds_data.get('main_force_ratio', funds_data.get('主力净占比', 50)))
    if main_force > 0.5: scores['大单流向'] = 80
    elif main_force > 0.1: scores['大单流向'] = 65
    elif main_force > -0.1: scores['大单流向'] = 45
    elif main_force > -0.5: scores['大单流向'] = 25
    else: scores['大单流向'] = 10

    return scores


# ================================================================
# 📡 板块层 (4维，精简) — 板块强度·主线健康度·微观信号·美股映射
# ================================================================
def dimension_sector(bundle):
    """板块(精简版)：强度合成+主线健康度(合并预警)+微观信号+美股映射"""
    sr = bundle.get('sector_ranking', [])
    sd = bundle.get('sector_decisions', {})
    sc = bundle.get('sector_cycle', {})
    sm = bundle.get('sector_micro', {})
    sa = bundle.get('sector_alerts', [])
    scores = {}

    # === 1. 板块强度合成（领涨涨幅×0.40+涨停密度×0.35+周期状态×0.25） ===
    if sr and len(sr) > 0:
        top_chg = safe_float(sr[0].get('change', 0))
        if top_chg >= 5: lz_score = 90
        elif top_chg >= 3: lz_score = 75
        elif top_chg >= 1: lz_score = 55
        elif top_chg >= 0: lz_score = 35
        else: lz_score = 15
    else:
        lz_score = 30

    if sr:
        total_limits = sum(safe_int(s.get('limits', 0)) for s in sr[:5])
        if total_limits >= 15: md_score = 90
        elif total_limits >= 8: md_score = 70
        elif total_limits >= 3: md_score = 50
        else: md_score = 25
    else:
        md_score = 25

    sc_info = sc.get('signals', sc.get('data', []))
    if sc_info:
        strong_count = sum(1 for s in sc_info if '强' in str(s.get('status', '')))
        if strong_count >= 3: cy_score = 80
        elif strong_count >= 1: cy_score = 55
        else: cy_score = 30
    else:
        cy_score = 45

    strength = round(lz_score * 0.40 + md_score * 0.35 + cy_score * 0.25)
    scores['板块强度(合成)'] = strength

    # === 2. 主线健康度（合并原来的主线清晰度+预警↘） ===
    main_line = sd.get('main_line', '')
    has_main = sd.get('has_main', False)
    
    # 预警修正
    alert_count = len(sa) if isinstance(sa, list) else 0
    alert_penalty = 0
    if alert_count >= 5: alert_penalty = -25
    elif alert_count >= 3: alert_penalty = -15
    elif alert_count >= 1: alert_penalty = -5
    
    if main_line and has_main:
        health = 80 + alert_penalty
    elif main_line:
        health = 55 + alert_penalty
    else:
        health = 25 + alert_penalty
    
    scores['主线健康度'] = clamp(health)

    # === 3. 微观信号 ===
    sms = sm.get('signals', sm.get('data', []))
    if isinstance(sms, list):
        pos_count = sum(1 for s in sms if s.get('signal', '') in ('买入', '关注', '龙头竞价'))
        if pos_count >= 5: scores['微观信号'] = 85
        elif pos_count >= 3: scores['微观信号'] = 65
        elif pos_count >= 1: scores['微观信号'] = 50
        else: scores['微观信号'] = 30
    else:
        scores['微观信号'] = 45

    # === 4. 美股映射 ===
    us_dual = bundle.get('us_dual', {})
    mapping = us_dual.get('mapping', us_dual.get('matched_sectors', []))
    if mapping and len(mapping) >= 2: scores['美股映射'] = 75
    elif mapping: scores['美股映射'] = 55
    else: scores['美股映射'] = 35

    return scores


# ================================================================
# 🔍 个股层 (7维→5维精简) — 候选质量·策略匹配·跟风热度·龙头梯队·龙虎榜实战策略
# ================================================================
# 回测数据源：lhb_practical_backtest_v2.json (严格时间线回测, 替代旧v1)
# TOP4王牌策略（按close_ret×close_win综合排名）:
#   1. 缩量+主力+机构 (vr<0.7+ratio>1.5+机构): +4.89%, 68.1%胜率, n=69
#   2. 极缩+量化 (vr<0.3+量化): +3.36%, 66.1%胜率, n=62
#   3. 连板+缩量+机构: +3.61%, 63.4%胜率, n=71
#   4. 缩量+主力买入 (vr<0.7+ratio>1.5): +2.75%, 60.8%胜率, n=148
BACKTEST_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'lhb_practical_backtest_v2.json')

def _load_strategy_backtest():
    """加载龙虎榜实战回测数据"""
    try:
        if os.path.exists(BACKTEST_PATH):
            with open(BACKTEST_PATH) as f:
                data = json.load(f)
            strategies = data.get('strategies', [])
            return {s['name']: s for s in strategies}
    except:
        pass
    return {}

def extract_tags(name):
    """从策略名提取核心标签"""
    tags = set()
    if '机构' in name: tags.add('机构')
    if '量化' in name: tags.add('量化')
    if '游资' in name or '非游资' in name: tags.add('游资')
    if '主力买入' in name or '买力' in name: tags.add('主力')
    if '净买入' in name or '净买' in name: tags.add('净买')
    if '连板' in name or '前日涨停' in name or '前日大涨' in name: tags.add('连板')
    if '缩量' in name: tags.add('缩量')
    if '振幅' in name or '窄幅' in name: tags.add('控盘')
    if '换手' in name: tags.add('换手')
    return tags

def _score_strategies_from_backtest():
    """从回测数据建立策略评分表（按综合分排序）"""
    bt = _load_strategy_backtest()
    if not bt:
        return []
    scored = []
    for name, s in bt.items():
        ret = safe_float(s.get('close_ret', 0))
        wr = safe_float(s.get('close_win', 0))
        n = safe_int(s.get('n', 0))
        coverage = safe_float(s.get('cover', 0))
        # 综合分 = 收益率×40% + 胜率×30% + 样本量权重(ln(n+1)/ln(50))×30%
        sample_factor = (__import__('math').log(n + 1) / __import__('math').log(50)) if n > 0 else 0
        sample_factor = min(sample_factor, 1.0)
        composite = ret * 4.0 + wr * 0.3 + sample_factor * 15.0
        scored.append({
            'name': name,
            'desc': s.get('desc', ''),
            'composite': round(composite, 1),
            'ret': ret,
            'win_rate': wr,
            'n': n,
            'cover': coverage,
            'big_win_pct': safe_float(s.get('big_win_pct', 0)),
            'big_loss_pct': safe_float(s.get('big_loss_pct', 0)),
        })
    scored.sort(key=lambda x: -x['composite'])
    return scored

def dimension_stock(bundle):
    """个股(5维精简版)：候选质量+龙虎榜实战策略匹配+跟风热度+龙头梯队+龙虎榜质量"""
    bc = bundle.get('buy_candidates', [])
    ts = bundle.get('top_strategies', [])
    wd = bundle.get('watch_dashboard', {})
    lhb_detail = bundle.get('lhb_selected_detail', {}) or bundle.get('lhb_selected_features', {})
    scores = {}

    # 候选质量——从buy_candidates评分
    if bc:
        a_count = sum(1 for c in bc if (c.get('tier') or 'B') in ('S', 'A'))
        total_score = sum(safe_float(c.get('score', 0)) for c in bc[:5])
        avg = total_score / max(len(bc[:5]), 1)
        if a_count >= 3 and avg >= 60: scores['候选质量'] = 90
        elif a_count >= 1 and avg >= 50: scores['候选质量'] = 70
        elif len(bc) >= 3: scores['候选质量'] = 50
        elif len(bc) >= 1: scores['候选质量'] = 30
        else: scores['候选质量'] = 15
    else:
        scores['候选质量'] = 10

    # 龙虎榜实战策略匹配——从真实回测数据精确匹配
    # 匹配规则：使用extract_tags提取策略核心标签，与龙虎榜资金标签取交集
    lhb_list = bundle.get('lhb_list', [])
    lhb_actionable = bundle.get('lhb_actionable', [])
    
    # 计算当前龙虎榜数据中可执行的策略标签
    strategy_tags = set()
    buy_ratio = 0
    
    for item in lhb_actionable if lhb_actionable else (lhb_list if lhb_list else []):
        if isinstance(item, dict):
            ratio = safe_float(item.get('buy_sell_ratio', item.get('ratio', 0)))
            if ratio > buy_ratio: buy_ratio = ratio

    lhb_inst = safe_int(lhb_detail.get('inst_count', lhb_detail.get('机构数量', 0)))
    lhb_quant = safe_int(lhb_detail.get('quant_count', lhb_detail.get('量化数量', 0)))
    lhb_buy = safe_float(lhb_detail.get('total_buy', lhb_detail.get('净买入', 0)))
    
    if lhb_inst > 0: strategy_tags.add('机构')
    if lhb_quant > 0: strategy_tags.add('量化')
    if lhb_buy > 0: strategy_tags.add('净买')
    if buy_ratio > 1.5: strategy_tags.add('主力')
    
    # 从回测数据匹配
    strategy_rankings = _score_strategies_from_backtest()
    matched_strategies = []
    
    for st in strategy_rankings:
        name = st['name']
        st_tags = extract_tags(name)
        
        if not st_tags:
            # 无标签策略（龙虎榜首板基准）
            if not strategy_tags:
                score = st['composite'] * 0.8
            else:
                continue
        else:
            # 核心标签交集
            core_tags = st_tags - {'缩量', '控盘', '换手', '连板'}
            core_hit = core_tags & strategy_tags
            
            if core_hit:
                score = st['composite'] * min(1.0, len(core_hit) * 0.5 + 0.3)
            else:
                continue  # 完全无匹配
        
        if score > 0:
            matched_strategies.append({**st, 'match_score': round(score, 1)})
    
    matched_strategies.sort(key=lambda x: -x['match_score'])
    
    if matched_strategies:
        top = matched_strategies[0]
        match_score = top['match_score']
        # 映射：最优策略(缩量+主力+机构 55分) → 90分
        raw_score = min(90, max(10, int(match_score / 55.0 * 100)))
        scores['策略匹配'] = raw_score
        
        bundle['_strategy_match_detail'] = {
            'top_strategy': top['name'],
            'win_rate': top['win_rate'],
            'expected_ret': top['ret'],
            'sample_n': top['n'],
            'total_matched': len(matched_strategies),
            'strategy_tags': list(strategy_tags),
            'strategy_rankings': [{'name': s['name'][:40], 'composite': s['composite'],
                                   'ret': s['ret'], 'win_rate': s['win_rate']} 
                                  for s in strategy_rankings[:5]],
        }
    else:
        scores['策略匹配'] = 20
        bundle['_strategy_match_detail'] = {
            'top_strategy': '无匹配',
            'total_matched': 0,
            'strategy_tags': list(strategy_tags),
            'benchmark_ret': 0.26,
            'benchmark_win': 48.2,
        }

    # 跟风热度（不变）
    fs = bundle.get('follower_signals', {})
    if isinstance(fs, dict):
        fs_list = fs.get('signals', fs.get('data', []))
    else:
        fs_list = []
    if isinstance(fs_list, list):
        fs_count = len(fs_list)
        if fs_count >= 5: scores['跟风热度'] = 80
        elif fs_count >= 3: scores['跟风热度'] = 65
        elif fs_count >= 1: scores['跟风热度'] = 45
        else: scores['跟风热度'] = 25
    else:
        scores['跟风热度'] = 30

    # 龙头梯队（不变）
    ladder = wd.get('ladder_stocks', wd.get('ladder', []))
    if isinstance(ladder, list):
        high_board = max([safe_int(s.get('board', 0)) for s in ladder], default=0)
        if high_board >= 5: scores['龙头梯队'] = 85
        elif high_board >= 3: scores['龙头梯队'] = 65
        elif high_board >= 1: scores['龙头梯队'] = 45
        else: scores['龙头梯队'] = 20
    else:
        scores['龙头梯队'] = 30

    # 龙虎榜质量（新增第5维）—— 从lhb_scoring读取真实S/A/B级分布
    lhb_scoring = bundle.get('lhb_scoring', {})
    tier_stats = lhb_scoring.get('tier_stats', {})
    s_cnt = safe_int(tier_stats.get('S', {}).get('count', 0))
    a_cnt = safe_int(tier_stats.get('A', {}).get('count', 0))
    total_scored = safe_int(lhb_scoring.get('total', 0))
    if s_cnt >= 1:
        scores['龙虎榜质量'] = 85  # 有S级=高质量
    elif a_cnt >= 2:
        scores['龙虎榜质量'] = 70
    elif a_cnt >= 1:
        scores['龙虎榜质量'] = 55
    elif total_scored >= 3:
        scores['龙虎榜质量'] = 40
    else:
        scores['龙虎榜质量'] = 20

    return scores


# ================================================================
# ⚡ 时序层 (2维) — 竞价合成·分时信号
# ================================================================
def dimension_timing(bundle):
    """时序(精简)：竞价合成(状态+决策)+分时信号"""
    ts = bundle.get('time_slice', {})
    au = bundle.get('auction', {})
    ad = bundle.get('auction_decision', {})
    scores = {}

    # === 1. 竞价合成（状态×0.5+决策×0.5） ===
    # 竞价状态
    if isinstance(au, dict):
        au_signal = au.get('signal', au.get('direction', ''))
        au_score = au.get('score', 50)
        if isinstance(au_score, (int, float)):
            if '强' in str(au_signal): au_state = clamp(au_score)
            elif '弱' in str(au_signal): au_state = clamp(100 - au_score)
            else: au_state = clamp(au_score)
        else:
            au_state = 50
    elif isinstance(au, list) and len(au) > 0:
        au_state = 50
    else:
        au_state = 40

    # 竞价决策
    if isinstance(ad, dict):
        ad_decision = ad.get('decision', ad.get('verdict', ''))
        if '可买' in str(ad_decision) or '强' in str(ad_decision):
            ad_state = 75
        elif '谨慎' in str(ad_decision) or '观察' in str(ad_decision):
            ad_state = 45
        elif '放弃' in str(ad_decision) or '弱' in str(ad_decision):
            ad_state = 20
        else:
            ad_state = 50
    else:
        ad_state = 45

    scores['竞价(合成)'] = round(au_state * 0.55 + ad_state * 0.45)

    # === 2. 分时信号（扩展为3维：买信号+卖信号+综合） ===
    ts_data = ts.get('data', ts.get('signals', []))
    if isinstance(ts_data, list) and len(ts_data):
        pos_signals = sum(1 for s in ts_data if '强' in str(s.get('signal', '')) or '买' in str(s.get('signal', '')))
        neg_signals = sum(1 for s in ts_data if '弱' in str(s.get('signal', '')) or '卖' in str(s.get('signal', '')) or '空' in str(s.get('signal', '')))
        
        # 买入信号
        if pos_signals >= 3: scores['分时买入信号'] = 80
        elif pos_signals >= 1: scores['分时买入信号'] = 60
        else: scores['分时买入信号'] = 35
        
        # 卖出信号（反向：越多说明越差）
        if neg_signals >= 3: scores['分时卖出信号↘'] = 20
        elif neg_signals >= 1: scores['分时卖出信号↘'] = 40
        else: scores['分时卖出信号↘'] = 70
    else:
        scores['分时买入信号'] = 45
        scores['分时卖出信号↘'] = 50

    return scores


# ================================================================
# 🧮 汇总引擎 v3 — 动态权重+策略融合
# ================================================================
def full_penetration_scan(bundle):
    """6层25维合成式穿透评分（v3 — 动态权重+策略回测融合）"""

    # 基准权重（源自回测高潮期胜率68.2%的发现，个股层在高分环境应加权重）
    layer_weights = {
        '🌐全球层': 0.15,
        '📈大盘层': 0.20,
        '💰资金层': 0.20,
        '📡板块层': 0.15,
        '🔍个股层': 0.15,
        '⚡时序层': 0.15,
    }

    # 执行所有层
    all_dimensions = {}
    all_dimensions['🌐全球层'] = dimension_global(bundle)
    all_dimensions['📈大盘层'] = dimension_market(bundle)
    all_dimensions['💰资金层'] = dimension_funds(bundle)
    all_dimensions['📡板块层'] = dimension_sector(bundle)
    all_dimensions['🔍个股层'] = dimension_stock(bundle)
    all_dimensions['⚡时序层'] = dimension_timing(bundle)

    # 动态权重调整
    # 用大盘层初步判断环境
    market_layer = all_dimensions.get('📈大盘层', {})
    market_raw = sum(market_layer.values()) / max(len(market_layer), 1) if market_layer else 0
    
    # 根据环境分桶动态调整权重
    if market_raw >= 70:
        # 🔥高潮期：加大个股/板块权重，降低大盘权重（都已经知道了）
        layer_weights = {
            '🌐全球层': 0.10, '📈大盘层': 0.10,
            '💰资金层': 0.20, '📡板块层': 0.20,
            '🔍个股层': 0.25, '⚡时序层': 0.15,
        }
    elif market_raw >= 55:
        # 🟢活跃期：均衡
        layer_weights = {
            '🌐全球层': 0.12, '📈大盘层': 0.18,
            '💰资金层': 0.20, '📡板块层': 0.17,
            '🔍个股层': 0.18, '⚡时序层': 0.15,
        }
    elif market_raw >= 40:
        # ⚪发酵期：维持基准
        pass  # 使用下面的基准权重
    elif market_raw >= 25:
        # 🔵震荡期：加大大盘/全球权重，降低个股
        layer_weights = {
            '🌐全球层': 0.20, '📈大盘层': 0.25,
            '💰资金层': 0.20, '📡板块层': 0.15,
            '🔍个股层': 0.10, '⚡时序层': 0.10,
        }
    else:
        # 🔴冰点期：大盘+全球权重过半，个股趋近于0
        layer_weights = {
            '🌐全球层': 0.25, '📈大盘层': 0.30,
            '💰资金层': 0.20, '📡板块层': 0.15,
            '🔍个股层': 0.05, '⚡时序层': 0.05,
        }
    
    # 策略加成微调：如果匹配到高胜率策略，个股层+0.03
    strategy_detail = bundle.get('_strategy_match_detail', {})
    matched_cnt = strategy_detail.get('total_matched', 0)
    top_wr = strategy_detail.get('win_rate', 0)
    top_ret = strategy_detail.get('expected_ret', 0)
    
    if top_wr > 60 and top_ret > 2 and market_raw >= 40:
        layer_weights['🔍个股层'] = min(0.30, layer_weights['🔍个股层'] + 0.03)
        # 从大盘和全球各扣0.015
        layer_weights['📈大盘层'] = max(0.08, layer_weights['📈大盘层'] - 0.015)
        layer_weights['🌐全球层'] = max(0.08, layer_weights['🌐全球层'] - 0.015)
    
    # 计算每层平均分（个股层使用加权平均，避免90 vs 20极端拉平）
    layer_scores = {}
    for layer, dim_dict in all_dimensions.items():
        if layer == '🔍个股层':
            # 个股层加权：策略匹配×0.30 + 龙虎榜质量×0.25 + 候选质量×0.20 + 龙头梯队×0.15 + 跟风热度×0.10
            dim_w = {'策略匹配': 0.30, '龙虎榜质量': 0.25, '候选质量': 0.20, '龙头梯队': 0.15, '跟风热度': 0.10}
            w_avg = 0
            w_sum = 0
            for d_name, d_score in dim_dict.items():
                w = dim_w.get(d_name, 0.20)
                w_avg += d_score * w
                w_sum += w
            avg = w_avg / w_sum if w_sum > 0 else 0
        elif layer == '⚡时序层':
            # 时序层只有2维，直接平均容易稀释，使用几何平均（维度数少时更保守）
            vals = list(dim_dict.values())
            if vals:
                import math
                product = 1
                for v in vals:
                    product *= max(v, 1)  # 避免0
                avg = product ** (1/len(vals))
            else:
                avg = 0
        else:
            avg = sum(dim_dict.values()) / len(dim_dict) if dim_dict else 0
        layer_scores[layer] = round(avg, 1)

    # 加权总分
    total = 0
    for layer, weight in layer_weights.items():
        total += layer_scores.get(layer, 0) * weight
    total = clamp(round(total, 1))

    # 决策（沿用阈值）
    if total >= 70: decision, pos = '✅重仓出击', '60~80%'
    elif total >= 55: decision, pos = '🟢正常操作', '30~50%'
    elif total >= 40: decision, pos = '⚠️轻仓参与', '15~30%'
    elif total >= 25: decision, pos = '👀观望为主', '0~15%'
    else: decision, pos = '❌空仓休息', '0%'

    # 全部维度排序（用于top/bottom 3）
    all_pairs = []
    for layer, dim_dict in all_dimensions.items():
        for dim_name, dim_score in dim_dict.items():
            all_pairs.append((layer, dim_name, dim_score))
    all_pairs.sort(key=lambda x: x[2])
    worst_3 = all_pairs[:3]
    best_3 = all_pairs[-3:]

    # 提取策略推荐摘要
    strategy_summary = {}
    if strategy_detail:
        strategy_summary = {
            'top_strategy': strategy_detail.get('top_strategy', ''),
            'win_rate': strategy_detail.get('win_rate', 0),
            'expected_ret': strategy_detail.get('expected_ret', 0),
            'sample_n': strategy_detail.get('sample_n', 0),
            'total_matched': strategy_detail.get('total_matched', 0),
            'strategy_tags': strategy_detail.get('strategy_tags', []),
        }

    # 数据质量标记（直接从bundle读取）
    b_market_idx = bundle.get('market_index', [])
    b_market_daily = bundle.get('market_daily', {})
    b_sector_rank = bundle.get('sector_ranking', [])
    b_lhb = bundle.get('lhb_actionable', []) or bundle.get('lhb_list', [])
    b_us_map = bundle.get('us_market_map', {})
    data_quality = {
        'bundle_has_market_index': bool(b_market_idx),
        'bundle_has_market_daily': bool(b_market_daily),
        'bundle_has_sector': bool(b_sector_rank),
        'bundle_has_lhb': bool(b_lhb),
        'bundle_has_us_map': bool(b_us_map),
    }
    
    return {
        'engine': 'penetration-scoring-v4',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_score': total,
        'decision': decision,
        'position': pos,
        'layer_weights': layer_weights,
        'layer_scores': layer_scores,
        'dimensions': {k: dict(sorted(v.items(), key=lambda x: -x[1])) for k, v in all_dimensions.items()},
        'total_dimensions': len(all_pairs),
        'worst_signals': [{'layer': l, 'dim': d, 'score': s} for l, d, s in worst_3],
        'best_signals': [{'layer': l, 'dim': d, 'score': s} for l, d, s in best_3],
        'strategy_from_backtest': strategy_summary,
        'strategy_rankings': _score_strategies_from_backtest()[:5],
        'data_quality': data_quality,  # 新增：标记哪些数据源有真实数据
    }


def generate_report(bundle=None):
    if bundle is None:
        bundle_path = os.path.join(BASE, 'dashboard_bundle.json')
        if os.path.exists(bundle_path):
            with open(bundle_path) as f:
                bundle = json.load(f)
        else:
            print("❌ 找不到bundle文件")
            return None
    result = full_penetration_scan(bundle)
    path = os.path.join(BASE, 'data', 'contradiction_report.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    return result


def print_report(r):
    if not r: return
    print("=" * 65)
    print(f"🧠 穿透评分 v3  |  {r['timestamp']}")
    print("=" * 65)
    print(f"\n🏆 总评分: {r['total_score']}/100  →  {r['decision']}  [{r['position']}]")
    print(f"   6层{r['total_dimensions']}维 合成精简 (v3 — 动态权重+策略融合)")
    print(f"\n📊 层级评分:")
    for layer, score in r['layer_scores'].items():
        bar = '█' * (int(score) // 5) + '░' * (20 - int(score) // 5)
        weight = r['layer_weights'].get(layer, 0)
        print(f"  {layer}: {score:5.1f} (×{weight:.2f}) {bar}")
    
    # 策略匹配
    st = r.get('strategy_from_backtest', {})
    if st and st.get('top_strategy'):
        print(f"\n🏅 回测策略匹配: {st['top_strategy']}")
        print(f"   胜率{st['win_rate']:.1f}% | 预期收益{st['expected_ret']:+.2f}% | 样本{st['sample_n']}笔 | 策略池{st['total_matched']}个")
    
    print(f"\n🟢 TOP3积极信号:")
    for s in r['best_signals']:
        print(f"  + {s['dim']}: {s['score']}/100  ({s['layer']})")
    print(f"\n🔴 TOP3风险信号:")
    for s in r['worst_signals']:
        print(f"  - {s['dim']}: {s['score']}/100  ({s['layer']})")
    print(f"\n📋 全维度({r['total_dimensions']}维):")
    for layer, dims in r['dimensions'].items():
        print(f"\n  [{layer}]")
        for d, s in list(dims.items())[:6]:
            bar = '█' * (int(s) // 10) if s > 0 else '░'
            print(f"    {d:<16} {s:3.0f} {bar}")


if __name__ == '__main__':
    r = generate_report()
    if r:
        print_report(r)
        if '--json' in sys.argv:
            print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
