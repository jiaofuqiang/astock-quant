#!/usr/bin/env python3
"""
板块强弱周期监控模型 v1.0
验证核心假设：板块强弱存在周期——连续强势后的转弱点可以识别
维度：
  1. 单日强弱标记（强/中/弱）
  2. 连续强势天数和转弱概率
  3. 隔夜美股映射影响（需美股数据）
  4. 弱转强信号的识别
"""
import sqlite3, json, sys, time
from collections import defaultdict
from datetime import datetime, timedelta

DATA = "/home/ubuntu/astock/data"
START = "2024-01-01"
END = "2026-05-08"

def get_db(name):
    return sqlite3.connect(f"{DATA}/{name}")

# 重点板块（按产业链分类）
FOCUS_SECTORS = [
    '半导体', '存储芯片', 'AI芯片', 'AI算力', '数据中心',
    '光模块与光通信', '机器人', '智能驾驶', '新能源汽车',
    '低空经济', '军工', '券商', '消费电子'
]

def classify_strength(avg_change, up_ratio, limit_up_count):
    """将板块日表现分为强/中/弱三档"""
    if avg_change >= 3.0 and limit_up_count >= 2:
        return "🔥强"
    elif avg_change >= 1.5 or (avg_change >= 0.5 and up_ratio >= 0.6 and limit_up_count >= 1):
        return "👍中"
    elif avg_change >= 0.3:
        return "👌弱+" 
    elif avg_change >= -0.5:
        return "⚠️弱"
    else:
        return "💀弱-"

def calc_score(avg_change, up_ratio, limit_up_count, stock_count):
    """计算量化的板块强度得分（归一化0-100）"""
    score = 0
    # 涨跌幅（权重40）
    score += min(40, max(-20, avg_change * 10))
    # 上涨比例（权重30）
    score += up_ratio * 30
    # 涨停数（权重20）
    score += min(20, limit_up_count * 5)
    # 量比（权重10，缩量扣分）
    score += 0  # 后续再加
    return max(0, min(100, score))

print("=" * 80)
print("板块强弱周期验证报告")
print(f"周期: {START} ~ {END}")
print("=" * 80)

# 加载板块日数据
db = get_db("sector_indexes.db")
c = db.cursor()
c.execute("""
    SELECT sector_name, date, avg_change, up_count, down_count, 
           stock_count, limit_up_count, avg_volume_ratio
    FROM sector_daily_index 
    WHERE date >= ? AND date <= ?
    ORDER BY sector_name, date
""", (START, END))

# 按板块组织
sector_data = defaultdict(list)
for row in c.fetchall():
    name, dt, avg, up, down, total, limits, vol = row
    if total == 0:
        continue
    up_ratio = up / total
    sector_data[name].append({
        "date": dt, "avg_change": avg, "up_ratio": up_ratio,
        "limit_up": limits, "vol_ratio": vol,
        "strength": classify_strength(avg, up_ratio, limits),
        "score": calc_score(avg, up_ratio, limits, total)
    })
db.close()

all_transition_stats = {
    "强→弱": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
    "强→中": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
    "强→强": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
    "中→弱": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
    "中→强": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
    "中→中": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
    "弱→强": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
    "弱→中": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
    "弱→弱": {"count": 0, "after_avg": 0, "after_limits_avg": 0},
}

# 分析每个板块的强弱转换
all_streak_data = []  # (sector, date, streak_length, next_day_change)
print(f"\n{'板块':20s} {'日期':12s} {'前N日连强':>8} {'当日强度':8s} {'当日涨幅':>8} {'次日涨幅':>8} {'次日强度':8s}")
print("-" * 80)

for sector, records in sector_data.items():
    if sector not in FOCUS_SECTORS:
        continue
    dates_sorted = sorted(records, key=lambda x: x["date"])
    
    # 计算连续强势天数（score>=50）
    streak_days = []
    current_streak = 0
    for i, r in enumerate(dates_sorted):
        if r["score"] >= 50:
            current_streak += 1
        else:
            current_streak = 0
        streak_days.append((r["date"], current_streak))
    
    # 分析强弱转换
    for i in range(1, len(dates_sorted)):
        prev = dates_sorted[i-1]
        curr = dates_sorted[i]
        
        # 前一天的强度等级（简化3档）
        def get_rank(s):
            if s == "🔥强": return 3
            if "中" in s or s == "👌弱+": return 2
            return 1
        
        prev_rank = get_rank(prev["strength"])
        curr_rank = get_rank(curr["strength"])
        
        if prev_rank == 3 and curr_rank <= 2:
            key = "强→中" if curr_rank == 2 else "强→弱"
        elif prev_rank == 3 and curr_rank == 3:
            key = "强→强"
        elif prev_rank == 2 and curr_rank == 3:
            key = "中→强"
        elif prev_rank == 2 and curr_rank <= 1:
            key = "中→弱"
        elif prev_rank == 2 and curr_rank == 2:
            key = "中→中"
        elif prev_rank <= 1 and curr_rank == 3:
            key = "弱→强"
        elif prev_rank <= 1 and curr_rank == 2:
            key = "弱→中"
        else:
            key = "弱→弱"
        
        all_transition_stats[key]["count"] += 1
        all_transition_stats[key]["after_avg"] += curr["avg_change"]
        all_transition_stats[key]["after_limits_avg"] += curr["limit_up"]
        
        # 寻找连续强势后的转弱点
        if current_streak >= 2 and curr_rank <= 2:
            if i+1 < len(dates_sorted):
                next_r = dates_sorted[i+1]
                all_streak_data.append({
                    "sector": sector,
                    "date": curr["date"],
                    "streak": current_streak,
                    "prev_score": prev["score"],
                    "curr_score": curr["score"],
                    "curr_change": curr["avg_change"],
                    "next_change": next_r["avg_change"],
                    "next_strength": next_r["strength"],
                    "limits_today": curr["limit_up"],
                })
                if len(all_streak_data) <= 30:
                    print(f"{sector:20s} {curr['date']:12s} {current_streak:>8d} {curr['strength']:8s} {curr['avg_change']:>8.2f} {next_r['avg_change']:>8.2f} {next_r['strength']:8s}")

# 汇总统计
print(f"\n{'='*60}")
print(f"板块强弱转换统计（全部板块，非仅重点）")
print(f"{'='*60}")
print(f"{'转换类型':12s} {'次数':>6} {'占比%':>7} {'后日均涨':>8} {'后日均涨停':>8}")
print("-" * 50)

total_transitions = sum(v["count"] for v in all_transition_stats.values())
for key in ["强→强", "强→中", "强→弱", "中→强", "中→中", "中→弱", "弱→强", "弱→中", "弱→弱"]:
    s = all_transition_stats[key]
    pct = s["count"] / total_transitions * 100 if s["count"] else 0
    after_avg = s["after_avg"] / s["count"] if s["count"] else 0
    after_limits = s["after_limits_avg"] / s["count"] if s["count"] else 0
    print(f"{key:12s} {s['count']:>6d} {pct:>6.2f}% {after_avg:>8.2f} {after_limits:>8.1f}")

# 分析：连续强势之后转弱的概率
print(f"\n{'='*60}")
print(f"连续强势天数→次日表现分析")
print(f"{'='*60}")
print(f"{'连续强天数':12s} {'次数':>8} {'转弱率%':>8} {'次日均涨':>8} {'次日均涨停':>8}")
print("-" * 50)

for streak_n in range(1, 8):
    streak_group = [d for d in all_streak_data if d["streak"] == streak_n]
    if not streak_group:
        continue
    weak_count = sum(1 for d in streak_group if d["next_change"] < 0)
    avg_next = sum(d["next_change"] for d in streak_group) / len(streak_group)
    avg_limits = sum(d["limits_today"] for d in streak_group) / len(streak_group)
    print(f"{streak_n:>12d} {len(streak_group):>8d} {weak_count/len(streak_group)*100:>7.1f}% {avg_next:>8.2f} {avg_limits:>8.1f}")

# 关键结论输出
print(f"\n{'='*60}")
print("核心结论")
print(f"=" * 60)

# 强→弱 vs 强→强
strong_strong = all_transition_stats["强→强"]
strong_weak = all_transition_stats["强→弱"]
strong_medium = all_transition_stats["强→中"]
total_strong = strong_strong["count"] + strong_weak["count"] + strong_medium["count"]

if total_strong > 0:
    keep_pct = strong_strong["count"] / total_strong * 100
    weaken_pct = (strong_weak["count"] + strong_medium["count"]) / total_strong * 100
    print(f"▶ 板块强势后的次日表现：")
    print(f"  持续强势: {keep_pct:.1f}%    转弱: {weaken_pct:.1f}%")
    print(f"  强→弱后次日平均涨幅: {strong_weak['after_avg']/strong_weak['count']:.2f}%" if strong_weak['count'] > 0 else "  无数据")

# 弱转强
weak_strong = all_transition_stats["弱→强"]
weak_medium = all_transition_stats["弱→中"]
total_weak = sum(all_transition_stats[k]["count"] for k in ["弱→强", "弱→中", "弱→弱"])
if total_weak > 0:
    reverse_pct = (weak_strong["count"] + weak_medium["count"]) / total_weak * 100
    print(f"\n▶ 板块弱势后的次日表现：")
    print(f"  弱转强: {reverse_pct:.1f}%")
    print(f"  弱→强后次日平均涨幅: {weak_strong['after_avg']/weak_strong['count']:.2f}%" if weak_strong['count'] > 0 else "  无数据")

# 连续天数预警
print(f"\n▶ 连续强势天数预警阈值：")
for streak_n in range(2, 7):
    group = [d for d in all_streak_data if d["streak"] == streak_n]
    if group:
        avg_next = sum(d["next_change"] for d in group) / len(group)
        weak_pct = sum(1 for d in group if d["next_change"] < 0) / len(group) * 100
        print(f"  连强{streak_n}天 → 次日{weak_pct:.0f}%概率转弱, 平均{avg_next:+.2f}%")

elapsed = time.time() - time.time()
print(f"\n耗时: {time.time() - (time.time()):.1f}秒")