#!/usr/bin/env python3
# 【15:10 升级报告】基于经验总结，生成系统升级建议
# 由用户阅读确认后执行参数调整
import json, os, sys
from datetime import date, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (EXPERIENCE_DIR, UPGRADE_DIR, AFTERNOON_DIR, PREDICTION_DIR,
                     report_filename, yest_report_filename)

TODAY = date.today().isoformat()

# 1. 加载今日经验报告
exp_file = report_filename(EXPERIENCE_DIR, 'experience')
experience = {}
if os.path.exists(exp_file):
    with open(exp_file) as f:
        experience = json.load(f)

# 2. 加载历史经验，计算趋势
last7_exp = []
for i in range(7):
    from datetime import timedelta
    d = (date.today() - timedelta(days=i)).isoformat()
    f = os.path.join(EXPERIENCE_DIR, f"experience_{d}.json")
    if os.path.exists(f):
        with open(f) as jf:
            last7_exp.append(json.load(jf))

# 3. 统计连续失败次数
consecutive_fails = experience.get('consecutive_fails', 0)
total_fails = sum(e.get('consecutive_fails', 0) for e in last7_exp)
total_items = len(last7_exp)
fail_rate = total_fails / total_items if total_items > 0 else 0

# 4. 生成升级建议
upgrades = []

# 参数级调整
if consecutive_fails >= 3:
    upgrades.append({
        'level': '参数调整',
        'reason': f'连续{consecutive_fails}次预测失败',
        'action': '降低仓位预期（从3只→2只）',
        'auto': False,  # 需要用户确认
    })

if fail_rate > 0.5:
    upgrades.append({
        'level': '权重调整',
        'reason': f'近期预测失败率{fail_rate*100:.0f}%',
        'action': '建议回测调整预测权重参数',
        'auto': False,
    })

# 回测结论调整
upgrades.append({
    'level': '买入阈值优化',
    'reason': '回测结论确认：龙≥5+开≥3%胜率最高（+7.6%/70%暴利）',
    'action': '将"龙≥5+开≥3%"作为最高优先级买入信号',
    'auto': True,
})

upgrades.append({
    'level': '卖出策略优化',
    'reason': '回测确认：T+1开≥7%竞价卖（冲高空间仅0.4%）',
    'action': '确认T+1高开7%竞价卖规则',
    'auto': True,
})

report = {
    'type': 'upgrade_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'stats': {
        'consecutive_fails': consecutive_fails,
        'total_history_days': total_items,
        'fail_rate_7d': round(fail_rate, 2),
    },
    'upgrades': upgrades,
}

upgrade_file = report_filename(UPGRADE_DIR, 'upgrade')
with open(upgrade_file, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"  升级报告已保存: {upgrade_file}")

# 输出升级摘要
print(f"\n=== 《升级报告》{TODAY} ===")
print(f"统计: 最近{total_items}天, 连续失败{consecutive_fails}次")
for u in upgrades:
    tag = "⚡需确认" if not u['auto'] else "✅自动"
    print(f"  {tag} {u['level']}: {u['action']}")

print(f"\n⚠️ {sum(1 for u in upgrades if not u['auto'])}项需要您确认后才能执行")
print(f"\n[15:10 完成]")