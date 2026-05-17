#!/usr/bin/env python3
"""
矛盾论未覆盖维度回测 — F2/F3/I1/I3
======================================
从kline_cache.db + env_daily_history.json + contradiction_predict_results.json
回测4个未覆盖理论维度，输出JSON+中文报告。

维度:
  F2: 量变临界点细化 (涨停数5日变化率7档)
  F3: 连续量变 vs 跳跃式量变
  I1: 急降vs缓降后收益 (从env_daily_history)
  I3: 切换后收益衰减曲线 (从contradiction_predict_results.json已有切换事件)
"""
import sqlite3, os, json, time, math
from datetime import datetime
from collections import defaultdict

HOME = os.path.expanduser("~")
KLINE_DB = os.path.join(HOME, "astock/data/kline_cache.db")
ENV_HISTORY = os.path.join(HOME, "astock/data/env_daily_history.json")
SWITCH_FILE = os.path.join(HOME, "astock/data/contradiction_predict_results.json")
OUTPUT = os.path.join(HOME, "astock/data/maodun_f2f3i1i3.json")

START_DATE = "2024-01-01"
END_DATE = "2026-05-15"


def get_buy_price(conn, code, date, close):
    """计算涨停买入价"""
    prev = conn.execute("""
        SELECT close FROM kline WHERE code=? AND date<? ORDER BY date DESC LIMIT 1
    """, (code, date)).fetchone()
    if prev and close >= prev['close'] * 1.095:
        return round(prev['close'] * 1.10, 2)
    return float(close)


def calc_t1_close_ret(conn, code, date, buy_price, end_date):
    """计算T+1收盘收益率"""
    row = conn.execute("""
        SELECT close FROM kline WHERE code=? AND date>? AND date<=? ORDER BY date LIMIT 1
    """, (code, date, end_date)).fetchone()
    if not row:
        return None
    return round((row['close'] - buy_price) / buy_price * 100, 2)


def calc_tn_close_rets(conn, code, date, buy_price, end_date, days=5):
    """计算T+1到T+days的逐日收盘收益率，返回list"""
    rows = conn.execute("""
        SELECT close FROM kline WHERE code=? AND date>? AND date<=? ORDER BY date LIMIT ?
    """, (code, date, end_date, days)).fetchall()
    rets = []
    for r in rows:
        ret = round((r['close'] - buy_price) / buy_price * 100, 2)
        rets.append(ret)
    return rets


def main():
    t0 = time.time()
    print("=" * 70)
    print("矛盾论未覆盖维度回测 — F2/F3/I1/I3")
    print(f"数据: {START_DATE} ~ {END_DATE}")
    print("=" * 70)

    conn = sqlite3.connect(KLINE_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # ===================================================================
    # 加载环境数据
    # ===================================================================
    print("\n[0/4] 加载数据...")
    with open(ENV_HISTORY) as f:
        env_data = json.load(f)
    env_daily = env_data['daily']  # date -> dict
    env_dates = sorted(env_daily.keys())
    print(f"  ✅ 环境历史: {len(env_dates)}天")

    # 加载切换事件
    with open(SWITCH_FILE) as f:
        switch_data = json.load(f)
    # 从已有结果中提取切换事件
    switch_events = switch_data.get('switch_stats', {})
    print(f"  ✅ 切换事件: {switch_events.get('total', 0)}次 (升级{switch_events.get('upgrades', 0)}, 降级{switch_events.get('downgrades', 0)})")

    # ===================================================================
    # 获取每日涨停数据 from kline_cache.db
    # ===================================================================
    print("\n  获取每日涨停数据...")
    t1 = time.time()

    daily_limit_rows = conn.execute(f"""
        SELECT date, COUNT(*) as total_limit, SUM(volume) as total_vol
        FROM kline
        WHERE date >= ? AND date <= ?
          AND (code LIKE '6%' OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
          AND close >= open * 1.095
        GROUP BY date
        ORDER BY date
    """, (START_DATE, END_DATE)).fetchall()

    all_dates = [r['date'] for r in daily_limit_rows]
    daily_limits = {}
    for r in daily_limit_rows:
        daily_limits[r['date']] = {
            'total_limit': r['total_limit'],
            'total_vol': r['total_vol'],
        }

    # 也拿涨停股票列表
    limit_stocks_by_date = defaultdict(list)
    limit_rows = conn.execute(f"""
        SELECT date, code, close, volume
        FROM kline
        WHERE date >= ? AND date <= ?
          AND (code LIKE '6%' OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
          AND close >= open * 1.095
        ORDER BY date, code
    """, (START_DATE, END_DATE)).fetchall()
    for r in limit_rows:
        limit_stocks_by_date[r['date']].append(r)

    print(f"  ✅ {len(all_dates)}个交易日有涨停 ({time.time()-t1:.1f}s)")

    # 建立日线索引map，方便查日期前后
    date_index_map = {d: i for i, d in enumerate(all_dates)}

    # ===================================================================
    # 维度F2: 量变临界点细化
    # ===================================================================
    print("\n" + "=" * 70)
    print("📊 维度F2: 量变临界点细化")
    print("  将涨停数5日变化率从-70%~+70%分7个档位")
    print("  统计各档位的次日主线升级率/降级率/净差")
    print("=" * 70)

    # 先计算每日主线score（复用contradiction_predict_backtest.py的逻辑）
    daily_mainline = {}
    for i, d in enumerate(all_dates):
        data = daily_limits[d]
        n = data['total_limit']

        window = all_dates[max(0, i - 4):i + 1]
        ma5 = sum(daily_limits[wd]['total_limit'] for wd in window) / len(window) if window else n

        window10 = all_dates[max(0, i - 9):i + 1]
        ma10 = sum(daily_limits[wd]['total_limit'] for wd in window10) / len(window10) if window10 else n

        vol_ma5 = sum(daily_limits[wd]['total_vol'] or 0 for wd in window) / len(window) if window else 0
        vol_ratio = (data['total_vol'] / vol_ma5) if vol_ma5 > 0 else 1.0

        limit_ratio = n / ma5 if ma5 > 0 else 1.0

        # 主线强度分
        if n >= 80:
            score_n = 35
        elif n >= 50:
            score_n = 25
        elif n >= 30:
            score_n = 15
        elif n >= 15:
            score_n = 8
        else:
            score_n = 3

        if limit_ratio >= 1.3:
            score_trend = 30
        elif limit_ratio >= 1.1:
            score_trend = 20
        elif limit_ratio >= 0.9:
            score_trend = 10
        elif limit_ratio >= 0.7:
            score_trend = 5
        else:
            score_trend = 0

        if vol_ratio >= 1.3:
            score_vol = 20
        elif vol_ratio >= 1.1:
            score_vol = 15
        elif vol_ratio >= 0.9:
            score_vol = 10
        elif vol_ratio >= 0.7:
            score_vol = 5
        else:
            score_vol = 0

        if ma5 >= ma10 * 1.1:
            score_stable = 15
        elif ma5 >= ma10:
            score_stable = 10
        elif ma5 >= ma10 * 0.9:
            score_stable = 5
        else:
            score_stable = 0

        mainline_score = score_n + score_trend + score_vol + score_stable

        if mainline_score >= 70:
            status = "主线确认"
        elif mainline_score >= 45:
            status = "主线形成中"
        elif mainline_score >= 25:
            status = "题材轮动"
        else:
            status = "主线缺失"

        daily_mainline[d] = {
            'date': d,
            'total_limit': n,
            'ma5': round(ma5, 1),
            'score': mainline_score,
            'status': status,
        }

    t1 = time.time()

    # F2: 7档位定义
    buckets = [
        (-70, -50, "≤-50%"),
        (-50, -30, "-50%~-30%"),
        (-30, -10, "-30%~-10%"),
        (-10, 10, "-10%~+10%"),
        (10, 30, "+10%~+30%"),
        (30, 50, "+30%~+50%"),
        (50, 70, "≥+50%"),
    ]

    f2_stats = []

    for lo, hi, label in buckets:
        bucket_data = {'samples': 0, 'upgrades': 0, 'downgrades': 0}

        for i in range(9, len(all_dates)):
            d = all_dates[i]
            # 5日涨停数变化率: 近5日 vs 前5日
            n5 = sum(daily_limits[all_dates[j]]['total_limit'] for j in range(i - 4, i + 1))
            n5_prev = sum(daily_limits[all_dates[j]]['total_limit'] for j in range(i - 9, i - 4))

            if n5_prev == 0:
                continue
            change_rate = (n5 - n5_prev) / n5_prev * 100

            # 判断在哪个档位
            if lo == -70 and change_rate <= -50:
                pass  # matches
            elif hi == 70 and change_rate >= 50:
                pass  # matches
            elif lo < change_rate <= hi:
                pass  # matches
            else:
                continue

            bucket_data['samples'] += 1

            # 次日主线变化
            if i + 1 < len(all_dates):
                next_md = daily_mainline[all_dates[i + 1]]
                curr_md = daily_mainline[d]
                diff = next_md['score'] - curr_md['score']
                if diff >= 15:
                    bucket_data['upgrades'] += 1
                elif diff <= -15:
                    bucket_data['downgrades'] += 1

        n = bucket_data['samples']
        if n > 0:
            up_rate = round(bucket_data['upgrades'] / n * 100, 1)
            down_rate = round(bucket_data['downgrades'] / n * 100, 1)
            net = round(up_rate - down_rate, 1)
        else:
            up_rate = 0
            down_rate = 0
            net = 0

        f2_stats.append({
            'bucket': label,
            'range': f"{lo}%~{hi}%",
            'samples': n,
            'upgrade_rate': up_rate,
            'downgrade_rate': down_rate,
            'net_diff': net,
        })

        print(f"  {label:<16} {n:>5}次  升级{up_rate:>5.1f}%  降级{down_rate:>5.1f}%  净差{net:>+5.1f}%")

    # 找最佳临界点
    best = max(f2_stats, key=lambda x: x['net_diff'])
    print(f"\n  🏆 最佳临界点: {best['bucket']} (净差{best['net_diff']:+.1f}%, 样本{best['samples']}次)")

    f2_result = {
        'dimension': 'F2',
        'name': '量变临界点细化',
        'description': '涨停数5日变化率7档位, 统计次日主线升级率/降级率/净差',
        'best_threshold': best['bucket'],
        'best_net_diff': best['net_diff'],
        'buckets': f2_stats,
    }

    print(f"  ⏱ {time.time()-t1:.1f}s")

    # ===================================================================
    # 维度F3: 连续量变 vs 跳跃式量变
    # ===================================================================
    print("\n" + "=" * 70)
    print("📊 维度F3: 连续量变 vs 跳跃式量变")
    print("  跳跃式量变: 3日内涨停数变化率超±50%")
    print("  连续量变: 3日内变化率±15%以内但累计5日达±30%")
    print("  统计后续3天涨停T+1收益")
    print("=" * 70)

    t1 = time.time()

    f3_jump_3d_rets = []  # 跳跃式: [T+1, T+2, T+3] 收益list of lists
    f3_continuous_3d_rets = []
    f3_jump_t1_rets = []
    f3_continuous_t1_rets = []

    for i in range(11, len(all_dates)):
        d = all_dates[i]

        # 3日变化率
        n3 = sum(daily_limits[all_dates[j]]['total_limit'] for j in range(i - 2, i + 1))
        n3_prev = sum(daily_limits[all_dates[j]]['total_limit'] for j in range(i - 5, i - 2))
        change_3d = (n3 - n3_prev) / n3_prev * 100 if n3_prev > 0 else 0

        # 5日累计变化率
        n5 = sum(daily_limits[all_dates[j]]['total_limit'] for j in range(i - 4, i + 1))
        n5_prev = sum(daily_limits[all_dates[j]]['total_limit'] for j in range(i - 9, i - 4))
        change_5d = (n5 - n5_prev) / n5_prev * 100 if n5_prev > 0 else 0

        is_jump = abs(change_3d) >= 50
        is_continuous = abs(change_3d) <= 15 and abs(change_5d) >= 30

        # 对当日涨停股计算后续3天T+1收益
        stocks_today = limit_stocks_by_date.get(d, [])
        for st in stocks_today:
            code = st['code']
            buy_price = get_buy_price(conn, code, d, st['close'])

            # 取后续3天的T+1收益
            rets = calc_tn_close_rets(conn, code, d, buy_price, END_DATE, days=3)

            if len(rets) >= 3:
                if is_jump:
                    f3_jump_3d_rets.append(rets)
                    f3_jump_t1_rets.append(rets[0])
                if is_continuous:
                    f3_continuous_3d_rets.append(rets)
                    f3_continuous_t1_rets.append(rets[0])

    # 统计
    def stats_3d(rets_list, label):
        if not rets_list:
            print(f"  {label}: 无样本")
            return {'samples': 0, 't1_avg': 0, 't1_win': 0, 't2_avg': 0, 't3_avg': 0}
        n = len(rets_list)
        t1_rets = [r[0] for r in rets_list]
        t2_rets = [r[1] for r in rets_list]
        t3_rets = [r[2] for r in rets_list]
        t1_avg = round(sum(t1_rets) / n, 2)
        t2_avg = round(sum(t2_rets) / n, 2)
        t3_avg = round(sum(t3_rets) / n, 2)
        t1_wins = sum(1 for r in t1_rets if r > 0)
        t1_wr = round(t1_wins / n * 100, 1) if n else 0
        print(f"  {label:<20} 样本{n:>5}  T+1:{t1_avg:>+6.2f}%({t1_wr:>5.1f}%)  T+2:{t2_avg:>+6.2f}%  T+3:{t3_avg:>+6.2f}%")
        return {
            'samples': n,
            't1_avg_ret': t1_avg,
            't1_win_rate': t1_wr,
            't2_avg_ret': t2_avg,
            't3_avg_ret': t3_avg,
        }

    print(f"  跳跃式量变: {len(f3_jump_t1_rets)}个样本")
    jump_stats = stats_3d(f3_jump_3d_rets, "跳跃式量变")
    print(f"  连续式量变: {len(f3_continuous_t1_rets)}个样本")
    cont_stats = stats_3d(f3_continuous_3d_rets, "连续式量变")

    # 对比分析
    if jump_stats['samples'] > 0 and cont_stats['samples'] > 0:
        diff = round(jump_stats['t1_avg_ret'] - cont_stats['t1_avg_ret'], 2)
        print(f"\n  📊 跳跃vs连续 T+1收益差: {diff:+.2f}%")
        if diff > 0:
            print(f"  → 跳跃式量变后收益更优 (胜出{diff:+.2f}%)")
        else:
            print(f"  → 连续式量变后收益更优 (胜出{-diff:+.2f}%)")

    f3_result = {
        'dimension': 'F3',
        'name': '连续量变 vs 跳跃式量变',
        'description': '跳跃式=3日变化率超±50%, 连续式=3日±15%内但5日累计±30%',
        'jump': jump_stats,
        'continuous': cont_stats,
        'comparison': {
            't1_diff': round(jump_stats.get('t1_avg_ret', 0) - cont_stats.get('t1_avg_ret', 0), 2),
            'better': '跳跃式量变' if jump_stats.get('t1_avg_ret', 0) > cont_stats.get('t1_avg_ret', 0) else '连续式量变',
        },
    }

    print(f"  ⏱ {time.time()-t1:.1f}s")

    # ===================================================================
    # 维度I1: 急降vs缓降后收益
    # ===================================================================
    print("\n" + "=" * 70)
    print("📊 维度I1: 急降vs缓降后收益")
    print("  从env_daily_history.json提取环境分变化")
    print("  急降: 3日跌>20分 | 缓降: 3日跌10-20分")
    print("  急升: 3日涨>20分 | 缓升: 3日涨10-20分")
    print("  统计变化后1/3/5天涨停T+1收益")
    print("=" * 70)

    t1 = time.time()

    # 提取环境分序列
    env_scores = {}
    for d in env_dates:
        env_scores[d] = env_daily[d]['env_score']

    # 找出变化事件
    # 注意：env_dates可能包含非交易日，而all_dates只有交易日
    # 用env_dates的index，找涨停日对应
    env_categories = defaultdict(lambda: {
        't1_rets': [], 't3_rets': [], 't5_rets': []
    })

    for i in range(3, len(env_dates)):
        d = env_dates[i]
        d3_ago = env_dates[i - 3]  # 3个自然/交易日前
        score_now = env_scores[d]
        score_before = env_scores[d3_ago]
        change = score_now - score_before

        # 分类
        if change > 20:
            cat = "急升"
        elif change >= 10:
            cat = "缓升"
        elif change < -20:
            cat = "急降"
        elif change <= -10:
            cat = "缓降"
        else:
            continue  # 无显著变化

        # 找到这个日期在all_dates中的位置（涨停交易日）
        if d not in date_index_map:
            continue
        idx = date_index_map[d]

        # 对当日涨停股计算T+1/T+3/T+5收益
        stocks_today = limit_stocks_by_date.get(d, [])
        for st in stocks_today:
            code = st['code']
            buy_price = get_buy_price(conn, code, d, st['close'])
            rets = calc_tn_close_rets(conn, code, d, buy_price, END_DATE, days=5)
            if len(rets) >= 5:
                env_categories[cat]['t1_rets'].append(rets[0])
                env_categories[cat]['t3_rets'].append(sum(rets[:3]) / 3)
                env_categories[cat]['t5_rets'].append(sum(rets[:5]) / 5)

    i1_dim = []
    for cat in ["急升", "缓升", "急降", "缓降"]:
        data = env_categories[cat]
        n = len(data['t1_rets'])
        if n == 0:
            print(f"  {cat:<8} 无样本")
            i1_dim.append({'category': cat, 'samples': 0})
            continue

        t1_avg = round(sum(data['t1_rets']) / n, 2)
        t3_avg = round(sum(data['t3_rets']) / n, 2)
        t5_avg = round(sum(data['t5_rets']) / n, 2)
        t1_wins = sum(1 for r in data['t1_rets'] if r > 0)
        t1_wr = round(t1_wins / n * 100, 1)

        print(f"  {cat:<8} 样本{n:>5}  T+1:{t1_avg:>+6.2f}%({t1_wr:>5.1f}%)  T+3:{t3_avg:>+6.2f}%  T+5:{t5_avg:>+6.2f}%")

        i1_dim.append({
            'category': cat,
            'samples': n,
            't1_avg_ret': t1_avg,
            't1_win_rate': t1_wr,
            't3_avg_ret': t3_avg,
            't5_avg_ret': t5_avg,
        })

    # 对比急降vs缓降
    jj = next((x for x in i1_dim if x.get('category') == '急降'), {})
    hj = next((x for x in i1_dim if x.get('category') == '缓降'), {})
    if jj.get('samples', 0) > 0 and hj.get('samples', 0) > 0:
        diff_t1 = round(jj.get('t1_avg_ret', 0) - hj.get('t1_avg_ret', 0), 2)
        print(f"\n  📊 急降vs缓降 T+1收益差: {diff_t1:+.2f}%")
        if diff_t1 > 0:
            print(f"  → 急降后涨停收益更高 (抄底资金更积极)")
        else:
            print(f"  → 缓降后涨停收益更高 (急降打击信心)")

    js = next((x for x in i1_dim if x.get('category') == '急升'), {})
    hs = next((x for x in i1_dim if x.get('category') == '缓升'), {})
    if js.get('samples', 0) > 0 and hs.get('samples', 0) > 0:
        diff_t1 = round(js.get('t1_avg_ret', 0) - hs.get('t1_avg_ret', 0), 2)
        print(f"  📊 急升vs缓升 T+1收益差: {diff_t1:+.2f}%")

    i1_result = {
        'dimension': 'I1',
        'name': '急降vs缓降后收益',
        'description': '急降=环境分3日跌>20, 缓降=3日跌10-20, 急升=3日涨>20, 缓升=3日涨10-20',
        'categories': i1_dim,
    }

    print(f"  ⏱ {time.time()-t1:.1f}s")

    # ===================================================================
    # 维度I3: 切换后收益衰减曲线
    # ===================================================================
    print("\n" + "=" * 70)
    print("📊 维度I3: 切换后收益衰减曲线")
    print("  从已有切换事件跟踪切换后第1/3/5/10天涨停数变化")
    print("  统计最优持有期")
    print("=" * 70)

    t1 = time.time()

    # 从daily_mainline重新提取切换事件（跟contradiction_predict_backtest.py一致）
    switch_events_detail = []
    prev_status = None
    prev_score = 0

    for d in all_dates:
        md = daily_mainline[d]
        if prev_status and md['status'] != prev_status:
            switch_events_detail.append({
                'from_date': all_dates[all_dates.index(d) - 1],
                'to_date': d,
                'from_status': prev_status,
                'to_status': md['status'],
                'from_score': prev_score,
                'to_score': md['score'],
            })
        prev_status = md['status']
        prev_score = md['score']

    print(f"  找到 {len(switch_events_detail)} 次切换事件")

    # 统计切换后第1/3/5/10天的涨停T+1收益
    i3_offsets = [1, 3, 5, 10]
    i3_stats = {offset: {'rets': []} for offset in i3_offsets}

    for e in switch_events_detail:
        switch_date = e['to_date']
        switch_idx = date_index_map.get(switch_date)
        if switch_idx is None:
            continue

        for offset in i3_offsets:
            target_idx = switch_idx + offset
            if target_idx >= len(all_dates):
                continue
            target_date = all_dates[target_idx]

            # 对切换后第offset天的涨停股计算T+1
            stocks = limit_stocks_by_date.get(target_date, [])
            for st in stocks:
                code = st['code']
                buy_price = get_buy_price(conn, code, target_date, st['close'])
                ret = calc_t1_close_ret(conn, code, target_date, buy_price, END_DATE)
                if ret is not None:
                    i3_stats[offset]['rets'].append(ret)

    i3_dim = []
    for offset in i3_offsets:
        rets = i3_stats[offset]['rets']
        n = len(rets)
        if n > 0:
            avg_ret = round(sum(rets) / n, 2)
            wins = sum(1 for r in rets if r > 0)
            wr = round(wins / n * 100, 1)
        else:
            avg_ret = 0
            wr = 0
        print(f"  切换后第{offset:>2}天: 样本{n:>5}  均收{avg_ret:>+7.2f}%  胜率{wr:>6.1f}%")
        i3_dim.append({
            'offset': offset,
            'label': f'切换后第{offset}天',
            'samples': n,
            'avg_ret': avg_ret,
            'win_rate': wr,
        })

    # 找最优持有期
    valid = [x for x in i3_dim if x['samples'] > 0]
    if valid:
        best_ret = max(valid, key=lambda x: x['avg_ret'])
        best_wr = max(valid, key=lambda x: x['win_rate'])
        print(f"\n  🏆 最优收益持有期: 切换后第{best_ret['offset']}天 (均收{best_ret['avg_ret']:+.2f}%)")
        print(f"  🏆 最优胜率持有期: 切换后第{best_wr['offset']}天 (胜率{best_wr['win_rate']:.1f}%)")

        # 衰减判断
        if len(valid) >= 2:
            first = valid[0]['avg_ret']
            last = valid[-1]['avg_ret']
            decay = first - last
            print(f"  收益衰减: 第1天{first:+.2f}% → 第{valid[-1]['offset']}天{last:+.2f}% (衰减{decay:+.2f}%)")
    else:
        best_ret = {}
        best_wr = {}

    i3_result = {
        'dimension': 'I3',
        'name': '切换后收益衰减曲线',
        'description': '跟踪主线切换后第1/3/5/10天的涨停T+1收益',
        'total_switches': len(switch_events_detail),
        'decay_curve': i3_dim,
        'optimal_retention': {
            'best_return': f"第{best_ret.get('offset', '?')}天" if best_ret else '无数据',
            'best_return_value': best_ret.get('avg_ret', 0) if best_ret else 0,
            'best_win_rate': f"第{best_wr.get('offset', '?')}天" if best_wr else '无数据',
            'best_win_rate_value': best_wr.get('win_rate', 0) if best_wr else 0,
        },
    }

    print(f"  ⏱ {time.time()-t1:.1f}s")

    # ===================================================================
    # 汇总输出
    # ===================================================================
    output = {
        'meta': {
            'script': '矛盾论未覆盖维度回测 v1.0 — F2/F3/I1/I3',
            'generated_at': datetime.now().isoformat(),
            'date_range': f'{START_DATE} ~ {END_DATE}',
            'total_trading_days': len(all_dates),
            'elapsed_seconds': round(time.time() - t0, 1),
        },
        'dimensions': {
            'F2': f2_result,
            'F3': f3_result,
            'I1': i1_result,
            'I3': i3_result,
        },
    }

    with open(OUTPUT, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 70}")
    print(f"✅ 完成！{round(time.time() - t0, 1)}s  已保存到 {OUTPUT}")
    print(f"{'=' * 70}")

    conn.close()


if __name__ == '__main__':
    main()
