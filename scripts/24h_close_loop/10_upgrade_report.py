#!/usr/bin/env python3
"""
【15:10 升级报告】基于今日经验总结 → 系统升级建议

auto=False 的需要用户阅读确认后执行
"""
import sys, os, json
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 15:10 升级报告 {TODAY}")
print("="*60)

# ============================================================
# 1. 加载今日经验报告 + 获利报告
# ============================================================
exp = load_json_or_empty(report_filename(EXPERIENCE_DIR, 'experience'))
profit = load_json_or_empty(report_filename(PROFIT_DIR, 'profit'))

if not exp:
    exp = {'success_items':[],'fail_items':[],'improvements':[],'consecutive_fails':0}
if not profit:
    profit = {'today_pnl':0,'total_profit_rate':0}

print(f"  经验报告: {'✅' if exp else '空'}")
print(f"  获利报告: {'✅' if profit else '空'}")

# ============================================================
# 2. 生成升级建议
# ============================================================
upgrade_items = []

# 连续失败
consecutive_fails = exp.get('consecutive_fails', 0)
if consecutive_fails >= 3:
    upgrade_items.append({
        'type': '连续预测失败',
        'auto': False,
        'severity': 'high',
        'desc': f'连续{consecutive_fails}次预测失败',
        'suggestion': '建议：①降低仓位至50% ②减少买入条件门槛 ③暂停打板操作',
    })

# 当日亏损
today_pnl = profit.get('today_pnl', 0)
if today_pnl < 0:
    upgrade_items.append({
        'type': '当日亏损',
        'auto': False,
        'severity': 'medium',
        'desc': f'今日亏损{today_pnl:.2f}',
        'suggestion': '检查买入信号是否有效，止损规则是否执行',
    })

# 总收益回撤
total_rate = profit.get('total_profit_rate', 0)
if total_rate < -10:
    upgrade_items.append({
        'type': '总收益回撤',
        'auto': False,
        'severity': 'high',
        'desc': f'累计收益回撤至{total_rate:.2f}%',
        'suggestion': '触发熔断机制，暂停交易，等待系统重新校准',
    })

# 板块预测偏差
sector_hit = exp.get('sectors', {}).get('hit_count', 0)
sector_total = exp.get('sectors', {}).get('predicted', [])
if sector_total and sector_hit / max(len(sector_total),1) < 0.3:
    upgrade_items.append({
        'type': '板块预测偏差',
        'auto': True,
        'severity': 'medium',
        'desc': f'板块预测命中率{sector_hit}/{len(sector_total)}',
        'suggestion': '自动增加消息面板块权重，降低右侧板块权重',
    })

# 最赚钱模式提醒
upgrade_items.append({
    'type': 'most_profitable',
    'auto': False,
    'severity': 'info',
    'desc': '最赚钱交易模式提醒',
    'suggestion': '龙≥5板+竞价开≥3%跟风(+7.6%/暴利70%) 是所有策略中最优的，明天优先执行此模式',
})

# ============================================================
# 3. 保存
# ============================================================
upgrade = {
    'type': 'upgrade_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'consecutive_fails': consecutive_fails,
    'today_pnl': today_pnl,
    'total_profit_rate': total_rate,
    'items': upgrade_items,
}

os.makedirs(UPGRADE_DIR, exist_ok=True)
with open(report_filename(UPGRADE_DIR, 'upgrade'), 'w', encoding='utf-8') as f:
    json.dump(upgrade, f, ensure_ascii=False, indent=2)

print(f"\n  升级建议:")
for item in upgrade_items:
    tag = '✅自动' if item.get('auto') else '⚠️需确认'
    print(f"    [{tag}] {item.get('desc','')}")
    print(f"           {item.get('suggestion','')}")

print(f"\n  升级报告已保存: {report_filename(UPGRADE_DIR, 'upgrade')}")
print(f"\n  ⚠️ 请阅读升级报告后确认 '确认升级' 才会生效")
print(f"\n[24h] 15:10 升级报告完成 ✅")
