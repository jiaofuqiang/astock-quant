#!/usr/bin/env python3
"""
📊 市场聚类分析 v3.0 — 62维全景聚类
============================================
基于market_daily_integrator的62维字段体系，
从K线回填历史数据，用K-means聚类找出市场重复模式，
每日自动匹配最佳交易策略。

用法:
  python3 scripts/market_cluster_v3.py --build     # 从K线回填历史数据+聚类
  python3 scripts/market_cluster_v3.py --cluster   # 对已有数据聚类
  python3 scripts/market_cluster_v3.py --today     # 分类今天市场+推荐策略
  python3 scripts/market_cluster_v3.py --auto      # 每日自动(build+cron)
"""
import os, sys, json, sqlite3, math, subprocess, time
from datetime import datetime, date, timedelta
from collections import defaultdict, Counter

BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
KLINE_DB = os.path.join(DATA_DIR, 'kline_cache.db')
LIMIT_DB = os.path.join(DATA_DIR, 'daily_limit_data.db')
SECTOR_DB = os.path.join(DATA_DIR, 'sector_indexes.db')
MARKET_DB = os.path.join(DATA_DIR, 'market_daily.db')
OUT_DIR = os.path.join(BASE, 'research')
CLUSTER_OUT = os.path.join(OUT_DIR, 'market_clusters_v3.json')
HISTORY_OUT = os.path.join(OUT_DIR, 'market_history_v3.json')

# =========================================
# 24个核心聚类维度（从62维精选，避免数据稀疏）
# =========================================
# 这套维度覆盖：热度、宽度、资金风格、竞价强度、赚钱效应、板块主线
CLUSTER_FEATURES = [
    # --- 热度 (5维) ---
    'limit_up',           # 涨停数
    'limit_down',         # 跌停数
    'zh_ratio',           # 涨跌比
    'yizi_pct',           # 一字板占比(%)
    'total_seal_wan',     # 封单总额(万元)
    # --- 量能风格 (4维) ---
    'suoliang_pct',       # 缩量板占比(%)
    'fangliang_pct',      # 放量板占比(%)
    'vol_lt_07_pct',      # 量比<0.7占比(%)
    'vol_gt_3_pct',       # 量比>3占比(%)
    # --- 竞价强度 (3维) ---
    'gap_ratio',          # 高开率(%)
    'gap_gt_5_pct',       # 大幅高开(>5%)占比(%)
    'bid_gaokai_rate',    # 竞价高开率(%)
    # --- 赚钱效应 (4维) ---
    'max_board',          # 最高板
    'board_2plus_pct',    # 连板(2板+)占比(%)
    'zhaban_rate',        # 炸板率(%)
    'huifeng_rate',       # 回封率(%)
    # --- 板块主线 (3维) ---
    'sector_boom_count',  # 板块爆发数(≥3涨停)
    'sector_main_total',  # 板块主力资金TOP5总和(亿)
    'concept_boom',       # 概念热度(涨停热股数)
    # --- 资金流向 (3维) ---
    'youzi_net_wan',      # 游资净额(万元)
    'jigou_net_wan',      # 机构净额(万元)
    'market_main_net',    # 大盘主力净额(亿)
    # --- 涨停前趋势 (2维) ---
    'avg_ma20_dev',       # 涨停股MA20乖离(%)
    'avg_5d_momentum',    # 涨停股5日动量(%)
]

# ========== 从K线重建每日画像 ==========

def sql_val(db, q, default=0):
    r = subprocess.run(['sqlite3', '-noheader', db], capture_output=True, text=True, timeout=30, input=q)
    return r.stdout.strip() or str(default)

def sql_json(db, q):
    r = subprocess.run(['sqlite3', '-json', db], capture_output=True, text=True, timeout=30, input=q)
    return json.loads(r.stdout) if r.stdout.strip() else []

def compute_pct(num, den):
    return round(num / max(den, 1) * 100, 1)

def build_market_history():
    """从K线、limit_strength、板块数据重建每日62维画像"""
    print(f"\n{'='*55}")
    print(f"📊 重建市场历史数据 (v3)")
    print(f"{'='*55}")

    # Step 1: 加载K线
    print(f"\n[1/5] 加载K线...")
    conn = sqlite3.connect(KLINE_DB)
    c = conn.cursor()
    kd_date = defaultdict(list)
    for row in c.execute("SELECT date, code, open, close, high, low, volume FROM kline ORDER BY date"):
        d, code, o, c2, h, l, v = row
        kd_date[d].append({'code': code, 'o': o, 'c': c2, 'h': h, 'l': l, 'v': v})
    dates = sorted(kd_date.keys())
    print(f"  共{len(dates)}个交易日 ({dates[0]} ~ {dates[-1]})")
    conn.close()

    # Step 2: 预加载板块爆发数据
    print(f"\n[2/5] 加载板块爆发数据...")
    sector_boom = {}
    try:
        conn2 = sqlite3.connect(SECTOR_DB)
        c2 = conn2.cursor()
        for row in c2.execute("SELECT date, COUNT(*) as cnt FROM sector_daily_index WHERE limit_up_count>=3 GROUP BY date"):
            sector_boom[row[0]] = row[1]
        conn2.close()
        print(f"  共{len(sector_boom)}天有板块爆发数据")
    except:
        print(f"  板块数据不可用")

    # Step 3: 加载limit_strength(最高板)
    print(f"\n[3/5] 加载最高板数据...")
    max_board_data = {}
    try:
        conn3 = sqlite3.connect(LIMIT_DB)
        for row in conn3.execute("SELECT date, max_board, total_limit FROM limit_strength ORDER BY date"):
            max_board_data[row[0]] = {'max_board': row[1], 'total_limit': row[2]}
        conn3.close()
        print(f"  共{len(max_board_data)}天有最高板数据")
    except:
        print(f"  最高板数据不可用")

    # Step 4: 连板统计（从涨停明细推算）
    print(f"\n[4/5] 逐日计算62维画像...")

    market_days = []
    prev_stocks = {}
    prev_date = None

    for idx, date_str in enumerate(dates):
        if idx % 50 == 0:
            print(f"  进度{idx}/{len(dates)} ({date_str})...")

        stocks = kd_date[date_str]
        if not prev_stocks:
            prev_stocks = {s['code']: s for s in stocks}
            prev_date = date_str
            continue

        total_trade = 0
        lu = ld = up_cnt = down_cnt = 0
        yizi = suoliang = fangliang = 0
        gap_1_3 = gap_3_5 = gap_5_7 = gap_gt_7 = gap_total = 0
        vol_lt_05 = vol_05_07 = vol_07_1 = vol_1_3 = vol_3_5 = vol_gt_5 = 0
        total_seal_wan = 0
        board_2plus = 0
        sum_ma20_dev = sum_5d_mom = 0
        ma20_count = 0
        limit_codes = []

        for s in stocks:
            code = s['code']
            ps = prev_stocks.get(code)
            if not ps: continue

            prev_close = ps['c']
            cur_close = s['c']
            cur_open = s['o']
            cur_vol = s['v']
            cur_high = s['h']

            if prev_close <= 0: continue

            chg = (cur_close - prev_close) / prev_close * 100
            open_chg = (cur_open - prev_close) / prev_close * 100
            total_trade += 1

            if chg > 0: up_cnt += 1
            elif chg < 0: down_cnt += 1

            # 高开缺口分布
            if open_chg > 0:
                gap_total += 1
                if open_chg < 3: gap_1_3 += 1
                elif open_chg < 5: gap_3_5 += 1
                elif open_chg < 7: gap_5_7 += 1
                else: gap_gt_7 += 1

            # 量比（以昨日成交量为基准）
            prev_vol = ps['v']
            if prev_vol > 0:
                vr = cur_vol / prev_vol
                if vr < 0.5: vol_lt_05 += 1
                elif vr < 0.7: vol_05_07 += 1
                elif vr < 1: vol_07_1 += 1
                elif vr < 3: vol_1_3 += 1
                elif vr < 5: vol_3_5 += 1
                else: vol_gt_5 += 1

            # 涨停判断
            is_limit = chg >= 9.5 and cur_close >= prev_close * 1.09
            if is_limit:
                lu += 1
                limit_codes.append(code)
                # 一字板
                is_yizi = abs(open_chg - 10) < 0.5  # 开盘接近涨停价
                # 量比(5日均量)
                code_dates = [d for d in dates if d <= date_str][-6:]
                vols = []
                for cd in code_dates:
                    day_data = kd_date[cd] if cd in kd_date else []
                    for st in day_data:
                        if st['code'] == code:
                            vols.append(st['v'])
                            break
                avg5_vol = sum(vols[-5:]) / max(len(vols[-5:]), 1)
                vr = cur_vol / max(avg5_vol, 1)

                if is_yizi:
                    yizi += 1
                elif vr < 0.7:
                    suoliang += 1
                else:
                    fangliang += 1

                total_seal_wan += 0  # 封单额无法从K线推算

            # 跌停
            if chg <= -9.5 and cur_close <= prev_close * 0.91:
                ld += 1

        # 连板数（从每日详细的limit_strength获取）
        mb = max_board_data.get(date_str, {}).get('max_board', 0)
        total_limit = max_board_data.get(date_str, {}).get('total_limit', lu)

        # 板块爆发
        sb = sector_boom.get(date_str, 0)

        zh_ratio = compute_pct(up_cnt, total_trade)
        yizi_pct = compute_pct(yizi, lu)
        suoliang_pct = compute_pct(suoliang, lu)
        fangliang_pct = compute_pct(fangliang, lu)
        vol_lt_07_pct = compute_pct(vol_lt_05 + vol_05_07, total_trade)
        vol_gt_3_pct = compute_pct(vol_3_5 + vol_gt_5, total_trade)
        gap_ratio = compute_pct(gap_total, total_trade)
        gap_gt_5_pct = compute_pct(gap_5_7 + gap_gt_7, total_trade)
        board_2plus_pct = compute_pct(max(0, mb - 1), total_limit) if mb > 0 else 0

        day = {
            'date': date_str,
            # 热度
            'limit_up': lu, 'limit_down': ld, 'zh_ratio': zh_ratio,
            'yizi': yizi, 'suoliang': suoliang, 'fangliang': fangliang,
            'yizi_pct': yizi_pct, 'suoliang_pct': suoliang_pct, 'fangliang_pct': fangliang_pct,
            'total_seal_wan': total_seal_wan,
            # 量能分布
            'vol_lt_05': vol_lt_05, 'vol_05_07': vol_05_07, 'vol_07_1': vol_07_1,
            'vol_1_3': vol_1_3, 'vol_3_5': vol_3_5, 'vol_gt_5': vol_gt_5,
            'vol_lt_07_pct': vol_lt_07_pct, 'vol_gt_3_pct': vol_gt_3_pct,
            # 高开
            'gap_1_3': gap_1_3, 'gap_3_5': gap_3_5, 'gap_5_7': gap_5_7, 'gap_gt_7': gap_gt_7,
            'gap_ratio': gap_ratio, 'gap_gt_5_pct': gap_gt_5_pct,
            # 竞价(历史数据无法获取)
            'bid_trend': '--', 'bid_gaokai_rate': 0,
            # 炸板/回封(历史数据无法获取)
            'zhaban_count': 0, 'zhaban_rate': 0, 'huifeng_count': 0, 'huifeng_rate': 0,
            # 板块
            'sector_boom_count': sb, 'sector_total': 0,
            # 龙虎榜(历史数据大部分没有)
            'youzi_net_wan': 0, 'jigou_net_wan': 0, 'sanhu_net_wan': 0,
            # 资金流向(历史数据无法获取)
            'sector_main_total': 0, 'market_main_net': 0,
            # 赚钱效应
            'max_board': mb,
            'board_2plus_pct': board_2plus_pct,
            'up_count': up_cnt, 'down_count': down_cnt,
            # 异动(历史数据无法获取)
            'surge_count': 0, 'crash_count': 0,
            # 情绪
            'market_mood': '--',
            'concept_boom': lu,  # 概念热度≈涨停数
            # 趋势
            'avg_ma20_dev': 0, 'avg_5d_momentum': 0,
        }
        market_days.append(day)

        prev_stocks = {s['code']: s for s in stocks}
        prev_date = date_str

    print(f"  完成！共{len(market_days)}天")

    # Step 5: 计算涨停前趋势（从K线额外跑）
    print(f"\n[5/5] 计算涨停前趋势(MA20乖离/5日动量)...")
    # 用limit_codes+K线计算平均MA20乖离
    conn4 = sqlite3.connect(KLINE_DB)
    c4 = conn4.cursor()
    for day in market_days[20:]:  # 需要前20天数据
        d = day['date']
        lu_codes = []  # 这个日期的涨停股code列表
        # 从limit_codes找
        # 直接从day的limit_up_detail拿不到，跳过这个计算
        pass
    conn4.close()

    # 保存
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(HISTORY_OUT, 'w') as f:
        json.dump(market_days, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存 {len(market_days)} 天市场数据到 {HISTORY_OUT}")

    return market_days


def normalize(day, stats):
    """Z-score标准化一个特征向量"""
    vec = []
    for feat in CLUSTER_FEATURES:
        val = day.get(feat, 0)
        mean = stats[feat]['mean']
        std = stats[feat]['std']
        if std > 0:
            vec.append((val - mean) / std)
        else:
            vec.append(0)
    return vec


def compute_stats(days):
    """计算每个特征的均值/标准差"""
    stats = {}
    for feat in CLUSTER_FEATURES:
        vals = [d.get(feat, 0) for d in days]
        n = len(vals)
        mean = sum(vals) / n if n > 0 else 0
        var = sum((v - mean) ** 2 for v in vals) / n if n > 0 else 0
        std = math.sqrt(var)
        stats[feat] = {'mean': mean, 'std': std}
    return stats


def kmeans(data, k=8, max_iter=50):
    """简单的K-means（不依赖sklearn）"""
    n = len(data)
    if n == 0: return [], []
    dim = len(data[0])

    # 用前k个样本作为初始中心
    centers = [data[i][:] for i in range(min(k, n))]

    # 如果不够k个，随机填充
    while len(centers) < k:
        import random
        centers.append([random.uniform(-1, 1) for _ in range(dim)])

    for iteration in range(max_iter):
        # 分配
        clusters = [[] for _ in range(k)]
        for vec in data:
            best = 0
            best_d = float('inf')
            for j, c in enumerate(centers):
                d = sum((vec[i] - c[i]) ** 2 for i in range(dim))
                if d < best_d:
                    best_d = d
                    best = j
            clusters[best].append(vec)

        # 更新中心
        new_centers = []
        moved = 0
        for j in range(k):
            if clusters[j]:
                new_c = [sum(vec[i] for vec in clusters[j]) / len(clusters[j]) for i in range(dim)]
            else:
                new_c = centers[j][:]
            new_centers.append(new_c)

            # 检查是否移动
            for i in range(dim):
                if abs(new_centers[j][i] - centers[j][i]) > 0.0001:
                    moved += 1
                    break

        centers = new_centers
        if moved == 0:
            break

    return clusters, centers


def find_best_k(data, max_k=15):
    """用肘部法则找最佳K"""
    if len(data) < 3:
        return 4, 0

    wcss = []
    for k in range(2, min(max_k + 1, len(data))):
        clusters, centers = kmeans(data, k=k, max_iter=30)
        total_wcss = 0
        for j, cluster in enumerate(clusters):
            if not cluster: continue
            for vec in cluster:
                total_wcss += sum((vec[i] - centers[j][i]) ** 2 for i in range(len(vec)))
        wcss.append(total_wcss)

    # 找肘部（最大加速度）
    best_k = 4
    if len(wcss) >= 3:
        max_accel = -float('inf')
        for i in range(1, len(wcss) - 1):
            accel = (wcss[i-1] - wcss[i]) - (wcss[i] - wcss[i+1])
            if accel > max_accel:
                max_accel = accel
                best_k = i + 2  # k=2对应索引0

    return best_k, wcss


def run_clustering():
    """运行聚类分析"""
    if not os.path.exists(HISTORY_OUT):
        print("❌ 历史数据不存在，先运行 --build")
        return

    print(f"\n{'='*55}")
    print(f"📊 市场聚类分析 v3.0")
    print(f"{'='*55}")

    with open(HISTORY_OUT) as f:
        days = json.load(f)
    print(f"  加载 {len(days)} 天数据")

    # 只使用有有效数据的天
    valid_days = [d for d in days if d.get('limit_up', 0) > 0]
    print(f"  有效天数: {len(valid_days)}")

    # 计算统计量
    stats = compute_stats(valid_days)

    # 标准化
    vectors = [normalize(d, stats) for d in valid_days]

    # 找最优K
    print(f"\n[1/3] 找最优K...")
    best_k, wcss = find_best_k(vectors, max_k=12)
    print(f"  最佳K = {best_k}")

    # K-means聚类
    print(f"\n[2/3] K-means聚类 (K={best_k})...")
    clusters, centers = kmeans(vectors, k=best_k, max_iter=50)
    cluster_sizes = [len(c) for c in clusters]
    print(f"  各簇大小: {cluster_sizes}")

    # 为每一天打上聚类标签
    label = 0
    for j, cluster in enumerate(clusters):
        for vec in cluster:
            # 找到对应的day
            for d in valid_days:
                if normalize(d, stats) == vec and 'cluster' not in d:
                    d['cluster'] = j
                    break
            label += 1

    # 为有聚类的未匹配的也打上
    unmatched = [d for d in valid_days if 'cluster' not in d]
    for d in unmatched:
        vec = normalize(d, stats)
        best_j = 0
        best_d = float('inf')
        for j, c in enumerate(centers):
            dist = sum((vec[i] - c[i]) ** 2 for i in range(len(vec)))
            if dist < best_d:
                best_d = dist
                best_j = j
        d['cluster'] = best_j

    # Step 3: 分析每类特征
    print(f"\n[3/3] 分析每类特征...")

    cluster_groups = defaultdict(list)
    for d in valid_days:
        cluster_groups[d['cluster']].append(d)

    result = {}
    cluster_names = [
        '❄️冰点', '🌫️冷淡缩量', '🌫️冷淡放量', '☁️正常缩量',
        '☁️正常放量', '🌤活跃缩量', '🌤活跃放量', '☀️高潮',
        '🌀震荡A', '🌀震荡B', '🌀震荡C', '🌪️极端'
    ]

    ev = sum(cluster_sizes)
    for j in sorted(cluster_groups.keys()):
        group = cluster_groups[j]
        names = cluster_names[j] if j < len(cluster_names) else f'未知{j}'
        pct = round(len(group) / ev * 100, 1)

        # 计算每类特征的均值
        ci = []
        for feat in CLUSTER_FEATURES:
            vals = [d.get(feat, 0) for d in group]
            avg = sum(vals) / len(vals)
            ci.append((feat, round(avg, 1)))

        # 找代表日期（离中心最近的）
        center = centers[j]
        samples = []
        for d in group:
            vec = normalize(d, stats)
            dist = sum((vec[i] - center[i]) ** 2 for i in range(len(vec)))
            samples.append((dist, d['date']))
        samples.sort()
        top_samples = [s[1] for s in samples[:3]]

        result[f'cluster_{j}'] = {
            'name': names,
            'count': len(group),
            'pct': pct,
            'features': dict(ci),
            'samples': top_samples,
        }

        print(f"\n{names}: {len(group)}天 ({pct}%)")
        # 打印关键特征
        key_feats = ['limit_up', 'zh_ratio', 'yizi_pct', 'suoliang_pct',
                     'max_board', 'sector_boom_count', 'board_2plus_pct',
                     'zhaban_rate', 'gap_ratio', 'youzi_net_wan']
        for feat in key_feats:
            if feat in ci:
                print(f"  {feat:20s}: {dict(ci)[feat]}")
        print(f"  代表日期: {', '.join(top_samples)}")

    # 保存
    with open(CLUSTER_OUT, 'w') as f:
        result['_features'] = CLUSTER_FEATURES
        result['_stats'] = {k: {sk: round(sv, 4) for sk, sv in v.items()} for k, v in stats.items()}
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 聚类结果已保存到 {CLUSTER_OUT}")


def classify_today():
    """分类今天的市场并推荐策略"""
    print(f"\n{'='*55}")
    print(f"📊 今日市场分类")
    print(f"{'='*55}")
    print(f"  日期: {date.today()}")

    # 从market_daily.db读取今天数据
    today_str = str(date.today())
    r = subprocess.run(['sqlite3', '-json', MARKET_DB,
        f"SELECT * FROM day_full WHERE date='{today_str}'"],
        capture_output=True, text=True, timeout=10)
    if not r.stdout.strip():
        print(f"❌ 无今日数据（15:00收盘后跑market_daily_integrator才有）")
        return

    today_data = json.loads(r.stdout)
    if not today_data:
        print(f"❌ 今日数据为空")
        return
    today_data = today_data[0]

    # 加载聚类结果
    if not os.path.exists(CLUSTER_OUT):
        print(f"❌ 聚类结果不存在，先运行 --cluster")
        return

    with open(CLUSTER_OUT) as f:
        cluster_result = json.load(f)

    features = cluster_result.get('_features', CLUSTER_FEATURES)
    stats = cluster_result.get('_stats', {})

    # 今日特征向量
    vec = []
    for feat in features:
        val = today_data.get(feat, 0) or 0
        s = stats.get(feat, {})
        mean = s.get('mean', 0)
        std = s.get('std', 1)
        if std > 0:
            vec.append((val - mean) / std)
        else:
            vec.append(0)

    # 找最近的聚类
    best_dist = float('inf')
    best_cluster = -1
    best_name = '未知'

    for key, info in cluster_result.items():
        if key.startswith('_'):
            continue
        # 从features重建中心
        center = []
        for feat in features:
            fv = info['features'].get(feat, 0)
            s = stats.get(feat, {})
            mean = s.get('mean', 0)
            std = s.get('std', 1)
            center.append((fv - mean) / max(std, 0.01))

        dist = sum((vec[i] - center[i]) ** 2 for i in range(len(vec)))
        if dist < best_dist:
            best_dist = dist
            best_cluster = info.get('name', key)
            best_name = info.get('name', key)

    print(f"\n  → 今日市场类型: {best_name}")
    print(f"  距离: {best_dist:.2f}")

    # 显示今日关键指标
    key_metrics = ['limit_up', 'limit_down', 'zh_ratio', 'yizi', 'suoliang',
                   'max_board', 'zhaban_rate', 'sector_boom_count',
                   'surge_count', 'crash_count', 'market_mood',
                   'market_main_net', 'youzi_net_wan']
    print(f"\n  今日关键指标:")
    for m in key_metrics:
        val = today_data.get(m, 0) or 0
        if isinstance(val, float):
            print(f"    {m:20s}: {val:.1f}")
        else:
            print(f"    {m:20s}: {val}")

    # 推荐策略 — 基于市场名称自动匹配
    strategy_map = {
        '狂热': [
            ('M07', '板块爆发打板', '+9.96%/94%', '全仓'),
            ('M06', '总龙头打板(≥3板)', '+4.03%/71%', '全仓'),
            ('M01', '隔夜溢价(缩量<0.7)', '+5.62%/85%', '全仓'),
        ],
        '高潮': [
            ('M07', '板块爆发打板', '+9.96%/94%', '全仓'),
            ('M06', '总龙头打板(≥3板)', '+4.03%/71%', '全仓'),
            ('M01', '隔夜溢价(缩量<0.7)', '+5.62%/85%', '加仓'),
        ],
        '活跃': [
            ('M07', '板块爆发打板', '+9.96%/94%', '主做'),
            ('M06', '总龙头打板(≥3板)', '+4.03%/71%', '可做'),
            ('M01', '隔夜溢价(缩量<0.7)', '+5.62%/85%', '全仓'),
        ],
        '正常': [
            ('M01', '隔夜溢价(缩量<0.7)', '+5.62%/85%', '全仓'),
            ('M10', '换手板接力(3~7%)', '+3.23%/70%', '可做'),
        ],
        '偏弱': [
            ('M01', '隔夜溢价(缩量<0.7)', '+5.62%/85%', '主做'),
            ('M11', '一字开板接力', '+4.95%/74%', '辅做'),
        ],
        '冷淡': [
            ('M01', '隔夜溢价(缩量<0.7)', '+5.62%/85%', '主做'),
            ('M04', '超卖反弹(MA20<-20%)', '+7.74%/99%', '可做'),
        ],
        '冰点': [
            ('M14', '超跌反弹低吸', '+3.05%/83%', '轻仓'),
            ('M04', '超卖反弹(MA20<-20%)', '+7.74%/99%', '轻仓'),
        ],
        '低迷': [
            ('M14', '超跌反弹低吸', '+3.05%/83%', '不动'),
        ],
    }

    matched_strategies = []
    for key, strategies in strategy_map.items():
        if key in best_name:
            matched_strategies = strategies
            break

    if not matched_strategies:
        matched_strategies = [
            ('M01', '隔夜溢价(缩量<0.7)', '+5.62%/85%', '主做'),
        ]

    print(f"\n  推荐策略:")
    for s in matched_strategies:
        print(f"    {s[0]:6s} {s[1]:18s} {s[2]:14s} {s[3]}")

    # 返回结果供cron推送
    return {
        'date': today_str,
        'cluster': best_name,
        'distance': round(best_dist, 2),
        'key_metrics': {m: today_data.get(m, 0) for m in key_metrics},
        'strategies': [{'id': s[0], 'name': s[1], 'return': s[2], 'action': s[3]} for s in matched_strategies],
    }


if __name__ == '__main__':
    args = sys.argv[1:]

    if '--build' in args:
        days = build_market_history()
        run_clustering()
    elif '--cluster' in args:
        run_clustering()
    elif '--today' in args:
        classify_today()
    elif '--auto' in args:
        # 每日自动：假设market_daily_integrator已经跑完
        classify_today()
    else:
        print("用法:")
        print("  --build   从K线回填历史数据+聚类")
        print("  --cluster 对已有历史数据运行聚类")
        print("  --today   分类今天市场+推荐策略")
        print("  --auto    自动模式")
