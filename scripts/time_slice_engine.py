#!/usr/bin/env python3
"""
⚡ 时间片引擎 v1.0
=================
三层核心逻辑：
  1. 实时匹配 — 当前时间片 → 找历史相似时间片
  2. 趋势预测 — 匹配到的历史后续走势 → 预测当前趋势
  3. 聚类分组 — 自动将相似时间片聚为群组，生成典型模式

流程：
  time_slice_collector (每5分钟采集) → 写入 DB
  → time_slice_engine (读 DB) → 匹配+预测+聚类
  → 输出到作战面板/微信推送

用法：
  python3 scripts/time_slice_engine.py               # 匹配当前最新时间片
  python3 scripts/time_slice_engine.py --match        # 同上
  python3 scripts/time_slice_engine.py --cluster      # 运行聚类
  python3 scripts/time_slice_engine.py --list         # 查看匹配记录
  python3 scripts/time_slice_engine.py --backfill     # 全量回填历史预测
"""

import os, sys, json, sqlite3, time, math
from datetime import datetime, date
from collections import defaultdict, Counter

BASE = os.path.expanduser('~/astock')
DATA = os.path.join(BASE, 'data')
HISTORY_DB = os.path.join(DATA, 'time_slice_history.db')

# ============ 连接 ============

def get_slices(conn, source=None, limit=100):
    """获取时间片列表"""
    sql = "SELECT id, date, ts, source FROM time_slices"
    params = []
    if source:
        sql += " WHERE source=?"
        params = [source]
    sql += " ORDER BY date DESC, ts DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()

def get_full_tag(conn, slice_id):
    """获取一个时间片的九维穿透标签（逐层）"""
    row = conn.execute(
        "SELECT l1_热度,l2_风格,l3_效应,l4_板块,l5_量价,l6_趋势,l7_情绪,l8_轮动,l9_博弈 "
        "FROM dim_cluster WHERE slice_id=?", (slice_id,)
    ).fetchone()
    if not row: return None
    return list(row)

def get_cluster_tag(conn, slice_id):
    """获取一个时间片的九维穿透标签（字符串形式）"""
    tags = get_full_tag(conn, slice_id)
    return '·'.join(tags) if tags else None

def get_limit(conn, slice_id):
    """获取涨停数据"""
    row = conn.execute(
        "SELECT limit_up, max_board, zh_ratio, up_count, down_count "
        "FROM dim_limit WHERE slice_id=?", (slice_id,)
    ).fetchone()
    return row if row else None

# ============ 九维标签评分匹配 ============

# 九维标签层级权重（L1最重要，依次递减）
TAG_WEIGHTS = [0.25, 0.15, 0.15, 0.10, 0.10, 0.10, 0.08, 0.05, 0.02]
# L1热度等级对照
HEAT_LEVELS = {'❄️恐慌冰点': 0, '❄️冰点': 1, '☁️平淡': 2, '🌤活跃': 3, '☀️狂热': 4}

def tag_similarity(t1, t2):
    """九维穿透标签逐层评分（0~1），t1/t2 是9元素列表"""
    if not t1 or not t2: return 0
    total = 0
    max_weight = sum(TAG_WEIGHTS)
    for i, (a, b) in enumerate(zip(t1, t2)):
        w = TAG_WEIGHTS[i]
        if i == 0:  # L1热度: 按等级差分
            la = HEAT_LEVELS.get(a, -1)
            lb = HEAT_LEVELS.get(b, -1)
            if la >= 0 and lb >= 0:
                diff = abs(la - lb)
                score = max(0, 1 - diff * 0.3)  # 差1级扣0.3
            else:
                score = 1.0 if a == b else 0.0
        else:  # L2~L9: 完全匹配+1, 同大类+0.5
            if a == b:
                score = 1.0
            elif a[:2] == b[:2]:  # emoji前缀相同（同大类）
                score = 0.5
            else:
                score = 0.0
        total += w * score
    return round(total / max_weight, 4)

def tag_distance(t1, t2):
    """标签距离 = 1 - 相似度"""
    return 1 - tag_similarity(t1, t2)

# ============ 1. 实时匹配（九维标签模式） ============

def get_source_type(conn, slice_id):
    """获取时间片的source和ts"""
    row = conn.execute(
        "SELECT source, ts FROM time_slices WHERE id=?", (slice_id,)
    ).fetchone()
    return row if row else None

def match_current(conn, target_slice_id=None, top_n=10):
    """
    九维标签模式匹配 — 替代旧版12维数值欧氏距离。

    匹配逻辑：
    1. 分时对齐 — 分钟片只匹配历史分钟片，日K片只匹配历史日K片
    2. 标签模式匹配 — 9层标签逐层加权评分
    3. L1热度等级差分 — 狂热vs冰点差4级得0分，活跃vs狂热差1级得0.7分
    """
    if target_slice_id is None:
        row = conn.execute(
            "SELECT id FROM time_slices ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row: return None
        target_slice_id = row[0]

    target_info = get_source_type(conn, target_slice_id)
    target_tags = get_full_tag(conn, target_slice_id)
    if not target_tags or not target_info:
        return None

    target_source = target_info[0]
    # 分时对齐
    if target_source == 'minute':
        fetch_source = 'minute'
    elif target_source == 'live':
        fetch_source = 'minute'
    else:
        fetch_source = 'simulated'

    rows = conn.execute(
        "SELECT t.id FROM time_slices t "
        "WHERE t.source=? AND t.id != ? "
        "ORDER BY t.date DESC, t.ts DESC",
        (fetch_source, target_slice_id)
    ).fetchall()

    matches = []
    for (sid,) in rows:
        tags = get_full_tag(conn, sid)
        if not tags: continue
        sim = tag_similarity(target_tags, tags)
        tag_str = '·'.join(tags)
        limit_data = get_limit(conn, sid)
        matches.append({
            'slice_id': sid,
            'distance': round(1 - sim, 4),
            'similarity': sim,
            'tag': tag_str,
            'limit_up': limit_data[0] if limit_data else 0,
            'max_board': limit_data[1] if limit_data else 0,
        })

    matches.sort(key=lambda x: -x['similarity'])
    top_matches = matches[:top_n]

    return {
        'target_id': target_slice_id,
        'target_tags': target_tags,
        'tag_str': '·'.join(target_tags),
        'matched': top_matches,
        'total_compared': len(matches),
        'avg_similarity': round(sum(m['similarity'] for m in top_matches) / max(len(top_matches), 1), 4),
        'max_similarity': top_matches[0]['similarity'] if top_matches else 0,
    }

# ============ 2. 趋势预测 ============

def predict_trend(conn, target_slice_id=None, top_n=10):
    """
    基于匹配结果预测后续走势:
    1. 找到当前时间片的TOP N相似历史
    2. 看这些历史时间片后续的标签演变
    3. 统计后续30分钟/60分钟/收盘时的走势分布
    4. 推荐最佳策略
    """
    result = match_current(conn, target_slice_id, top_n)
    if not result:
        return None
    
    target_info = conn.execute(
        "SELECT date, ts FROM time_slices WHERE id=?",
        (result['target_id'],)
    ).fetchone()
    
    # 对每个匹配的历史，找其后续时间片看演变
    predictions = []
    for m in result['matched']:
        hist_info = conn.execute(
            "SELECT date, ts FROM time_slices WHERE id=?",
            (m['slice_id'],)
        ).fetchone()
        
        hist_date, hist_ts = hist_info
        
        # 获取后续时间片（同天后面的时间戳，或者下一交易日）
        next_slices = conn.execute(
            "SELECT ts, id FROM time_slices "
            "WHERE date=? AND source IN ('live','simulated') AND ts > ? "
            "ORDER BY ts ASC LIMIT 6",
            (hist_date, hist_ts)
        ).fetchall()
        
        next_tags = []
        for nts, nid in next_slices:
            tag = get_cluster_tag(conn, nid)
            limit_data = get_limit(conn, nid)
            if tag:
                next_tags.append({
                    'ts': nts,
                    'tag': tag,
                    'limit_up': limit_data[0] if limit_data else 0,
                    'max_board': limit_data[1] if limit_data else 0,
                })
        
        predictions.append({
            'match_id': m['slice_id'],
            'similarity': m['similarity'],
            'tag': m['tag'],
            'next_steps': next_tags,
        })
    
    # 统计T+1方向
    tags_counter = Counter()
    for p in predictions:
        if p['next_steps']:
            tags_counter[p['next_steps'][0]['tag'][:10]] += 1
    
    total_p = len(predictions)
    trend_dist = {}
    for tag_name, count in tags_counter.most_common():
        trend_dist[tag_name] = round(count / max(total_p, 1) * 100, 1)
    
    # 九维穿透变化分析
    heat_evol = Counter()
    for p in predictions:
        tag = p['tag'] or ''
        heat = tag.split('·')[0] if '·' in tag else tag[:5]
        heat_evol[heat] += 1
    
    # 策略推荐（基于当前九维穿透+历史走势）
    current_tag = get_cluster_tag(conn, result['target_id'])
    best_strategy = recommend_strategy(current_tag, result)
    
    return {
        'target_id': result['target_id'],
        'current_tag': current_tag,
        'matched_count': len(result['matched']),
        'avg_similarity': result['avg_similarity'],
        'best_similarity': result['max_similarity'],
        'trend_distribution': trend_dist,
        'predictions': predictions[:5],
        'heat_distribution': dict(heat_evol.most_common(5)),
        'best_strategy': best_strategy,
    }

def recommend_strategy(current_tag, match_result):
    """基于当前九维穿透+匹配结果推荐策略"""
    if not current_tag:
        return {'action': '观望', 'reason': '无当前标签'}
    
    parts = current_tag.split('·')
    l1 = parts[0] if len(parts) > 0 else ''
    l3 = parts[2] if len(parts) > 2 else ''
    l6 = parts[5] if len(parts) > 5 else ''
    l7 = parts[6] if len(parts) > 6 else ''
    
    strategies = []
    
    # 基于L1热度
    if '狂热' in l1:
        strategies.append(('🔥', '积极做多', '市场狂热，涨停潮，果断参与最高板接力'))
    elif '活跃' in l1:
        strategies.append(('🌤', '参与轮动', '市场活跃，适合板块轮动+首板套利'))
    elif '冰点' in l1:
        strategies.append(('❄️', '空仓观望', '市场冰点，等待回暖信号'))
    
    # 基于L3效应
    if '龙头接力' in l3:
        strategies.append(('🏆', '关注最高板', '龙头接力效应强，最高板溢价高'))
    elif '首板套利' in l3:
        strategies.append(('🎯', '首板策略', '适合隔夜溢价缩量<0.7策略'))
    elif '轮动' in l3:
        strategies.append(('🔄', '低吸不追高', '轮动行情，跟风首板风险大'))
    
    # 基于L6趋势
    if '加速' in l6 or '强势' in l6:
        strategies.append(('📈', '顺势做多', '趋势延续，持股为主'))
    elif '震荡' in l6:
        strategies.append(('↔️', '高抛低吸', '震荡市，冲高减仓，回落低吸'))
    elif '超跌' in l6:
        strategies.append(('📉', '抄底机会', '超跌反弹，关注日内反转信号'))
    
    # 基于L7情绪
    if '绝望' in l7:
        strategies.append(('💀', '绝对空仓', '绝望情绪，不参与'))
    elif '悲观' in l7:
        strategies.append(('😔', '轻仓试错', '悲观中可小额试错'))
    elif '狂热' in l7:
        strategies.append(('🤩', '减仓止盈', '情绪过热，分批止盈'))
    
    if not strategies:
        strategies.append(('➖', '观望', '无明确信号'))
    
    return strategies

# ============ 3. 聚类 ============

def auto_cluster(conn, min_similarity=0.85):
    """
    自动将相似时间片聚为群组。
    使用九维标签匹配替代之前的数值余弦相似度。
    """
    rows = conn.execute(
        "SELECT t.id, t.date, t.ts, t.source FROM time_slices t "
        "WHERE EXISTS (SELECT 1 FROM dim_cluster d WHERE d.slice_id = t.id) "
        "ORDER BY t.date ASC, t.ts ASC"
    ).fetchall()
    
    total = len(rows)
    if total < 10:
        return {'error': '数据不足(至少需要10个时间片)', 'count': total}
    
    print(f"  加载 {total} 个时间片的标签...")
    
    slices = []
    for row in rows:
        fid = row[0]
        tags = get_full_tag(conn, fid)
        if tags:
            slices.append({'id': fid, 'date': row[1], 'ts': row[2], 'source': row[3], 'tags': tags})
    
    n = len(slices)
    clusters = []
    assigned = set()
    
    print(f"  计算 {n}x{n} 标签相似度矩阵...")
    
    for i in range(n):
        if i in assigned: continue        
        cluster = [i]
        assigned.add(i)        
        if i % 50 == 0:
            print(f"   种子 {i}/{n}...", end='\r', flush=True)        
        for j in range(i+1, n):
            if j in assigned: continue            
            sim = tag_similarity(slices[i]['tags'], slices[j]['tags'])
            if sim >= min_similarity:
                cluster.append(j)
                assigned.add(j)        
        if len(cluster) > 1:
            clusters.append(cluster)
        else:
            assigned.discard(i)
    
    orphans = [i for i in range(n) if i not in assigned]
    for idx in orphans:
        clusters.append([idx])
    
    print(f"\n  分析 {len(clusters)} 个簇...")
    
    cluster_info = []
    for ci, cluster in enumerate(clusters):
        tags = []
        for idx in cluster:
            s = slices[idx]
            tag = get_cluster_tag(conn, s['id'])
            if tag: tags.append(tag)
        
        tag_counter = Counter(tags)
        typical_tag = tag_counter.most_common(1)[0][0] if tag_counter else ''
        tag_diversity = len(tag_counter)
        
        # 簇内平均相似度
        intra_sim = 0
        pair_count = 0
        for i_idx in range(len(cluster)):
            for j_idx in range(i_idx+1, len(cluster)):
                sim = tag_similarity(
                    slices[cluster[i_idx]]['tags'],
                    slices[cluster[j_idx]]['tags']
                )
                intra_sim += sim
                pair_count += 1
        avg_intra_sim = round(intra_sim / max(pair_count, 1), 4)
        
        dates = [slices[idx]['date'] for idx in cluster]
        
        cluster_info.append({
            'cluster_id': ci + 1,
            'size': len(cluster),
            'typical_tag': typical_tag,
            'tag_diversity': tag_diversity,
            'center_feat': [0]*12,
            'avg_intra_sim': avg_intra_sim,
            'date_range': (min(dates), max(dates)),
            'slice_ids': [slices[idx]['id'] for idx in cluster],
        })
    
    cluster_info.sort(key=lambda x: -x['size'])
    save_clusters(conn, cluster_info, n)
    
    return {
        'total_slices': n,
        'total_clusters': len(clusters),
        'biggest_cluster': cluster_info[0] if cluster_info else None,
        'clusters': cluster_info,
        'threshold': min_similarity,
    }

def save_clusters(conn, cluster_info, total_slices):
    """将聚类结果写入数据库"""
    c = conn.cursor()
    
    for ci in cluster_info:
        cluster_id = ci['cluster_id']
        for sid in ci['slice_ids']:
            c.execute(
                "UPDATE dim_cluster SET full_tag = full_tag || ? WHERE slice_id=?",
                (f' [C{cluster_id}]', sid)
            )
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS tag_clusters (
            cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
            size INTEGER,
            typical_tag TEXT,
            tag_diversity INTEGER,
            center_vector TEXT,
            avg_intra_similarity REAL,
            date_min TEXT,
            date_max TEXT,
            member_count REAL
        )
    """)
    
    c.execute("DELETE FROM tag_clusters")
    for ci in cluster_info:
        c.execute(
            "INSERT INTO tag_clusters VALUES(?,?,?,?,?,?,?,?,?)",
            (ci['cluster_id'], ci['size'], ci['typical_tag'][:100],
             ci['tag_diversity'], json.dumps(ci['center_feat']),
             ci['avg_intra_sim'], ci['date_range'][0], ci['date_range'][1],
             round(ci['size'] / max(total_slices, 1) * 100, 1))
        )
    
    conn.commit()
    print(f"  ✅ 已写入 {len(cluster_info)} 个簇到 tag_clusters")

# ============ 4. 输出报告 ============

def format_tags_for_display(tag_str):
    """把完整标签截为两行显示"""
    parts = tag_str.split('·')
    if len(parts) >= 9:
        return '·'.join(parts[:4]), '·'.join(parts[4:8]) + ('·' + parts[8] if len(parts) > 8 else '')
    return tag_str[:45], ''

def print_match_report(result):
    """打印匹配结果报告（九维标签模式匹配版）"""
    target_tag = result.get('tag_str', '')
    target_line1, target_line2 = format_tags_for_display(target_tag)
    print(f"\n{'=' * 60}")
    print(f"⚡ 时间片匹配报告")
    print(f"{'=' * 60}")
    print(f"目标时间片: #{result['target_id']}")
    print(f"目标标签:   {target_line1}")
    if target_line2:
        print(f"            {target_line2}")
    print(f"对比总数: {result['total_compared']} 个历史时间片")
    print(f"最大相似度: {result['max_similarity']:.4f}")
    print(f"平均相似度(TOP{len(result['matched'])}): {result['avg_similarity']:.4f}")
    print(f"{'─' * 60}")
    print(f"{'排名':<6} {'ID':<6} {'相似度':<8} {'标签(前4层)':<30} {'涨/板':<10}")
    print(f"{'─' * 60}")
    for i, m in enumerate(result['matched'][:10]):
        tag = m['tag'] or ''
        line1, _ = format_tags_for_display(tag)
        lu = m.get('limit_up', 0)
        mb = m.get('max_board', 0)
        print(f"{i+1:<6} #{m['slice_id']:<4} {m['similarity']:<8.4f} {line1:<30} {lu}/{mb}")

def print_predict_report(result):
    """打印趋势预测报告"""
    print(f"\n{'=' * 55}")
    print(f"🔮 时间片趋势预测")
    print(f"{'=' * 55}")
    print(f"当前标签: {result['current_tag']}")
    print(f"匹配数: {result['matched_count']} | 平均相似度: {result['avg_similarity']:.4f}")
    print(f"{'─' * 55}")
    print(f"趋势分布:")
    for tag_name, pct in result['trend_distribution'].items():
        bar = '█' * int(pct / 5)
        print(f"  {tag_name:<15} {pct:>5.1f}% {bar}")
    print(f"{'─' * 55}")
    print(f"策略推荐:")
    for emoji, action, reason in result['best_strategy']:
        print(f"  {emoji} {action:<10} — {reason}")
    print(f"\n历史相似案例:")
    for p in result['predictions'][:3]:
        tag = (p['tag'] or '无')[:50]
        print(f"  #{p['match_id']} [{p['similarity']:.3f}] {tag}")
        for ns in p['next_steps'][:3]:
            print(f"    → {ns['ts']}: {ns['tag'][:50]} (涨{ns['limit_up']})")

def print_cluster_report(result):
    """打印聚类报告"""
    print(f"\n{'=' * 55}")
    print(f"📊 时间片聚类结果")
    print(f"{'=' * 55}")
    print(f"总时间片: {result['total_slices']}")
    print(f"总簇数: {result['total_clusters']}")
    if result.get('biggest_cluster'):
        bc = result['biggest_cluster']
        print(f"最大簇: [{bc['cluster_id']}] {bc['size']}个成员 ({round(bc['size']/result['total_slices']*100,1)}%)")
        print(f"  |典型标签: {bc['typical_tag'][:60]}")
        print(f"  |平均簇内相似度: {bc['avg_intra_sim']:.4f}")
        print(f"  |日期范围: {bc['date_range'][0]} ~ {bc['date_range'][1]}")
    print(f"{'─' * 55}")
    print(f"{'簇ID':<6} {'规模':<6} {'占比':<8} {'簇内相似':<10} {'典型标签':<40}")
    print(f"{'─' * 55}")
    for ci in result['clusters'][:15]:
        tag = (ci['typical_tag'] or '无')[:40]
        print(f"C{ci['cluster_id']:<4} {ci['size']:<6} {ci.get('member_count',0):>5.1f}% {ci['avg_intra_sim']:<10.4f} {tag}")
    if len(result['clusters']) > 15:
        print(f"... 还有 {len(result['clusters']) - 15} 个簇")

# ============ 主入口 ============

def main():
    args = sys.argv[1:]
    mode = 'match'
    
    if '--cluster' in args:
        mode = 'cluster'
    elif '--list' in args:
        mode = 'list'
    elif '--backfill' in args:
        mode = 'backfill'
    
    conn = sqlite3.connect(HISTORY_DB)
    
    if mode == 'match':
        print(f"⚡ 时间片引擎 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 55)
        
        row = conn.execute(
            "SELECT id, date, ts, source FROM time_slices ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            print("❌ 数据库没有时间片数据")
            return
        
        print(f"当前时间片: #{row[0]} {row[1]} {row[2]} ({row[3]})")
        
        print("\n🔍 匹配相似历史（九维标签模式匹配）...")
        match_result = match_current(conn, row[0], 20)
        if match_result:
            print_match_report(match_result)
        
        print("\n🔮 趋势预测...")
        pred_result = predict_trend(conn, row[0], 10)
        if pred_result:
            print_predict_report(pred_result)
        
        if match_result and match_result['matched']:
            c = conn.cursor()
            target_id = match_result['target_id']
            c.execute("DELETE FROM match_results WHERE date=? AND ts=?",
                     (row[1], row[2]))
            
            next_slices = conn.execute(
                "SELECT ts, id FROM time_slices "
                "WHERE date=? AND ts > ? ORDER BY ts ASC LIMIT 6",
                (row[1], row[2])
            ).fetchall()
            next_20min_chg = None
            if len(next_slices) >= 1:
                nid = next_slices[0][1]
                nd = conn.execute(
                    "SELECT limit_up FROM dim_limit WHERE slice_id=?", (nid,)
                ).fetchone()
                if nd: next_20min_chg = nd[0]
            
            for idx, m in enumerate(match_result['matched'][:10]):
                c.execute(
                    "INSERT OR IGNORE INTO match_results "
                    "(date, ts, target_slice_id, match_slice_id, similarity, "
                    "next_30min_chg, created_at) VALUES(?,?,?,?,?,?,datetime('now','localtime'))",
                    (row[1], row[2], target_id, m['slice_id'], m['similarity'], next_20min_chg)
                )
            conn.commit()
            print(f"\n  ✅ 已保存 {min(10, len(match_result['matched']))} 条匹配记录到 match_results")
    
    elif mode == 'cluster':
        print(f"📊 时间片聚类 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 55)
        threshold = 0.85
        for a in args:
            if a.startswith('--th='):
                try: threshold = float(a.split('=')[1])
                except: pass
        
        print(f"相似度阈值: {threshold}")
        result = auto_cluster(conn, threshold)
        if result.get('error'):
            print(f"❌ {result['error']}")
        else:
            print_cluster_report(result)
    
    elif mode == 'list':
        print(f"📋 时间片列表")
        print("=" * 55)
        rows = get_slices(conn, limit=30)
        print(f"{'ID':<6} {'日期':<12} {'时间':<8} {'来源':<12} {'标签':<50}")
        print("-" * 88)
        for r in rows:
            tag = get_cluster_tag(conn, r[0]) or '无'
            print(f"#{r[0]:<4} {r[1]:<12} {r[2]:<8} {r[3]:<12} {tag[:50]}")
    
    elif mode == 'backfill':
        print("🔄 全量回填历史预测...")
        print("=" * 55)
        all_slices = conn.execute(
            "SELECT t.id, t.date, t.ts FROM time_slices t "
            "WHERE EXISTS (SELECT 1 FROM dim_cluster d WHERE d.slice_id = t.id) "
            "ORDER BY t.date ASC, t.ts ASC"
        ).fetchall()
        total = len(all_slices)
        print(f"  共 {total} 个时间片需要回填")
        
        success = 0
        for idx, (sid, sdate, sts) in enumerate(all_slices):
            if idx % 10 == 0:
                pct = (idx + 1) / total * 100
                print(f"\r  [{idx+1}/{total} {pct:.0f}%] #{sid} {sdate} {sts}", end='', flush=True)
            
            try:
                result = match_current(conn, sid, top_n=10)
                if result and result['matched']:
                    c = conn.cursor()
                    c.execute("DELETE FROM match_results WHERE target_slice_id=?", (sid,))
                    for m in result['matched'][:10]:
                        c.execute(
                            "INSERT OR IGNORE INTO match_results "
                            "(date, ts, target_slice_id, match_slice_id, similarity, created_at) "
                            "VALUES(?,?,?,?,?,datetime('now','localtime'))",
                            (sdate, sts, sid, m['slice_id'], m['similarity'])
                        )
                    conn.commit()
                    success += 1
            except:
                pass
        
        print(f"\n  ✅ 回填完成: {success}/{total} 个时间片成功匹配")
    
    conn.close()
    print(f"\n{'=' * 55}")
    print("✅ 完成")

if __name__ == '__main__':
    main()
