#!/usr/bin/env python3
"""换手板竞价选股引擎 v2 — 基于v6研究成果，加入龙头维度"""
import json
import os
import subprocess
from datetime import datetime, date

V2BOARD = os.path.expanduser('~/V2board')
DB = os.path.expanduser('~/astock/data/kline_cache.db')
OUT = os.path.join(V2BOARD, 'huanshouban_signal.json')

# 从v6回测结果提取的高胜率策略
TIER_STRATEGIES = [
    {
        'tier': 'S级🔥🔥🔥',
        'name': '总龙头+涨停40+竞价≥8%',
        'desc': 'T-1总龙头(全市场最高板≥2板)+T-1涨停≥40家+T日竞价≥8%',
        'win_rate': '72.6%',
        'sample': 62,
        't1_max': '+8.48%',
        'condition_type': 'leader_limit_gap',
        'params': {'board_min': 2, 'limit_min': 40, 'gap_min': 8},
    },
    {
        'tier': 'S级🔥🔥🔥',
        'name': '总龙头+涨停30+竞价≥8%',
        'desc': 'T-1总龙头+涨停≥30家+竞价≥8%',
        'win_rate': '72.0%',
        'sample': 75,
        't1_max': '+7.88%',
        'condition_type': 'leader_limit_gap',
        'params': {'board_min': 2, 'limit_min': 30, 'gap_min': 8},
    },
    {
        'tier': 'A级🔥🔥',
        'name': '总龙头+涨停50+竞价≥7%',
        'desc': 'T-1总龙头+涨停≥50家+竞价≥7%',
        'win_rate': '71.7%',
        'sample': 53,
        't1_max': '+7.95%',
        'condition_type': 'leader_limit_gap',
        'params': {'board_min': 2, 'limit_min': 50, 'gap_min': 7},
    },
    {
        'tier': 'A级🔥🔥',
        'name': '总龙头+涨停40+竞价≥7%',
        'desc': 'T-1总龙头+涨停≥40家+竞价≥7%',
        'win_rate': '70.0%',
        'sample': 70,
        't1_max': '+8.02%',
        'condition_type': 'leader_limit_gap',
        'params': {'board_min': 2, 'limit_min': 40, 'gap_min': 7},
    },
    {
        'tier': 'B级🔥',
        'name': '大盘强+连板2+竞价≥7%',
        'desc': 'T-1大盘涨≥0.5%+2+板+竞价≥7%',
        'win_rate': '66.4%',
        'sample': 387,
        't1_max': '+7.79%',
        'condition_type': 'market_board_gap',
        'params': {'market_min': 0.5, 'board_min': 2, 'gap_min': 7},
    },
    {
        'tier': 'B级🔥',
        'name': '涨停30+三板+竞价≥7%',
        'desc': 'T-1涨停≥30家+3+板+竞价≥7%',
        'win_rate': '65.2%',
        'sample': 397,
        't1_max': '+8.18%',
        'condition_type': 'limit_board_gap',
        'params': {'limit_min': 30, 'board_min': 3, 'gap_min': 7},
    },
    {
        'tier': 'C级⭐',
        'name': '涨停30+连板2+竞价≥7%',
        'desc': 'T-1涨停≥30家+2+板+竞价≥7%',
        'win_rate': '64.7%',
        'sample': 1041,
        't1_max': '+8.19%',
        'condition_type': 'limit_board_gap',
        'params': {'limit_min': 30, 'board_min': 2, 'gap_min': 7},
    },
    {
        'tier': 'C级⭐',
        'name': '大盘好+涨停30+连板2+竞价≥3%',
        'desc': 'T-1大盘涨≥0.5%+涨停≥30家+连板≥2+竞价≥3%',
        'win_rate': '60.6%',
        'sample': 480,
        't1_max': '+7.77%',
        'condition_type': 'market_limit_board_gap',
        'params': {'market_min': 0.5, 'limit_min': 30, 'board_min': 2, 'gap_min': 3},
    },
]

def get_history_stats():
    path = os.path.expanduser('~/astock/research/huanshouban_v6_deep.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def export():
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    is_trade_time = now.weekday() < 5 and 9 <= now.hour <= 15
    
    history = get_history_stats()
    
    data = {
        'timestamp': now.isoformat(),
        'today': today,
        'is_trade_time': is_trade_time,
        'history': {
            'total_samples': history['total_samples'] if history else 0,
            'base_limit_rate': history['base_limit_rate'] if history else 0,
            'leader_limit_rate': (history.get('leader_vs_follower') or {}).get('leader_limit_rate', 0),
            'follower_limit_rate': (history.get('leader_vs_follower') or {}).get('follower_limit_rate', 0),
        },
        'strategies': [],
        'today_candidates': [],
        'core_rules': [],
    }
    
    # 填充策略
    for s in TIER_STRATEGIES:
        data['strategies'].append({
            'tier': s['tier'],
            'name': s['name'],
            'desc': s['desc'],
            'win_rate': s['win_rate'],
            'sample': s['sample'],
            't1_max': s['t1_max'],
        })
    
    # 核心规律
    data['core_rules'] = [
        '🐲 总龙头(全市场最高板)的T日连板概率 = 39.6%，是首板(17.8%)的2.2倍',
        '🔥 总龙头+竞价≥8% → 70.5%连板率(88笔)',
        '🔥🔥 总龙头+涨停≥40家+竞价≥8% → 72.6%胜率(62笔)',
        '🔥 大盘好+连板2+竞价≥7% → 66.4%(387笔，大样本日常可用)',
        '💡 熊市慎用：上述策略依赖大盘好+涨停多的环境',
        '📊 v6回测基础量：30,000笔换手板，20.1%基础连板率',
    ]
    
    # 研究摘要
    data['research_summary'] = (
        f"回测{data['history']['total_samples']:,}笔换手板样本，基础连板率{data['history']['base_limit_rate']}%。"
        f"核心发现：总龙头地位的T日连板率(39.6%)是首板(17.8%)的2.2倍。"
        f"最高确定策略：总龙头+涨停≥40+竞价≥8%(72.6%)，大数据策略：涨停≥30+连板2+竞价≥7%(64.7%,1041笔)。"
    )
    
    os.makedirs(V2BOARD, exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"换手板信号v2已导出: {OUT}")
    print(f"  S级策略: {sum(1 for s in TIER_STRATEGIES if 'S' in s['tier'])}")
    print(f"  A级策略: {sum(1 for s in TIER_STRATEGIES if 'A' in s['tier'])}")
    print(f"  B级策略: {sum(1 for s in TIER_STRATEGIES if 'B' in s['tier'])}")
    print(f"  C级策略: {sum(1 for s in TIER_STRATEGIES if 'C' in s['tier'])}")

if __name__ == '__main__':
    export()
