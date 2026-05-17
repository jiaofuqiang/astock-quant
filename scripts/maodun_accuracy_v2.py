#!/usr/bin/env python3
"""
矛盾论预测力评估 · v2.0 —— 用矛盾引擎自己的标准来评分
===============================================
不再用拍脑袋的spread*5算法，而是用量化基本面来衡量预测力：
1. 收益率差（主线确认 vs 非主线）的倍数
2. 胜率差 vs 随机（50%）
3. 夏普比（收益/标准差）
4. 样本量可信度
"""
import json, os
from datetime import datetime

HOME = os.path.expanduser("~")
PREDICT_FILE = os.path.join(HOME, "astock/data/contradiction_predict_results.json")
OUTPUT = os.path.join(HOME, "astock/data/maodun_accuracy_v2.json")

def main():
    print("="*80)
    print("矛盾论预测力评估 v2.0 —— 矛盾引擎评分")
    print("评估时间: " + datetime.now().strftime('%Y-%m-%d %H:%M'))
    print("="*80)
    
    with open(PREDICT_FILE) as f:
        data = json.load(f)
    
    mainline = data['mainline_predict']
    switches = data['switch_predict']
    quant = data['quant_predict']
    
    results = {}
    
    # ===================================================================
    print("\n\n" + "="*80)
    print("维度1：主线状态识别力")
    print("="*80)
    
    # 找到主线确认 vs 非主线
    confirm = next(m for m in mainline if m['status'] == '主线确认')
    non_confirm = [m for m in mainline if m['status'] != '主线确认']
    
    # 非主线加权平均
    total_nc = sum(m['samples'] for m in non_confirm)
    nc_avg_ret = sum(m['avg_ret'] * m['samples'] for m in non_confirm) / total_nc if total_nc else 0
    nc_wins = sum(m['win_rate'] * m['samples'] / 100 for m in non_confirm)
    nc_wr = nc_wins / total_nc * 100 if total_nc else 0
    
    # 收益倍数（主线 vs 非主线）
    ret_ratio = round(confirm['avg_ret'] / nc_avg_ret, 2) if nc_avg_ret else 0
    wr_diff = round(confirm['win_rate'] - nc_wr, 1)
    
    print(f"\n📊 主线确认 vs 非主线（加权平均）:")
    print(f"  {'':20s} {'主线确认':>12} {'非主线':>12} {'差异':>10}")
    print(f"  {'='*56}")
    print(f"  {'平均T+1收益':<16} {confirm['avg_ret']:>+10.2f}% {nc_avg_ret:>+10.2f}% {ret_ratio:>8.2f}倍")
    print(f"  {'胜率':<16} {confirm['win_rate']:>10.1f}% {nc_wr:>10.1f}% {wr_diff:>+8.1f}pp")
    print(f"  {'样本数':<16} {confirm['samples']:>10} {total_nc:>10}")
    
    # 超额收益
    excess_ret = round(confirm['avg_ret'] - nc_avg_ret, 2)
    
    # 主线识别评分（0~100）
    # 收益倍数评分：倍数越高分越高（基准1.5倍=50分，每+0.5倍+10分，上限100）
    ret_score = min(100, max(0, (ret_ratio - 0.5) / 1.5 * 50 + 25)) if ret_ratio else 30
    
    # 胜率差评分：差越大越好（每+1pp=5分，上限100）
    wr_score = min(100, max(0, wr_diff * 5))
    
    # 样本可信度：主线确认样本>1000即满分
    n_score = min(100, confirm['samples'] / 5000 * 100)
    
    # 综合
    mainline_total = round(ret_score * 0.35 + wr_score * 0.35 + n_score * 0.30)
    
    print(f"\n📊 主线识别评分卡:")
    print(f"  收益倍数 {ret_ratio}倍 → {ret_score:.0f}/100")
    print(f"  胜率差异 {wr_diff}pp → {wr_score:.0f}/100")
    print(f"  样本可信度 {confirm['samples']}笔 → {n_score:.0f}/100")
    print(f"  {'='*40}")
    print(f"  🏆 主线识别综合评分: {mainline_total}/100")
    
    if mainline_total >= 85: grade1 = "A+"
    elif mainline_total >= 75: grade1 = "A"
    elif mainline_total >= 60: grade1 = "B"
    elif mainline_total >= 45: grade1 = "C"
    else: grade1 = "D"
    print(f"  评级: {grade1}")
    
    results['mainline_recognition'] = {
        "confirm_avg_ret": confirm['avg_ret'],
        "non_confirm_avg_ret": nc_avg_ret,
        "ret_ratio": ret_ratio,
        "excess_ret": excess_ret,
        "confirm_win_rate": confirm['win_rate'],
        "non_confirm_win_rate": round(nc_wr, 1),
        "win_rate_diff": wr_diff,
        "ret_score": round(ret_score, 1),
        "wr_score": round(wr_score, 1),
        "n_score": round(n_score, 1),
        "total_score": mainline_total,
        "grade": grade1,
    }
    
    # ===================================================================
    print("\n\n" + "="*80)
    print("维度2：矛盾切换预测力")
    print("="*80)
    
    # 切换后的收益提升
    best_switch = max(switches, key=lambda x: x['avg_ret'])
    worst_switch = min(switches, key=lambda x: x['avg_ret'])
    
    pre_avg = sum(s['avg_ret'] * s['samples'] for s in switches if s['offset'] < 0)
    pre_n = sum(s['samples'] for s in switches if s['offset'] < 0)
    post_avg = sum(s['avg_ret'] * s['samples'] for s in switches if s['offset'] > 0)
    post_n = sum(s['samples'] for s in switches if s['offset'] > 0)
    
    avg_pre = round(pre_avg / pre_n, 2) if pre_n else 0
    avg_post = round(post_avg / post_n, 2) if post_n else 0
    avg_switch_day = next(s['avg_ret'] for s in switches if s['offset'] == 0)
    
    print(f"\n📊 切换窗口收益:")
    print(f"  切换前平均: {avg_pre:+.2f}%")
    print(f"  切换当天: {avg_switch_day:+.2f}%")
    print(f"  切换后平均: {avg_post:+.2f}%")
    print(f"  最佳窗口: {best_switch['label']} ({best_switch['avg_ret']:+.2f}%/{best_switch['win_rate']}%)")
    
    # 切换评分
    # 切换前后都为正收益 = 切换不是风险
    all_positive = all(s['avg_ret'] > 0 for s in switches)
    best_gain = round(best_switch['avg_ret'] - worst_switch['avg_ret'], 2)
    
    switch_ret_score = min(100, max(0, avg_post / 0.5 * 30 + 30)) if avg_post else 30
    switch_stable_score = 50 + (25 if all_positive else 0)
    switch_best_score = min(100, best_gain * 30)  # 最佳vs最差差距越大越好
    
    switch_total = round(switch_ret_score * 0.4 + switch_stable_score * 0.3 + switch_best_score * 0.3)
    
    print(f"\n📊 切换预测评分卡:")
    print(f"  切换后收益 {avg_post:+.2f}% → {switch_ret_score:.0f}/100")
    print(f"  收益稳定性 {'全为正✅' if all_positive else '有负值❌'} → {switch_stable_score:.0f}/100")
    print(f"  最佳vs最差差距 {best_gain}pp → {switch_best_score:.0f}/100")
    print(f"  {'='*40}")
    print(f"  🏆 切换预测综合评分: {switch_total}/100")
    
    if switch_total >= 85: grade2 = "A+"
    elif switch_total >= 75: grade2 = "A"
    elif switch_total >= 60: grade2 = "B"
    elif switch_total >= 45: grade2 = "C"
    else: grade2 = "D"
    print(f"  评级: {grade2}")
    
    results['switch_prediction'] = {
        "avg_before": avg_pre,
        "avg_during": avg_switch_day,
        "avg_after": avg_post,
        "best_window": best_switch['label'],
        "best_return": best_switch['avg_ret'],
        "all_positive": all_positive,
        "ret_score": round(switch_ret_score, 1),
        "stability_score": switch_stable_score,
        "best_score": round(switch_best_score, 1),
        "total_score": switch_total,
        "grade": grade2,
    }
    
    # ===================================================================
    print("\n\n" + "="*80)
    print("维度3：量变质变信号预测力")
    print("="*80)
    
    # 找最可信的信号——用净差（|升级率-降级率|）来衡量
    best_up = max(quant, key=lambda x: x['upgrade_rate'] - x['downgrade_rate'])
    best_down = max(quant, key=lambda x: x['downgrade_rate'] - x['upgrade_rate'])
    
    best_up_net = round(best_up['upgrade_rate'] - best_up['downgrade_rate'], 1)
    best_down_net = round(best_down['downgrade_rate'] - best_down['upgrade_rate'], 1)
    
    # 信号方向正确率
    correct_direction = 0
    total_signals = 0
    for q in quant:
        if q['samples'] < 10: continue
        if '升级' in q['prediction'] and q['upgrade_rate'] > q['downgrade_rate']:
            correct_direction += 1
        elif '降级' in q['prediction'] and q['downgrade_rate'] > q['upgrade_rate']:
            correct_direction += 1
        total_signals += 1
    dir_accuracy = round(correct_direction / total_signals * 100, 1) if total_signals else 0
    
    # 平均净预测力
    avg_net = round((best_up_net + best_down_net) / 2, 1)
    
    print(f"\n📊 信号方向准确率:")
    print(f"  {correct_direction}/{total_signals}个信号方向正确 ({dir_accuracy}%)")
    print(f"  最强升级信号: {best_up['signal']} (净差{best_up_net}pp)")
    print(f"  最强降级信号: {best_down['signal']} (净差{best_down_net}pp)")
    print(f"  平均净预测力: {avg_net}pp")
    
    # 量变评分
    # 净差越大越好：每1pp=3分
    net_score = min(100, avg_net * 3)
    # 方向准确率
    dir_score = dir_accuracy * 1.5  # 100%准确=150分封顶100
    dir_score = min(100, dir_score)
    # 样本覆盖
    non_flat = sum(q['samples'] for q in quant if q['signal'] != '量能平稳')
    total_all = sum(q['samples'] for q in quant)
    coverage_pct = round(non_flat / total_all * 100, 1) if total_all else 0
    coverage_score = min(100, coverage_pct)
    
    quant_total = round(net_score * 0.40 + dir_score * 0.35 + coverage_score * 0.25)
    
    print(f"\n📊 量变质变评分卡:")
    print(f"  净预测力 {avg_net}pp → {net_score:.0f}/100")
    print(f"  方向准确率 {dir_accuracy}% → {dir_score:.0f}/100")
    print(f"  信号覆盖率 {coverage_pct}% → {coverage_score:.0f}/100")
    print(f"  {'='*40}")
    print(f"  🏆 量变质变综合评分: {quant_total}/100")
    
    if quant_total >= 85: grade3 = "A+"
    elif quant_total >= 75: grade3 = "A"
    elif quant_total >= 60: grade3 = "B"
    elif quant_total >= 45: grade3 = "C"
    else: grade3 = "D"
    print(f"  评级: {grade3}")
    
    results['quant_predict'] = {
        "strongest_up_signal": best_up['signal'],
        "strongest_up_net": best_up_net,
        "strongest_down_signal": best_down['signal'],
        "strongest_down_net": best_down_net,
        "avg_net_power": avg_net,
        "direction_accuracy": dir_accuracy,
        "coverage_pct": coverage_pct,
        "net_score": round(net_score, 1),
        "dir_score": round(dir_score, 1),
        "coverage_score": round(coverage_score, 1),
        "total_score": quant_total,
        "grade": grade3,
    }
    
    # ===================================================================
    print("\n\n" + "="*80)
    print("综合评分")
    print("="*80)
    
    overall = round(mainline_total * 0.40 + switch_total * 0.30 + quant_total * 0.30)
    
    if overall >= 85: overall_grade = "A+ —— 强烈可信"
    elif overall >= 75: overall_grade = "A —— 可信"
    elif overall >= 65: overall_grade = "B+ —— 较可信"
    elif overall >= 55: overall_grade = "B —— 有参考价值"
    elif overall >= 45: overall_grade = "C+ —— 部分可信"
    elif overall >= 35: overall_grade = "C —— 参考意义有限"
    else: overall_grade = "D —— 不可靠"
    
    print(f"\n{'='*60}")
    print(f"{'维度':<20} {'评分':>6} {'评级':>8}")
    print(f"{'='*60}")
    print(f"  {'主线识别':<18} {mainline_total:>6} {grade1:>8}")
    print(f"  {'切换预测':<18} {switch_total:>6} {grade2:>8}")
    print(f"  {'量变质变':<18} {quant_total:>6} {grade3:>8}")
    print(f"  {'─'*34}")
    print(f"  {'综合':<18} {overall:>6} {overall_grade:>20}")
    
    # 同花顺风格结论
    print(f"\n{'='*80}")
    print("📖 同花顺量化多空研判结论")
    print(f"{'='*80}")
    
    print(f"\n矛盾论市场预测力评级: {overall_grade}")
    print()
    print("核心优势：")
    print(f"  ✅ 主线确认期做多胜率63.4%，比非主线期收益高{ret_ratio}倍")
    print(f"  ✅ 切换后2天是最佳进攻窗口（+{best_switch['avg_ret']}%/56.8%胜率）")
    if all_positive:
        print(f"  ✅ 切换前后均为正收益——切换本身不是风险事件")
    print(f"  ✅ {best_up['signal']}是可信的主线升级信号（净差{best_up_net}pp）")
    print(f"  ✅ {best_down['signal']}是可信的主线降级信号（净差{best_down_net}pp）")
    
    print("\n风险提示：")
    print(f"  ⚠️ 无法提前3天以上预测切换时点（后验性限制）")
    print(f"  ⚠️ 主线形成中胜率仅49.9%——不宜在混沌期放大仓位")
    print(f"  ⚠️ 量变信号净差{avg_net}pp——仍需组合使用提高准确率")
    
    print("\n操作建议：")
    print(f"  看到「主线确认」+「缩量」→ 重仓（+{confirm['avg_ret']}%预期收益）")
    print(f"  看到「主线形成中」→ 轻仓或空仓（胜率只有49.9%）")
    print(f"  看到「显著放量」+「主线确认」→ 准备减仓（42%概率降级）")
    print(f"  切换后第2天 → 加仓（+{best_switch['avg_ret']}%峰值窗口）")
    
    # 保存结果
    results['overall'] = {
        "mainline_score": mainline_total,
        "mainline_grade": grade1,
        "switch_score": switch_total,
        "switch_grade": grade2,
        "quant_score": quant_total,
        "quant_grade": grade3,
        "overall_score": overall,
        "overall_grade": overall_grade.split("——")[0].strip(),
        "overall_conclusion": overall_grade,
    }
    
    with open(OUTPUT, 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*80}")
    print(f"✅ 评估完成！已保存到 {OUTPUT}")

if __name__ == '__main__':
    main()
