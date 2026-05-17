#!/usr/bin/env python3
"""
竞价开盘数据 vs T+1收益研究 v2.0
=================================
从kline_cache.db全量K线数据中计算：
1. 开盘涨跌幅（open_chg%）对T+1收益的预测力
2. 竞价换手率对T+1收益的影响（用volume近似）
3. 竞价成交额对T+1收益的影响（用open*volume近似）

输出：~/astock/data/auction_research_v2.json
"""

import sqlite3, os, json, sys
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.path.expanduser("~/astock")
KLINE_DB = os.path.join(BASE, "data", "kline_cache.db")
OUTPUT = os.path.join(BASE, "data", "auction_research_v2.json")

print("=" * 60)
print("竞价开盘数据 vs T+1收益研究 v2.0")
print("=" * 60)

# ===== 1. 从K线数据提取 =====
# 对每个股票每天，计算开盘涨跌幅，并映射到下一日收益
# open_chg% = (open_t - close_{t-1}) / close_{t-1} * 100
# T+1收益 = (close_{t+1} - close_t) / close_t * 100

conn = sqlite3.connect(KLINE_DB)
c = conn.cursor()

# Get all dates sorted
c.execute("SELECT DISTINCT date FROM kline ORDER BY date")
all_dates = [r[0] for r in c.fetchall()]
print(f"📅 总交易日: {len(all_dates)} 天")
print(f"   日期范围: {all_dates[0]} ~ {all_dates[-1]}")

date_set = set(all_dates)
date_to_next = {}
for i, d in enumerate(all_dates):
    if i + 1 < len(all_dates):
        date_to_next[d] = all_dates[i + 1]
    else:
        date_to_next[d] = None

date_to_prev = {}
for i, d in enumerate(all_dates):
    if i > 0:
        date_to_prev[d] = all_dates[i - 1]
    else:
        date_to_prev[d] = None

# ===== 2. 用SQL一次性计算 =====
# We need: for each stock on each date:
#   - close price (today)
#   - close of prev day → open_chg%
#   - open of today
#   - close of next day → T+1 return

# Strategy: Use SQL window functions or batch processing
# Let's use a self-join approach

print("\n📊 计算开盘涨跌幅和T+1收益...")

# Create a temp table approach: for each code, get consecutive days
# Actually let me do it in batches per stock

# First, get all stock codes
c.execute("SELECT DISTINCT code FROM kline ORDER BY code")
all_codes = [r[0] for r in c.fetchall()]
print(f"📈 股票总数: {len(all_codes)}")

# For efficiency, let's use a single SQL query with window functions
# SQLite 3.25+ supports LAG/LEAD
c.execute("SELECT sqlite_version()")
ver = c.fetchone()[0]
print(f"🗄️ SQLite版本: {ver}")

# Use LEAD/LAG
try:
    query = """
    WITH ordered AS (
        SELECT 
            code, date, open, close, volume,
            LAG(close) OVER (PARTITION BY code ORDER BY date) as prev_close,
            LEAD(close) OVER (PARTITION BY code ORDER BY date) as next_close
        FROM kline
    )
    SELECT 
        code, date, open, close, volume,
        prev_close, next_close
    FROM ordered
    WHERE prev_close IS NOT NULL AND next_close IS NOT NULL
    ORDER BY date, code
    """
    c.execute(query)
    rows = c.fetchall()
    print(f"✅ 提取 {len(rows):,} 条有效记录（有前一日和后一日数据）")
except Exception as e:
    print(f"⚠️ Window function失败: {e}")
    print("回退到Python分批处理...")
    # Fallback: Python loop
    rows = []
    batch_size = 200
    for i in range(0, len(all_codes), batch_size):
        codes_batch = all_codes[i:i+batch_size]
        placeholders = ','.join(['?'] * len(codes_batch))
        c.execute(f"""
            SELECT code, date, open, close, volume 
            FROM kline 
            WHERE code IN ({placeholders})
            ORDER BY code, date
        """, codes_batch)
        krows = c.fetchall()
        
        # Group by code
        by_code = defaultdict(list)
        for r in krows:
            by_code[r[0]].append(r)
        
        for code, klines in by_code.items():
            for j in range(1, len(klines) - 1):
                prev = klines[j-1]
                cur = klines[j]
                nxt = klines[j+1]
                # cur date must be exactly 1 day after prev, and nxt date 1 day after cur
                # (check consecutive dates)
                if prev[1] != date_to_prev.get(cur[1]): continue
                if nxt[1] != date_to_next.get(cur[1]): continue
                rows.append((
                    code, cur[1], cur[2], cur[3], cur[4],
                    prev[3],  # prev_close = prev's close
                    nxt[3],   # next_close = next's close
                ))
        
        if (i + batch_size) % 1000 == 0 or (i+batch_size) >= len(all_codes):
            print(f"  🆗 已处理 {min(i+batch_size, len(all_codes))}/{len(all_codes)} 只股票...")

    print(f"✅ 提取 {len(rows):,} 条有效记录")

# ===== 3. 计算核心指标 =====
print("\n🔬 计算核心指标...")

records = []
for r in rows:
    code, date, open_p, close, volume, prev_close, next_close = r
    
    # 开盘涨跌幅(%) = (今日开盘 - 昨日收盘) / 昨日收盘
    open_chg = round((open_p - prev_close) / prev_close * 100, 4) if prev_close > 0 else 0
    
    # T+1收益(%) = (明日收盘 - 今日收盘) / 今日收盘  (T+1 whole day)
    t1_chg = round((next_close - close) / close * 100, 4) if close > 0 else 0
    
    # T+1开盘溢价 = next_open relative to today's close (not available in this data)
    # But we can use next_close as proxy for "T+1 whole day return"
    
    # 量/价/换手 proxy
    # volume * 100 = 手数 (股) — 用成交额近似
    amount_yi = round(volume * open_p / 100000000, 4)  # 成交额(亿)
    # volume relative to average — not possible without avg, use raw volume rank
    
    records.append({
        'code': code,
        'date': date,
        'open_chg': open_chg,
        'close_chg': round((close - prev_close) / prev_close * 100, 4),  # 当日涨跌幅
        't1_chg': t1_chg,
        'volume': int(volume),
        'amount_yi': amount_yi,
        'close': close
    })

print(f"📊 有效记录: {len(records):,} 条")

# ===== 4. 按开盘涨跌幅分组分析 =====
print("\n" + "=" * 60)
print("📊 分析1: 开盘涨跌幅对T+1收益的预测力")
print("=" * 60)

buckets_open = [
    ("开盘跌停(-10~-7%)", -10, -7),
    ("大幅低开(-7~-4%)", -7, -4),
    ("中幅低开(-4~-2%)", -4, -2),
    ("小幅低开(-2~-0.5%)", -2, -0.5),
    ("平开(-0.5~+0.5%)", -0.5, 0.5),
    ("小幅高开(+0.5~+2%)", 0.5, 2),
    ("中幅高开(+2~+4%)", 2, 4),
    ("大幅高开(+4~+7%)", 4, 7),
    ("开盘涨停(+7~+10%)", 7, 10),
    ("一字涨停(>=+10%)", 10, 999),
]

open_analysis = []
for label, lo, hi in buckets_open:
    group = [r for r in records if lo <= r['open_chg'] < hi]
    if not group:
        continue
    n = len(group)
    t1_returns = [r['t1_chg'] for r in group]
    avg_t1 = round(sum(t1_returns) / n, 4)
    wins = sum(1 for r in t1_returns if r > 0)
    win_rate = round(wins / n * 100, 2)
    avg_open = round(sum(r['open_chg'] for r in group) / n, 2)
    # T+1收益分布
    p25 = sorted(t1_returns)[int(n * 0.25)]
    p50 = sorted(t1_returns)[int(n * 0.5)]
    p75 = sorted(t1_returns)[int(n * 0.75)]
    top10_avg = round(sum(sorted(t1_returns)[-int(n*0.1):]) / max(int(n*0.1), 1), 4) if n >= 10 else None
    
    oa = {
        'bucket': label,
        'count': n,
        'avg_open_chg': avg_open,
        'avg_t1_return': avg_t1,
        'win_rate': win_rate,
        'p25_t1': round(p25, 4),
        'p50_t1': round(p50, 4),
        'p75_t1': round(p75, 4),
        'top10_percent_avg_t1': top10_avg,
    }
    open_analysis.append(oa)
    
    print(f"  {label:20s} | N={n:>7,} | 开盘{avg_open:>+6.2f}% | T+1{avg_t1:>+7.2f}% | 胜率{win_rate:>6.2f}% | P50={p50:>+6.2f}%")

# ===== 5. 按成交额分组分析 =====
print("\n" + "=" * 60)
print("📊 分析2: 成交额（竞价近似）对T+1收益的影响")
print("=" * 60)

# Compute amount percentiles
amounts = sorted([r['amount_yi'] for r in records])
n_total = len(amounts)
p20_a = amounts[int(n_total * 0.2)]
p40_a = amounts[int(n_total * 0.4)]
p60_a = amounts[int(n_total * 0.6)]
p80_a = amounts[int(n_total * 0.8)]
print(f"  成交额分位: P20={p20_a:.3f}亿  P40={p40_a:.3f}亿  P60={p60_a:.3f}亿  P80={p80_a:.3f}亿")

amount_buckets = [
    ("极小额(<P20)", 0, p20_a),
    ("小额(P20-P40)", p20_a, p40_a),
    ("中等额(P40-P60)", p40_a, p60_a),
    ("大额(P60-P80)", p60_a, p80_a),
    ("极大额(>=P80)", p80_a, 999999),
]

amount_analysis = []
for label, lo, hi in amount_buckets:
    group = [r for r in records if lo <= r['amount_yi'] < hi]
    if not group:
        continue
    n = len(group)
    t1_returns = [r['t1_chg'] for r in group]
    avg_t1 = round(sum(t1_returns) / n, 4)
    wins = sum(1 for r in t1_returns if r > 0)
    win_rate = round(wins / n * 100, 2)
    avg_amount = round(sum(r['amount_yi'] for r in group) / n, 4)
    
    # T+1收益分布
    p25 = sorted(t1_returns)[int(n * 0.25)]
    p50 = sorted(t1_returns)[int(n * 0.5)]
    p75 = sorted(t1_returns)[int(n * 0.75)]
    
    aa = {
        'bucket': label,
        'count': n,
        'avg_amount_yi': avg_amount,
        'avg_t1_return': avg_t1,
        'win_rate': win_rate,
        'p25_t1': round(p25, 4),
        'p50_t1': round(p50, 4),
        'p75_t1': round(p75, 4),
    }
    amount_analysis.append(aa)
    
    print(f"  {label:20s} | N={n:>7,} | 成交额{avg_amount:>7.3f}亿 | T+1{avg_t1:>+7.2f}% | 胜率{win_rate:>6.2f}% | P50={p50:>+6.2f}%")

# ===== 6. 按量比/换手率分组 =====
print("\n" + "=" * 60)
print("📊 分析3: 成交量对T+1收益的影响（用volume代表换手活跃度）")
print("=" * 60)

volumes = sorted([r['volume'] for r in records])
p20_v = volumes[int(n_total * 0.2)]
p40_v = volumes[int(n_total * 0.4)]
p60_v = volumes[int(n_total * 0.6)]
p80_v = volumes[int(n_total * 0.8)]
print(f"  量分位: P20={p20_v:>12,}  P40={p40_v:>12,}  P60={p60_v:>12,}  P80={p80_v:>12,}")

vol_buckets = [
    ("极低量(<P20)", 0, p20_v),
    ("低量(P20-P40)", p20_v, p40_v),
    ("中量(P40-P60)", p40_v, p60_v),
    ("高量(P60-P80)", p60_v, p80_v),
    ("极高量(>=P80)", p80_v, 999999999999),
]

vol_analysis = []
for label, lo, hi in vol_buckets:
    group = [r for r in records if lo <= r['volume'] < hi]
    if not group:
        continue
    n = len(group)
    t1_returns = [r['t1_chg'] for r in group]
    avg_t1 = round(sum(t1_returns) / n, 4)
    wins = sum(1 for r in t1_returns if r > 0)
    win_rate = round(wins / n * 100, 2)
    avg_vol = int(sum(r['volume'] for r in group) / n)
    
    p25 = sorted(t1_returns)[int(n * 0.25)]
    p50 = sorted(t1_returns)[int(n * 0.5)]
    p75 = sorted(t1_returns)[int(n * 0.75)]
    
    va = {
        'bucket': label,
        'count': n,
        'avg_volume': avg_vol,
        'avg_t1_return': avg_t1,
        'win_rate': win_rate,
        'p25_t1': round(p25, 4),
        'p50_t1': round(p50, 4),
        'p75_t1': round(p75, 4),
    }
    vol_analysis.append(va)
    
    print(f"  {label:20s} | N={n:>7,} | 均量{avg_vol:>10,} | T+1{avg_t1:>+7.2f}% | 胜率{win_rate:>6.2f}% | P50={p50:>+6.2f}%")

# ===== 7. 二维交叉分析：开盘涨跌幅×成交量 =====
print("\n" + "=" * 60)
print("📊 分析4: 开盘涨跌幅×成交额 二维交叉 (寻找最优组合)")
print("=" * 60)

cross_analysis = []
for o_label, o_lo, o_hi in buckets_open:
    for a_label, a_lo, a_hi in amount_buckets:
        group = [r for r in records if o_lo <= r['open_chg'] < o_hi and a_lo <= r['amount_yi'] < a_hi]
        if len(group) < 30:
            continue
        n = len(group)
        t1_returns = [r['t1_chg'] for r in group]
        avg_t1 = round(sum(t1_returns) / n, 4)
        wins = sum(1 for r in t1_returns if r > 0)
        win_rate = round(wins / n * 100, 2)
        avg_open = round(sum(r['open_chg'] for r in group) / n, 2)
        
        cross_analysis.append({
            'open_bucket': o_label,
            'amount_bucket': a_label,
            'count': n,
            'avg_open_chg': avg_open,
            'avg_t1_return': avg_t1,
            'win_rate': win_rate,
        })

# Sort by T+1 return descending for top combos
cross_analysis.sort(key=lambda x: x['avg_t1_return'], reverse=True)

print(f"  Top 10 最佳开盘×成交额组合（N>=30）:")
for i, ca in enumerate(cross_analysis[:10]):
    print(f"  {i+1}. {ca['open_bucket']:20s} × {ca['amount_bucket']:15s} | N={ca['count']:>6,} | T+1{ca['avg_t1_return']:>+7.2f}% | 胜率{ca['win_rate']:>5.1f}%")

print(f"\n  Bottom 10 最差开盘×成交额组合:")
for i, ca in enumerate(cross_analysis[-10:]):
    print(f"  {i+1}. {ca['open_bucket']:20s} × {ca['amount_bucket']:15s} | N={ca['count']:>6,} | T+1{ca['avg_t1_return']:>+7.2f}% | 胜率{ca['win_rate']:>5.1f}%")

# ===== 8. 开盘涨跌幅 vs T+1 散点回归统计 =====
print("\n" + "=" * 60)
print("📊 分析5: 开盘涨跌幅与T+1收益的线性回归")
print("=" * 60)

# Pearson correlation
import math
n_reg = len(records)
sum_x = sum(r['open_chg'] for r in records)
sum_y = sum(r['t1_chg'] for r in records)
sum_xy = sum(r['open_chg'] * r['t1_chg'] for r in records)
sum_x2 = sum(r['open_chg'] ** 2 for r in records)
sum_y2 = sum(r['t1_chg'] ** 2 for r in records)

r_num = n_reg * sum_xy - sum_x * sum_y
r_den = math.sqrt((n_reg * sum_x2 - sum_x ** 2) * (n_reg * sum_y2 - sum_y ** 2))
pearson_r = round(r_num / r_den, 6) if r_den > 0 else 0

# Slope
slope = round((n_reg * sum_xy - sum_x * sum_y) / (n_reg * sum_x2 - sum_x ** 2), 6) if (n_reg * sum_x2 - sum_x ** 2) > 0 else 0
intercept = round((sum_y - slope * sum_x) / n_reg, 6)

print(f"  相关系数(Pearson r): {pearson_r}")
print(f"  斜率: {slope}")
print(f"  截距: {intercept}")
print(f"  解释: 开盘涨跌幅每变化1%，T+1收益平均变化{slope*100:.4f}%")

# ===== 9. 特殊场景：开盘涨停/跌停 =====
print("\n" + "=" * 60)
print("📊 分析6: 特殊场景 - 开盘涨停 vs 开盘跌停")
print("=" * 60)

limit_up_group = [r for r in records if r['open_chg'] >= 9.5]
limit_down_group = [r for r in records if r['open_chg'] <= -9.5]

if limit_up_group:
    n_lu = len(limit_up_group)
    t1_lu = [r['t1_chg'] for r in limit_up_group]
    avg_lu = round(sum(t1_lu) / n_lu, 4)
    win_lu = sum(1 for r in t1_lu if r > 0) / n_lu * 100
    print(f"  开盘涨停(>=9.5%): N={n_lu:>7,} | T+1平均{avg_lu:>+7.2f}% | 胜率{win_lu:>5.1f}%")
    
    # 开盘涨停中，继续分大/小成交额
    lu_amounts = [r['amount_yi'] for r in limit_up_group]
    lu_med = sorted(lu_amounts)[len(lu_amounts)//2]
    lu_small = [r for r in limit_up_group if r['amount_yi'] < lu_med]
    lu_large = [r for r in limit_up_group if r['amount_yi'] >= lu_med]
    if lu_small:
        s_avg = round(sum(r['t1_chg'] for r in lu_small)/len(lu_small), 4)
        s_wins = sum(1 for r in lu_small if r['t1_chg']>0)/len(lu_small)*100
        print(f"    ├─ 小成交额: N={len(lu_small):>6,} | T+1{s_avg:>+7.2f}% | 胜率{s_wins:>5.1f}%")
    if lu_large:
        l_avg = round(sum(r['t1_chg'] for r in lu_large)/len(lu_large), 4)
        l_wins = sum(1 for r in lu_large if r['t1_chg']>0)/len(lu_large)*100
        print(f"    └─ 大成交额: N={len(lu_large):>6,} | T+1{l_avg:>+7.2f}% | 胜率{l_wins:>5.1f}%")

if limit_down_group:
    n_ld = len(limit_down_group)
    t1_ld = [r['t1_chg'] for r in limit_down_group]
    avg_ld = round(sum(t1_ld) / n_ld, 4)
    win_ld = sum(1 for r in t1_ld if r > 0) / n_ld * 100
    print(f"  开盘跌停(<=-9.5%): N={n_ld:>7,} | T+1平均{avg_ld:>+7.2f}% | 胜率{win_ld:>5.1f}%")

# ===== 10. 涨停股次日的开盘特征 =====
print("\n" + "=" * 60)
print("📊 分析7: 涨停次日开盘特征对T+1收益的影响")
print("=" * 60)

# 今日涨停(close_chg >= 9.5) → 明日开盘(open_chg_t1) → T+1收益
# Need to compute: yesterday's close_chg, today's open_chg

# Filter: today's close_chg >= 9.5 (涨停), then next day's open_chg
limit_records = [r for r in records if r['close_chg'] >= 9.0 and abs(r['open_chg']) < 10]
print(f"  涨停次日样本: {len(limit_records):,} 条")

limit_open_groups = [
    ("低开(-10~-2%)", -10, -2),
    ("小幅低开(-2~0%)", -2, 0),
    ("平开(0~+1%)", 0, 1),
    ("小幅高开(+1~+3%)", 1, 3),
    ("中幅高开(+3~+6%)", 3, 6),
    ("大幅高开(+6~+10%)", 6, 10),
]

limit_open_analysis = []
for label, lo, hi in limit_open_groups:
    group = [r for r in limit_records if lo <= r['open_chg'] < hi]
    if len(group) < 10:
        continue
    n = len(group)
    t1 = [r['t1_chg'] for r in group]
    avg_t1 = round(sum(t1)/n, 4)
    wins = sum(1 for r in t1 if r > 0)
    wr = round(wins/n*100, 2)
    avg_open = round(sum(r['open_chg'] for r in group)/n, 2)
    
    loa = {
        'bucket': label,
        'count': n,
        'avg_open_chg': avg_open,
        'avg_t1_return': avg_t1,
        'win_rate': wr,
    }
    limit_open_analysis.append(loa)
    
    print(f"  {label:20s} | N={n:>7,} | 开盘{avg_open:>+6.2f}% | T+1{avg_t1:>+7.2f}% | 胜率{wr:>6.2f}%")

# ===== 11. 盘前竞价成交额 vs T+1(成交量proxy分析) =====
print("\n" + "=" * 60)
print("📊 分析8: 换手率（量比）分档 × 开盘涨跌幅 交叉分析")
print("=" * 60)

# Use volume buckets as proxy for "换手活跃度"
# For 涨停股, 高开+高量 = 换手接力, 高开+低量 = 缩量加速
limit_cross = []
for o_label, o_lo, o_hi in [
    ("低开/平开(<+1%)", -999, 1),
    ("小幅高开(+1~+4%)", 1, 4),
    ("中幅高开(+4~+7%)", 4, 7),
    ("大幅高开(>=+7%)", 7, 999),
]:
    for v_label, v_lo, v_hi in [
        ("低量(<P40)", 0, p40_v),
        ("中量(P40-P70)", p40_v, p70_v := volumes[int(n_total * 0.7)]),
        ("高量(>=P70)", volumes[int(n_total * 0.7)], 999999999999),
    ]:
        # Only for 涨停次日的样本
        group = [r for r in limit_records if o_lo <= r['open_chg'] < o_hi and v_lo <= r['volume'] < v_hi]
        if len(group) < 20:
            continue
        n = len(group)
        t1 = [r['t1_chg'] for r in group]
        avg_t1 = round(sum(t1)/n, 4)
        wins = sum(1 for r in t1 if r > 0)
        wr = round(wins/n*100, 2)
        limit_cross.append({
            'open_bucket': o_label,
            'vol_bucket': v_label,
            'count': n,
            'avg_t1_return': avg_t1,
            'win_rate': wr,
        })

limit_cross.sort(key=lambda x: x['avg_t1_return'], reverse=True)
print("  Top 5 涨停次日最佳开盘×量组合:")
for i, lc in enumerate(limit_cross[:5]):
    print(f"  {i+1}. {lc['open_bucket']:20s} × {lc['vol_bucket']:12s} | N={lc['count']:>6,} | T+1{lc['avg_t1_return']:>+7.2f}% | 胜率{lc['win_rate']:>5.1f}%")
print(f"  Bottom 5:")
for i, lc in enumerate(limit_cross[-5:]):
    print(f"  {i+1}. {lc['open_bucket']:20s} × {lc['vol_bucket']:12s} | N={lc['count']:>6,} | T+1{lc['avg_t1_return']:>+7.2f}% | 胜率{lc['win_rate']:>5.1f}%")

# ===== 12. 汇总结论 =====
print("\n" + "=" * 60)
print("📋 核心发现总结")
print("=" * 60)

print("""
结论1: 开盘涨跌幅 vs T+1收益
  - 正相关/负相关？相关系数反应了什么？
  - 哪个开盘区间T+1胜率最高？
  - 哪个开盘区间T+1期望收益最高？

结论2: 成交额 vs T+1收益
  - 大额开盘是好事还是坏事？
  - 涨停次日的量价关系？

结论3: 实战建议
  - 什么开盘特征值得买入？
  - 什么开盘特征应该卖出/回避？
""")

# ===== 13. 输出JSON =====
result = {
    'meta': {
        'source_db': 'kline_cache.db',
        'date_range': f"{all_dates[0]}~{all_dates[-1]}",
        'total_records': len(records),
        'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    },
    'open_chg_vs_t1': open_analysis,
    'amount_vs_t1': amount_analysis,
    'volume_vs_t1': vol_analysis,
    'open_x_amount_cross': cross_analysis,
    'regression': {
        'pearson_r': pearson_r,
        'slope': slope,
        'intercept': intercept,
        'n': n_reg,
    },
    'limit_up_next_day': limit_open_analysis,
    'limit_up_open_x_vol': limit_cross,
    'special_scenarios': {
        'limit_up_open_gt_9.5': {
            'count': len(limit_up_group),
            'avg_t1': round(sum(r['t1_chg'] for r in limit_up_group) / len(limit_up_group), 4) if limit_up_group else None,
            'win_rate': round(sum(1 for r in limit_up_group if r['t1_chg'] > 0) / len(limit_up_group) * 100, 2) if limit_up_group else None,
        } if limit_up_group else None,
        'limit_down_open_lt_neg9.5': {
            'count': len(limit_down_group),
            'avg_t1': round(sum(r['t1_chg'] for r in limit_down_group) / len(limit_down_group), 4) if limit_down_group else None,
            'win_rate': round(sum(1 for r in limit_down_group if r['t1_chg'] > 0) / len(limit_down_group) * 100, 2) if limit_down_group else None,
        } if limit_down_group else None,
    },
}

with open(OUTPUT, 'w') as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
print(f"\n✅ 结果已输出到: {OUTPUT}")

conn.close()
