#!/usr/bin/env python3
"""
板块强弱周期监控模型 v2.0
聚焦验证：板块连续走强→转弱的信号点
核心改进：用更宽松的"走强"定义（板块涨+涨停>0），分析连续走强后的转弱概率
同时验证：弱转强的信号可识别性
"""
import sqlite3, json, sys, time
from collections import defaultdict
from datetime import datetime, timedelta

DATA = "/home/ubuntu/astock/data"
START = "2024-01-01"
END = "2026-05-08"

FOCUS_SECTORS = [
    '半导体', '存储芯片', 'AI芯片', 'AI算力', '数据中心',
    '光模块与光通信', '机器人', '智能驾驶', '新能源汽车',
    '低空经济', '军工', '券商', '消费电子'
]

def load_sector_data():
    db = sqlite3.connect(f"{DATA}/sector_indexes.db")
    c = db.cursor()
    c.execute("""
        SELECT sector_name, date, avg_change, up_count, down_count, 
               stock_count, limit_up_count, avg_volume_ratio
        FROM sector_daily_index 
        WHERE date >= ? AND date <= ?
        ORDER BY sector_name, date
    """, (START, END))
    sector_data = defaultdict(list)
    for row in c.fetchall():
        name, dt, avg, up, down, total, limits, vol = row
        if total == 0:
            continue
        up_ratio = up / total
        sector_data[name].append({
            "date": dt, "avg_change": avg, "up_ratio": up_ratio,
            "limit_up": limits, "vol_ratio": vol, "stock_count": total
        })
    db.close()
    return sector_data

def is_strong(rec):
    """板块今天算不算'强'——涨+有涨停"""
    return rec["avg_change"] > 0.5 and rec["limit_up"] > 0

def is_weak(rec):
    """板块今天算不算'弱'——跌或者无涨停"""
    return rec["avg_change"] < -0.5 or (rec["avg_change"] < 0.3 and rec["limit_up"] == 0)

def is_very_weak(rec):
    """板块今天'很弱'——大跌"""
    return rec["avg_change"] < -1.5

def is_very_strong(rec):
    """板块今天'很强'——大涨+多涨停"""
    return rec["avg_change"] > 2.0 and rec["limit_up"] >= 2

t0 = time.time()
sector_data = load_sector_data()

print("=" * 80)
print("【板块强弱周期监控模型 v2.0】")
print(f"周期: {START} ~ {END} | 重点板块: {len(FOCUS_SECTORS)}个")
print("=" * 80)

# ===== 1. 强转弱统计 =====
print("\n\n📊 一、强转弱分析（昨天强→今天弱）")
print("定义：板块涨>0.5%+有涨停 → 次日跌或")
print("=" * 60)

all_results = {
    "强→弱": [],  # (sector, date, prev_change, prev_limits, curr_change, curr_limits, next_change)
    "强→强": [],
    "连强转弱": [],
    "弱→强": [],
}

for sector in FOCUS_SECTORS:
    if sector not in sector_data:
        continue
    records = sorted(sector_data[sector], key=lambda x: x["date"])
    
    streak = 0  # 连续强势天数
    dates = [r["date"] for r in records]
    
    for i, rec in enumerate(records):
        if is_strong(rec):
            streak += 1
        else:
            streak = 0
        
        if i < 1:
            continue
        
        prev = records[i-1]
        curr = rec
        
        # 昨天强→今天弱
        if is_strong(prev) and not is_strong(curr):
            # 看后天（T+2）表现，如果后天能修复说明是短暂调整
            next_rec = records[i+1] if i+1 < len(records) else None
            all_results["强→弱"].append({
                "sector": sector, "date": curr["date"],
                "prev_change": prev["avg_change"], "prev_limits": prev["limit_up"],
                "curr_change": curr["avg_change"], "curr_limits": curr["limit_up"],
                "streak_before": streak,
                "next_change": next_rec["avg_change"] if next_rec else None,
                "next_strong": is_strong(next_rec) if next_rec else None,
                "is_collapse": is_very_weak(curr),
            })
        
        # 昨天强→今天也强
        elif is_strong(prev) and is_strong(curr):
            all_results["强→强"].append({
                "sector": sector, "date": curr["date"],
                "prev_change": prev["avg_change"], "prev_limits": prev["limit_up"],
                "curr_change": curr["avg_change"], "curr_limits": curr["limit_up"],
                "streak_before": streak,
            })
        
        # 连续强势x天后转弱
        if streak >= 2 and not is_strong(curr):
            next_rec = records[i+1] if i+1 < len(records) else None
            all_results["连强转弱"].append({
                "sector": sector, "date": curr["date"],
                "streak": streak,
                "prev_change": prev["avg_change"],
                "curr_change": curr["avg_change"],
                "next_change": next_rec["avg_change"] if next_rec else None,
                "next_strong": is_strong(next_rec) if next_rec else None,
                "is_collapse": is_very_weak(curr),
            })
        
        # 昨天弱→今天强（弱转强信号）
        if not is_strong(prev) and is_strong(curr):
            # 之前连续弱势天数
            prev_streak = 0
            for j in range(i-1, max(i-10, -1), -1):
                if not is_strong(records[j]):
                    prev_streak += 1
                else:
                    break
            all_results["弱→强"].append({
                "sector": sector, "date": curr["date"],
                "prev_change": prev["avg_change"],
                "prev_weak_days": prev_streak,
                "curr_change": curr["avg_change"],
                "curr_limits": curr["limit_up"],
            })

# ===== 2. 强转弱核心统计 =====
sw = all_results["强→弱"]
ss = all_results["强→强"]
print(f"\n总样本：昨天强→今天弱 {len(sw)}次  → 昨天强→今天强 {len(ss)}次")
total = len(sw) + len(ss)
if total > 0:
    print(f"\n📌 板块强势后次日转弱概率: {len(sw)/total*100:.1f}%")
    print(f"   板块强势后次日持续强势概率: {len(ss)/total*100:.1f}%")

# 强转弱后，有多少是"崩盘式下跌"（<-1.5%）？
collapsed = [s for s in sw if s["is_collapse"]]
print(f"\n强转弱中大跌(<-1.5%)的比例: {len(collapsed)}/{len(sw)} = {len(collapsed)/len(sw)*100:.1f}%" if sw else "无数据")

# 强转弱后，次日修复概率
fixed = [s for s in sw if s.get("next_strong") == True]
print(f"强转弱后次日修复(再次走强)概率: {len(fixed)}/{len(sw)} = {len(fixed)/len(sw)*100:.1f}%" if sw else "无数据")

# 强转弱后次日的平均表现
next_changes = [s["next_change"] for s in sw if s["next_change"] is not None]
if next_changes:
    avg_next = sum(next_changes) / len(next_changes)
    print(f"强转弱后次日的平均涨幅: {avg_next:+.2f}%")

# ===== 3. 连续强势预警 =====
print(f"\n\n📊 二、连续强势预警分析")
print(f"{'='*60}")

streak_groups = defaultdict(list)
for s in all_results["连强转弱"]:
    streak_groups[s["streak"]].append(s)

for streak_n in sorted(streak_groups.keys()):
    group = streak_groups[streak_n]
    avg_curr = sum(s["curr_change"] for s in group) / len(group)
    next_changes = [s["next_change"] for s in group if s["next_change"] is not None]
    avg_next = sum(next_changes) / len(next_changes) if next_changes else 0
    recover = sum(1 for s in group if s["next_strong"] == True)
    collapse = sum(1 for s in group if s["is_collapse"])
    
    print(f"  连续强势{streak_n}天后转弱: {len(group)}次")
    print(f"    转弱当日平均涨跌: {avg_curr:+.2f}%")
    print(f"    崩盘率(<-1.5%): {collapse/len(group)*100:.1f}%")
    print(f"    转弱后次日平均涨跌: {avg_next:+.2f}%")
    print(f"    次日修复概率: {recover/len(group)*100:.1f}%")
    print()

# ===== 4. 弱转强信号 =====
print(f"\n📊 三、弱转强信号分析")
print(f"{'='*60}")

ws = all_results["弱→强"]
print(f"弱→强总次数: {len(ws)}次")
if ws:
    changes = [s["curr_change"] for s in ws]
    avg_rev = sum(changes) / len(changes)
    avg_limits = sum(s["curr_limits"] for s in ws) / len(ws)
    print(f"弱转强当日平均涨幅: {avg_rev:+.2f}%")
    print(f"弱转强当日平均涨停数: {avg_limits:.1f}")

    # 弱势天数与转强涨幅的关系
    for days in [1, 2, 3, 5]:
        group = [s for s in ws if s["prev_weak_days"] == days]
        if group:
            avg = sum(s["curr_change"] for s in group) / len(group)
            print(f"  弱势{days}天后转强: {len(group)}次, 平均+{avg:.2f}%")

# ===== 5. 逐板块分析 =====
print(f"\n\n📊 四、重点板块强弱转换排行榜")
print(f"{'='*60}")
print(f"{'板块':15s} {'强转弱次数':>8} {'强转弱率%':>9} {'崩盘率%':>8} {'修复率%':>8} {'弱转强次':>8} {'平均跌幅':>8}")
print("-" * 65)

for sector in FOCUS_SECTORS:
    if sector not in sector_data:
        continue
    sw_sec = [s for s in sw if s["sector"] == sector]
    ss_sec = [s for s in ss if s["sector"] == sector]
    ws_sec = [s for s in ws if s["sector"] == sector]
    
    total_sec = len(sw_sec) + len(ss_sec)
    if total_sec == 0:
        continue
    
    sw_rate = len(sw_sec) / total_sec * 100 if total_sec > 0 else 0
    collapse_rate = len([s for s in sw_sec if s["is_collapse"]]) / len(sw_sec) * 100 if sw_sec else 0
    fix_rate = len([s for s in sw_sec if s.get("next_strong") == True]) / len(sw_sec) * 100 if sw_sec else 0
    avg_curr = sum(s["curr_change"] for s in sw_sec) / len(sw_sec) if sw_sec else 0
    
    print(f"{sector:15s} {len(sw_sec):>8d} {sw_rate:>8.1f}% {collapse_rate:>7.1f}% {fix_rate:>7.1f}% {len(ws_sec):>8d} {avg_curr:>7.2f}%")

# ===== 6. 具体案例分析 =====
print(f"\n\n📊 五、典型案例——连续强势后的崩盘")
print(f"{'='*60}")
# 找崩盘最严重的
worst_cases = sorted(sw, key=lambda x: x["curr_change"])[:20]
for case in worst_cases:
    print(f"{case['date']} {case['sector']:15s} 昨+{case['prev_change']:.1f}%→今{case['curr_change']:+.2f}%→次{case['next_change']:+.2f}%" if case['next_change'] else f"{case['date']} {case['sector']:15s} 昨+{case['prev_change']:.1f}%→今{case['curr_change']:+.2f}%")

# ===== 7. 总结 =====
print(f"\n\n{'='*60}")
print("🔑 核心结论与实战建议")
print(f"{'='*60}")

# 强转弱率
if total > 0:
    weaken_rate = len(sw) / total * 100
    print(f"\n① 板块连续强势后的次日:")
    if weaken_rate > 60:
        print(f"   ⚠️ {weaken_rate:.0f}%概率转弱——强板块次日开盘警惕竞价走弱")
    else:
        print(f"   {weaken_rate:.0f}%概率转弱——持续强势概率较高")

# 崩盘率
if sw:
    c_rate = len([s for s in sw if s["is_collapse"]]) / len(sw) * 100
    print(f"\n② 强转弱中有 {c_rate:.0f}% 是'崩盘式下跌'(板块<-1.5%)")

# 修复率
if sw:
    f_rate = len([s for s in sw if s.get("next_strong") == True]) / len([s for s in sw if s.get("next_strong") is not None]) * 100
    print(f"\n③ 强转弱后次日修复概率: {f_rate:.0f}%")
    if f_rate > 40:
        print(f"   说明强转弱后很多是'假摔'，第二天又能回来")
    else:
        print(f"   说明强转弱后多数是真跌，不要轻易抄底")

# 连续强势天数建议
print(f"\n④ 连续强势天数预警:")
for streak_n in sorted(streak_groups.keys())[:4]:
    group = streak_groups[streak_n]
    next_changes = [s["next_change"] for s in group if s["next_change"] is not None]
    avg_next = sum(next_changes) / len(next_changes) if next_changes else 0
    print(f"   连强{streak_n}天后转弱 → 后日均值{avg_next:+.1f}%")

# 弱转强建议
if ws:
    print(f"\n⑤ 弱转强信号频率: 共{len(ws)}次")
    print(f"   弱转强当日平均涨幅: {sum(s['curr_change'] for s in ws)/len(ws):+.2f}%")

# 最终信号输出
print(f"\n{'-'*60}")
print("【盘中实战信号规则建议】")
print(f"{'-'*60}")
print("""
🔴 卖出/减仓信号（强转弱）:
  - 板块昨涨>0.5%+有涨停 → 今日竞价涨幅<0.3%且无涨停 → 强转弱概率xx%
  - 连续3天强势 → 第4天竞价走弱 → 大概率进入回调期

🟢 买入/加仓信号（弱转强）:
  - 板块昨跌→今日竞价涨幅>0.5%且有股票高开涨停 → 弱转强信号
  - 弱势盘整后突然放量拉升 → 关注板块龙头
""")

print(f"\n耗时: {time.time()-t0:.1f}秒")