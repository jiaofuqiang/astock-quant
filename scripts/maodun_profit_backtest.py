#!/usr/bin/env python3
"""
赚钱维度回测 v4：精准分类 + 真实数据分布适配
封板时间、跳空高开、日内分时强度
输出到 ~/astock/data/maodun_profit_results.json
"""
import sqlite3
import json
import os
from collections import defaultdict

DB_PATH = os.path.expanduser("~/astock/data/kline_cache.db")
STOCK_DB = os.path.expanduser("~/astock/data/stock_profiles.db")
MAIN_BOARD_FILE = os.path.expanduser("~/astock/data/all_main_board.txt")
OUT_PATH = os.path.expanduser("~/astock/data/maodun_profit_results.json")
START_DATE = "2024-01-01"


def load_main_board():
    codes = set()
    if os.path.exists(MAIN_BOARD_FILE):
        with open(MAIN_BOARD_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    codes.add(line)
    try:
        conn = sqlite3.connect(STOCK_DB)
        cur = conn.cursor()
        cur.execute("SELECT code FROM stock_basic WHERE market IN ('沪主板','深主板')")
        for row in cur.fetchall():
            codes.add(row[0])
        conn.close()
    except Exception as e:
        print(f"  [警告] 从stock_profiles加载主板失败: {e}")
    return codes


def get_enriched_data(main_board_codes):
    codes_list = list(main_board_codes)
    print(f"  主板股票数: {len(codes_list)}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("  加载全部K线数据...")
    all_rows = []
    batch_size = 500
    for i in range(0, len(codes_list), batch_size):
        batch = codes_list[i:i+batch_size]
        placeholders = ",".join(["?"] * len(batch))
        sql = f"""
            SELECT code, date, open, close, high, low, volume
            FROM kline
            WHERE code IN ({placeholders})
            ORDER BY code, date
        """
        cur.execute(sql, batch)
        all_rows.extend(cur.fetchall())

    conn.close()
    print(f"  总K线行数: {len(all_rows)}")

    by_code = defaultdict(list)
    for r in all_rows:
        by_code[r["code"]].append(r)

    enriched = []
    from datetime import datetime, timedelta

    for code, days in by_code.items():
        days.sort(key=lambda x: x["date"])
        for i in range(len(days)):
            today = days[i]
            if today["date"] < START_DATE:
                continue
            if i == 0:
                continue
            prev_close = days[i-1]["close"]
            d_today = datetime.strptime(today["date"], "%Y-%m-%d")
            d_prev = datetime.strptime(days[i-1]["date"], "%Y-%m-%d")
            if (d_today - d_prev).days > 5:
                continue
            if i + 1 >= len(days):
                continue
            t1 = days[i+1]
            d_t1 = datetime.strptime(t1["date"], "%Y-%m-%d")
            if (d_t1 - d_today).days > 5:
                continue

            enriched.append({
                "code": code,
                "date": today["date"],
                "open": today["open"],
                "close": today["close"],
                "high": today["high"],
                "low": today["low"],
                "volume": today["volume"],
                "prev_close": prev_close,
                "t1_open": t1["open"],
                "t1_close": t1["close"],
                "t1_high": t1["high"],
                "t1_low": t1["low"],
            })

    print(f"  有prev_close和T+1的行数: {len(enriched)}")
    return enriched


def is_limit_up(close, open_):
    return close >= open_ * 1.095


# ============================================================
# 任务1：封板时间回测（基于真实gap分布调整阈值）
# ============================================================
def task1_fengban_time(enriched):
    """
    基于实际gap分布调整的分类：
    - 早盘封板(高开秒封): gap >= 1% (open接近涨停价)
    - 盘中封板(自然涨停): gap在-1%~1%之间，平开或微幅
    - 尾盘封板(偷袭涨停): gap < -1% (低开后拉升封板)
    """
    results = {
        "早盘封板(高开秒封,gap>=1%)": {"count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "盘中封板(平开涨停,gap -1%~1%)": {"count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "尾盘封板(低开偷袭,gap<-1%)": {"count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
    }
    returns = {g: [] for g in results}

    detail = {"gap_geo": []}  # 记录详细gap用于分析

    for r in enriched:
        open_ = r["open"]
        close = r["close"]
        prev_close = r["prev_close"]
        t1_close = r["t1_close"]
        t1_open = r["t1_open"]

        if not is_limit_up(close, open_):
            continue

        gap_pct = (open_ - prev_close) / prev_close * 100
        t1_return = (t1_close - t1_open) / t1_open

        detail["gap_geo"].append(round(gap_pct, 2))

        if gap_pct >= 1:
            group = "早盘封板(高开秒封,gap>=1%)"
        elif gap_pct >= -1:
            group = "盘中封板(平开涨停,gap -1%~1%)"
        else:
            group = "尾盘封板(低开偷袭,gap<-1%)"

        returns[group].append(t1_return)

    for g in results:
        d = results[g]
        arr = returns[g]
        d["count"] = len(arr)
        if arr:
            d["t1_avg_return"] = round(sum(arr) / len(arr) * 100, 2)
            d["t1_win_rate"] = round(sum(1 for x in arr if x > 0) / len(arr) * 100, 2)

    # 附加统计信息
    gap_geo = detail["gap_geo"]
    if gap_geo:
        gap_geo.sort()
        results["_gap_distribution"] = {
            "mean": round(sum(gap_geo)/len(gap_geo), 2),
            "median": gap_geo[len(gap_geo)//2],
            "p25": gap_geo[len(gap_geo)//4],
            "p75": gap_geo[len(gap_geo)*3//4],
            "min": min(gap_geo),
            "max": max(gap_geo),
            "lt_minus2_pct": round(sum(1 for g in gap_geo if g < -2)/len(gap_geo)*100, 1),
            "btw_minus2_0_pct": round(sum(1 for g in gap_geo if -2 <= g < 0)/len(gap_geo)*100, 1),
            "btw_0_1_pct": round(sum(1 for g in gap_geo if 0 <= g < 1)/len(gap_geo)*100, 1),
            "gt_1_pct": round(sum(1 for g in gap_geo if g >= 1)/len(gap_geo)*100, 1),
        }

    return results


# ============================================================
# 任务2：跳空高开vs平开的赚钱效应
# ============================================================
def task2_gap_effect(enriched):
    gaps = {
        "gap_lt_minus2": {"label": "大幅低开(<-2%)", "count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "gap_minus2_minus1": {"label": "中幅低开(-2%~-1%)", "count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "gap_minus1_0": {"label": "微幅低开(-1%~0%)", "count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "gap_0_1": {"label": "平开微幅(0%~1%)", "count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "gap_gt_1": {"label": "高开(>1%)", "count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
    }
    returns = {k: [] for k in gaps}

    for r in enriched:
        open_ = r["open"]
        close = r["close"]
        prev_close = r["prev_close"]
        t1_close = r["t1_close"]
        t1_open = r["t1_open"]

        if not is_limit_up(close, open_):
            continue

        gap_pct = (open_ - prev_close) / prev_close * 100

        if gap_pct < -2:
            k = "gap_lt_minus2"
        elif gap_pct < -1:
            k = "gap_minus2_minus1"
        elif gap_pct < 0:
            k = "gap_minus1_0"
        elif gap_pct < 1:
            k = "gap_0_1"
        else:
            k = "gap_gt_1"

        t1_return = (t1_close - t1_open) / t1_open
        returns[k].append(t1_return)

    for k in gaps:
        d = gaps[k]
        arr = returns[k]
        d["count"] = len(arr)
        if arr:
            d["t1_avg_return"] = round(sum(arr) / len(arr) * 100, 2)
            d["t1_win_rate"] = round(sum(1 for x in arr if x > 0) / len(arr) * 100, 2)

    return gaps


# ============================================================
# 任务3：日内分时强度
# ============================================================
def task3_intraday_strength(enriched):
    results = {
        "高位收盘(>80%位置)": {"count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "中位收盘(30%~80%)": {"count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "低位收盘(<30%位置)": {"count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
        "涨停尾封(close=high)": {"count": 0, "t1_avg_return": 0.0, "t1_win_rate": 0.0},
    }
    returns = {g: [] for g in results}

    for r in enriched:
        close = r["close"]
        open_ = r["open"]
        high = r["high"]
        low = r["low"]
        t1_close = r["t1_close"]
        t1_open = r["t1_open"]

        if not is_limit_up(close, open_):
            continue

        if high == low:
            position = 1.0
        else:
            position = (close - low) / (high - low)

        t1_return = (t1_close - t1_open) / t1_open

        if close == high:
            group = "涨停尾封(close=high)"
        elif position > 0.8:
            group = "高位收盘(>80%位置)"
        elif position < 0.3:
            group = "低位收盘(<30%位置)"
        else:
            group = "中位收盘(30%~80%)"

        returns[group].append(t1_return)

    for g in results:
        d = results[g]
        arr = returns[g]
        d["count"] = len(arr)
        if arr:
            d["t1_avg_return"] = round(sum(arr) / len(arr) * 100, 2)
            d["t1_win_rate"] = round(sum(1 for x in arr if x > 0) / len(arr) * 100, 2)

    return results


# ============================================================
# 附加分析：分年度和季度
# ============================================================
def additional_analysis(enriched):
    """分年度季度分析"""
    by_period = defaultdict(list)
    for r in enriched:
        if not is_limit_up(r["close"], r["open"]):
            continue
        year = r["date"][:4]
        month = int(r["date"][5:7])
        season = (month - 1) // 3 + 1
        period = f"{year}Q{season}"
        t1_return = (r["t1_close"] - r["t1_open"]) / r["t1_open"]
        by_period[period].append(t1_return)

    result = {}
    for period in sorted(by_period.keys()):
        arr = by_period[period]
        avg = sum(arr) / len(arr) * 100
        win = sum(1 for x in arr if x > 0) / len(arr) * 100
        result[period] = {
            "count": len(arr),
            "t1_avg_return": round(avg, 2),
            "t1_win_rate": round(win, 2),
        }
    return result


def main():
    print("=" * 60)
    print("赚钱维度回测脚本 v4")
    print("=" * 60)

    print("\n[加载主板列表]")
    main_board = load_main_board()
    print(f"  主板股票总数: {len(main_board)}")

    print("\n[加载K线数据 + 构建数据集]")
    enriched = get_enriched_data(main_board)
    if len(enriched) == 0:
        print("错误：未获取到有效数据")
        return

    limit_up_count = sum(1 for r in enriched if is_limit_up(r["close"], r["open"]))
    print(f"  涨停样本数: {limit_up_count}")

    all_results = {}

    # ========== 任务1 ==========
    print("\n" + "=" * 60)
    print("【任务1】封板时间回测")
    print("=" * 60)
    print("赚钱假设：早盘高开封板=强势，午盘平开封板=正常，尾盘低开封板=偷袭")
    print()
    r1 = task1_fengban_time(enriched)
    all_results["任务1_封板时间回测"] = r1
    for g in r1:
        if g.startswith("_"):
            continue
        d = r1[g]
        print(f"  {g}:")
        print(f"    样本数: {d['count']}")
        print(f"    T+1平均收益: {d['t1_avg_return']}%")
        print(f"    T+1胜率: {d['t1_win_rate']}%")
        print()
    # 打印gap分布
    if "_gap_distribution" in r1:
        gd = r1["_gap_distribution"]
        print(f"  Gap分布统计:")
        print(f"    均值: {gd['mean']}%  中位数: {gd['median']}%")
        print(f"    25%分位: {gd['p25']}%  75%分位: {gd['p75']}%")
        print(f"    区间: [{gd['min']}%, {gd['max']}%]")
        print(f"    <-2%: {gd['lt_minus2_pct']}%  -2%~0%: {gd['btw_minus2_0_pct']}%")
        print(f"    0%~1%: {gd['btw_0_1_pct']}%  >1%: {gd['gt_1_pct']}%")

    # ========== 任务2 ==========
    print("\n" + "=" * 60)
    print("【任务2】跳空高开vs平开的赚钱效应")
    print("=" * 60)
    print("赚钱假设：跳空幅度影响次日表现")
    print()
    r2 = task2_gap_effect(enriched)
    all_results["任务2_跳空高开效应"] = r2
    for k in r2:
        d = r2[k]
        print(f"  {d['label']}:")
        print(f"    样本数: {d['count']}")
        print(f"    T+1平均收益: {d['t1_avg_return']}%")
        print(f"    T+1胜率: {d['t1_win_rate']}%")
        print()

    # ========== 任务3 ==========
    print("\n" + "=" * 60)
    print("【任务3】日内分时强度")
    print("=" * 60)
    print("赚钱假设：收盘位置反映封板力度")
    print()
    r3 = task3_intraday_strength(enriched)
    all_results["任务3_日内分时强度"] = r3
    for g, d in r3.items():
        print(f"  {g}:")
        print(f"    样本数: {d['count']}")
        print(f"    T+1平均收益: {d['t1_avg_return']}%")
        print(f"    T+1胜率: {d['t1_win_rate']}%")
        print()

    # ========== 附加分析 ==========
    print("\n" + "=" * 60)
    print("【附加分析】分季度收益趋势")
    print("=" * 60)
    period_analysis = additional_analysis(enriched)
    all_results["附加_分季度收益"] = period_analysis
    for p in sorted(period_analysis.keys()):
        d = period_analysis[p]
        print(f"  {p}: 样本{d['count']:>5d}  收益{d['t1_avg_return']:>+6.2f}%  胜率{d['t1_win_rate']:>5.1f}%")

    # ========== 汇总建议 ==========
    print("\n" + "=" * 60)
    print("【赚钱操作建议】")
    print("=" * 60)

    all_groups = []
    for g, d in r1.items():
        if g.startswith("_"):
            continue
        all_groups.append((f"封板时间-{g}", d["count"], d["t1_avg_return"], d["t1_win_rate"]))
    for k, d in r2.items():
        all_groups.append((f"跳空幅度-{d['label']}", d["count"], d["t1_avg_return"], d["t1_win_rate"]))
    for g, d in r3.items():
        all_groups.append((f"日内强度-{g}", d["count"], d["t1_avg_return"], d["t1_win_rate"]))

    sorted_groups = sorted(all_groups, key=lambda x: x[2], reverse=True)

    print(f"\n{'维度':45s} {'样本数':>6s} {'T+1收益':>8s} {'胜率':>6s}")
    print(f"{'-'*45} {'-'*6} {'-'*8} {'-'*6}")
    for name, cnt, avg_ret, win_rate in sorted_groups:
        if cnt >= 20:
            marker = " ✓" if avg_ret > 0 else " ✗"
            print(f"  {name:43s} {cnt:>6d} {avg_ret:>+7.2f}%{marker} {win_rate:>5.1f}%")

    print()
    pos = [x for x in sorted_groups if x[1] >= 20 and x[2] > 0]
    if pos:
        print("★ 最佳赚钱操作建议（正收益维度）：")
        for i, (name, cnt, avg_ret, win_rate) in enumerate(pos, 1):
            print(f"  {i}. {name}")
            print(f"     样本数: {cnt}, T+1平均收益: {avg_ret:+.2f}%, 胜率: {win_rate:.1f}%")
    else:
        print("★ 所有维度整体为负收益，涨停打板次日总体亏钱。")
        print("   最优策略可能是：不开新仓、持有现金")

    neg = [x for x in sorted_groups if x[1] >= 20 and x[2] < 0]
    if neg:
        print("\n★ 应回避的操作维度：")
        for name, cnt, avg_ret, win_rate in neg[:5]:
            print(f"  - {name}: 收益{avg_ret:+.2f}% (回避)")

    # 整体统计
    all_limit_returns = []
    for r in enriched:
        if is_limit_up(r["close"], r["open"]):
            all_limit_returns.append((r["t1_close"] - r["t1_open"]) / r["t1_open"])
    if all_limit_returns:
        avg_all = sum(all_limit_returns) / len(all_limit_returns) * 100
        win_all = sum(1 for x in all_limit_returns if x > 0) / len(all_limit_returns) * 100
        print(f"\n  【涨停板整体统计】样本:{len(all_limit_returns)} T+1平均收益:{avg_all:.2f}% 胜率:{win_all:.1f}%")
        all_results["_整体统计"] = {
            "样本数": len(all_limit_returns),
            "T+1平均收益%": round(avg_all, 2),
            "T+1胜率%": round(win_all, 2),
        }

    # 保存
    print(f"\n[保存结果到 {OUT_PATH}]")
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print("  完成！")


if __name__ == "__main__":
    main()
