#!/usr/bin/env python3
"""
注入策略进化数据到dashboard_bundle.json
"""
import json
import os

HOME = os.path.expanduser("~")
BUNDLE = os.path.join(HOME, "V2board/dashboard_bundle.json")
EVO_FILE = os.path.join(HOME, "V2board/data/strategy_evolution.json")
MINER_FILE = os.path.join(HOME, "astock/data/strategies_mined_v2_DEPRECATED.json")

# 读bundle
if not os.path.exists(BUNDLE):
    print("❌ bundle不存在")
    exit(1)

with open(BUNDLE) as f:
    bundle = json.load(f)

# 读策略进化数据
evo_data = None
if os.path.exists(EVO_FILE):
    with open(EVO_FILE) as f:
        evo_data = json.load(f)
elif os.path.exists(MINER_FILE):
    with open(MINER_FILE) as f:
        raw = json.load(f)
    evo_data = {
        'generated_at': raw.get('generated_at', ''),
        'total_strategies': raw.get('total_strategies', 0),
        'total_signals': raw.get('total_signals', 0),
        'cost_method': raw.get('cost_method', '涨停价'),
        'top10': raw.get('strategies', [])[:10],
        'evolution': raw.get('evolution', {}),
    }

if evo_data:
    bundle['strategy_evolution'] = evo_data
    # 写入
    with open(BUNDLE, 'w') as f:
        json.dump(bundle, f, ensure_ascii=False)
    print(f"✅ 已注入策略进化数据到bundle")
    print(f"   策略数: {evo_data.get('total_strategies', 0)}")
    print(f"   TOP1: {evo_data.get('top10', [{}])[0].get('name','')}")
else:
    print("❌ 无策略进化数据")
