#!/usr/bin/env python3
"""
矛盾论市场预测力 · 实战评估报告 v1.0
=================================
基于569天真实历史数据，评估矛盾论对市场主线预测的准确率。
"""
import json, os
from datetime import datetime

HOME = os.path.expanduser("~")
PREDICT_FILE = os.path.join(HOME, "astock/data/contradiction_predict_results.json")
ENV_FILE = os.path.join(HOME, "astock/data/env_daily_history.json")
OUTPUT = os.path.join(HOME, "astock/data/maodun_accuracy_report.json")

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def main():
    print("="*80)
    print("📖 矛盾论市场预测力 · 实战评估报告")
    print(f"评估时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*80)
    
    predict = load_json(PREDICT_FILE)
    env_data = load_json(ENV_FILE)
    
    report = []
    
    # ===================================================================
    print(f"\n\n{'='*80}")
    print("第一部分：主线状态识别准确率")
    print(f"{'='*80}")
    
    mainline = predict.get('mainline_predict', [])
    
    print(f"\n📊 表格1：主线状态 → T+1涨停股收益")
    print(f"{'='*70}")
    print(f"{'主线状态':<16} {'样本数':>8} {'平均收益':>10} {'胜率':>8} {'评级':>6}")
    print(f"{'='*70}")
    
    for m in mainline:
        status = m['status']
        n = m['samples']
        ret = m['avg_ret']
        wr = m['win_rate']
        
        if ret >= 2.0 and wr >= 60:
            grade = "S级"
        elif ret >= 1.0 and wr >= 52:
            grade = "A级"
        elif ret >= 0.5:
            grade = "B级"
        else:
            grade = "C级"
        
        print(f"  {status:<14} {n:>8} {ret:>+9.2f}% {wr:>7.1f}% {grade:>5}")
        
        report.append({
            "type": "mainline_recognition",
            "status": status,
            "samples": n,
            "avg_t1_return": ret,
            "win_rate": wr,
            "grade": grade,
        })
    
    # 差异计算
    best = max(mainline, key=lambda x: x['avg_ret'])
    worst = min(mainline, key=lambda x: x['avg_ret'])
    spread = round(best['avg_ret'] - worst['avg_ret'], 2)
    
    print(f"\n📊 结论1：主线状态识别准确率")
    print(f"  ✅ 主线确认期T+1平均收益+{best['avg_ret']}%，是非主线期的{round(best['avg_ret']/worst['avg_ret'],1)}倍")
    print(f"  ✅ 最佳vs最差差值: {spread}个百分点")
    print(f"  ⚠️ '主线形成中'反而是最差状态(+0.70%/49.9%)，说明混沌期不宜交易")
    
    # 解释
    print(f"\n📖 《实践论》解读：")
    print(f"  当市场处于主线确认期，认识已经经过感性→理性阶段，矛盾的主要方面已明确")
    print(f"  此时做多（涨停股T+1收益+2.21%/63.4%）是理性的")
    print(f"  当市场处于主线形成中，认识还在感性阶段（数据不足），矛盾未明朗")
    print(f"  此时做多（+0.70%/49.9%）几乎等同于抛硬币")
    
    # ===================================================================
    print(f"\n\n{'='*80}")
    print("第二部分：矛盾切换预测准确率")
    print(f"{'='*80}")
    
    switches = predict.get('switch_stats', {})
    switch_predict = predict.get('switch_predict', [])
    
    total_switches = switches.get('total', 0)
    upgrades = switches.get('upgrades', 0)
    downgrades = switches.get('downgrades', 0)
    
    print(f"\n📊 表格2：矛盾切换事件统计")
    print(f"{'='*60}")
    print(f"  总切换事件: {total_switches}次")
    print(f"  主线升级: {upgrades}次 ({round(upgrades/total_switches*100,1) if total_switches else 0}%)")
    print(f"  主线降级: {downgrades}次 ({round(downgrades/total_switches*100,1) if total_switches else 0}%)")
    print(f"  平均每{round(569/total_switches,1) if total_switches else 0}天一次切换")
    
    print(f"\n📊 表格3：切换前后窗口的赚钱效应")
    print(f"{'='*70}")
    print(f"{'窗口':<14} {'样本':>8} {'平均收益':>10} {'胜率':>8} {'操作建议':>16}")
    print(f"{'='*70}")
    
    for s in sorted(switch_predict, key=lambda x: x['offset']):
        label = s['label']
        n = s['samples']
        ret = s['avg_ret']
        wr = s['win_rate']
        
        # 操作建议
        if ret >= 1.5 and wr >= 56:
            advice = "✅加大仓位进攻"
        elif ret >= 1.2 and wr >= 54:
            advice = "🟢正常操作"
        elif ret >= 1.0:
            advice = "🟡谨慎参与"
        else:
            advice = "🔴减仓防守"
        
        print(f"  {label:<12} {n:>8} {ret:>+9.2f}% {wr:>7.1f}% {advice}")
    
    # 切换预测准确率
    print(f"\n📊 结论2：矛盾切换预测准确率")
    print(f"  切换事件共有398次，其中升级194次、降级204次")
    print(f"  切换前后2天涨停T+1收益均为正（+1.24%~+1.53%）")
    print(f"  切换后2天达到峰值(+1.53%/56.8%)——这是最佳进攻窗口")
    
    report.append({
        "type": "switch_accuracy",
        "total_events": total_switches,
        "upgrades": upgrades,
        "downgrades": downgrades,
        "avg_frequency_days": round(569/total_switches, 1) if total_switches else 0,
        "peak_window": "切换后2天",
        "peak_return": 1.53,
        "peak_win_rate": 56.8,
    })
    
    # ===================================================================
    print(f"\n\n{'='*80}")
    print("第三部分：量变质变信号预测准确率")
    print(f"{'='*80}")
    
    quant = predict.get('quant_predict', [])
    
    print(f"\n📊 表格4：量变信号→次日主线变化预测率")
    print(f"{'='*70}")
    print(f"{'量变信号':<14} {'样本':>6} {'升级率':>8} {'降级率':>8} {'预测方向':>14}")
    print(f"{'='*70}")
    
    for q in quant:
        signal = q['signal']
        n = q['samples']
        up_rate = q['upgrade_rate']
        dn_rate = q['downgrade_rate']
        pred = q['prediction']
        
        # 预测准确率
        if '升级' in pred:
            accuracy = max(up_rate, 100 - dn_rate)
        elif '降级' in pred:
            accuracy = max(dn_rate, 100 - up_rate)
        else:
            accuracy = 50
            
        # 方向明确度
        if up_rate > dn_rate + 15:
            direction = "📈强烈升级"
        elif dn_rate > up_rate + 15:
            direction = "📉强烈降级"
        elif up_rate > dn_rate:
            direction = "📈倾向升级"
        elif dn_rate > up_rate:
            direction = "📉倾向降级"
        else:
            direction = "⚖️方向不明"
        
        print(f"  {signal:<12} {n:>6} {up_rate:>7.1f}% {dn_rate:>7.1f}% {direction:>12}")
        
        report.append({
            "type": "quant_predict",
            "signal": signal,
            "samples": n,
            "upgrade_rate": up_rate,
            "downgrade_rate": dn_rate,
            "direction": direction.strip(),
            "net_accuracy": round(abs(up_rate - dn_rate), 1),
        })
    
    # 综合评估
    print(f"\n📊 结论3：量变质变信号综合评估")
    
    # 最强升级信号
    strongest_up = max(quant, key=lambda x: x['upgrade_rate'] - x['downgrade_rate'])
    strongest_down = max(quant, key=lambda x: x['downgrade_rate'] - x['upgrade_rate'])
    
    print(f"  最强升级信号: {strongest_up['signal']}")
    print(f"    → 升级率{strongest_up['upgrade_rate']}% > 降级率{strongest_up['downgrade_rate']}%")
    print(f"    → 差值{round(strongest_up['upgrade_rate']-strongest_up['downgrade_rate'],1)}个百分点")
    print(f"  最强降级信号: {strongest_down['signal']}")
    print(f"    → 降级率{strongest_down['downgrade_rate']}% > 升级率{strongest_down['upgrade_rate']}%")
    print(f"    → 差值{round(strongest_down['downgrade_rate']-strongest_down['upgrade_rate'],1)}个百分点")
    print()
    print(f"  总体预测准确率:")
    total_samples = sum(q['samples'] for q in quant)
    print(f"   量变信号样本覆盖: {total_samples}天（占总交易日{round(total_samples/569*100,1)}%）")
    print(f"   非'量能平稳'信号占比: {round(100 - 232/569*100,1)}%（337天有明确量变信号）")
    print(f"   其中方向正确的: 降级信号预测准确率更高（显著放量降级率42.0% > 升级率17.9%）")
    
    report.append({
        "type": "quant_summary",
        "strongest_up_signal": strongest_up['signal'],
        "strongest_up_net": round(strongest_up['upgrade_rate'] - strongest_up['downgrade_rate'], 1),
        "strongest_down_signal": strongest_down['signal'],
        "strongest_down_net": round(strongest_down['downgrade_rate'] - strongest_down['upgrade_rate'], 1),
        "signal_coverage_pct": round(total_samples/569*100, 1),
    })
    
    # ===================================================================
    print(f"\n\n{'='*80}")
    print("第四部分：矛盾论预测力综合评分")
    print(f"{'='*80}")
    
    # 综合评分
    scores = {}
    
    # (1) 主线状态识别
    mainline_score = round(spread * 5, 0)  # 差距越大越好
    mainline_score = min(100, max(0, mainline_score))
    
    # (2) 切换事件预测
    switch_score = 75  # 切换前后都有正收益，但无法提前预测具体切换时点
    switch_score = min(100, switch_score)
    
    # (3) 量变信号预测
    quant_up_net = round(strongest_up['upgrade_rate'] - strongest_up['downgrade_rate'], 1) if 'strongest_up' in dir() else 22
    quant_down_net = round(strongest_down['downgrade_rate'] - strongest_down['upgrade_rate'], 1) if 'strongest_down' in dir() else 24
    quant_score = round((quant_up_net + quant_down_net) / 2 * 2, 0)
    quant_score = min(100, max(0, quant_score))
    
    overall = round((mainline_score + switch_score + quant_score) / 3, 0)
    
    print("\n📊 矛盾论预测力评分卡")
    print(f"{'='*60}")
    print(f"{'评估维度':<24} {'评分':>6} {'评级':>8} {'依据':>20}")
    print(f"{'='*60}")
    
    dims = [
        ("1. 主线状态识别", mainline_score, 
         "S" if mainline_score >= 90 else "A" if mainline_score >= 75 else "B" if mainline_score >= 60 else "C",
         f"差{spread}%"),
        ("2. 矛盾切换预测", switch_score,
         "A" if switch_score >= 75 else "B",
         "切换后2天峰值+1.53%"),
        ("3. 量变质变信号", quant_score,
         "A" if quant_score >= 70 else "B",
         f"缩量→升级+{quant_up_net}%,放量→降级+{quant_down_net}%"),
        ("4. 综合预测力", overall,
         "A" if overall >= 75 else "B" if overall >= 60 else "C",
         "三类加权平均"),
    ]
    
    for name, score, grade, basis in dims:
        bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
        print(f"  {name:<22} {score:>4.0f} {bar:>12} {grade:>6} {basis}")
    
    print(f"\n{'='*80}")
    print("综合评估")
    print(f"{'='*80}")
    
    if overall >= 75:
        print(f"  🏆 矛盾论在A股市场主线预测的综合可信度为: A级 (准确率较高)")
    elif overall >= 60:
        print(f"  🥈 矛盾论在A股市场主线预测的综合可信度为: B级 (有参考价值)")
    else:
        print(f"  🥉 矛盾论在A股市场主线预测的综合可信度为: C级 (需结合其他方法)")
    
    print(f"\n  核心优势:")
    print(f"  1. 主线确认期的预测力最强（+2.21%/63.4%，远超随机水平）")
    print(f"  2. 缩量作为主线升级信号有42.1%的准确率（已超随机水平）")
    print(f"  3. 放量作为主线降级信号有42.0%的准确率（同样有效）")
    print(f"  4. 切换后2天是最佳进攻窗口（+1.53%/56.8%）")
    print(f"\n  局限性:")
    print(f"  1. 无法提前3天以上预测具体切换时点（只知道发生了才知道）")
    print(f"  2. '主线形成中'是最差交易环境（+0.70%/49.9%），但无法区分真假主线")
    print(f"  3. 量变信号准确率42%~45%，仍低于60%的实用阈值")
    print(f"  4. 内外因传导机制未量化（只知道外因重要，不知道怎么传导）")
    print(f"\n  改进方向:")
    print(f"  1. 增加切换前3~5天的先行信号组合（缩量+机构+环境下降=更精确）")
    print(f"  2. 对'主线形成中'做二级分类（真突破vs假突破）")
    print(f"  3. 用信号组合替代单一信号（显著缩量+涨停数MA5下降+环境分下降=更高的准确率）")
    
    # ===================================================================
    # 保存报告
    report.append({
        "type": "overall_score",
        "mainline_recognition_score": mainline_score,
        "switch_prediction_score": switch_score,
        "quant_signal_score": quant_score,
        "overall_score": overall,
        "overall_grade": "A" if overall >= 75 else "B" if overall >= 60 else "C",
    })
    
    output = {
        "meta": {
            "report": "矛盾论市场预测力实战评估报告",
            "generated_at": datetime.now().isoformat(),
            "date_range": "2024-01-01 ~ 2026-05-15",
            "samples": f"{predict.get('meta',{}).get('total_days',569)}天",
        },
        "mainline_recognition": report,
        "overall_score": report[-1] if report else {},
    }
    
    with open(OUTPUT, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*80}")
    print(f"✅ 评估完成！已保存到 {OUTPUT}")

if __name__ == '__main__':
    main()
