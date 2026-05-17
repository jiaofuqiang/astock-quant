#!/usr/bin/env python3
"""
【11:30 早盘报告】上午盘面总结 + 下午预测修正 + 交易调整
"""
import sys, os, json, urllib.request
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 11:30 早盘报告 {TODAY}")
print("="*60)

# ============================================================
# 1. 获取上午实时数据
# ============================================================
def get_morning_market():
    try:
        url = 'http://qt.gtimg.cn/q=sh000001,sz399001,sz399006'
        req = urllib.request.urlopen(url, timeout=10)
        raw = req.read().decode('gbk')
        mkt = {}
        for line in raw.strip().split(';'):
            if not line or '=' not in line: continue
            parts = line.split('~')
            if len(parts) < 40: continue
            mkt[parts[1]] = {
                'change_pct': float(parts[32]) if parts[32] else 0,
                'volume_ratio': float(parts[39]) if len(parts)>39 and parts[39] else 0,
            }
        return mkt
    except:
        return {'上证':{'change_pct':0,'volume_ratio':0}}

market = get_morning_market()
sh_pct = market.get('上证',{}).get('change_pct',0)
sh_vr = market.get('上证',{}).get('volume_ratio',0)
print(f"  上午大盘: {sh_pct:+.2f}% | 量比{sh_vr:.2f}")

# ============================================================
# 2. 对比早盘预测 → 修正下午预测
# ============================================================
prediction = load_json_or_empty(yest_report_filename(PREDICTION_DIR, 'prediction'))
plan_report = load_json_or_empty(report_filename(PLAN_DIR, 'plan'))

original_scenario = plan_report.get('market_prediction',{}).get('scenario','震荡')

# 实际 vs 预测
if (sh_pct > 0.5 and original_scenario == '强更强') or \
   (sh_pct < -0.5 and original_scenario == '强转弱') or \
   (-0.5 <= sh_pct <= 0.5 and original_scenario == '震荡'):
    pred_correct = True
else:
    pred_correct = False

# 下午修正
if sh_pct > 0.5:
    afternoon_adjust = '维持强更强，下午看能否突破上午高点'
elif sh_pct < -0.5:
    afternoon_adjust = '转弱确认，下午看支撑位是否有效'
else:
    afternoon_adjust = '震荡延续，等待方向选择'

print(f"  早盘预测: {'✅正确' if pred_correct else '❌偏差'}")
print(f"  下午预测: {afternoon_adjust}")

# ============================================================
# 3. 交易调整建议
# ============================================================
execute = load_json_or_empty(os.path.join(REPORTS_DIR, 'execution', f'execute_{TODAY}.json'))
bought_count = len(execute.get('buy_signals',[])) if execute else 0
sell_count = len(execute.get('sell_signals',[])) if execute else 0

trade_adjust = {}
if pred_correct:
    trade_adjust = {'action': '维持原计划', 'risk': '正常'}
else:
    if sh_pct > 0.5 and original_scenario == '强转弱':
        trade_adjust = {'action': '修正为积极', 'risk': '注意冲高回落'}
    elif sh_pct < -0.5 and original_scenario == '强更强':
        trade_adjust = {'action': '修正为防御', 'risk': '降低仓位至50%'}
    else:
        trade_adjust = {'action': '小幅调整', 'risk': '保持仓位适中'}

print(f"  交易调整: {trade_adjust.get('action','维持原计划')}")

# ============================================================
# 4. 保存
# ============================================================
report = {
    'type': 'early_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'morning_market': {k:{'change_pct':v.get('change_pct',0)} for k,v in market.items()},
    'original_scenario': original_scenario,
    'prediction_correct': pred_correct,
    'afternoon_adjustment': afternoon_adjust,
    'trade_adjustment': trade_adjust,
    'morning_buy_count': bought_count,
    'morning_sell_count': sell_count,
}

os.makedirs(os.path.join(REPORTS_DIR, 'early_report'), exist_ok=True)
with open(os.path.join(REPORTS_DIR, 'early_report', f'early_{TODAY}.json'), 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"\n[24h] 11:30 早盘报告完成 ✅")
