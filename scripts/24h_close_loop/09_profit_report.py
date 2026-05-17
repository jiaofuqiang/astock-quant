#!/usr/bin/env python3
"""
【15:00 获利报告】当日获利 + 隔日获利 + 多日获利统计

同时对比昨日各报告 → 经验报告
"""
import sys, os, json
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 15:00 获利报告 {TODAY}")
print("="*60)

# ============================================================
# 1. 获取当日交易数据
# ============================================================
def get_trade_data():
    """从bundle获取交易数据"""
    bundle = load_json_or_empty(BUNDLE_JSON)
    trade = bundle.get('trade', {}) if bundle else {}
    if isinstance(trade, dict):
        return trade
    return {}

trade = get_trade_data()
print(f"  交易数据: {'✅' if trade else '空'}")

# ============================================================
# 2. 当日获利
# ============================================================
today_pnl = trade.get('today_pnl', 0) if trade else 0
total_profit = trade.get('total_profit', 0) if trade else 0
total_profit_rate = trade.get('total_profit_rate', 0) if trade else 0
positions = trade.get('positions', []) if trade else []
today_buys = [p for p in positions if p.get('buy_date')==TODAY] if isinstance(positions, list) else []
today_sells = [p for p in positions if p.get('sell_date')==TODAY] if isinstance(positions, list) else []

print(f"  当日盈亏: {today_pnl:+.2f}")
print(f"  累计收益: {total_profit_rate:+.2f}%")
print(f"  今日买入: {len(today_buys)}笔")
print(f"  今日卖出: {len(today_sells)}笔")

# ============================================================
# 3. 隔日获利（昨日买入今日卖出）
# ============================================================
yesterday = (date.today() - timedelta(days=1)).isoformat()
yest_buys = [p for p in positions if p.get('buy_date')==yesterday] if isinstance(positions, list) else []
print(f"  隔日交易(昨买今卖): {len(yest_buys)}笔")

# ============================================================
# 4. 多日获利（所有持仓）
# ============================================================
hold_positions = [p for p in positions if not p.get('sell_date')] if isinstance(positions, list) else []
print(f"  持仓中: {len(hold_positions)}只")

# ============================================================
# 5. 对比昨日报告 → 经验总结
# ============================================================
def compare_reports():
    """对比昨日预测vs今日实际交易结果"""
    yest_pred = load_json_or_empty(yest_report_filename(PREDICTION_DIR, 'prediction'))
    yest_plan = load_json_or_empty(yest_report_filename(PLAN_DIR, 'plan'))
    yest_profit = load_json_or_empty(yest_report_filename(PROFIT_DIR, 'profit'))

    comparison = {}
    if yest_pred:
        pred_scenario = yest_pred.get('market_scenarios',[{}])[0].get('scenario','?')
        comparison['yest_market_prediction'] = pred_scenario

    if yest_profit:
        yest_today_pnl = yest_profit.get('today_pnl', 0)
        comparison['yest_today_pnl'] = yest_today_pnl

    comparison['today_today_pnl'] = today_pnl

    return comparison

comparison = compare_reports()
print(f"  对比分析: {json.dumps(comparison, ensure_ascii=False)[:200]}")

# ============================================================
# 6. 保存
# ============================================================
report = {
    'type': 'profit_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'today_pnl': today_pnl,
    'total_profit': total_profit,
    'total_profit_rate': total_profit_rate,
    'today_buy_count': len(today_buys),
    'today_sell_count': len(today_sells),
    'yest_buy_today_sell_count': len(yest_buys),
    'hold_count': len(hold_positions),
    'positions': positions if isinstance(positions, list) else [],
    'comparison': comparison,
}

os.makedirs(PROFIT_DIR, exist_ok=True)
with open(report_filename(PROFIT_DIR, 'profit'), 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"  获利报告已保存: {report_filename(PROFIT_DIR, 'profit')}")
print(f"\n[24h] 15:00 获利报告完成 ✅")
