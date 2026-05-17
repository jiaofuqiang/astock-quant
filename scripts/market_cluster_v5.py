#!/usr/bin/env python3
"""
📊 市场六层多维聚类引擎 v5.0
===============================
六层穿透式市场分类：
  1️⃣ 市场热度  — 做不做？（狂热/活跃/平淡/冰点）
  2️⃣ 资金风格  — 怎么做？（游资/缩量/机构/散户）
  3️⃣ 赚钱效应  — 做什么？（龙头/首板/集群/超跌/轮动）
  4️⃣ 板块结构  — 主线清晰度？（集中/双线/散乱/无主线）
  5️⃣ 量价健康  — 资金真实度？（健康/温和/虚胖/异常）
  6️⃣ 动量趋势  — 趋势位置？（加速/强势/震荡/超跌）

输出格式：☀️·放量游资·龙头接力·集中主线·量价健康·强势趋势

用法：
  python3 scripts/market_cluster_v5.py --build     # 回填+分类
  python3 scripts/market_cluster_v5.py --today     # 今日分类
  python3 scripts/market_cluster_v5.py --stats     # 组合统计
"""
import os, sys, json, sqlite3, math, subprocess
from datetime import datetime, date
from collections import defaultdict, Counter

BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
KLINE_DB = os.path.join(DATA_DIR, 'kline_cache.db')
LIMIT_DB = os.path.join(DATA_DIR, 'daily_limit_data.db')
SECTOR_DB = os.path.join(DATA_DIR, 'sector_indexes.db')
MARKET_DB = os.path.join(DATA_DIR, 'market_daily.db')
OUT_DIR = os.path.join(BASE, 'research')
os.makedirs(OUT_DIR, exist_ok=True)
HISTORY_OUT = os.path.join(OUT_DIR, 'market_history_v5.json')
CLUSTER_OUT = os.path.join(OUT_DIR, 'market_clusters_v5.json')

def pct(v, total):
    return round(v / max(total, 1) * 100, 1)

def sql_json(db, q):
    try:
        r = subprocess.run(['sqlite3', '-json', db], capture_output=True, text=True, timeout=30, input=q)
        return json.loads(r.stdout) if r.stdout.strip() else []
    except: return []

def build_history():
    """从K线回填，计算6层所有特征"""
    print(f"\n{'='*55}")
    print(f"📊 六层多维历史回填 (v5)")
    print(f"{'='*55}")

    # ----- 加载K线到内存（含20日均线预计算）-----
    print(f"\n[1/5] 加载K线+预计算...")
    conn = sqlite3.connect(KLINE_DB)
    c = conn.cursor()
    kd_date = defaultdict(list)
    kd_code = defaultdict(dict)
    for row in c.execute("SELECT date, code, open, close, high, low, volume FROM kline ORDER BY date"):
        d, code, o, c2, h, l, v = row
        entry = {'o': o, 'c': c2, 'h': h, 'l': l, 'v': v}
        kd_date[d].append({'code': code, **entry})
        kd_code[code][d] = entry
    dates = sorted(kd_date.keys())
    conn.close()
    print(f"  共{len(dates)}天, {sum(len(v) for v in kd_date.values()):,}条K线")

    # 预计算MA20（按code预先计算好）
    print(f"  预计算MA20乖离...")
    ma20_cache = {}  # (code, date) -> ma20_dev
    for code, code_dates in kd_code.items():
        sorted_cd = sorted(code_dates.keys())
        for i, cd in enumerate(sorted_cd):
            if i >= 20:
                closes = [code_dates[sorted_cd[j]]['c'] for j in range(i-20, i)]
                ma20 = sum(closes) / 20
                cur_c = code_dates[cd]['c']
                ma20_cache[(code, cd)] = (cur_c - ma20) / ma20 * 100 if ma20 > 0 else 0
            else:
                ma20_cache[(code, cd)] = 0

    # ----- 加载最高板 -----
    print(f"\n[2/5] 加载辅助数据...")
    mb_data = {}
    try:
        conn2 = sqlite3.connect(LIMIT_DB)
        for row in conn2.execute("SELECT date, max_board FROM limit_strength"):
            mb_data[row[0]] = row[1]
        conn2.close()
        print(f"  最高板: {len(mb_data)}天")
    except: pass

    # ----- 加载板块数据 -----
    print(f"\n[3/5] 加载板块数据...")
    sector_daily = defaultdict(list)
    try:
        conn3 = sqlite3.connect(SECTOR_DB)
        for row in conn3.execute("SELECT date, sector_name, limit_up_count FROM sector_daily_index ORDER BY date, limit_up_count DESC"):
            sector_daily[row[0]].append({'name': row[1], 'lu': row[2] or 0})
        conn3.close()
        print(f"  板块: {len(sector_daily)}天")
    except: pass

    # ----- 加载游资数据 -----
    print(f"\n[4/5] 加载龙虎榜数据...")
    youzi_data = {}
    try:
        conn4 = sqlite3.connect(os.path.join(DATA_DIR, 'lhb_cache.db'))
        for row in conn4.execute("""
            SELECT l.date,
              ROUND(SUM(CASE WHEN d.direction='buy' THEN d.net ELSE 0 END)/10000, 0) as buy_wan,
              ROUND(SUM(CASE WHEN d.direction='sell' THEN d.net ELSE 0 END)/10000, 0) as sell_wan,
              (SELECT ROUND(SUM(CASE WHEN direction='buy' THEN net ELSE 0 END)/10000, 0)
               FROM lhb_list l2 JOIN lhb_detail d2 ON l2.date=d2.date AND l2.code=d2.code
               WHERE l2.type='03' AND l2.date=l.date) as jg_buy
            FROM lhb_list l JOIN lhb_detail d ON l.date=d.date AND l.code=d.code
            WHERE l.type IN ('04','34','37','38','39','40')
            GROUP BY l.date
        """):
            youzi_data[row[0]] = {'net': (row[1] or 0) - (row[2] or 0), 'jg_net': (row[3] or 0) * -1}
        conn4.close()
        print(f"  龙虎榜: {len(youzi_data)}天")
    except: pass

    # ----- 逐日计算 -----
    print(f"\n[5/5] 逐日计算六层特征...")
    days = []
    prev_stocks = {}

    for idx, dt in enumerate(dates):
        if idx % 50 == 0:
            print(f"  进度{idx}/{len(dates)} ({dt})...")
        stocks = kd_date[dt]
        if not prev_stocks:
            prev_stocks = {s['code']: s for s in stocks}
            continue

        lu = ld = up = dn = 0
        yz = sl = fl = 0
        gap_gt_5 = gap_total = trade_cnt = 0
        vol_lt_07 = vol_gt_3 = 0
        total_seal = 0

        # 用于MA20计算的缓存
        ma20_vals = []
        momentum_vals = []

        for s in stocks:
            ps = prev_stocks.get(s['code'])
            if not ps or ps['c'] <= 0: continue
            pc = ps['c']; cc = s['c']; co = s['o']; cv = s['v']; ch = s['h']
            trade_cnt += 1
            chg = (cc - pc) / pc * 100
            ochg = (co - pc) / pc * 100

            if chg > 0: up += 1
            elif chg < 0: dn += 1

            if ochg > 0: gap_total += 1
            if ochg >= 5: gap_gt_5 += 1

            # 量比(对昨日)
            if ps['v'] > 0:
                vr = cv / ps['v']
                if vr < 0.7: vol_lt_07 += 1
                if vr > 3: vol_gt_3 += 1

            # 涨停
            if chg >= 9.5 and cc >= pc * 1.09:
                lu += 1
                # 一字判断
                is_yz = abs(ochg - 10) < 0.5
                # 量比(5日均量)
                code_dates = [d for d in dates if d <= dt][-6:]
                vols = []
                for cd in code_dates:
                    for st in kd_date[cd]:
                        if st['code'] == s['code']:
                            vols.append(st['v'])
                            break
                avg5 = sum(vols[-5:]) / max(len(vols[-5:]), 1)
                vr = cv / max(avg5, 0.01)
                if is_yz: yz += 1
                elif vr < 0.7: sl += 1
                else: fl += 1
                total_seal += 0

            # 跌停
            if chg <= -9.5 and cc <= pc * 0.91:
                ld += 1

        zh_r = pct(up, trade_cnt)
        mb = mb_data.get(dt, 0)
        yz_pct = pct(yz, lu)
        sl_pct = pct(sl, lu)
        fl_pct = pct(fl, lu)

        # ----- 第4层：板块结构 -----
        sector_list = sector_daily.get(dt, [])
        total_sector_lu = sum(s['lu'] for s in sector_list)
        top1_lu = sector_list[0]['lu'] if sector_list else 0
        top3_lu = sum(s['lu'] for s in sector_list[:3]) if len(sector_list) >= 3 else top1_lu
        # 集中度=TOP1涨停/总涨停, HHI指数
        concentration = pct(top1_lu, lu) if lu > 0 else 0
        top3_concentration = pct(top3_lu, lu) if lu > 0 else 0
        # 板块爆发数
        sb = sum(1 for s in sector_list if s['lu'] >= 3)

        # ----- 第5层：量价健康 -----
        vol_lt_07_pct = pct(vol_lt_07, trade_cnt)
        vol_gt_3_pct = pct(vol_gt_3, trade_cnt)

        # ----- 第6层：动量趋势 -----
        # 从MA20缓存取涨停股的均值
        avg_ma20 = 0; avg_5d_mom = 0; ma20_cnt = 0
        for s in stocks:
            code = s['code']
            if code not in prev_stocks: continue
            if ma20_cache.get((code, dt), 0) != 0:
                avg_ma20 += ma20_cache.get((code, dt), 0)
                ma20_cnt += 1
        avg_ma20_dev = round(avg_ma20 / max(ma20_cnt, 1), 1) if ma20_cnt > 0 else 0
        mom5_avg = 0

        yz_net = youzi_data.get(dt, {}).get('net', 0) if dt in youzi_data else 0
        jg_net = youzi_data.get(dt, {}).get('jg_net', 0) if dt in youzi_data else 0

        day = {
            'date': dt,
            # -- 第1层 热度 --
            'limit_up': lu, 'zh_ratio': zh_r, 'max_board': mb, 'limit_down': ld,
            # -- 第2层 资金风格 --
            'yizi_pct': yz_pct, 'suoliang_pct': sl_pct, 'fangliang_pct': fl_pct,
            'youzi_net_wan': yz_net, 'jigou_net_wan': jg_net,
            'gap_gt_5_pct': pct(gap_gt_5, trade_cnt),
            'total_seal_wan': total_seal,
            # -- 第3层 赚钱效应 --
            'board_2plus_pct': pct(max(0, mb-1), lu),
            'sector_boom_count': sb,
            'avg_ma20_dev': avg_ma20_dev, 'avg_5d_momentum': mom5_avg,
            # -- 第4层 板块结构 --
            'top1_concentration': concentration,
            'top3_concentration': top3_concentration,
            'sector_count': len(sector_list),
            # -- 第5层 量价健康 --
            'vol_lt_07_pct': vol_lt_07_pct,
            'vol_gt_3_pct': vol_gt_3_pct,
            'fangliang_pct': fl_pct,
            # -- 第6层 动量趋势 --
            'up_count': up, 'down_count': dn,
        }
        days.append(day)
        prev_stocks = {s['code']: s for s in stocks}

    with open(HISTORY_OUT, 'w') as f:
        json.dump(days, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存 {len(days)} 天到 {HISTORY_OUT}")
    return days


# =========================================
# 六层分类逻辑
# =========================================

def l1_heat(d):
    """第1层：市场热度"""
    lu = d.get('limit_up', 0); zh = d.get('zh_ratio', 0)
    mb = d.get('max_board', 0); ld = d.get('limit_down', 0)
    if ld >= 10 and lu < 20: return '❄️恐慌冰点', 0
    if lu >= 80 and zh >= 55: return '☀️狂热', 4
    if lu >= 60 and zh >= 60: return '☀️狂热', 4
    if lu >= 40 or (lu >= 30 and mb >= 5): return '🌤活跃', 3
    if lu >= 30 and zh >= 50: return '🌤活跃', 3
    if lu >= 15 or mb >= 3: return '☁️平淡', 2
    if lu >= 10 and zh >= 35: return '☁️平淡', 2
    return '❄️冰点', 1

def l2_style(d):
    """第2层：资金风格"""
    fl = d.get('fangliang_pct', 80); sl = d.get('suoliang_pct', 0)
    yz = d.get('yizi_pct', 0); yz_net = d.get('youzi_net_wan', 0)
    jg_net = d.get('jigou_net_wan', 0); zh = d.get('zh_ratio', 50)
    zb = d.get('zhaban_rate', 10); g5 = d.get('gap_gt_5_pct', 0)
    scores = {}
    scores['放量游资'] = (2 if fl >= 65 else 0) + (1 if sl <= 8 else 0) + (2 if yz_net >= 200 else 0) + (1 if yz_net >= 0 else 0)
    scores['缩量惜售'] = (2 if sl >= 12 else 0) + (2 if yz >= 10 else 0) + (1 if zb <= 12 else 0)
    scores['机构趋势'] = (2 if jg_net >= 100 else 0) + (1 if g5 >= 15 else 0) + (1 if zh >= 55 else 0) + (1 if fl >= 60 and zb <= 15 else 0)
    scores['散户博弈'] = (2 if fl >= 85 else 0) + (2 if zb >= 20 else 0) + (1 if yz_net <= -200 else 0)
    best = max(scores, key=scores.get)
    return best, scores[best]

def l3_profit(d):
    """第3层：赚钱效应"""
    mb = d.get('max_board', 0); sb = d.get('sector_boom_count', 0)
    bp = d.get('board_2plus_pct', 0); lu = d.get('limit_up', 0)
    zb = d.get('zhaban_rate', 10); ma20 = d.get('avg_ma20_dev', 0)
    fl = d.get('fangliang_pct', 80); surge = d.get('surge_count', 0)
    scores = {}
    scores['龙头接力'] = (3 if mb >= 7 else 2 if mb >= 5 else 0) + (1 if sb >= 8 else 0) + (1 if bp >= 12 else 0)
    scores['首板套利'] = (1 if mb <= 4 else 0) + (1 if lu >= 30 else 0) + (1 if bp <= 8 else 0) + (1 if zb >= 15 else 0)
    scores['板块集群'] = (3 if sb >= 10 else 2 if sb >= 6 else 0) + (1 if lu >= 50 else 0) + (1 if mb >= 4 else 0)
    scores['超跌修复'] = (2 if ma20 <= -10 else 0) + (1 if fl >= 50 else 0) + (1 if lu >= 20 and mb <= 3 else 0)
    scores['轮动打地鼠'] = (2 if mb <= 3 else 0) + (1 if sb <= 3 else 0) + (1 if bp <= 7 else 0) + (1 if 20 <= lu <= 50 else 0)
    best = max(scores, key=scores.get)
    return best, scores[best]

def l4_sector(d):
    """第4层：板块结构"""
    c = d.get('top1_concentration', 0)
    sb = d.get('sector_boom_count', 0)
    lu = d.get('limit_up', 0)
    tc3 = d.get('top3_concentration', 0)

    if c >= 50 and sb >= 5:
        return '🎯集中主线', 4
    if c >= 30 or (sb >= 3 and tc3 >= 40):
        return '🔀双线并行', 3
    if sb >= 1 and lu >= 20:
        return '📊散乱多线', 2
    return '🌫️无主线', 1

def l5_health(d):
    """第5层：量价健康度"""
    vlt = d.get('vol_lt_07_pct', 50)
    vgt = d.get('vol_gt_3_pct', 5)
    fl = d.get('fangliang_pct', 80)
    sl = d.get('suoliang_pct', 5)
    seal = d.get('total_seal_wan', 0)

    if vlt >= 40 and sl >= 15:
        return '💎缩量惜售', 4
    if fl <= 70 and vgt <= 10:
        return '✅量价健康', 3
    if fl <= 85:
        return '⚖️量价温和', 2
    return '⚠️量价虚胖', 1

def l6_trend(d):
    """第6层：动量趋势"""
    ma20 = d.get('avg_ma20_dev', 0)
    mom5 = d.get('avg_5d_momentum', 0)
    lu = d.get('limit_up', 0)
    mb = d.get('max_board', 0)
    ld = d.get('limit_down', 0)

    if ma20 >= 15 and mb >= 5 and lu >= 40:
        return '🚀加速冲顶', 4
    if ma20 >= 5 or (mb >= 4 and lu >= 30):
        return '📈强势延续', 3
    if ma20 >= -5 or lu >= 20:
        return '↔️震荡筑底', 2
    if ma20 < -5 or (ld >= 5 and lu < 20):
        return '📉超跌反弹', 1
    return '↔️震荡筑底', 2


def classify_day(d):
    """六层分类"""
    l1, s1 = l1_heat(d)
    l2, s2 = l2_style(d)
    l3, s3 = l3_profit(d)
    l4, s4 = l4_sector(d)
    l5, s5 = l5_health(d)
    l6, s6 = l6_trend(d)

    tag = f"{l1}·{l2}·{l3}·{l4}·{l5}·{l6}"
    detail = {
        'l1_热度': l1, 'l2_风格': l2, 'l3_效应': l3,
        'l4_板块': l4, 'l5_量价': l5, 'l6_趋势': l6,
    }
    return tag, detail


def run_all(days):
    """全量分类+输出统计"""
    print(f"\n{'='*55}")
    print(f"📊 六层多维分类结果")
    print(f"{'='*55}")

    combo = Counter()
    l1_cnt = Counter(); l2_cnt = Counter(); l3_cnt = Counter()
    l4_cnt = Counter(); l5_cnt = Counter(); l6_cnt = Counter()

    for d in days:
        tag, detail = classify_day(d)
        combo[tag] += 1
        l1_cnt[detail['l1_热度']] += 1
        l2_cnt[detail['l2_风格']] += 1
        l3_cnt[detail['l3_效应']] += 1
        l4_cnt[detail['l4_板块']] += 1
        l5_cnt[detail['l5_量价']] += 1
        l6_cnt[detail['l6_趋势']] += 1

    n = len(days)
    print(f"\n共{n}天, 出现{len(combo)}种组合")
    print(f"\n━━━ 单层分布 ━━━")
    print(f"第1层 热度: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l1_cnt.most_common())}")
    print(f"第2层 风格: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l2_cnt.most_common())}")
    print(f"第3层 效应: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l3_cnt.most_common())}")
    print(f"第4层 板块: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l4_cnt.most_common())}")
    print(f"第5层 量价: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l5_cnt.most_common())}")
    print(f"第6层 趋势: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l6_cnt.most_common())}")

    print(f"\n━━━ TOP30组合 ━━━")
    for tag, cnt in combo.most_common(30):
        print(f"  {cnt:3d}天 ({pct(cnt,n):4.1f}%)  {tag}")

    # 保存
    out = {'_meta': {'total_days': n, 'total_types': len(combo),
                      'layers': {'1热度':'狂热/活跃/平淡/冰点/恐慌冰点',
                                '2风格':'放量游资/缩量惜售/机构趋势/散户博弈',
                                '3效应':'龙头接力/首板套利/板块集群/超跌修复/轮动打地鼠',
                                '4板块':'集中主线/双线并行/散乱多线/无主线',
                                '5量价':'缩量惜售/量价健康/量价温和/量价虚胖',
                                '6趋势':'加速冲顶/强势延续/震荡筑底/超跌反弹'}}}
    for tag, cnt in combo.most_common():
        sample_days = [d for d in days if classify_day(d)[0] == tag][:3]
        out[tag] = {'count': cnt, 'pct': round(cnt/n*100, 1),
                    'samples': [d['date'] for d in sample_days]}

    with open(CLUSTER_OUT, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存到 {CLUSTER_OUT}")


def classify_today():
    """今日分类"""
    today_str = str(date.today())
    print(f"\n{'='*55}")
    print(f"📊 今日六层分类 — {today_str}")
    print(f"{'='*55}")

    # 读数据
    rows = sql_json(MARKET_DB, f"SELECT * FROM day_full WHERE date='{today_str}'")
    if rows:
        day = rows[0]
    elif os.path.exists(HISTORY_OUT):
        with open(HISTORY_OUT) as f:
            hist = json.load(f)
        hd = {d['date']: d for d in hist}
        if today_str in hd:
            day = hd[today_str]
        else:
            print("❌ 无今日数据")
            return None
    else:
        print("❌ 无今日数据")
        return None

    tag, detail = classify_day(day)
    print(f"\n  🏷️  {tag}")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━")
    for k, v in detail.items():
        print(f"  {k}: {v}")

    print(f"\n  关键指标:")
    for k in ['limit_up','zh_ratio','max_board','yizi_pct','suoliang_pct',
              'top1_concentration','vol_lt_07_pct','avg_ma20_dev','youzi_net_wan']:
        print(f"    {k}: {day.get(k, 0)}")

    # 策略
    print(f"\n  策略建议:")
    for s in get_strategies(tag, day):
        print(f"    {s}")

    return {'tag': tag, 'detail': detail}


def get_strategies(tag, day):
    """基于6层标签生成策略"""
    parts = tag.split('·')
    l1 = parts[0] if len(parts) > 0 else ''
    l2 = parts[1] if len(parts) > 1 else ''
    l3 = parts[2] if len(parts) > 2 else ''
    l4 = parts[3] if len(parts) > 3 else ''
    l5 = parts[4] if len(parts) > 4 else ''
    l6 = parts[5] if len(parts) > 5 else ''

    s = []

    # 仓位
    if '狂热' in l1: s.append('📌 仓位: 满仓(80~100%)')
    elif '活跃' in l1: s.append('📌 仓位: 重仓(60~80%)')
    elif '平淡' in l1: s.append('📌 仓位: 半仓(30~50%)')
    else: s.append('📌 仓位: 轻仓(10~20%)或空仓')

    # 模式
    if '游资' in l2: s += ['🎯 模式: 打板(游资票)', '   M07板块爆发 +9.96%/94%', '   M06总龙头 +4.03%/71%']
    elif '惜售' in l2: s += ['🎯 模式: 隔夜溢价(缩量)', '   M01隔夜溢价 +5.62%/85%', '   M02极缩<0.3 +7.77%/96%']
    elif '机构' in l2: s += ['🎯 模式: 趋势低吸', '   M10换手板接力 +3.23%/70%', '   M14超跌反弹 +3.05%/83%']
    elif '散户' in l2: s += ['🎯 模式: 一字开板接力', '   M11一字开板 +4.95%/74%']

    # 板块
    if '集中' in l4: s.append('🎯 主线明确 → 怼龙头')
    elif '双线' in l4: s.append('🎯 双线轮动 → 哪条强做哪条')
    elif '散乱' in l4: s.append('🎯 多线发散 → 做最强的首板')
    elif '无' in l4: s.append('🎯 无主线 → 观望或做独立逻辑股')

    # 趋势
    if '加速' in l6: s.append('⚠️ 加速冲顶 → 可以追但设好止损')
    elif '强势' in l6: s.append('📈 强势延续 → 跟随趋势')
    elif '震荡' in l6: s.append('↔️ 震荡筑底 → 不追高等低吸')
    elif '超跌' in l6: s.append('📉 超跌反弹 → 低吸为主')

    # 量价
    if '惜售' in l5: s.append('💎 缩量惜售 → 选缩量涨停股')
    elif '虚胖' in l5: s.append('⚠️ 量价虚胖 → 放量烂板多, 谨慎接力')

    return s


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--build' in args:
        days = build_history()
        run_all(days)
    elif '--classify' in args:
        if os.path.exists(HISTORY_OUT):
            with open(HISTORY_OUT) as f:
                days = json.load(f)
            run_all(days)
    elif '--today' in args:
        classify_today()
    elif '--stats' in args:
        if os.path.exists(CLUSTER_OUT):
            with open(CLUSTER_OUT) as f:
                d = json.load(f)
            print(f"共{d['_meta']['total_types']}种组合")
            for tag, info in d.items():
                if tag.startswith('_'): continue
                print(f"  {tag:60s} {info['count']:3d}天 ({info['pct']:4.1f}%)")
    else:
        print("用法: --build | --classify | --today | --stats")
