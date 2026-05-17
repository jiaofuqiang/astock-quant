#!/usr/bin/env python3
"""
矛盾论维度1-4 完整回测脚本
================================
基于《矛盾论》四大维度，用历史数据验证每个维度的预测力。

数据源: kline_cache.db + lhb_cache.db
时间范围: 2024-01-01 ~ 2026-05-15
标的筛选: 主板股票 (6xx/000/001/002/003开头，排除68x/30x/8xx)

维度1: 主要矛盾识别准确率（大盘环境的预测力）
维度2: 各层矛盾方向一致性
维度3: 内因vs外因判断
维度4: 开盘vs昨收矛盾方向变化

输出: ~/astock/data/contradiction_bt_dim1_4.json
"""

import sqlite3
import os
import json
import sys
from datetime import datetime, timedelta
from collections import defaultdict
import math

HOME = os.path.expanduser("~")
KLINE_DB = os.path.join(HOME, "astock/data/kline_cache.db")
LHB_DB = os.path.join(HOME, "astock/data/lhb_cache.db")
OUTPUT = os.path.join(HOME, "astock/data/contradiction_bt_dim1_4.json")

START_DATE = "2024-01-01"
END_DATE = "2026-05-15"

# 主板股票前缀（排除68x科创板、30x创业板、8xx/92x北交所）
MAIN_BOARD_PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003')


def is_main_board(code):
    """判断是否主板股票"""
    return any(code.startswith(p) for p in MAIN_BOARD_PREFIXES)


def connect_db(path):
    """连接SQLite数据库"""
    if not os.path.exists(path):
        print(f"  ❌ 数据库不存在: {path}")
        return None
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    # 用内存加速大量读取
    conn.execute("PRAGMA cache_size=-800000")
    return conn


def get_trading_dates(conn):
    """获取所有交易日（升序）"""
    rows = conn.execute("""
        SELECT DISTINCT date FROM kline 
        WHERE date >= ? AND date <= ?
          AND code LIKE '600%'
        ORDER BY date
    """, (START_DATE, END_DATE)).fetchall()
    return [r[0] for r in rows]


def get_daily_market_stats(conn, date):
    """计算单个交易日的大盘环境指标（仅主板股票）"""
    rows = conn.execute("""
        SELECT code, open, close 
        FROM kline 
        WHERE date = ? 
          AND (code LIKE '600%' OR code LIKE '601%' OR code LIKE '603%' OR code LIKE '605%'
               OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
    """, (date,)).fetchall()

    if not rows:
        return None

    total = len(rows)
    up_count = 0
    limit_up_count = 0
    rets = []

    for code, open_p, close_p in rows:
        if open_p <= 0 or close_p <= 0:
            continue
        ret = round((close_p - open_p) / open_p * 100, 4)
        rets.append(ret)
        if ret > 0:
            up_count += 1
        # 涨停判断: 主板涨停10%，考虑ST(5%)和准涨停>=9.5%
        if ret >= 9.5:
            limit_up_count += 1

    if not rets:
        return None

    avg_ret = round(sum(rets) / len(rets), 4)
    sorted_rets = sorted(rets)
    med_ret = sorted_rets[len(sorted_rets) // 2]
    up_ratio = round(up_count / total, 4)
    limit_up_ratio = round(limit_up_count / total, 4)

    return {
        'date': date,
        'total': total,
        'up_count': up_count,
        'limit_up_count': limit_up_count,
        'up_ratio': up_ratio,
        'limit_up_ratio': limit_up_ratio,
        'avg_ret': avg_ret,
        'med_ret': med_ret,
    }


def get_prev_close(conn, code, date):
    """获取某只股票前一天的收盘价"""
    row = conn.execute("""
        SELECT close FROM kline 
        WHERE code = ? AND date < ? AND date >= ?
        ORDER BY date DESC LIMIT 1
    """, (code, date, START_DATE)).fetchone()
    return float(row[0]) if row else None


def get_next_day_kline(conn, code, date):
    """获取T+1日K线"""
    row = conn.execute("""
        SELECT date, open, close, high, low 
        FROM kline 
        WHERE code = ? AND date > ? 
        ORDER BY date LIMIT 1
    """, (code, date)).fetchone()
    return row


def classify_env(up_ratio):
    """将大盘环境分为5档"""
    if up_ratio < 0.25:
        return "冰点", 1
    elif up_ratio < 0.40:
        return "弱势", 2
    elif up_ratio < 0.55:
        return "震荡", 3
    elif up_ratio < 0.70:
        return "发酵", 4
    else:
        return "高潮", 5


def classify_env_from_avg(avg_ret):
    """用平均涨跌幅分类环境（辅助）"""
    if avg_ret < -1.5:
        return "冰点", 1
    elif avg_ret < -0.3:
        return "弱势", 2
    elif avg_ret < 0.3:
        return "震荡", 3
    elif avg_ret < 1.5:
        return "发酵", 4
    else:
        return "高潮", 5


# ====================================================================
# 维度1: 主要矛盾识别准确率
# ====================================================================
def run_dim1(conn, dates, date_stats):
    """维度1: 大盘环境对T日涨停股T+1收益的预测力"""
    print("\n" + "=" * 80)
    print("📊 维度1: 主要矛盾识别准确率（大盘环境预测力）")
    print("=" * 80)

    # 用涨跌比分5档，统计每档下涨停股次日表现
    env_groups = defaultdict(lambda: {
        'samples': 0, 'total_ret': 0.0, 'wins': 0,
        'total_close_ret': 0.0, 'close_wins': 0
    })

    total_limit_stocks = 0
    processed_dates = 0

    for date in dates:
        stats = date_stats.get(date)
        if not stats:
            continue

        env_name, env_level = classify_env(stats['up_ratio'])

        # 找到当日所有主板涨停股
        limit_rows = conn.execute("""
            SELECT code, open, close 
            FROM kline 
            WHERE date = ? 
              AND (code LIKE '600%' OR code LIKE '601%' OR code LIKE '603%' OR code LIKE '605%'
                   OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
        """, (date,)).fetchall()

        for code, open_p, close_p in limit_rows:
            if open_p <= 0 or close_p <= 0:
                continue
            day_ret = round((close_p - open_p) / open_p * 100, 4)
            if day_ret < 9.5:
                continue  # 非涨停

            # 有涨停才有所谓"矛盾"
            prev_close = get_prev_close(conn, code, date)
            if prev_close is None or prev_close <= 0:
                continue

            # 涨停买入价 = 前收盘 * 1.10
            buy_price = round(prev_close * 1.10, 2)

            # T+1 K线
            t1 = get_next_day_kline(conn, code, date)
            if t1 is None:
                continue

            t1_date, t1_open, t1_close, t1_high, t1_low = t1
            t1_open = float(t1_open)
            t1_close = float(t1_close)

            # 开盘收益（如果有溢价）
            open_ret = round((t1_open - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0
            close_ret = round((t1_close - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0

            env_groups[(env_name, env_level)]['samples'] += 1
            env_groups[(env_name, env_level)]['total_ret'] += open_ret
            env_groups[(env_name, env_level)]['total_close_ret'] += close_ret
            if open_ret > 0:
                env_groups[(env_name, env_level)]['wins'] += 1
            if close_ret > 0:
                env_groups[(env_name, env_level)]['close_wins'] += 1

            total_limit_stocks += 1

        processed_dates += 1

    # 输出结果
    results = []
    print(f"\n总交易日: {processed_dates} | 总涨停样本: {total_limit_stocks}")
    print(f"{'环境':<8} {'样本数':<8} {'开盘均收%':<12} {'开盘胜率%':<12} {'收盘均收%':<12} {'收盘胜率%':<12} {'盈亏比':<10}")
    print("-" * 74)

    for (env_name, env_level) in sorted(env_groups.keys(), key=lambda x: x[1]):
        g = env_groups[(env_name, env_level)]
        n = g['samples']
        avg_open_ret = round(g['total_ret'] / n, 2) if n > 0 else 0
        avg_close_ret = round(g['total_close_ret'] / n, 2) if n > 0 else 0
        win_rate = round(g['wins'] / n * 100, 1) if n > 0 else 0
        close_win_rate = round(g['close_wins'] / n * 100, 1) if n > 0 else 0

        # 盈亏比 = 平均盈利/平均亏损
        win_rets = []
        loss_rets = []
        # 简化: 用市场整体统计
        profit_loss_ratio = 0
        if g['close_wins'] > 0 and (n - g['close_wins']) > 0:
            avg_win = g['total_close_ret'] / g['close_wins'] if g['close_wins'] > 0 else 0
            avg_loss = abs(g['total_close_ret'] - avg_close_ret * g['close_wins']) / (n - g['close_wins']) if (n - g['close_wins']) > 0 else 1
            if avg_loss > 0:
                profit_loss_ratio = round(abs(avg_win) / avg_loss, 2)

        results.append({
            'env': env_name,
            'samples': n,
            'avg_open_ret': avg_open_ret,
            'open_win_rate': win_rate,
            'avg_close_ret': avg_close_ret,
            'close_win_rate': close_win_rate,
            'profit_loss_ratio': profit_loss_ratio,
        })

        print(f"{env_name:<8} {n:<8} {avg_open_ret:>+8.2f}%  {win_rate:>7.1f}%   {avg_close_ret:>+8.2f}%  {close_win_rate:>7.1f}%   {profit_loss_ratio:<8}")

    return results


# ====================================================================
# 维度2: 各层矛盾方向一致性
# ====================================================================
def run_dim2(conn, dates, date_stats):
    """维度2: 用全市场加权涨幅 vs 中位数涨幅 vs 涨停数推断方向一致性"""
    print("\n" + "=" * 80)
    print("📊 维度2: 各层矛盾方向一致性")
    print("=" * 80)

    # 对每个交易日判断一致性类型
    consistency_groups = defaultdict(lambda: {
        'samples': 0, 'total_ret': 0.0, 'wins': 0,
        'total_close_ret': 0.0, 'close_wins': 0
    })

    total_limit_stocks = 0
    concurrency_stats = defaultdict(int)

    for date in dates:
        stats = date_stats.get(date)
        if not stats:
            continue

        avg_ret = stats['avg_ret']
        up_ratio = stats['up_ratio']

        # 判断方向一致性
        # up_ratio是涨跌比(0~1)，要计算涨跌比=上涨数/下跌数
        total = stats['total']
        up_count = stats['up_count']
        down_count = total - up_count
        up_down_ratio = up_count / down_count if down_count > 0 else 999.0

        if avg_ret > 0 and up_down_ratio > 1.5:
            consistency = "一致偏多"
            concurrency = 1
        elif avg_ret > 0 and up_down_ratio < 1.0:
            consistency = "方向分歧(权重拉)"
            concurrency = 2
        elif avg_ret < 0 and up_down_ratio < 0.5:
            consistency = "一致偏空"
            concurrency = 3
        elif avg_ret < 0 and up_down_ratio > 1.0:
            consistency = "方向分歧(权重砸)"
            concurrency = 4
        elif avg_ret > 0:
            consistency = "偏多震荡"
            concurrency = 5
        else:
            consistency = "偏空震荡"
            concurrency = 6

        concurrency_stats[consistency] += 1

        # 找到当日所有主板涨停股，验证T+1表现
        limit_rows = conn.execute("""
            SELECT code, open, close 
            FROM kline 
            WHERE date = ? 
              AND (code LIKE '600%' OR code LIKE '601%' OR code LIKE '603%' OR code LIKE '605%'
                   OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
        """, (date,)).fetchall()

        for code, open_p, close_p in limit_rows:
            if open_p <= 0 or close_p <= 0:
                continue
            day_ret = round((close_p - open_p) / open_p * 100, 4)
            if day_ret < 9.5:
                continue

            prev_close = get_prev_close(conn, code, date)
            if prev_close is None or prev_close <= 0:
                continue
            buy_price = round(prev_close * 1.10, 2)

            t1 = get_next_day_kline(conn, code, date)
            if t1 is None:
                continue

            t1_open = float(t1[1])
            t1_close = float(t1[2])
            open_ret = round((t1_open - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0
            close_ret = round((t1_close - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0

            consistency_groups[consistency]['samples'] += 1
            consistency_groups[consistency]['total_ret'] += open_ret
            consistency_groups[consistency]['total_close_ret'] += close_ret
            if open_ret > 0:
                consistency_groups[consistency]['wins'] += 1
            if close_ret > 0:
                consistency_groups[consistency]['close_wins'] += 1

            total_limit_stocks += 1

    # 输出结果
    results = []
    print(f"\n总涨停样本: {total_limit_stocks}")
    print(f"\n各一致性状态出现天数:")
    for con, cnt in sorted(concurrency_stats.items(), key=lambda x: -x[1]):
        print(f"  {con}: {cnt}天")

    print(f"\n{'方向状态':<18} {'样本数':<8} {'开盘均收%':<12} {'开盘胜率%':<12} {'收盘均收%':<12} {'收盘胜率%':<12}")
    print("-" * 74)

    for consistency in ['一致偏多', '方向分歧(权重拉)', '一致偏空', '方向分歧(权重砸)', '偏多震荡', '偏空震荡']:
        g = consistency_groups[consistency]
        n = g['samples']
        if n == 0:
            continue
        avg_open_ret = round(g['total_ret'] / n, 2)
        avg_close_ret = round(g['total_close_ret'] / n, 2)
        win_rate = round(g['wins'] / n * 100, 1)
        close_win_rate = round(g['close_wins'] / n * 100, 1)

        results.append({
            'consistency': consistency,
            'samples': n,
            'avg_open_ret': avg_open_ret,
            'open_win_rate': win_rate,
            'avg_close_ret': avg_close_ret,
            'close_win_rate': close_win_rate,
        })

        print(f"{consistency:<18} {n:<8} {avg_open_ret:>+8.2f}%  {win_rate:>7.1f}%   {avg_close_ret:>+8.2f}%  {close_win_rate:>7.1f}%")

    return results


# ====================================================================
# 维度3: 内因vs外因判断
# ====================================================================
def run_dim3(conn, lhb_conn, dates, date_stats):
    """维度3: 内因vs外因判断"""
    print("\n" + "=" * 80)
    print("📊 维度3: 内因vs外因判断")
    print("=" * 80)

    # 外因: 大盘环境（涨跌比、涨停数）
    # 内因: 龙虎榜机构参与度、连板数
    # 用lhb_list中的type字段判断机构参与

    # 先构建龙虎榜日期索引
    lhb_by_date = defaultdict(list)
    if lhb_conn:
        rows = lhb_conn.execute("""
            SELECT date, code, name, type, chg, price 
            FROM lhb_list 
            WHERE date >= ? AND date <= ?
        """, (START_DATE, END_DATE)).fetchall()
        for r in rows:
            lhb_by_date[r[0]].append(r)

    print(f"  龙虎榜数据: {sum(len(v) for v in lhb_by_date.values())}条, {len(lhb_by_date)}个交易日")

    # 内外因4类
    factor_groups = defaultdict(lambda: {
        'samples': 0, 'total_ret': 0.0, 'wins': 0,
        'total_close_ret': 0.0, 'close_wins': 0
    })

    total_samples = 0

    for date in dates:
        stats = date_stats.get(date)
        if not stats:
            continue

        # 外因强弱: 涨跌比>0.5且涨停比>0.02为强
        env_strong = stats['up_ratio'] > 0.5 and stats['limit_up_ratio'] > 0.02

        # 获取当日龙虎榜股票（视为"内因强"候选）
        lhb_stocks = lhb_by_date.get(date, [])
        lhb_codes = set(r[1] for r in lhb_stocks)

        # 获取当日涨停股
        limit_rows = conn.execute("""
            SELECT code, open, close 
            FROM kline 
            WHERE date = ? 
              AND (code LIKE '600%' OR code LIKE '601%' OR code LIKE '603%' OR code LIKE '605%'
                   OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
        """, (date,)).fetchall()

        for code, open_p, close_p in limit_rows:
            if open_p <= 0 or close_p <= 0:
                continue
            day_ret = round((close_p - open_p) / open_p * 100, 4)
            if day_ret < 9.5:
                continue

            # 内因强弱: 出现在龙虎榜视为机构/游资关注 = 内因强
            inner_strong = code in lhb_codes

            prev_close = get_prev_close(conn, code, date)
            if prev_close is None or prev_close <= 0:
                continue
            buy_price = round(prev_close * 1.10, 2)

            t1 = get_next_day_kline(conn, code, date)
            if t1 is None:
                continue

            t1_open = float(t1[1])
            t1_close = float(t1[2])
            open_ret = round((t1_open - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0
            close_ret = round((t1_close - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0

            # 分4类
            if env_strong and inner_strong:
                cat = "a)外因强+内因强"
            elif not env_strong and inner_strong:
                cat = "b)外因弱+内因强(最矛盾)"
            elif env_strong and not inner_strong:
                cat = "c)外因强+内因弱"
            else:
                cat = "d)外因弱+内因弱"

            factor_groups[cat]['samples'] += 1
            factor_groups[cat]['total_ret'] += open_ret
            factor_groups[cat]['total_close_ret'] += close_ret
            if open_ret > 0:
                factor_groups[cat]['wins'] += 1
            if close_ret > 0:
                factor_groups[cat]['close_wins'] += 1

            total_samples += 1

    # 输出结果
    results = []
    print(f"\n总涨停样本(有龙虎榜标签): {total_samples}")
    print(f"\n{'类别':<28} {'样本数':<8} {'开盘均收%':<12} {'开盘胜率%':<12} {'收盘均收%':<12} {'收盘胜率%':<12}")
    print("-" * 80)

    cat_order = [
        "a)外因强+内因强",
        "b)外因弱+内因强(最矛盾)",
        "c)外因强+内因弱",
        "d)外因弱+内因弱",
    ]

    for cat in cat_order:
        g = factor_groups[cat]
        n = g['samples']
        if n == 0:
            continue
        avg_open_ret = round(g['total_ret'] / n, 2)
        avg_close_ret = round(g['total_close_ret'] / n, 2)
        win_rate = round(g['wins'] / n * 100, 1)
        close_win_rate = round(g['close_wins'] / n * 100, 1)

        results.append({
            'category': cat,
            'samples': n,
            'avg_open_ret': avg_open_ret,
            'open_win_rate': win_rate,
            'avg_close_ret': avg_close_ret,
            'close_win_rate': close_win_rate,
        })

        print(f"{cat:<28} {n:<8} {avg_open_ret:>+8.2f}%  {win_rate:>7.1f}%   {avg_close_ret:>+8.2f}%  {close_win_rate:>7.1f}%")

    return results


# ====================================================================
# 维度4: 开盘vs昨收矛盾方向变化
# ====================================================================
def run_dim4(conn, dates, date_stats):
    """维度4: 开盘vs昨收矛盾方向变化"""
    print("\n" + "=" * 80)
    print("📊 维度4: 开盘vs昨收矛盾方向变化")
    print("=" * 80)

    # 用T日开盘价 vs T-1日收盘价模拟竞价变化
    gap_groups = defaultdict(lambda: {
        'samples': 0, 'total_ret': 0.0, 'wins': 0,
        'total_close_ret': 0.0, 'close_wins': 0
    })

    total_limit_stocks = 0

    for date in dates:
        stats = date_stats.get(date)
        if not stats:
            continue

        # 获取当日所有主板涨停股
        limit_rows = conn.execute("""
            SELECT code, open, close 
            FROM kline 
            WHERE date = ? 
              AND (code LIKE '600%' OR code LIKE '601%' OR code LIKE '603%' OR code LIKE '605%'
                   OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
        """, (date,)).fetchall()

        for code, open_p, close_p in limit_rows:
            if open_p <= 0 or close_p <= 0:
                continue
            day_ret = round((close_p - open_p) / open_p * 100, 4)
            if day_ret < 9.5:
                continue

            # T日开盘价 vs T-1日收盘价的竞价变化
            prev_close = get_prev_close(conn, code, date)
            if prev_close is None or prev_close <= 0:
                continue

            gap_pct = round((open_p - prev_close) / prev_close * 100, 2)

            # 分类竞价变化
            if gap_pct > 3:
                gap_type = "高开>3%(多方延续强化)"
            elif gap_pct > 0:
                gap_type = "高开0~3%(多方延续弱化)"
            elif gap_pct > -3:
                gap_type = "低开0~-3%(空方反击)"
            else:
                gap_type = "低开>3%(矛盾转化)"

            buy_price = round(prev_close * 1.10, 2)

            t1 = get_next_day_kline(conn, code, date)
            if t1 is None:
                continue

            t1_open = float(t1[1])
            t1_close = float(t1[2])
            open_ret = round((t1_open - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0
            close_ret = round((t1_close - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0

            gap_groups[gap_type]['samples'] += 1
            gap_groups[gap_type]['total_ret'] += open_ret
            gap_groups[gap_type]['total_close_ret'] += close_ret
            if open_ret > 0:
                gap_groups[gap_type]['wins'] += 1
            if close_ret > 0:
                gap_groups[gap_type]['close_wins'] += 1

            total_limit_stocks += 1

    # 输出结果
    results = []
    print(f"\n总涨停样本: {total_limit_stocks}")
    print(f"\n{'竞价类型':<28} {'样本数':<8} {'开盘均收%':<12} {'开盘胜率%':<12} {'收盘均收%':<12} {'收盘胜率%':<12}")
    print("-" * 80)

    gap_order = [
        "高开>3%(多方延续强化)",
        "高开0~3%(多方延续弱化)",
        "低开0~-3%(空方反击)",
        "低开>3%(矛盾转化)",
    ]

    for gap_type in gap_order:
        g = gap_groups[gap_type]
        n = g['samples']
        if n == 0:
            continue
        avg_open_ret = round(g['total_ret'] / n, 2)
        avg_close_ret = round(g['total_close_ret'] / n, 2)
        win_rate = round(g['wins'] / n * 100, 1)
        close_win_rate = round(g['close_wins'] / n * 100, 1)

        results.append({
            'gap_type': gap_type,
            'samples': n,
            'avg_open_ret': avg_open_ret,
            'open_win_rate': win_rate,
            'avg_close_ret': avg_close_ret,
            'close_win_rate': close_win_rate,
        })

        print(f"{gap_type:<28} {n:<8} {avg_open_ret:>+8.2f}%  {win_rate:>7.1f}%   {avg_close_ret:>+8.2f}%  {close_win_rate:>7.1f}%")

    return results


# ====================================================================
# 主函数
# ====================================================================
def main():
    print("=" * 80)
    print("📊 矛盾论维度1-4 完整回测")
    print(f"   时间范围: {START_DATE} ~ {END_DATE}")
    print(f"   启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 连接数据库
    print("\n📦 连接数据库...")
    conn = connect_db(KLINE_DB)
    if not conn:
        print("❌ 无法连接kline_cache.db，退出")
        sys.exit(1)

    lhb_conn = connect_db(LHB_DB)

    # 获取交易日列表
    print("📅 获取交易日...")
    dates = get_trading_dates(conn)
    print(f"   交易日数: {len(dates)}")

    # 预计算每日大盘统计数据（只用主板）
    print("📈 预计算每日大盘环境指标...")
    date_stats = {}
    for i, date in enumerate(dates):
        stats = get_daily_market_stats(conn, date)
        if stats:
            date_stats[date] = stats
        if (i + 1) % 100 == 0:
            print(f"   进度: {i+1}/{len(dates)}")
    print(f"   完成: {len(date_stats)}个交易日有有效数据")

    # 运行4个维度
    all_results = {}

    print("\n" + "=" * 80)
    print("🟦 开始维度1回测...")
    all_results['dim1'] = run_dim1(conn, dates, date_stats)

    print("\n" + "=" * 80)
    print("🟩 开始维度2回测...")
    all_results['dim2'] = run_dim2(conn, dates, date_stats)

    print("\n" + "=" * 80)
    print("🟨 开始维度3回测...")
    all_results['dim3'] = run_dim3(conn, lhb_conn, dates, date_stats)

    print("\n" + "=" * 80)
    print("🟥 开始维度4回测...")
    all_results['dim4'] = run_dim4(conn, dates, date_stats)

    # 输出JSON
    output_data = {
        'meta': {
            'start_date': START_DATE,
            'end_date': END_DATE,
            'total_trading_days': len(dates),
            'total_stocks': 3196,  # 主板股票数
            'generated_at': datetime.now().isoformat(),
        },
        'results': all_results,
    }

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 80}")
    print(f"✅ 回测完成！结果已保存至: {OUTPUT}")
    print(f"{'=' * 80}")

    conn.close()
    if lhb_conn:
        lhb_conn.close()


if __name__ == '__main__':
    main()
