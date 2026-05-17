#!/usr/bin/env python3
"""
矛盾论未覆盖维度回测 — H1/J3/I2/G3
======================================
从kline_cache.db + lhb_cache.db + env_daily_history.json
回测4个未覆盖理论维度，输出JSON+中文报告。

维度:
  H1: 一字板 vs 自然涨停 (T+1收益/胜率/开盘溢价)
  J3: 游资/机构/量化三方合力 (按资金类型组合分组)
  I2: 内部因素vs外部因素的驱动力 (env_score变化+量能判断)
  G3: 跌破首板的后续表现 (炸板回封)
"""

import sqlite3, os, json, time
from datetime import datetime
from collections import defaultdict

HOME = os.path.expanduser("~")
KLINE_DB = os.path.join(HOME, "astock/data/kline_cache.db")
LHB_DB = os.path.join(HOME, "astock/data/lhb_cache.db")
ENV_HISTORY = os.path.join(HOME, "astock/data/env_daily_history.json")
OUTPUT = os.path.join(HOME, "astock/data/maodun_h1j3i2g3.json")

START_DATE = "2024-01-01"
END_DATE = "2026-05-15"

# ─── 主板过滤 ───
def is_master(code):
    return code.startswith(('6', '000', '001', '002', '003'))

# ─── 辅助函数 ───

def get_prev_close(conn, code, date):
    """获取前收盘价"""
    row = conn.execute("""
        SELECT close FROM kline WHERE code=? AND date<? ORDER BY date DESC LIMIT 1
    """, (code, date)).fetchone()
    return row['close'] if row else None

def get_buy_price(conn, code, date, close):
    """涨停买入价 => 前收*1.10 四舍五入到分"""
    prev = get_prev_close(conn, code, date)
    if prev and close >= prev * 1.095:
        return round(prev * 1.10, 2)
    return float(close)

def calc_t1_close_ret(conn, code, date, buy_price, end_date):
    """T+1收盘收益%"""
    row = conn.execute("""
        SELECT close FROM kline WHERE code=? AND date>? AND date<=? ORDER BY date LIMIT 1
    """, (code, date, end_date)).fetchone()
    if not row:
        return None
    return round((row['close'] - buy_price) / buy_price * 100, 2)

def calc_tn_close_rets(conn, code, date, buy_price, end_date, days=5):
    """T+1..T+days 逐日收益率"""
    rows = conn.execute("""
        SELECT close FROM kline WHERE code=? AND date>? AND date<=? ORDER BY date LIMIT ?
    """, (code, date, end_date, days)).fetchone()
    # Actually need fetchall
    rows = conn.execute("""
        SELECT close FROM kline WHERE code=? AND date>? AND date<=? ORDER BY date LIMIT ?
    """, (code, date, end_date, days)).fetchall()
    rets = []
    for r in rows:
        ret = round((r['close'] - buy_price) / buy_price * 100, 2)
        rets.append(ret)
    return rets

def calc_open_premium(conn, code, date, buy_price):
    """次日开盘溢价%"""
    row = conn.execute("""
        SELECT open FROM kline WHERE code=? AND date>? ORDER BY date LIMIT 1
    """, (code, date)).fetchone()
    if not row:
        return None
    return round((row['open'] - buy_price) / buy_price * 100, 2)

def stats_rets(rets_list):
    """统计收益率list: 返回样本数/均值/胜率"""
    if not rets_list:
        return 0, 0, 0
    n = len(rets_list)
    avg = round(sum(rets_list) / n, 2)
    wins = sum(1 for r in rets_list if r > 0)
    wr = round(wins / n * 100, 1)
    return n, avg, wr


# ─── 主力类型判断 (J3) ───

# 常见游资营业部关键词
YOUZI_KEYWORDS = [
    '国泰君安', '华泰证券', '中信证券', '中国银河', '招商证券',
    '广发证券', '海通证券', '申万宏源', '光大证券', '兴业证券',
    '东方证券', '方正证券', '国信证券', '中泰证券', '财通证券',
    '华鑫证券', '国金证券', '东莞证券', '华安证券', '浙商证券',
    '华福证券', '长城证券', '东北证券', '山西证券', '西藏东方财富',
    '东方财富证券', '平安证券', '天风证券', '民生证券', '太平洋证券',
    '西南证券', '湘财证券', '国联证券', '华西证券', '南京证券',
    '中银国际', '上海证券', '东海证券', '世纪证券', '万联证券',
    '国都证券', '新时代证券', '宏信证券', '华林证券', '开源证券',
    '国元证券', '东吴证券', '长江证券', '中原证券', '西部证券',
    '国海证券', '恒泰证券', '财达证券', '第一创业', '华创证券',
    '红塔证券', '九州证券', '金元证券', '五矿证券', '银泰证券',
]

# 量化相关关键词
QUANT_KEYWORDS = [
    '量化', '量化基金', '量化打板', '量化抢筹',
]

# 机构相关关键词
INST_KEYWORDS = [
    '机构专用', '机构',
]

def classify_dealer_type(dealer, yzmc=''):
    """判断营业部类型 → '机构' / '游资' / '量化' / '其他'"""
    name = dealer + ' ' + yzmc
    for kw in QUANT_KEYWORDS:
        if kw in name:
            return '量化'
    for kw in INST_KEYWORDS:
        if kw in name:
            return '机构'
    for kw in YOUZI_KEYWORDS:
        if kw in name:
            return '游资'
    # 深股通/沪股通视为机构
    if '股通' in name or '瑞银' in name or '高盛' in name or '摩根' in name:
        return '机构'
    return '其他'


def main():
    t0 = time.time()
    print("=" * 72)
    print("矛盾论未覆盖维度回测 — H1/J3/I2/G3")
    print(f"数据: {START_DATE} ~ {END_DATE}")
    print("=" * 72)

    # ─── 连接数据库 ───
    conn = sqlite3.connect(KLINE_DB)
    conn.row_factory = sqlite3.Row

    conn_lhb = sqlite3.connect(LHB_DB)
    conn_lhb.row_factory = sqlite3.Row

    # ─── 加载环境数据 ───
    print("\n[加载数据]")
    with open(ENV_HISTORY) as f:
        env_data = json.load(f)
    env_daily = env_data['daily']
    env_dates = sorted(env_daily.keys())
    print(f"  ✅ 环境历史: {len(env_dates)}天")

    # ─── 获取涨停股票列表 (用于多个维度) ───
    print("  获取涨停数据...")
    t1 = time.time()

    # 所有涨停股 (主板)
    limit_stocks_rows = conn.execute(f"""
        SELECT date, code, open, close, high, low, volume
        FROM kline
        WHERE date >= ? AND date <= ?
          AND ({' OR '.join(f"code LIKE '{p}%'" for p in ['6','000','001','002','003'])})
          AND close >= open * 0.995  -- 先取所有close接近high的
        ORDER BY date, code
    """, (START_DATE, END_DATE)).fetchall()

    # 精确筛选涨停: close >= prev_close * 1.095
    limit_stocks = []
    for r in limit_stocks_rows:
        prev = get_prev_close(conn, r['code'], r['date'])
        if prev and r['close'] >= prev * 1.095:
            limit_stocks.append({
                'date': r['date'], 'code': r['code'],
                'open': r['open'], 'close': r['close'],
                'high': r['high'], 'low': r['low'], 'volume': r['volume'],
                'prev_close': prev,
            })

    # 按日期分组
    limit_by_date = defaultdict(list)
    for st in limit_stocks:
        limit_by_date[st['date']].append(st)

    all_dates = sorted(limit_by_date.keys())
    date_index_map = {d: i for i, d in enumerate(all_dates)}
    print(f"  ✅ {len(limit_stocks)}条涨停记录, {len(all_dates)}个交易日 ({time.time()-t1:.1f}s)")

    # ===================================================================
    # 维度 H1: 一字板 vs 自然涨停
    # ===================================================================
    print("\n" + "=" * 72)
    print("📊 维度H1: 一字板 vs 自然涨停")
    print("  一字板: abs(open-prev_close)/prev_close<0.005 且 close>=prev_close*1.095")
    print("  自然涨停: close>=prev_close*1.095 但 open>prev_close*1.005")
    print("  对比T+1收益、胜率、开盘溢价")
    print("=" * 72)

    t1 = time.time()

    h1_yizi = {'t1_rets': [], 'premiums': []}
    h1_natural = {'t1_rets': [], 'premiums': []}

    for st in limit_stocks:
        code = st['code']
        date = st['date']
        prev = st['prev_close']
        op = st['open']
        cl = st['close']

        # 一字板: 开盘价≈前收盘 (涨幅<0.5%)
        is_yizi = abs(op - prev) / prev < 0.005

        buy_price = get_buy_price(conn, code, date, cl)

        if is_yizi:
            h1_yizi['t1_rets'].append(calc_t1_close_ret(conn, code, date, buy_price, END_DATE))
            h1_yizi['premiums'].append(calc_open_premium(conn, code, date, buy_price))
        else:
            h1_natural['t1_rets'].append(calc_t1_close_ret(conn, code, date, buy_price, END_DATE))
            h1_natural['premiums'].append(calc_open_premium(conn, code, date, buy_price))

    # 过滤None
    for d in [h1_yizi, h1_natural]:
        d['t1_rets'] = [x for x in d['t1_rets'] if x is not None]
        d['premiums'] = [x for x in d['premiums'] if x is not None]

    def print_h1(label, data):
        n, avg, wr = stats_rets(data['t1_rets'])
        n2, avg_prem, _ = stats_rets(data['premiums'])
        print(f"  {label:<12} 样本{n:>5}  T+1均收{avg:>+7.2f}%  胜率{wr:>6.1f}%  开盘溢价{avg_prem:>+7.2f}%")
        return {'samples': n, 't1_avg_ret': avg, 't1_win_rate': wr, 'open_premium_avg': avg_prem}

    h1_yizi_stats = print_h1("一字板", h1_yizi)
    h1_nat_stats = print_h1("自然涨停", h1_natural)

    # 对比
    if h1_yizi_stats['samples'] > 0 and h1_nat_stats['samples'] > 0:
        diff_ret = round(h1_yizi_stats['t1_avg_ret'] - h1_nat_stats['t1_avg_ret'], 2)
        diff_prem = round(h1_yizi_stats['open_premium_avg'] - h1_nat_stats['open_premium_avg'], 2)
        print(f"\n  📊 一字板vs自然涨停 T+1收益差: {diff_ret:+.2f}%")
        print(f"  📊 一字板vs自然涨停 开盘溢价差: {diff_prem:+.2f}%")
        if diff_ret > 0:
            print(f"  → 一字板后T+1收益更高 (+{diff_ret:.2f}%)")
        else:
            print(f"  → 自然涨停后T+1收益更高 ({diff_ret:+.2f}%)")

    h1_result = {
        'dimension': 'H1',
        'name': '一字板 vs 自然涨停',
        'description': '一字板=开盘价≈前收盘且涨停, 自然涨停=非一字涨停',
        'yizi_board': h1_yizi_stats,
        'natural_board': h1_nat_stats,
        'comparison': {
            't1_ret_diff': round(h1_yizi_stats['t1_avg_ret'] - h1_nat_stats['t1_avg_ret'], 2),
            'premium_diff': round(h1_yizi_stats['open_premium_avg'] - h1_nat_stats['open_premium_avg'], 2),
            'better_t1': '一字板' if h1_yizi_stats['t1_avg_ret'] > h1_nat_stats['t1_avg_ret'] else '自然涨停',
        },
    }
    print(f"  ⏱ {time.time()-t1:.1f}s")

    # ===================================================================
    # 维度 J3: 游资/机构/量化三方合力
    # ===================================================================
    print("\n" + "=" * 72)
    print("📊 维度J3: 游资/机构/量化三方合力")
    print("  用lhb_detail_tdx.dealer + yzmc判断资金类型")
    print("  统计7种组合的T+1收益和胜率")
    print("=" * 72)

    t1 = time.time()

    # 获取所有打板日的龙虎榜数据
    lhb_rows = conn_lhb.execute(f"""
        SELECT code, date, direction, seq, dealer, buy_amt, sell_amt, yzmc, sblx
        FROM lhb_detail_tdx
        WHERE sblx='dr'
          AND date >= ? AND date <= ?
        ORDER BY date, code, seq
    """, (START_DATE, END_DATE)).fetchall()

    print(f"  ✅ 龙虎榜打板记录: {len(lhb_rows)}条")

    # 按 code+date 分组，统计各类资金出现次数
    code_date_types = defaultdict(lambda: {'机构': 0, '游资': 0, '量化': 0})

    for r in lhb_rows:
        if r['direction'] != 'buy':
            continue
        dealer = r['dealer']
        yzmc = r['yzmc'] or ''
        ftype = classify_dealer_type(dealer, yzmc)
        if ftype in ('机构', '游资', '量化'):
            code_date_types[(r['code'], r['date'])][ftype] += 1

    # 按组合分组
    combo_groups = defaultdict(list)  # combo_name -> [t1_ret, ...]

    combo_names = {
        (True, True, True): '三家合力(机构+游资+量化)',
        (True, True, False): '机构+游资',
        (True, False, True): '机构+量化',
        (False, True, True): '游资+量化',
        (True, False, False): '仅机构',
        (False, True, False): '仅游资',
        (False, False, True): '仅量化',
    }

    for (code, date), types in code_date_types.items():
        has_jg = types['机构'] > 0
        has_yz = types['游资'] > 0
        has_lh = types['量化'] > 0
        key = (has_jg, has_yz, has_lh)
        combo_name = combo_names.get(key, '其他')
        if combo_name == '其他':
            continue

        # 找到该股的涨停数据
        st_matches = [s for s in limit_stocks if s['code'] == code and s['date'] == date]
        if not st_matches:
            continue
        st = st_matches[0]
        buy_price = get_buy_price(conn, code, date, st['close'])
        ret = calc_t1_close_ret(conn, code, date, buy_price, END_DATE)
        if ret is not None:
            combo_groups[combo_name].append(ret)

    j3_dim = []
    print(f"\n  {'组合名称':<30} {'样本':>6} {'T+1均收':>10} {'胜率':>8}")
    print(f"  {'-'*30} {'-'*6} {'-'*10} {'-'*8}")

    for combo_name in ['三家合力(机构+游资+量化)', '机构+游资', '机构+量化', '游资+量化', '仅机构', '仅游资', '仅量化']:
        rets = combo_groups.get(combo_name, [])
        n, avg, wr = stats_rets(rets)
        print(f"  {combo_name:<30} {n:>6} {avg:>+9.2f}% {wr:>7.1f}%")
        j3_dim.append({
            'group': combo_name,
            'samples': n,
            't1_avg_ret': avg,
            't1_win_rate': wr,
        })

    # 找最佳组合
    valid = [x for x in j3_dim if x['samples'] >= 10]
    if valid:
        best_ret = max(valid, key=lambda x: x['t1_avg_ret'])
        best_wr = max(valid, key=lambda x: x['t1_win_rate'])
        print(f"\n  🏆 最佳收益组合: {best_ret['group']} (均收{best_ret['t1_avg_ret']:+.2f}%)")
        print(f"  🏆 最佳胜率组合: {best_wr['group']} (胜率{best_wr['t1_win_rate']:.1f}%)")
    else:
        best_ret = best_wr = None

    j3_result = {
        'dimension': 'J3',
        'name': '游资/机构/量化三方合力',
        'description': '按龙虎榜买入方资金类型组合, 统计T+1收益和胜率',
        'groups': j3_dim,
        'best_return_group': best_ret['group'] if best_ret else None,
        'best_return_value': best_ret['t1_avg_ret'] if best_ret else 0,
        'best_winrate_group': best_wr['group'] if best_wr else None,
        'best_winrate_value': best_wr['t1_win_rate'] if best_wr else 0,
    }

    print(f"  ⏱ {time.time()-t1:.1f}s")

    # ===================================================================
    # 维度 I2: 内部因素 vs 外部因素
    # ===================================================================
    print("\n" + "=" * 72)
    print("📊 维度I2: 内部因素vs外部因素对转化的驱动力")
    print("  外部驱动(消息面): 环境急升+放量 (量比>1.3)")
    print("  内部驱动(技术面): 环境急升+缩量 (量比<0.7)")
    print("  对比两种驱动的后续T+3收益稳定性")
    print("=" * 72)

    t1 = time.time()

    # 用env_score变化识别"急升"事件
    i2_external = {'t1_rets': [], 't3_rets': [], 't5_rets': []}
    i2_internal = {'t1_rets': [], 't3_rets': [], 't5_rets': []}

    env_scores_list = [(d, env_daily[d]['env_score']) for d in env_dates if d in env_daily]
    env_score_map = {d: s for d, s in env_scores_list}

    # 遍历涨停日
    for d in all_dates:
        # 找环境分
        if d not in env_score_map:
            continue

        # 找3日前的环境分
        idx = env_dates.index(d) if d in env_dates else -1
        if idx < 3:
            continue
        d3_ago = env_dates[idx - 3]

        score_now = env_score_map[d]
        score_before = env_score_map.get(d3_ago, score_now)
        change = score_now - score_before

        # 只关注急升事件 (环境改善)
        if change < 15:
            continue

        # 对当日涨停股计算成交量的量比 (相对于5日均量)
        stks = limit_by_date.get(d, [])
        for st in stks:
            code = st['code']
            date = st['date']
            vol = st['volume']

            # 前5日均量
            prev_rows = conn.execute("""
                SELECT volume FROM kline
                WHERE code=? AND date<? ORDER BY date DESC LIMIT 5
            """, (code, date)).fetchall()
            if len(prev_rows) < 3:
                continue
            avg_vol = sum(r['volume'] for r in prev_rows) / len(prev_rows)
            vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

            buy_price = get_buy_price(conn, code, date, st['close'])
            rets = calc_tn_close_rets(conn, code, date, buy_price, END_DATE, days=5)

            if len(rets) < 3:
                continue

            if vol_ratio >= 1.3:
                # 放量 = 外部驱动(消息面)
                i2_external['t1_rets'].append(rets[0])
                i2_external['t3_rets'].append(sum(rets[:3]) / 3)
                if len(rets) >= 5:
                    i2_external['t5_rets'].append(sum(rets[:5]) / 5)
            elif vol_ratio <= 0.7:
                # 缩量 = 内部驱动(技术面)
                i2_internal['t1_rets'].append(rets[0])
                i2_internal['t3_rets'].append(sum(rets[:3]) / 3)
                if len(rets) >= 5:
                    i2_internal['t5_rets'].append(sum(rets[:5]) / 5)

    def print_i2(label, data):
        n1, avg1, wr1 = stats_rets(data['t1_rets'])
        n3, avg3, _ = stats_rets(data['t3_rets'])
        n5, avg5, _ = stats_rets(data['t5_rets'])
        print(f"  {label:<16} T+1样本{n1:>5} 均收{avg1:>+7.2f}% 胜率{wr1:>6.1f}%  |  T+3{avg3:>+7.2f}%  T+5{avg5:>+7.2f}%")
        return {'samples': n1, 't1_avg_ret': avg1, 't1_win_rate': wr1, 't3_avg_ret': avg3, 't5_avg_ret': avg5}

    i2_ext_stats = print_i2("外部驱动(消息)", i2_external)
    i2_int_stats = print_i2("内部驱动(技术)", i2_internal)

    # 对比稳定性和收益差
    if i2_ext_stats['samples'] > 0 and i2_int_stats['samples'] > 0:
        diff_t1 = round(i2_ext_stats['t1_avg_ret'] - i2_int_stats['t1_avg_ret'], 2)
        diff_t3 = round(i2_ext_stats['t3_avg_ret'] - i2_int_stats['t3_avg_ret'], 2)
        diff_t5 = round(i2_ext_stats['t5_avg_ret'] - i2_int_stats['t5_avg_ret'], 2)
        print(f"\n  📊 外部vs内部 T+1收益差: {diff_t1:+.2f}%")
        print(f"  📊 外部vs内部 T+3收益差: {diff_t3:+.2f}% (稳定性对比)")
        print(f"  📊 外部vs内部 T+5收益差: {diff_t5:+.2f}%")
        if abs(diff_t3) < abs(diff_t1):
            print(f"  → 外部驱动收益衰减更快 (消息面短期效应)")
        else:
            print(f"  → 内部驱动收益衰减更快 (技术面持续性弱)")

    i2_result = {
        'dimension': 'I2',
        'name': '内部因素vs外部因素对转化的驱动力',
        'description': '环境急升+放量=外部驱动(消息面), 环境急升+缩量=内部驱动(技术面)',
        'external_driven': i2_ext_stats,
        'internal_driven': i2_int_stats,
        'comparison': {
            't1_diff': round(i2_ext_stats['t1_avg_ret'] - i2_int_stats['t1_avg_ret'], 2) if i2_ext_stats['samples'] and i2_int_stats['samples'] else 0,
            't3_diff': round(i2_ext_stats['t3_avg_ret'] - i2_int_stats['t3_avg_ret'], 2) if i2_ext_stats['samples'] and i2_int_stats['samples'] else 0,
            't5_diff': round(i2_ext_stats['t5_avg_ret'] - i2_int_stats['t5_avg_ret'], 2) if i2_ext_stats['samples'] and i2_int_stats['samples'] else 0,
        },
    }

    print(f"  ⏱ {time.time()-t1:.1f}s")

    # ===================================================================
    # 维度 G3: 跌破首板的后续表现 (炸板回封补充)
    # ===================================================================
    print("\n" + "=" * 72)
    print("📊 维度G3: 跌破首板的后续表现 (炸板回封补充)")
    print("  炸板: 盘中摸涨停(high>=prev_close*1.095)但收盘未封住(close<prev_close*1.095)")
    print("  封板: 收盘封住涨停")
    print("  回封板: 盘中打开涨停后再次封住")
    print("  对比三种情况的T+1收益")
    print("=" * 72)

    t1 = time.time()

    # 找到所有"摸过涨停"的股票
    touched_stocks_rows = conn.execute(f"""
        SELECT date, code, open, close, high, low, volume
        FROM kline
        WHERE date >= ? AND date <= ?
          AND ({' OR '.join(f"code LIKE '{p}%'" for p in ['6','000','001','002','003'])})
        ORDER BY date, code
    """, (START_DATE, END_DATE)).fetchall()

    g3_zhaban = {'t1_rets': [], 'premiums': []}
    g3_fengban = {'t1_rets': [], 'premiums': []}
    g3_huifeng = {'t1_rets': [], 'premiums': []}

    for r in touched_stocks_rows:
        code = r['code']
        date = r['date']
        op = r['open']
        cl = r['close']
        hi = r['high']
        prev = get_prev_close(conn, code, date)
        if not prev:
            continue

        touched_limit = hi >= prev * 1.095
        if not touched_limit:
            continue

        is_fengban = cl >= prev * 1.095

        buy_price = get_buy_price(conn, code, date, cl)

        # 判断回封: 炸板后又封住 => 开盘非一字, 但最终封住
        # 简化: 开盘后打开(open≈prev)且收盘封住(close>=prev*1.095)且盘中最高>开盘
        is_yizi_open = abs(op - prev) / prev < 0.005
        is_huifeng = is_fengban and not is_yizi_open and hi > op * 1.02

        if is_fengban:
            if is_huifeng:
                g3_huifeng['t1_rets'].append(calc_t1_close_ret(conn, code, date, buy_price, END_DATE))
                g3_huifeng['premiums'].append(calc_open_premium(conn, code, date, buy_price))
            elif is_yizi_open:
                # 一字板不算普通封板
                pass
            else:
                g3_fengban['t1_rets'].append(calc_t1_close_ret(conn, code, date, buy_price, END_DATE))
                g3_fengban['premiums'].append(calc_open_premium(conn, code, date, buy_price))
        else:
            # 炸板未封住
            g3_zhaban['t1_rets'].append(calc_t1_close_ret(conn, code, date, buy_price, END_DATE))
            g3_zhaban['premiums'].append(calc_open_premium(conn, code, date, buy_price))

    # 过滤None
    for d in [g3_zhaban, g3_fengban, g3_huifeng]:
        d['t1_rets'] = [x for x in d['t1_rets'] if x is not None]
        d['premiums'] = [x for x in d['premiums'] if x is not None]

    def print_g3(label, data):
        n, avg, wr = stats_rets(data['t1_rets'])
        n2, avg_prem, _ = stats_rets(data['premiums'])
        print(f"  {label:<12} 样本{n:>5}  T+1均收{avg:>+7.2f}%  胜率{wr:>6.1f}%  开盘溢价{avg_prem:>+7.2f}%")
        return {'samples': n, 't1_avg_ret': avg, 't1_win_rate': wr, 'open_premium_avg': avg_prem}

    g3_zhb_stats = print_g3("炸板", g3_zhaban)
    g3_fb_stats = print_g3("一次封死", g3_fengban)
    g3_hf_stats = print_g3("回封板", g3_huifeng)

    # 对比
    if g3_zhb_stats['samples'] > 0 and g3_fb_stats['samples'] > 0:
        diff = round(g3_zhb_stats['t1_avg_ret'] - g3_fb_stats['t1_avg_ret'], 2)
        print(f"\n  📊 炸板vs封板 T+1收益差: {diff:+.2f}%")
        if diff > 0:
            print(f"  → 炸板股次日反而收益更好 ({diff:+.2f}%)")
        else:
            print(f"  → 封板股次日收益更优 ({-diff:+.2f}%)")

    if g3_hf_stats['samples'] > 0 and g3_fb_stats['samples'] > 0:
        diff = round(g3_hf_stats['t1_avg_ret'] - g3_fb_stats['t1_avg_ret'], 2)
        print(f"  📊 回封板vs一次封死 T+1收益差: {diff:+.2f}%")
        if diff > 0:
            print(f"  → 回封板次日收益更高 (资金二次认可)")
        else:
            print(f"  → 一次封死次日收益更高")

    g3_result = {
        'dimension': 'G3',
        'name': '跌破首板的后续表现 (炸板回封补充)',
        'description': '炸板=摸涨停未封住, 封板=收盘封住, 回封板=盘中打开后重新封住',
        'zhaban': g3_zhb_stats,
        'fengban_once': g3_fb_stats,
        'huifeng_ban': g3_hf_stats,
        'comparison': {
            'zhaban_vs_fengban_diff': round(g3_zhb_stats['t1_avg_ret'] - g3_fb_stats['t1_avg_ret'], 2),
            'huifeng_vs_fengban_diff': round(g3_hf_stats['t1_avg_ret'] - g3_fb_stats['t1_avg_ret'], 2),
        },
    }

    print(f"  ⏱ {time.time()-t1:.1f}s")

    # ===================================================================
    # 汇总输出
    # ===================================================================
    output = {
        'meta': {
            'script': '矛盾论未覆盖维度回测 v1.0 — H1/J3/I2/G3',
            'generated_at': datetime.now().isoformat(),
            'date_range': f'{START_DATE} ~ {END_DATE}',
            'total_trading_days': len(all_dates),
            'total_limit_stocks': len(limit_stocks),
            'elapsed_seconds': round(time.time() - t0, 1),
        },
        'dimensions': {
            'H1': h1_result,
            'J3': j3_result,
            'I2': i2_result,
            'G3': g3_result,
        },
    }

    with open(OUTPUT, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 72}")
    print(f"✅ 完成！{round(time.time() - t0, 1)}s  已保存到 {OUTPUT}")
    print(f"{'=' * 72}")

    conn.close()
    conn_lhb.close()


if __name__ == '__main__':
    main()
