#!/usr/bin/env python3
"""
🎯 穿透式实战入口 — 明日08:30全链路跑分使用
===========================================
读取当前市场环境 → 匹配穿透式信号库 → 输出完整操作计划

用法:
  python3 scripts/penetration_tactical.py                  # 今日推荐
  python3 scripts/penetration_tactical.py --live           # 明日执行模式
  python3 scripts/penetration_tactical.py --date=2026-05-18 # 指定日期

输出:
  - 环境诊断: 星期+环境+涨停数+资金特征
  - 最佳策略: 基于1,541笔严格时间线回测
  - 风险提醒: 回避区域检测
  - 仓位建议: 0%~80%
"""

import os, sys, json
from datetime import datetime

BASE = os.path.expanduser("~/astock")
DATA = os.path.join(BASE, "data")
DOCS = os.path.join(BASE, "docs")
SIGNAL_DB = os.path.join(DOCS, "signals_penetration_v1.json")
KLINE_DB = os.path.join(DATA, "lhb_cache.db")

# ============================================================
# 信号数据库
# ============================================================
_g_signal_db = None
def load_signal_db():
    global _g_signal_db
    if _g_signal_db is None:
        if os.path.exists(SIGNAL_DB):
            with open(SIGNAL_DB) as f:
                _g_signal_db = json.load(f)
    return _g_signal_db

# ============================================================
# 环境感知
# ============================================================
def detect_environment(date_str=None):
    """检测当前市场环境"""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    wd = datetime.strptime(date_str, '%Y-%m-%d').weekday()
    wd_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    
    # 从env_daily_history获取最近交易日环境
    env_history = {}
    env_path = os.path.join(DATA, "env_daily_history.json")
    if os.path.exists(env_path):
        with open(env_path) as f:
            env_history = json.load(f).get('daily', {})
    
    # 找最近交易日
    available_dates = sorted(env_history.keys(), reverse=True)
    latest_date = available_dates[0] if available_dates else None
    
    if latest_date:
        env = env_history[latest_date]
        lu = env.get('limit_up', 0) or env.get('涨停数', 0)
        if isinstance(lu, str):
            try: lu = int(lu)
            except: lu = 0
    else:
        lu = 0
    
    # 环境分层
    if lu < 17: tier = '冰点'
    elif lu < 40: tier = '震荡'
    elif lu < 70: tier = '活跃'
    else: tier = '高潮'
    
    # 涨停数分段
    if lu < 25: lu_label = '低涨停<25'
    elif lu < 50: lu_label = '中涨停25~50'
    else: lu_label = '高涨停≥50'
    
    return {
        'date': date_str,
        'weekday': wd,
        'weekday_name': wd_names[wd] if wd < 7 else '未知',
        'latest_trade_date': latest_date,
        'limit_up': lu,
        'env_tier': tier,
        'lu_label': lu_label,
    }

# ============================================================
# 策略匹配
# ============================================================
def match_strategy(env_info):
    """根据环境信息匹配最佳策略"""
    sdb = load_signal_db()
    if not sdb:
        return {'action': '信号库未加载', 'confidence': 0}
    
    wd = env_info['weekday']
    tier = env_info['env_tier']
    lu = env_info['limit_up']
    wd_name = env_info['weekday_name']
    
    results = []
    warnings = []
    
    # 1. 环境层推荐
    env_recommendations = {
        '冰点': {
            'action': '谨慎参与 — 冰点涨停不足17只',
            'tier_ret': '+0.35%', 'tier_wr': '47.9%', 'n': 317,
            'strategy': '只做沪股通买入或机构+量化双买',
        },
        '震荡': {
            'action': '精选个股 — 震荡期全量-0.28%/43.2%',
            'tier_ret': '-0.28%', 'tier_wr': '43.2%', 'n': 1032,
            'strategy': '跟炒股养家(+2.32%)或机构买入(+0.99%)',
        },
        '活跃': {
            'action': '✅ 积极打板 — 最佳窗口',
            'tier_ret': '+1.42%', 'tier_wr': '53.9%', 'n': 165,
            'strategy': '沪股通买入(+4.39%)或游资标签买入(+2.45%)',
        },
        '高潮': {
            'action': '⚠️ 谨慎追高 — 高潮期零和博弈',
            'tier_ret': '+0.02%', 'tier_wr': '48.1%', 'n': 27,
            'strategy': '仅周三+高潮(+2.41%/64.3%)时参与',
        },
    }
    
    er = env_recommendations.get(tier, {})
    results.append({
        'type': '环境层',
        'action': er.get('action', ''),
        'data': f"{tier}(涨停{lu}只): {er.get('tier_ret', '')}/{er.get('tier_wr', '')}",
        'n': er.get('n', 0),
        'strategy': er.get('strategy', ''),
    })
    
    # 2. 星期×环境黄金组合
    diamond_combos = {
        (1, '活跃'): ('🏆 周二+活跃+涨停≥50', '+5.26%', '71.4%', 14, 80),
        (1,): ('🥇 周二+活跃', '+2.59%', '66.7%', 54, 70),
        (2, '高潮'): ('🥇 周三+高潮', '+2.41%', '64.3%', 14, 60),
    }
    
    best_combo = None
    for key, (label, ret, wr, n, pos) in diamond_combos.items():
        if len(key) == 1 and key[0] == wd:
            if tier not in ['冰点', '震荡']:
                best_combo = (label, ret, wr, n, pos)
            break
        elif len(key) == 2 and key[0] == wd and key[1] == tier:
            if lu >= 50:
                best_combo = (label, ret, wr, n, pos)
            else:
                best_combo = ('🥇 周二+活跃(涨停<50)', '+2.59%', '66.7%', 54, 70)
            break
        elif len(key) == 2 and key[0] == wd and key[1] == tier:
            best_combo = (label, ret, wr, n, pos)
            break
    
    # 3. 回避检测
    avoid_conditions = [
        (2, '震荡', lu < 25, '周三震荡低涨停', '-1.00%', '37.5%', 120),
        (2, '冰点', True, '周三冰点', '-0.38%', '47.8%', 67),
        (0, '活跃', lu >= 50, '周一活跃涨停≥50', '-0.23%', '20.0%', 10),
    ]
    
    for cond_wd, cond_tier, cond_extra, label, ret, wr, n in avoid_conditions:
        if wd == cond_wd and tier == cond_tier and cond_extra:
            warnings.append({
                'label': f"🚫 {label}",
                'ret': ret,
                'wr': wr,
                'n': n,
                'advice': '强烈建议空仓或极轻仓(≤10%)',
            })
    
    # 4. 综合仓位
    base_position = 50  # 默认50%
    
    if warnings:
        base_position = 10  # 有回避信号就10%
    elif tier == '活跃':
        base_position = 70
        if best_combo:
            base_position = min(80, base_position + 10)
    elif tier == '冰点':
        base_position = 30
    elif tier == '震荡':
        base_position = 50
    elif tier == '高潮':
        base_position = 40
    
    return {
        'env': env_info,
        'recommendations': results,
        'best_combo': best_combo,
        'warnings': warnings,
        'base_position_pct': base_position,
        'signal_source': f"1,541笔严格时间线回测(type=01, T-1→T开盘买→T+1收盘卖)",
    }

# ============================================================
# 输出
# ============================================================
def print_tactical_plan(strategy):
    """打印完整的作战计划"""
    env = strategy['env']
    
    print(f"\n{'='*60}")
    print(f"🎯 穿透式实战策略 — {env['weekday_name']} {env['date']}")
    print(f"{'='*60}")
    print(f"\n📡 环境感知:")
    print(f"  最近交易日: {env['latest_trade_date']}")
    print(f"  涨停数: {env['limit_up']}只 → {env['env_tier']}")
    print(f"  涨停分段: {env['lu_label']}")
    
    print(f"\n📊 策略推荐:")
    for r in strategy['recommendations']:
        print(f"  [{r['type']}] {r['action']}")
        print(f"    回测: {r['data']} | 样本{r['n']}笔")
        print(f"    建议: {r.get('strategy', '')}")
    
    if strategy['best_combo']:
        label, ret, wr, n, pos = strategy['best_combo']
        print(f"\n🏆 最佳组合:")
        print(f"  {label}")
        print(f"  回测: {ret}/{wr} (样本{n}笔)")
    
    if strategy['warnings']:
        print(f"\n🚨 警报:")
        for w in strategy['warnings']:
            print(f"  {w['label']}: 仅{w['ret']}/胜率{w['wr']}({w['n']}笔)")
            print(f"  → {w['advice']}")
    
    print(f"\n💼 仓位建议: {strategy['base_position_pct']}%")
    print(f"\n📋 操作清单:")
    if strategy['base_position_pct'] >= 70:
        print(f"  ✅ 环境最优 — 可积极打板")
        print(f"  ✅ 首选沪股通买入标的")
        print(f"  ✅ 其次游资标签(炒股养家等)")
        print(f"  ⚠️ 单票仓位≤25%")
    elif strategy['base_position_pct'] >= 50:
        print(f"  🟡 环境中性 — 精选个股")
        print(f"  ✅ 跟机构买入(+0.99%)")
        print(f"  ✅ 跟炒股养家(+2.32%)")
        print(f"  ⚠️ 单票仓位≤20%")
    elif strategy['base_position_pct'] >= 30:
        print(f"  🟠 环境偏弱 — 只做最强信号")
        print(f"  ✅ 只选有机构参与的票")
        print(f"  ⚠️ 单票仓位≤10%")
    else:
        print(f"  🔴 环境危险 — 空仓或极轻仓")
        print(f"  ✅ 如必须参与: 冰点沪股通(+3.91%)")
        print(f"  ⚠️ 单票仓位≤5%")
    
    # 数据等级标签
    env = strategy['env']
    lu = env['limit_up']
    data_grade = 'A级' if lu >= 40 else 'B级' if lu >= 17 else 'C级'
    
    print(f"\n📚 数据源: {strategy['signal_source']}")
    print(f"📋 数据等级: {data_grade} (当前涨停{lu}只)")
    print(f"⚠️ 注意: {'活跃环境结论可信度高' if data_grade=='A级' else '环境偏弱，回测结论仅供参考'}")
    print(f"{'='*60}")
    
    return strategy

# ============================================================
# 主入口
# ============================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='穿透式实战策略')
    parser.add_argument('--live', action='store_true', help='明日执行模式')
    parser.add_argument('--date', default=None, help='指定日期')
    parser.add_argument('--json', action='store_true', help='JSON输出')
    
    args = parser.parse_args()
    
    env = detect_environment(args.date)
    strategy = match_strategy(env)
    
    if args.json:
        import json as j
        print(j.dumps(strategy, ensure_ascii=False, indent=2))
    else:
        print_tactical_plan(strategy)
