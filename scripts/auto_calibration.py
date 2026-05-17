#!/usr/bin/env python3
"""
实践论·自动校准闭环 v1.0
=======================
《实践论》核心：实践→认识→再实践→再认识，循环往复，螺旋上升。

功能：
1. 读取maodun_redblack.db的评级红黑榜累积数据
2. 计算每个评级的实际命中率 vs 回测预期
3. 如果偏差超过阈值，自动调整矛盾评级阈值
4. 输出调整建议到bundle，供次日三刀流引擎使用
"""
import sqlite3, os, json, sys
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
REDBLACK_DB = os.path.join(HOME, "astock/data/maodun_redblack.db")
CONTRADICTION_FILE = os.path.join(HOME, "astock/data/contradiction_engine_result.json")
CACHE_FILE = os.path.join(HOME, "astock/data/auto_calibration_cache.json")
BUNDLE_PATH = os.path.join(HOME, "V2board/dashboard_bundle.json")
OUTPUT = os.path.join(HOME, "astock/data/calibration_result.json")

def main():
    print("=" * 72)
    print("实践论·自动校准闭环 v1.0")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)

    if not os.path.exists(REDBLACK_DB):
        print("  📭 红黑榜数据库不存在（首次运行需要先积累数据）")
        # 创建数据库表结构（提前准备）
        conn = sqlite3.connect(REDBLACK_DB)
        conn.execute("""CREATE TABLE IF NOT EXISTS grade_hitrate (
            grade TEXT PRIMARY KEY, total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0, total_return REAL DEFAULT 0.0, last_updated TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS trade_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, code TEXT, name TEXT,
            grade TEXT, strategy TEXT, buy_score INTEGER,
            expected REAL, actual REAL, actual_high REAL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS system_calibration (
            key TEXT PRIMARY KEY, value REAL, note TEXT, updated_at TEXT
        )""")
        conn.commit()
        conn.close()
        print("  ✅ 红黑榜数据库已初始化（等待明日首次运行积累数据）")
        return

    conn = sqlite3.connect(REDBLACK_DB)
    
    # 检查表并初始化
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if 'trade_records' not in tables:
        conn.execute("""CREATE TABLE IF NOT EXISTS trade_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, code TEXT, name TEXT,
            grade TEXT, strategy TEXT, buy_score INTEGER,
            expected REAL, actual REAL, actual_high REAL
        )""")
    if 'grade_hitrate' not in tables:
        conn.execute("""CREATE TABLE IF NOT EXISTS grade_hitrate (
            grade TEXT PRIMARY KEY, total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0, total_return REAL DEFAULT 0.0, last_updated TEXT
        )""")
    if 'system_calibration' not in tables:
        conn.execute("""CREATE TABLE IF NOT EXISTS system_calibration (
            key TEXT PRIMARY KEY, value REAL, note TEXT, updated_at TEXT
        )""")
    conn.commit()
    
    # 如果有老的grade_backtest表，迁移数据
    if 'grade_backtest' in tables:
        old_data = conn.execute("SELECT grade, n, avg_close, win_rate FROM grade_backtest").fetchall()
        if old_data:
            for grade, n, avg_close, win_rate in old_data:
                wins = round(n * win_rate / 100) if win_rate and n else 0
                conn.execute("""INSERT OR REPLACE INTO grade_hitrate 
                    (grade, total_trades, wins, total_return, last_updated)
                    VALUES (?,?,?,?,?)""", 
                    (grade, n, wins, avg_close * n, datetime.now().isoformat()))
            conn.commit()
            print(f"  ✅ 从grade_backtest迁入{len(old_data)}条数据")
    
    # ===== 读取评级红黑榜 =====
    grades = conn.execute("""
        SELECT grade, total_trades, wins, total_return, last_updated
        FROM grade_hitrate ORDER BY total_trades DESC
    """).fetchall()

    if not grades:
        print("  📭 无评级数据")
        conn.close()
        return

    print(f"\n📊 当前红黑榜 ({len(grades)}个评级有数据):")
    grade_data = {}
    for grade, total, wins, total_ret, updated in grades:
        avg_ret = round(total_ret / total, 2) if total > 0 else 0
        wr = round(wins / total * 100, 1) if total > 0 else 0
        grade_data[grade] = {'total': total, 'wins': wins, 'avg_ret': avg_ret, 'win_rate': wr, 'total_ret': total_ret}
        print(f"  {grade}: {total}次 | {wr}%胜率 | 均收{avg_ret:+.2f}% | 最后{updated[:10] if updated else '?'}")

    # ===== 回测预期（从8维回测数据） =====
    # 这些是v2.0回测中15988个涨停样本的基线
    baseline = {
        '甲等': {'expected_ret': 1.30, 'expected_wr': 56.3, 'min_samples': 30},
        '乙等': {'expected_ret': 0.51, 'expected_wr': 52.1, 'min_samples': 50},
        '丙等': {'expected_ret': -0.70, 'expected_wr': 43.3, 'min_samples': 50},
        '丁等': {'expected_ret': -1.50, 'expected_wr': 40.0, 'min_samples': 10},
    }

    print(f"\n📐 回测基线（基于15988个涨停×570天回测）:")
    for grade, bl in baseline.items():
        print(f"  {grade}: 预期均收{bl['expected_ret']:+.2f}%, 预期胜率{bl['expected_wr']}%")

    # ===== 偏差计算 =====
    print(f"\n⚙️ 实际vs预期偏差:")
    deviations = []
    adjustments = {}

    for grade, bl in baseline.items():
        if grade not in grade_data:
            print(f"  {grade}: 暂无数据")
            continue

        gd = grade_data[grade]
        if gd['total'] < bl['min_samples']:
            print(f"  {grade}: 样本不足({gd['total']}<{bl['min_samples']})，暂不校准")
            continue

        ret_diff = round(gd['avg_ret'] - bl['expected_ret'], 2)
        wr_diff = round(gd['win_rate'] - bl['expected_wr'], 1)

        status = ''
        action = ''
        adjust = 0

        if ret_diff > 0.5 and wr_diff > 5:
            status = '🔥远超预期'
            action = '提升评级权重+5%'
            adjust = 5
        elif ret_diff > 0.2 and wr_diff > 2:
            status = '✅略超预期'
            action = '维持'
            adjust = 0
        elif ret_diff < -0.5 and wr_diff < -5:
            status = '🔴远低于预期'
            action = '降低评级权重-5%'
            adjust = -5
        elif ret_diff < -0.2 and wr_diff < -2:
            status = '⚠️略低于预期'
            action = '降低评级权重-2%'
            adjust = -2
        else:
            status = '✅符合预期'
            action = '维持'
            adjust = 0

        deviations.append({
            'grade': grade,
            'actual_ret': gd['avg_ret'],
            'expected_ret': bl['expected_ret'],
            'ret_diff': ret_diff,
            'actual_wr': gd['win_rate'],
            'expected_wr': bl['expected_wr'],
            'wr_diff': wr_diff,
            'status': status,
            'action': action,
            'adjust_score': adjust,
        })

        adjustments[grade] = adjust
        print(f"  {grade}: 实际{gd['avg_ret']:+.2f}%/{gd['win_rate']}% vs 预期{bl['expected_ret']:+.2f}%/{bl['expected_wr']}% → {status} → {action}")

    # ===== 阈值校准计算 =====
    print(f"\n📐 评级阈值自动校准:")
    # 当前阈值
    current_thresholds = {
        '甲等': {'score': 55, 'require_inst': True},
        '乙等': {'score': 50, 'require_inst': False},
        '丙等': {'score': 30, 'require_inst': False},
        '丁等': {'score': 0, 'require_inst': False},
    }

    new_thresholds = {}
    for grade, ct in current_thresholds.items():
        adj = adjustments.get(grade, 0)
        if adj != 0:
            new_score = max(0, ct['score'] + adj)
            new_thresholds[grade] = {
                'old': ct['score'],
                'new': new_score,
                'adjust': adj,
                'require_inst': ct['require_inst'],
            }
            print(f"  {grade}: 阈值{ct['score']}→{new_score} ({adj:+d})")
        else:
            new_thresholds[grade] = {
                'old': ct['score'],
                'new': ct['score'],
                'adjust': 0,
                'require_inst': ct['require_inst'],
            }
            print(f"  {grade}: 维持{ct['score']}")

    # ===== 写入校准缓存（供次日morning_pipeline读取） =====
    calibration = {
        'generated_at': datetime.now().isoformat(),
        'total_trades_analyzed': sum(gd['total'] for gd in grade_data.values()),
        'threshold_adjustments': new_thresholds,
        'grade_deviations': deviations,
        'current_grade_stats': {g: d for g, d in grade_data.items() if d['total'] >= 10},
        'recommendations': [],
    }

    # 生成建议
    if deviations:
        worst = min(deviations, key=lambda x: x['ret_diff'])
        best = max(deviations, key=lambda x: x['ret_diff'])
        calibration['recommendations'].append({
            'priority': 'P0' if worst['ret_diff'] < -0.5 else 'P1',
            'grade': worst['grade'],
            'issue': f"{worst['grade']}实际均收{worst['actual_ret']:+.2f}%低于预期{worst['expected_ret']:+.2f}%",
            'suggest': f"阈值从{current_thresholds.get(worst['grade'],{}).get('score','?')}→{new_thresholds.get(worst['grade'],{}).get('new','?')}",
        })
        if best['ret_diff'] > 0.5:
            calibration['recommendations'].append({
                'priority': 'P0',
                'grade': best['grade'],
                'issue': f"{best['grade']}实际均收{best['actual_ret']:+.2f}%远超预期{best['expected_ret']:+.2f}%",
                'suggest': f"可适当提高{best['grade']}的资金合力权重",
            })

    # 保存校准缓存
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(calibration, f, ensure_ascii=False, indent=2)

    # 写入bundle
    try:
        if os.path.exists(BUNDLE_PATH):
            with open(BUNDLE_PATH) as f:
                bundle = json.load(f)
            bundle['auto_calibration'] = calibration
            bundle['_calibration_at'] = datetime.now().isoformat()
            with open(BUNDLE_PATH, 'w') as f:
                json.dump(bundle, f, ensure_ascii=False, default=str)
            print(f"\n✅ 已注入bundle: auto_calibration")
    except Exception as e:
        print(f"  ⚠️ bundle写入失败: {e}")

    # 输出报告
    with open(OUTPUT, 'w') as f:
        json.dump(calibration, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 72}")
    print("汇总:")
    print(f"  红黑榜累积交易: {sum(gd['total'] for gd in grade_data.values())}笔")
    print(f"  校准阈值调整: {sum(1 for _, nt in new_thresholds.items() if nt['adjust'] != 0)}项")
    print(f"  输出文件: {CACHE_FILE}")
    print(f"           {OUTPUT}")

    conn.close()
    print("\n✅ 实践论闭环：实践→认识→校准完成")


if __name__ == '__main__':
    main()
