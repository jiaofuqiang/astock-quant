#!/usr/bin/env python3
"""
📊 市场九层穿透式聚类引擎 v6.0
===============================
九维市场全景分析：
  1️⃣ 市场热度    — 做不做？
  2️⃣ 资金风格    — 怎么做？
  3️⃣ 赚钱效应    — 做什么？
  4️⃣ 板块结构    — 主线清晰度？
  5️⃣ 量价健康    — 成交质量？
  6️⃣ 动量趋势    — 趋势位置？
  7️⃣ 情绪周期    — 心理阶段？   ← 新增(时间序列)
  8️⃣ 板块轮动    — 切换速度？   ← 新增(板块变化)
  9️⃣ 大资金博弈   — 谁主导？    ← 新增(龙虎榜)

输出示例：
  🌤活跃·放量游资·龙头接力·🎯集中主线·✅量价健康·📈强势延续·😊乐观·🐢单主线·🤝合力

用法：
  python3 scripts/market_cluster_v6.py --build     # 回填+分类
  python3 scripts/market_cluster_v6.py --today     # 今日分类
  python3 scripts/market_cluster_v6.py --stats     # 组合统计
"""
import os, sys, json, sqlite3, math, subprocess
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter

BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
KLINE_DB = os.path.join(DATA_DIR, 'kline_cache.db')
LIMIT_DB = os.path.join(DATA_DIR, 'daily_limit_data.db')
SECTOR_DB = os.path.join(DATA_DIR, 'sector_indexes.db')
LHB_DB = os.path.join(DATA_DIR, 'lhb_cache.db')
MARKET_DB = os.path.join(DATA_DIR, 'market_daily.db')
OUT_DIR = os.path.join(BASE, 'research')
os.makedirs(OUT_DIR, exist_ok=True)
HISTORY_OUT = os.path.join(OUT_DIR, 'market_history_v6.json')
CLUSTER_OUT = os.path.join(OUT_DIR, 'market_clusters_v6.json')

def pct(v, total):
    return round(v / max(total, 1) * 100, 1)

def sf(v, d=0):
    try: return float(v) if v not in (None, '-', '') else d
    except: return d

def sql_val(db, q, defv=0):
    try:
        r = subprocess.run(['sqlite3', '-noheader', db], capture_output=True, text=True, timeout=15, input=q)
        return r.stdout.strip() or str(defv)
    except: return str(defv)

def sql_json(db, q):
    try:
        r = subprocess.run(['sqlite3', '-json', db], capture_output=True, text=True, timeout=15, input=q)
        return json.loads(r.stdout) if r.stdout.strip() else []
    except: return []

# =========================================
# 9层分类器（纯函数，可单独测试）
# =========================================

def l1_heat(d):
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
    fl = d.get('fangliang_pct', 80); sl = d.get('suoliang_pct', 0)
    yz = d.get('yizi_pct', 0); yz_net = d.get('youzi_net_wan', 0)
    jg_net = d.get('jigou_net_wan', 0); zh = d.get('zh_ratio', 50)
    zb = d.get('zhaban_rate', 10); g5 = d.get('gap_gt_5_pct', 0)
    sc = {}
    sc['放量游资'] = (2 if fl >= 65 else 0)+(1 if sl <= 8 else 0)+(2 if yz_net >= 200 else 0)+(1 if yz_net >= 0 else 0)
    sc['缩量惜售'] = (2 if sl >= 12 else 0)+(2 if yz >= 10 else 0)+(1 if zb <= 12 else 0)
    sc['机构趋势'] = (2 if jg_net >= 100 else 0)+(1 if g5 >= 15 else 0)+(1 if zh >= 55 else 0)+(1 if fl >= 60 and zb <= 15 else 0)
    sc['散户博弈'] = (2 if fl >= 85 else 0)+(2 if zb >= 20 else 0)+(1 if yz_net <= -200 else 0)
    b = max(sc, key=sc.get); return b, sc[b]

def l3_profit(d):
    mb = d.get('max_board', 0); sb = d.get('sector_boom_count', 0)
    bp = d.get('board_2plus_pct', 0); lu = d.get('limit_up', 0)
    zb = d.get('zhaban_rate', 10); ma20 = d.get('avg_ma20_dev', 0)
    fl = d.get('fangliang_pct', 80)
    sc = {}
    sc['龙头接力'] = (3 if mb >= 7 else 2 if mb >= 5 else 0)+(1 if sb >= 8 else 0)+(1 if bp >= 12 else 0)
    sc['首板套利'] = (1 if mb <= 4 else 0)+(1 if lu >= 30 else 0)+(1 if bp <= 8 else 0)+(1 if zb >= 15 else 0)
    sc['板块集群'] = (3 if sb >= 10 else 2 if sb >= 6 else 0)+(1 if lu >= 50 else 0)+(1 if mb >= 4 else 0)
    sc['超跌修复'] = (2 if ma20 <= -10 else 0)+(1 if fl >= 50 else 0)+(1 if lu >= 20 and mb <= 3 else 0)
    sc['轮动打地鼠'] = (2 if mb <= 3 else 0)+(1 if sb <= 3 else 0)+(1 if bp <= 7 else 0)+(1 if 20 <= lu <= 50 else 0)
    b = max(sc, key=sc.get); return b, sc[b]

def l4_sector(d):
    c = d.get('top1_concentration', 0); sb = d.get('sector_boom_count', 0)
    if c >= 50 and sb >= 5: return '🎯集中主线', 4
    if c >= 30 or (sb >= 3): return '🔀双线并行', 3
    if sb >= 1: return '📊散乱多线', 2
    return '🌫️无主线', 1

def l5_health(d):
    vlt = d.get('vol_lt_07_pct', 50); fl = d.get('fangliang_pct', 80)
    sl = d.get('suoliang_pct', 5)
    if vlt >= 40 and sl >= 15: return '💎缩量惜售', 4
    if fl <= 70: return '✅量价健康', 3
    if fl <= 85: return '⚖️量价温和', 2
    return '⚠️量价虚胖', 1

def l6_trend(d):
    ma20 = d.get('avg_ma20_dev', 0); lu = d.get('limit_up', 0)
    mb = d.get('max_board', 0); ld = d.get('limit_down', 0)
    if ma20 >= 15 and mb >= 5 and lu >= 40: return '🚀加速冲顶', 4
    if ma20 >= 5 or (mb >= 4 and lu >= 30): return '📈强势延续', 3
    if ma20 >= -5 or lu >= 20: return '↔️震荡筑底', 2
    return '📉超跌反弹', 1

# =========================================
# 新增3层
# =========================================

def l7_cycle(d, prev_days):
    """
    第7层：情绪周期（基于前3天变化趋势）
    7阶段: 绝望→恐慌→悲观→怀疑→乐观→狂热→幻灭
    """
    if not prev_days:
        return '😐平衡', 3

    lu = d.get('limit_up', 0)
    zh = d.get('zh_ratio', 0)
    lu0 = prev_days[0].get('limit_up', 0) if len(prev_days) > 0 else lu
    lu1 = prev_days[1].get('limit_up', 0) if len(prev_days) > 1 else lu0
    zh0 = prev_days[0].get('zh_ratio', 50) if len(prev_days) > 0 else zh
    ld = d.get('limit_down', 0)
    ld0 = prev_days[0].get('limit_down', 0) if len(prev_days) > 0 else ld

    # 3天变化率
    lu_chg = lu - lu1  # vs -2 day
    lu_chg1 = lu - lu0  # vs -1 day
    zh_chg = zh - zh0

    # 绝望：连续下跌后的极致冰点
    if lu <= 10 and ld >= 5 and lu_chg <= 0 and lu1 <= 15:
        return '😱绝望', 0
    # 恐慌：快速崩盘
    if lu_chg <= -20 and lu <= 25:
        return '😰恐慌', 1
    # 悲观：持续低迷
    if lu <= 20 or (zh <= 35 and lu <= 30):
        return '😔悲观', 2
    # 怀疑：开始回暖但不信任
    if lu_chg >= 5 and lu <= 35 and zh <= 50:
        return '🤔怀疑', 3
    # 乐观：持续升温
    if lu >= 30 and lu_chg >= 5 and zh >= 50:
        return '😊乐观', 4
    # 狂热：全面高潮
    if lu >= 60 and zh >= 60 and lu_chg >= 5:
        return '🤩狂热', 5
    # 幻灭：高潮后快速回落
    if lu >= 40 and lu_chg <= -15:
        return '😵幻灭', 6

    return '😐平衡', 3


def l8_rotation(d, prev_rotations):
    """
    第8层：板块轮动速度
    基于前3天板块TOP3的变化
    """
    if not prev_rotations or len(prev_rotations) < 2:
        return '🐢慢速', 2

    top3_today = set(s['name'] for s in prev_rotations[0][:3]) if prev_rotations[0] else set()
    top3_yest = set(s['name'] for s in prev_rotations[1][:3]) if len(prev_rotations) > 1 and prev_rotations[1] else set()

    overlap = len(top3_today & top3_yest)
    sb = d.get('sector_boom_count', 0)

    if overlap >= 2 and sb >= 3:
        return '🐢单主线', 4
    if overlap >= 1 or sb >= 4:
        return '🐇双线轮动', 3
    if sb >= 1:
        return '⚡高速轮动', 2
    return '🌪️混沌', 1


def l9_game(d):
    """
    第9层：大资金博弈格局
    基于游资/机构/散户净额的相对关系
    """
    yz = d.get('youzi_net_wan', 0) or 0
    jg = d.get('jigou_net_wan', 0) or 0
    sh = d.get('sanhu_net_wan', 0) or 0
    lu = d.get('limit_up', 0)

    # 没有龙虎榜数据的用默认
    if yz == 0 and jg == 0 and sh == 0:
        # 用涨停结构推测
        yz_hat = d.get('fangliang_pct', 80)
        if yz_hat >= 70: return '🃏游资主导(推断)', 2
        return '❓未知', 1

    # 判断方向一致
    yz_dir = 1 if yz > 0 else -1 if yz < 0 else 0
    jg_dir = 1 if jg > 0 else -1 if jg < 0 else 0
    sh_dir = 1 if sh > 0 else -1 if sh < 0 else 0

    if yz_dir == jg_dir and yz_dir != 0:
        if yz_dir > 0: return '🤝合力做多', 4
        return '👊合力做空', 1
    if yz_dir > 0 and jg_dir < 0:
        return '🃏游资主导', 3
    if yz_dir < 0 and jg_dir > 0:
        return '🏦机构主导', 3
    return '⚔️分歧', 2


# =========================================
# 主分类器
# =========================================

def classify_full(d, prev_days=None, prev_rotations=None):
    l1n, l1s = l1_heat(d)
    l2n, l2s = l2_style(d)
    l3n, l3s = l3_profit(d)
    l4n, l4s = l4_sector(d)
    l5n, l5s = l5_health(d)
    l6n, l6s = l6_trend(d)
    l7n, l7s = l7_cycle(d, prev_days or [])
    l8n, l8s = l8_rotation(d, prev_rotations or [])
    l9n, l9s = l9_game(d)

    tag = f"{l1n}·{l2n}·{l3n}·{l4n}·{l5n}·{l6n}·{l7n}·{l8n}·{l9n}"
    return tag, {
        'l1_热度': l1n, 'l2_风格': l2n, 'l3_效应': l3n,
        'l4_板块': l4n, 'l5_量价': l5n, 'l6_趋势': l6n,
        'l7_情绪': l7n, 'l8_轮动': l8n, 'l9_博弈': l9n,
    }


# =========================================
# 回填历史
# =========================================

def build_history():
    print(f"\n{'='*55}")
    print(f"📊 九层穿透历史回填 (v6)")
    print(f"{'='*55}")

    print(f"\n[1/4] 加载K线+MA20...")
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

    print(f"  预计算MA20...")
    ma20_cache = {}
    for code, cd in kd_code.items():
        scd = sorted(cd.keys())
        for i, dt in enumerate(scd):
            if i >= 20:
                cls = [cd[scd[j]]['c'] for j in range(i-20, i)]
                ma20 = sum(cls)/20
                cur = cd[dt]['c']
                ma20_cache[(code, dt)] = (cur-ma20)/ma20*100 if ma20 > 0 else 0
            else:
                ma20_cache[(code, dt)] = 0

    print(f"\n[2/4] 加载辅助数据...")
    mb_data = {}
    try:
        conn2 = sqlite3.connect(LIMIT_DB)
        for row in conn2.execute("SELECT date, max_board FROM limit_strength"):
            mb_data[row[0]] = row[1]
        conn2.close()
    except: pass

    sector_daily = defaultdict(list)
    try:
        conn3 = sqlite3.connect(SECTOR_DB)
        for row in conn3.execute("SELECT date, sector_name, limit_up_count FROM sector_daily_index ORDER BY date, limit_up_count DESC"):
            sector_daily[row[0]].append({'name': row[1], 'lu': row[2] or 0})
        conn3.close()
    except: pass

    youzi_data = {}
    try:
        conn4 = sqlite3.connect(LHB_DB)
        for row in conn4.execute("""
            SELECT l.date,
              ROUND(SUM(CASE WHEN d.direction='buy' THEN d.net ELSE 0 END)/10000, 0),
              ROUND(SUM(CASE WHEN d.direction='sell' THEN d.net ELSE 0 END)/10000, 0),
              (SELECT ROUND(SUM(CASE WHEN direction='buy' THEN net ELSE 0 END)/10000, 0)
               FROM lhb_list l2 JOIN lhb_detail d2 ON l2.date=d2.date AND l2.code=d2.code
               WHERE l2.type='03' AND l2.date=l.date),
              (SELECT ROUND(SUM(CASE WHEN direction='sell' THEN net ELSE 0 END)/10000, 0)
               FROM lhb_list l2 JOIN lhb_detail d2 ON l2.date=d2.date AND l2.code=d2.code
               WHERE l2.type='03' AND l2.date=l.date),
              (SELECT ROUND(SUM(CASE WHEN direction='buy' THEN net ELSE 0 END)/10000, 0)
               FROM lhb_list l2 JOIN lhb_detail d2 ON l2.date=d2.date AND l2.code=d2.code
               WHERE l2.type='05' AND l2.date=l.date),
              (SELECT ROUND(SUM(CASE WHEN direction='sell' THEN net ELSE 0 END)/10000, 0)
               FROM lhb_list l2 JOIN lhb_detail d2 ON l2.date=d2.date AND l2.code=d2.code
               WHERE l2.type='05' AND l2.date=l.date)
            FROM lhb_list l JOIN lhb_detail d ON l.date=d.date AND l.code=d.code
            WHERE l.type IN ('04','34','37','38','39','40')
            GROUP BY l.date
        """):
            yz_net = (row[1] or 0) - (row[2] or 0)
            jg_net = (row[3] or 0) - (row[4] or 0)
            sh_net = (row[5] or 0) - (row[6] or 0)
            youzi_data[row[0]] = {'y': yz_net, 'j': jg_net, 's': sh_net}
        conn4.close()
        print(f"  龙虎榜: {len(youzi_data)}天")
    except: pass

    print(f"\n[3/4] 逐日计算9层特征...")
    days = []
    prev_stocks = {}
    sector_history = []  # 用于第8层

    for idx, dt in enumerate(dates):
        if idx % 50 == 0: print(f"  进度{idx}/{len(dates)} ({dt})...")
        stocks = kd_date[dt]
        if not prev_stocks or idx < 20:
            prev_stocks = {s['code']: s for s in stocks}
            sector_history.append(sector_daily.get(dt, []))
            continue

        lu = ld = up = dn = 0; yz = sl = fl = 0
        gap_gt_5 = gap_total = trade_cnt = 0
        vol_lt_07 = vol_gt_3 = 0
        avg_ma20_acc = 0; ma20_cnt = 0

        for s in stocks:
            ps = prev_stocks.get(s['code'])
            if not ps or ps['c'] <= 0: continue
            pc = ps['c']; cc = s['c']; co = s['o']; cv = s['v']
            trade_cnt += 1
            chg = (cc-pc)/pc*100; ochg = (co-pc)/pc*100

            if chg > 0: up += 1
            elif chg < 0: dn += 1

            if ochg > 0: gap_total += 1
            if ochg >= 5: gap_gt_5 += 1

            if ps['v'] > 0:
                vr = cv/ps['v']
                if vr < 0.7: vol_lt_07 += 1
                if vr > 3: vol_gt_3 += 1

            # MA20
            mac = ma20_cache.get((s['code'], dt), 0)
            if abs(mac) > 0:
                avg_ma20_acc += mac; ma20_cnt += 1

            # 涨停
            if chg >= 9.5 and cc >= pc*1.09:
                lu += 1
                is_yz = abs(ochg-10) < 0.5
                code_dates = [d for d in dates if d <= dt][-6:]
                vols = [kd_date[cd] for cd in code_dates if cd in kd_date]
                # 简化量比
                if is_yz: yz += 1
                else: fl += 1  # 简化：没有缩量判断

            if chg <= -9.5 and cc <= pc*0.91: ld += 1

        zh_r = pct(up, trade_cnt)
        mb = mb_data.get(dt, 0)

        # 板块结构
        slist = sector_daily.get(dt, [])
        top1_lu = slist[0]['lu'] if slist else 0
        top3_lu = sum(s['lu'] for s in slist[:3])
        conc = pct(top1_lu, lu) if lu > 0 else 0
        sb = sum(1 for s in slist if s['lu'] >= 3)

        # 龙虎榜
        lhb = youzi_data.get(dt, {'y':0,'j':0,'s':0})

        day = {
            'date': dt,
            'limit_up': lu, 'limit_down': ld, 'zh_ratio': zh_r, 'max_board': mb,
            'yizi_pct': pct(yz, lu), 'suoliang_pct': 0, 'fangliang_pct': pct(fl, lu),
            'youzi_net_wan': lhb['y'], 'jigou_net_wan': lhb['j'], 'sanhu_net_wan': lhb['s'],
            'gap_gt_5_pct': pct(gap_gt_5, trade_cnt),
            'board_2plus_pct': pct(max(0, mb-1), lu),
            'sector_boom_count': sb, 'top1_concentration': conc,
            'vol_lt_07_pct': pct(vol_lt_07, trade_cnt), 'vol_gt_3_pct': pct(vol_gt_3, trade_cnt),
            'avg_ma20_dev': round(avg_ma20_acc/max(ma20_cnt,1), 1),
            'zhaban_rate': 0, 'total_seal_wan': 0,
            'up_count': up, 'down_count': dn,
        }
        days.append(day)
        prev_stocks = {s['code']: s for s in stocks}
        sector_history.append(slist)

    print(f"\n[4/4] 计算第7-9层(需前序数据)...")
    for i, dt in enumerate(days):
        d = days[i]
        prev_d = [days[i-1]] if i > 0 else []
        prev_d2 = [days[i-2], days[i-1]] if i > 1 else [days[i-1]] if i > 0 else []
        prev_rot = [sector_history[i-1], sector_history[i-1]]  # 简化

        tag, detail = classify_full(d, prev_d2, [sector_history[max(0,i-1):i+1]])
        d['_tag'] = tag
        for k, v in detail.items():
            d[f'_{k}'] = v

    with open(HISTORY_OUT, 'w') as f:
        json.dump(days, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存 {len(days)} 天到 {HISTORY_OUT}")

    # 统计
    combo = Counter()
    for d in days:
        combo[d.get('_tag', '?')] += 1

    l7_c = Counter(d.get('_l7_情绪', '?') for d in days)
    l8_c = Counter(d.get('_l8_轮动', '?') for d in days)
    l9_c = Counter(d.get('_l9_博弈', '?') for d in days)

    n = len(days)
    print(f"\n共{n}天, 出现{len(combo)}种组合")
    print(f"\n━━━ 新增3层分布 ━━━")
    print(f"第7层 情绪: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l7_c.most_common())}")
    print(f"第8层 轮动: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l8_c.most_common())}")
    print(f"第9层 博弈: {' | '.join(f'{k}={v}({pct(v,n)}%)' for k,v in l9_c.most_common())}")

    print(f"\n━━━ TOP20组合 ━━━")
    for tag, cnt in combo.most_common(20):
        print(f"  {cnt:3d}天 ({pct(cnt,n):4.1f}%)  {tag}")

    out = {'_meta': {'total_days': n, 'total_types': len(combo)}}
    for tag, cnt in combo.most_common():
        sample_days = [d for d in days if d.get('_tag') == tag][:3]
        out[tag] = {'count': cnt, 'pct': round(cnt/n*100, 1),
                    'samples': [d['date'] for d in sample_days]}

    with open(CLUSTER_OUT, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存到 {CLUSTER_OUT}")
    return days


# =========================================
# 今日分类
# =========================================

def classify_today():
    today_str = str(date.today())
    print(f"\n{'='*55}")
    print(f"📊 今日九层穿透 — {today_str}")
    print(f"{'='*55}")

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
            print("❌ 无今日数据"); return None
    else:
        print("❌ 无今日数据"); return None

    tag, detail = classify_full(day)
    print(f"\n  🏷️  {tag}")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━")
    for k, v in detail.items():
        print(f"  {k}: {v}")

    print(f"\n  关键指标:")
    for k in ['limit_up','zh_ratio','max_board','sector_boom_count',
              'top1_concentration','avg_ma20_dev','youzi_net_wan','jigou_net_wan']:
        print(f"    {k}: {day.get(k, 0)}")

    return {'tag': tag, 'detail': detail}


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--build' in args:
        build_history()
    elif '--today' in args:
        classify_today()
    elif '--stats' in args:
        if os.path.exists(CLUSTER_OUT):
            with open(CLUSTER_OUT) as f:
                d = json.load(f)
            print(f"共{d['_meta']['total_types']}种组合")
            for tag, info in d.items():
                if tag.startswith('_'): continue
                print(f"  {tag:70s} {info['count']:3d}天 ({info['pct']:4.1f}%)")
    else:
        print("用法: --build | --today | --stats")
