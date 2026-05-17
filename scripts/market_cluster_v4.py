#!/usr/bin/env python3
"""
📊 市场多维多层聚类引擎 v4.0
===============================
三层聚类架构：
  第1层：市场热度(4类)  — 做不做？
  第2层：资金风格(4类)  — 怎么做？
  第3层：赚钱效应(5类)  — 做什么？

输出格式：☀️·放量游资·龙头接力  (3层标签组合)

用法：
  python3 scripts/market_cluster_v4.py --build     # 回填历史+聚类
  python3 scripts/market_cluster_v4.py --today     # 分类今日
  python3 scripts/market_cluster_v4.py --auto      # 自动模式
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

HISTORY_OUT = os.path.join(OUT_DIR, 'market_history_v4.json')
CLUSTER_OUT = os.path.join(OUT_DIR, 'market_clusters_v4.json')

# =========================================
# 三层聚类配置
# =========================================

# 第1层：市场热度（基于涨停数+涨跌比+最高板）
LAYER1_HEAT = {
    'name': '热度',
    'features': ['limit_up', 'zh_ratio', 'max_board'],
    'thresholds': {
        '☀️狂热': {'limit_up': 80, 'zh_ratio': 55, 'max_board': 5},
        '🌤活跃': {'limit_up': 40, 'zh_ratio': 40, 'max_board': 3},
        '☁️平淡': {'limit_up': 20, 'zh_ratio': 30, 'max_board': 1},
        # ❄️冰点 = 低于以上所有
    }
}

# 第2层：资金风格（基于涨停质量+资金流向+炸板）
LAYER2_STYLE = {
    'name': '资金风格',
    'features': ['yizi_pct', 'suoliang_pct', 'fangliang_pct',
                 'youzi_net_wan', 'jigou_net_wan',
                 'zhaban_rate', 'gap_gt_5_pct'],
    'types': {
        '放量游资': {'suoliang_pct': 8, 'youzi_net_wan': 200, 'fangliang_pct': 65},
        '缩量惜售': {'suoliang_pct': 12, 'yizi_pct': 10, 'zhaban_rate': 12},
        '机构趋势': {'jigou_net_wan': 100, 'gap_gt_5_pct': 15, 'zh_ratio': 55},
        '散户博弈': {'fangliang_pct': 85, 'zhaban_rate': 20, 'youzi_net_wan': -200},
    }
}

# 第3层：赚钱效应（基于最高板+板块集中度+涨停前趋势）
LAYER3_PROFIT = {
    'name': '赚钱效应',
    'features': ['max_board', 'sector_boom_count', 'board_2plus_pct',
                 'avg_ma20_dev', 'total_seal_wan',
                 'surge_count', 'crash_count'],
    'types': {
        '龙头接力': {'max_board': 7, 'sector_boom_count': 8, 'board_2plus_pct': 12},
        '首板套利': {'max_board': 4, 'limit_up': 30, 'zhaban_rate': 15},
        '板块集群': {'sector_boom_count': 10, 'limit_up': 50, 'max_board': 4},
        '超跌修复': {'avg_ma20_dev': -10, 'crash_count': 3, 'fangliang_pct': 50},
        '轮动打地鼠': {'max_board': 3, 'sector_boom_count': 3, 'surge_count': 8},
    }
}

# =========================================
# K线数据 → 日画像
# =========================================

def sql_val(db, q, defv=0):
    r = subprocess.run(['sqlite3', '-noheader', db], capture_output=True, text=True, timeout=30, input=q)
    return r.stdout.strip() or str(defv)

def sql_json(db, q):
    r = subprocess.run(['sqlite3', '-json', db], capture_output=True, text=True, timeout=30, input=q)
    return json.loads(r.stdout) if r.stdout.strip() else []

def pct(part, total):
    return round(part / max(total, 1) * 100, 1)

def build_history():
    """从K线回填历史数据，计算三维聚类所需特征"""
    print(f"\n{'='*55}")
    print(f"📊 重建多层历史数据 (v4)")
    print(f"{'='*55}")

    # 加载K线
    print(f"\n[1/3] 加载K线...")
    conn = sqlite3.connect(KLINE_DB)
    c = conn.cursor()
    kd_date = defaultdict(list)
    for row in c.execute("SELECT date, code, open, close, high, low, volume FROM kline ORDER BY date"):
        d, code, o, c2, h, l, v = row
        kd_date[d].append({'code': code, 'o': o, 'c': c2, 'h': h, 'l': l, 'v': v})
    dates = sorted(kd_date.keys())
    conn.close()
    print(f"  共{len(dates)}天 ({dates[0]} ~ {dates[-1]})")

    # 加载最高板
    print(f"\n[2/3] 加载辅助数据...")
    mb_data = {}
    try:
        conn2 = sqlite3.connect(LIMIT_DB)
        for row in conn2.execute("SELECT date, max_board FROM limit_strength"):
            mb_data[row[0]] = row[1]
        conn2.close()
    except: pass

    sb_data = {}
    try:
        conn3 = sqlite3.connect(SECTOR_DB)
        for row in conn3.execute("SELECT date, COUNT(*) FROM sector_daily_index WHERE limit_up_count>=3 GROUP BY date"):
            sb_data[row[0]] = row[1]
        conn3.close()
    except: pass

    # 逐日计算
    print(f"\n[3/3] 逐日计算三维特征...")
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
        gap_gt_5 = gap_total = 0
        total_trade = 0

        for s in stocks:
            ps = prev_stocks.get(s['code'])
            if not ps or ps['c'] <= 0: continue
            pc = ps['c']
            cc = s['c']
            co = s['o']
            cv = s['v']
            total_trade += 1
            chg = (cc - pc) / pc * 100
            open_chg = (co - pc) / pc * 100

            if chg > 0: up += 1
            elif chg < 0: dn += 1

            if open_chg > 0: gap_total += 1
            if open_chg >= 5: gap_gt_5 += 1

            if chg >= 9.5 and cc >= pc * 1.09:
                lu += 1
                is_yz = abs(open_chg - 10) < 0.5
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

            if chg <= -9.5 and cc <= pc * 0.91:
                ld += 1

        zh_r = pct(up, total_trade)
        mb = mb_data.get(dt, 0)
        sb = sb_data.get(dt, 0)

        day = {
            'date': dt,
            # === 第1层：热度 ===
            'limit_up': lu, 'zh_ratio': zh_r, 'max_board': mb,
            'limit_down': ld,
            # === 第2层：资金风格 ===
            'yizi_pct': pct(yz, lu), 'suoliang_pct': pct(sl, lu), 'fangliang_pct': pct(fl, lu),
            'zhaban_rate': 0,  # K线无法算炸板
            'gap_gt_5_pct': pct(gap_gt_5, total_trade),
            'youzi_net_wan': 0, 'jigou_net_wan': 0,
            'total_seal_wan': 0,
            # === 第3层：赚钱效应 ===
            'board_2plus_pct': pct(max(0, mb-1), lu),
            'sector_boom_count': sb,
            'avg_ma20_dev': 0,  # K线可算但跳过简化
            'surge_count': 0, 'crash_count': 0,
            'up_count': up, 'down_count': dn,
        }
        days.append(day)
        prev_stocks = {s['code']: s for s in stocks}

    with open(HISTORY_OUT, 'w') as f:
        json.dump(days, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存 {len(days)} 天")
    return days


# =========================================
# 三层分类
# =========================================

def classify_layer1(day):
    """第1层：市场热度"""
    lu = day.get('limit_up', 0)
    zh = day.get('zh_ratio', 0)
    mb = day.get('max_board', 0)
    ld = day.get('limit_down', 0)

    # 特殊判断：跌停潮
    if ld >= 10 and lu < 20:
        return '❄️恐慌冰点', 0

    # 狂热
    if lu >= 80 and zh >= 55 and mb >= 5:
        return '☀️狂热', 4
    if lu >= 60 and zh >= 60:
        return '☀️狂热', 4

    # 活跃
    if lu >= 40 or (lu >= 30 and mb >= 5):
        return '🌤活跃', 3
    if lu >= 30 and zh >= 50:
        return '🌤活跃', 3

    # 平淡
    if lu >= 15 or mb >= 3:
        return '☁️平淡', 2
    if lu >= 10 and zh >= 35:
        return '☁️平淡', 2

    # 冰点
    return '❄️冰点', 1


def classify_layer2(day):
    """第2层：资金风格"""
    yz = day.get('yizi_pct', 0)
    sl = day.get('suoliang_pct', 0)
    fl = day.get('fangliang_pct', 80)
    yz_net = day.get('youzi_net_wan', 0)
    jg_net = day.get('jigou_net_wan', 0)
    zb = day.get('zhaban_rate', 10)
    gap5 = day.get('gap_gt_5_pct', 0)
    zh = day.get('zh_ratio', 50)

    scores = {}

    # 放量游资型
    s1 = 0
    if fl >= 65: s1 += 2
    if sl <= 8: s1 += 1
    if yz_net >= 200: s1 += 2
    if yz_net >= 0: s1 += 1
    scores['放量游资'] = s1

    # 缩量惜售型
    s2 = 0
    if sl >= 12: s2 += 2
    if yz >= 10: s2 += 2
    if zb <= 12: s2 += 1
    scores['缩量惜售'] = s2

    # 机构趋势型
    s3 = 0
    if jg_net >= 100: s3 += 2
    if gap5 >= 15: s3 += 1
    if zh >= 55: s3 += 1
    if fl >= 60 and zb <= 15: s3 += 1
    scores['机构趋势'] = s3

    # 散户博弈型
    s4 = 0
    if fl >= 85: s4 += 2
    if zb >= 20: s4 += 2
    if yz_net <= -200: s4 += 1
    scores['散户博弈'] = s4

    best = max(scores, key=scores.get)
    return best, scores[best]


def classify_layer3(day):
    """第3层：赚钱效应"""
    mb = day.get('max_board', 0)
    sb = day.get('sector_boom_count', 0)
    bp = day.get('board_2plus_pct', 0)
    lu = day.get('limit_up', 0)
    zb = day.get('zhaban_rate', 10)
    ma20 = day.get('avg_ma20_dev', 0)
    crash = day.get('crash_count', 0)
    fl = day.get('fangliang_pct', 80)
    surge = day.get('surge_count', 0)

    scores = {}

    # 龙头接力
    s1 = 0
    if mb >= 7: s1 += 3
    elif mb >= 5: s1 += 2
    if sb >= 8: s1 += 1
    if bp >= 12: s1 += 1
    scores['龙头接力'] = s1

    # 首板套利
    s2 = 0
    if mb <= 4: s2 += 1
    if lu >= 30: s2 += 1
    if bp <= 8: s2 += 1
    if zb >= 15: s2 += 1
    scores['首板套利'] = s2

    # 板块集群
    s3 = 0
    if sb >= 10: s3 += 3
    elif sb >= 6: s3 += 2
    if lu >= 50: s3 += 1
    if mb >= 4: s3 += 1
    scores['板块集群'] = s3

    # 超跌修复
    s4 = 0
    if ma20 <= -10: s4 += 2
    if crash >= 3: s4 += 1
    if fl >= 50: s4 += 1
    if lu >= 20 and mb <= 3: s4 += 1
    scores['超跌修复'] = s4

    # 轮动打地鼠
    s5 = 0
    if mb <= 3: s5 += 2
    if sb <= 3: s5 += 1
    if surge >= 8: s5 += 1
    if bp <= 7: s5 += 1
    if lu >= 20 and lu <= 50: s5 += 1
    scores['轮动打地鼠'] = s5

    best = max(scores, key=scores.get)
    return best, scores[best]


def classify_day(day):
    """三层分类，返回组合标签和详情"""
    l1, l1s = classify_layer1(day)
    l2, l2s = classify_layer2(day)
    l3, l3s = classify_layer3(day)

    # 修正：如果龙头接力得分=0但首板套利=0，则归为轮动
    return f"{l1}·{l2}·{l3}", {
        'layer1': l1, 'layer1_score': l1s,
        'layer2': l2, 'layer2_score': l2s,
        'layer3': l3, 'layer3_score': l3s,
    }


def run_classify(days):
    """对所有天数做三层分类"""
    print(f"\n{'='*55}")
    print(f"📊 三层分类结果")
    print(f"{'='*55}")

    results = {}
    combo_counts = Counter()

    for day in days:
        tag, detail = classify_day(day)
        combo_counts[tag] += 1
        day['_tag'] = tag
        day['_l1'] = detail['layer1']
        day['_l2'] = detail['layer2']
        day['_l3'] = detail['layer3']

    # 按组合出现频次排序
    print(f"\n共{len(days)}天, 出现{len(combo_counts)}种组合\n")
    for tag, cnt in combo_counts.most_common(30):
        p = cnt / len(days) * 100
        print(f"  {tag:30s}  {cnt:3d}天 ({p:4.1f}%)")

    # 保存
    out = {}
    for tag, cnt in combo_counts.most_common():
        # 找代表日
        sample_days = [d for d in days if d['_tag'] == tag][:3]
        samples = [d['date'] for d in sample_days]

        # 平均特征
        feat_avg = {}
        feat_list = ['limit_up', 'zh_ratio', 'max_board', 'yizi_pct',
                     'suoliang_pct', 'fangliang_pct', 'sector_boom_count',
                     'board_2plus_pct', 'limit_down']
        for f in feat_list:
            vals = [d.get(f, 0) for d in sample_days] if sample_days else [0]
            feat_avg[f] = round(sum(vals) / len(vals), 1)

        out[tag] = {
            'count': cnt,
            'pct': round(cnt / len(days) * 100, 1),
            'samples': samples,
            'features': feat_avg,
        }

    out['_meta'] = {
        'total_days': len(days),
        'total_types': len(combo_counts),
        'layers': {
            'layer1': '市场热度(☀️狂热/🌤活跃/☁️平淡/❄️冰点)',
            'layer2': '资金风格(放量游资/缩量惜售/机构趋势/散户博弈)',
            'layer3': '赚钱效应(龙头接力/首板套利/板块集群/超跌修复/轮动打地鼠)',
        }
    }

    with open(CLUSTER_OUT, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存到 {CLUSTER_OUT}")

    return combo_counts


def classify_today():
    """分类今天的市场"""
    today_str = str(date.today())
    print(f"\n{'='*55}")
    print(f"📊 今日三层分类 — {today_str}")
    print(f"{'='*55}")

    # 优先从market_daily.db读取
    rows = sql_json(MARKET_DB, f"SELECT * FROM day_full WHERE date='{today_str}'")
    if rows:
        day = rows[0]
    else:
        # 从历史数据中看是否有今日（没有就是新日）
        if os.path.exists(HISTORY_OUT):
            with open(HISTORY_OUT) as f:
                hist = json.load(f)
            hist_days = {d['date']: d for d in hist}
            if today_str in hist_days:
                day = hist_days[today_str]
            else:
                print(f"❌ 无今日数据")
                return None
        else:
            print(f"❌ 无今日数据，先 --build")
            return None

    tag, detail = classify_day(day)
    print(f"\n  🏷️  {tag}")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━")
    print(f"  第1层 市场热度: {detail['layer1']} (得分{detail['layer1_score']})")
    print(f"  第2层 资金风格: {detail['layer2']} (得分{detail['layer2_score']})")
    print(f"  第3层 赚钱效应: {detail['layer3']} (得分{detail['layer3_score']})")

    # 关键指标
    metrics = {
        '涨停': day.get('limit_up', 0),
        '跌停': day.get('limit_down', 0),
        '涨跌比': f"{day.get('zh_ratio', 0)}%",
        '最高板': day.get('max_board', 0),
        '一字比': f"{day.get('yizi_pct', 0)}%",
        '缩量比': f"{day.get('suoliang_pct', 0)}%",
        '板块爆发': day.get('sector_boom_count', 0),
    }
    print(f"\n  关键指标:")
    for k, v in metrics.items():
        print(f"    {k}: {v}")

    # 策略建议
    strategies = get_strategies(tag, day)
    print(f"\n  策略建议:")
    for s in strategies:
        print(f"    {s}")

    return {'tag': tag, 'detail': detail, 'metrics': metrics, 'strategies': strategies}


def get_strategies(tag, day):
    """基于三层标签生成策略建议"""
    l1 = tag.split('·')[0] if '·' in tag else ''
    l2 = tag.split('·')[1] if '·' in tag and len(tag.split('·')) > 1 else ''
    l3 = tag.split('·')[2] if '·' in tag and len(tag.split('·')) > 2 else ''

    strategies = []

    # 仓位建议
    if '狂热' in l1:
        strategies.append('📌 仓位: 满仓(80~100%)')
    elif '活跃' in l1:
        strategies.append('📌 仓位: 重仓(60~80%)')
    elif '平淡' in l1:
        strategies.append('📌 仓位: 半仓(30~50%)')
    elif '冰点' in l1 or '恐慌' in l1:
        strategies.append('📌 仓位: 轻仓(10~20%)或空仓')

    # 模式建议
    if '放量游资' in l2:
        strategies.append('🎯 模式: 打板(游资票为主)')
        strategies.append('   M07板块爆发打板 +9.96%/94%')
        strategies.append('   M06总龙头打板 +4.03%/71%')
    elif '缩量惜售' in l2:
        strategies.append('🎯 模式: 隔夜溢价+缩量板')
        strategies.append('   M01隔夜溢价(缩量<0.7) +5.62%/85%')
        strategies.append('   M02极缩<0.3 +7.77%/96%')
    elif '机构趋势' in l2:
        strategies.append('🎯 模式: 趋势低吸(机构票)')
        strategies.append('   M10换手板接力 +3.23%/70%')
        strategies.append('   M14超跌反弹低吸 +3.05%/83%')
    elif '散户博弈' in l2:
        strategies.append('🎯 模式: 一字开板接力')
        strategies.append('   M11一字开板接力 +4.95%/74%')
        strategies.append('   回避烂板放量股')

    # 选股建议
    if '龙头接力' in l3:
        strategies.append('🎯 选股: 追高板(3板以上)')
    elif '首板套利' in l3:
        strategies.append('🎯 选股: 首板+缩量')
    elif '板块集群' in l3:
        strategies.append('🎯 选股: 板块龙头+跟风')
    elif '超跌修复' in l3:
        strategies.append('🎯 选股: MA20乖离<-15%的超跌股')
    elif '轮动打地鼠' in l3:
        strategies.append('🎯 选股: 不追高,等回调低吸')

    return strategies


if __name__ == '__main__':
    args = sys.argv[1:]

    if '--build' in args:
        days = build_history()
        run_classify(days)
    elif '--classify' in args:
        if os.path.exists(HISTORY_OUT):
            with open(HISTORY_OUT) as f:
                days = json.load(f)
            run_classify(days)
        else:
            print("先 --build")
    elif '--today' in args:
        classify_today()
    elif '--auto' in args:
        res = classify_today()
        if res:
            # 输出简洁摘要
            print(f"\n{'='*35}")
            print(f"📋 今日市场三维画像")
            print(f"{'='*35}")
            print(f"  {res['tag']}")
    else:
        print("用法:")
        print("  --build    回填历史数据+三层聚类")
        print("  --classify 对已有历史运行三层分类")
        print("  --today    分类今日市场")
