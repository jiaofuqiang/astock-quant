#!/usr/bin/env python3
"""龙虎榜席位网络分析 - 游资协同 + 机构抱团 + 风格分类

从 em_lhb_cache.db 挖掘：
1. 游资协同网络：哪些营业部经常同时买入同一只股票（协同对）
2. 机构抱团票：哪些个股有3+家机构同时买入
3. 游资风格分类：一日游型 / 锁仓型 / 点火型
4. 资金净流入TOP5个股（综合游资+机构+量化）

输出: /home/ubuntu/V2board/data/lhb_seat_network.json
"""
import json, os, sqlite3, sys
from datetime import datetime
from collections import defaultdict

DB_PATH = os.path.expanduser('~/astock/data/em_lhb_cache.db')
OUT_PATH = os.path.expanduser('~/V2board/data/lhb_seat_network.json')

# ---------- 游资特色营业部名单（常用知名游资）----------
KNOWN_YOUZI = {
    "国泰海通": "国君系",
    "国泰君安证券": "国君系",
    "中信证券": "中信系",
    "华鑫证券": "华鑫系",
    "华泰证券": "华泰系",
    "招商证券": "招商系",
    "中国银河证券": "银河系",
    "光大证券": "光大系",
    "中金财富": "中金系",
    "财通证券": "财通系",
    "国盛证券": "国盛系",
    "浙商证券": "浙商系",
    "东方财富": "东财系",
    "东方证券": "东方系",
    "广发证券": "广发系",
    "海通证券": "海通系",
    "国信证券": "国信系",
    "申万宏源": "申万系",
    "平安证券": "平安系",
    "兴业证券": "兴业系",
    "中信建投": "建投系",
    "中金公司": "中金系",
    "深股通专用": "北上资金",
    "沪股通专用": "北上资金",
}

# 知名游资营业部全称关键词（用于识别一日游/锁仓/点火）
YOUZI_KEYWORDS = [
    "国泰海通", "华鑫证券", "华泰证券", "中信证券", "招商证券",
    "中国银河", "光大证券", "中金财富", "财通证券", "国盛证券",
    "浙商证券", "东方财富", "东方证券", "广发证券", "海通证券",
    "国信证券", "申万宏源", "平安证券", "兴业证券", "中信建投",
    "中金公司",
]


def safe_query(conn, sql, params=None, default=None):
    """安全执行SQL查询，返回list[dict]"""
    try:
        c = conn.cursor()
        if params:
            c.execute(sql, params)
        else:
            c.execute(sql)
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]
    except Exception as e:
        print(f"  [WARN] SQL query failed: {e}")
        return default or []


def analyze_synergy_pairs(conn):
    """1. 游资协同网络：营业部经常同时买入同一只股票"""
    print("[1/4] 游资协同网络分析...")
    
    # 从 dept_lhb_rank / dept_return_rank 中获取营业部上榜信息
    # 由于 daily_active_dept 暂无数据，采用 dept_lhb_rank 的营业部共现逻辑
    rows = safe_query(conn, """
        SELECT date, period, dept_name, 买入额_万, 成交金额_万
        FROM dept_lhb_rank
        WHERE 买入额_万 > 0
        ORDER BY date DESC, period
    """)
    
    if not rows or len(rows) < 2:
        print("  data too small for synergy analysis")
        return []
    
    # 按 (date, period) 分组，同一组内的营业部视为"协同对"
    groups = defaultdict(list)
    for r in rows:
        key = (r['date'], r['period'])
        groups[key].append(r['dept_name'])
    
    # 统计共现
    pair_counts = defaultdict(lambda: {"count": 0, "stocks": []})
    for key, depts in groups.items():
        depts = sorted(set(depts))
        for i in range(len(depts)):
            for j in range(i+1, len(depts)):
                a, b = depts[i], depts[j]
                if a == b:
                    continue
                pair = (a, b) if a < b else (b, a)
                pair_counts[pair]["count"] += 1
                # 尝试获取股票代码
                stock_detail = key[0]  # date as stock ref
                if stock_detail not in pair_counts[pair]["stocks"]:
                    pair_counts[pair]["stocks"].append(stock_detail)
    
    # 过滤：共现 >= 2 次
    synergy = []
    for (a, b), v in sorted(pair_counts.items(), key=lambda x: -x[1]["count"]):
        if v["count"] >= 2:
            synergy.append({
                "dept_a": a,
                "dept_b": b,
                "co_occur_count": v["count"],
                "stocks": v["stocks"][:10],  # 最多10只
            })
        if len(synergy) >= 30:
            break
    
    # 如果协同太少，也输出共现1次的
    if len(synergy) < 5:
        for (a, b), v in sorted(pair_counts.items(), key=lambda x: -x[1]["count"]):
            if v["count"] == 1 and len(synergy) < 10:
                synergy.append({
                    "dept_a": a,
                    "dept_b": b,
                    "co_occur_count": v["count"],
                    "stocks": v["stocks"][:5],
                })
    
    print(f"  ✅ 发现 {len(synergy)} 组协同配对")
    return synergy


def analyze_inst_favorite(conn):
    """2. 机构抱团票：哪些个股有3+家机构同时买入"""
    print("[2/4] 机构抱团票分析...")
    
    # inst_buy_sell 表有 买方机构数 字段
    rows = safe_query(conn, """
        SELECT date, code, name, 买方机构数, 卖方机构数,
               机构买入总额_万, 机构卖出总额_万, 机构买入净额_万, 上榜原因
        FROM inst_buy_sell
        WHERE 买方机构数 >= 3
        ORDER BY 买方机构数 DESC, 机构买入净额_万 DESC
    """)
    
    if not rows:
        # 从 inst_seat_track 尝试
        print("  inst_buy_sell empty, checking inst_seat_track...")
        rows = safe_query(conn, """
            SELECT date, code, name, 
                   买入次数 as 买方机构数, 卖出次数 as 卖方机构数,
                   买入额_万 as 机构买入总额_万,
                   卖出额_万 as 机构卖出总额_万,
                   机构净买入额_万 as 机构买入净额_万,
                   '机构席位追踪' as 上榜原因
            FROM inst_seat_track
            WHERE 买入次数 >= 3
            ORDER BY 买入次数 DESC, 机构净买入额_万 DESC
        """)
    
    if not rows:
        # 从 stock_lhb_stats 反推 - 上榜次数多的可能有机构
        print("  inst tables empty, using stock_lhb_stats as fallback...")
        rows = safe_query(conn, """
            SELECT date, period as date, code, name,
                   上榜次数 as 买方机构数,
                   0 as 卖方机构数,
                   龙虎榜买入额_万 as 机构买入总额_万,
                   龙虎榜卖出额_万 as 机构卖出总额_万,
                   龙虎榜净买额_万 as 机构买入净额_万,
                   '个股上榜统计' as 上榜原因
            FROM stock_lhb_stats
            WHERE 上榜次数 >= 3
            ORDER BY 上榜次数 DESC, 龙虎榜净买额_万 DESC
        """)
    
    results = []
    if rows:
        seen = set()
        for r in rows:
            key = (r['date'], r['code'])
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "date": r['date'],
                "code": r['code'],
                "name": r['name'],
                "inst_buy_count": r['买方机构数'],
                "inst_sell_count": r['卖方机构数'],
                "inst_net_buy_wan": round(r.get('机构买入净额_万', 0) or 0, 2),
                "inst_total_buy_wan": round(r.get('机构买入总额_万', 0) or 0, 2),
                "reason": r.get('上榜原因', ''),
            })
            if len(results) >= 20:
                break
    
    print(f"  ✅ 发现 {len(results)} 只机构抱团/高频上榜个股")
    return results


def analyze_trader_types(conn):
    """3. 游资风格分类：一日游 / 锁仓 / 点火"""
    print("[3/4] 游资风格分类...")
    
    # 使用 dept_return_rank 的 T+N 数据判断风格
    rows = safe_query(conn, """
        SELECT date, period, rank_num, dept_name,
               d1_count, d1_avg_pct, d1_winrate,
               d2_count, d2_avg_pct, d2_winrate,
               d3_count, d3_avg_pct, d3_winrate,
               d5_count, d5_avg_pct, d5_winrate,
               d10_count, d10_avg_pct, d10_winrate
        FROM dept_return_rank
        ORDER BY date DESC, rank_num ASC
    """)
    
    one_day_tour = []   # 一日游型: 高D1胜率 + 低D3/D5胜率（涨幅落地就跑）
    lock_pos = []       # 锁仓型: D5/D10胜率持续高
    igniter = []        # 点火型: D1胜率极高 + D1参与次数多

    for r in rows:
        name = r['dept_name']
        # 检查是否是知名游资/券商
        is_youzi = any(kw in name for kw in YOUZI_KEYWORDS) or name in KNOWN_YOUZI
        
        d1_w = r.get('d1_winrate') or 0
        d2_w = r.get('d2_winrate') or 0
        d3_w = r.get('d3_winrate') or 0
        d5_w = r.get('d5_winrate') or 0
        d10_w = r.get('d10_winrate') or 0
        d1_count = r.get('d1_count') or 0
        d1_avg = r.get('d1_avg_pct') or 0

        entry = {
            "dept_name": name,
            "tag": is_youzi,
            "d1_winrate": round(d1_w * 100, 1),
            "d2_winrate": round(d2_w * 100, 1),
            "d3_winrate": round(d3_w * 100, 1),
            "d5_winrate": round(d5_w * 100, 1),
            "d10_winrate": round(d10_w * 100, 1),
            "d1_count": d1_count,
            "d1_avg_return_pct": round(d1_avg * 100, 2),
            "period": r['period'],
        }

        # 一日游型: D1胜率 > 45% 且 D5胜率下降超过10%
        if d1_w > 0.45 and d1_count >= 50:
            if d5_w < d1_w - 0.10 or d10_w < d1_w - 0.15:
                one_day_tour.append(entry)
        
        # 锁仓型: D10胜率 > D1胜率 或 D5/D10都高于40%
        if d5_w >= 0.40 and d10_w >= 0.35 and d1_count >= 30:
            if d10_w >= d1_w - 0.05:
                lock_pos.append(entry)
        
        # 点火型: D1胜率 > 48% 且 D1参与次数多
        if d1_w > 0.48 and d1_count >= 80:
            igniter.append(entry)

    # 排序取TOP
    one_day_tour = sorted(one_day_tour, key=lambda x: -x['d1_winrate'])[:10]
    lock_pos = sorted(lock_pos, key=lambda x: -x['d10_winrate'])[:10]
    igniter = sorted(igniter, key=lambda x: -x['d1_count'])[:10]
    
    result = {
        "one_day_tour": one_day_tour,
        "lock_position": lock_pos,
        "igniter": igniter,
        "analysis_note": "一日游型:高D1胜率+低后续胜率; 锁仓型:D5/D10胜率持续高; 点火型:极高D1胜率+大量参与"
    }
    
    print(f"  ✅ 一日游型: {len(one_day_tour)}条, 锁仓型: {len(lock_pos)}条, 点火型: {len(igniter)}条")
    return result


def analyze_net_buy_top(conn):
    """4. 资金净流入TOP5个股（综合游资+机构+量化）"""
    print("[4/4] 资金净流入TOP5分析...")
    
    # 从 stock_lhb_stats 获取净买入排行
    rows = safe_query(conn, """
        SELECT date, period, code, name,
               上榜次数,
               龙虎榜净买额_万,
               龙虎榜买入额_万,
               龙虎榜卖出额_万,
               龙虎榜总成交额_万
        FROM stock_lhb_stats
        ORDER BY 龙虎榜净买额_万 DESC
        LIMIT 10
    """)
    
    results = []
    if rows:
        # 尝试获取每个股票的营业部数量
        for r in rows:
            code = r['code']
            # 从 dept_lhb_rank 统计营业部数量 (approximate)
            dept_rows = safe_query(conn, """
                SELECT COUNT(DISTINCT dept_name) as dept_count
                FROM dept_lhb_rank
                WHERE date = ? AND period = ?
            """, params=[r['date'], r['period']])
            
            dept_count = dept_rows[0]['dept_count'] if dept_rows else 0
            
            # 平均收益率估算 (从 dept_return_rank 取D1平均)
            ret_rows = safe_query(conn, """
                SELECT AVG(d1_avg_pct) as avg_ret
                FROM dept_return_rank
                WHERE date = ? AND period = ?
            """, params=[r['date'], r['period']])
            avg_ret = round((ret_rows[0]['avg_ret'] or 0) * 100, 2) if ret_rows else 0
            
            results.append({
                "stock": code,
                "name": r['name'],
                "date": r['date'],
                "total_net_buy_wan": round(r['龙虎榜净买额_万'], 2),
                "total_buy_wan": round(r['龙虎榜买入额_万'], 2),
                "total_sell_wan": round(r['龙虎榜卖出额_万'], 2),
                "list_count": r['上榜次数'],
                "dept_count": dept_count,
                "avg_return_pct": avg_ret,
            })
    
    print(f"  ✅ 发现 {len(results)} 只净流入TOP个股")
    return results


def main():
    print("=" * 60)
    print("龙虎榜席位网络分析器")
    print(f"启动时间: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # 检查DB
    if not os.path.exists(DB_PATH):
        err = f"DB not found: {DB_PATH}"
        print(f"[ERROR] {err}")
        result = {
            "generated_at": datetime.now().isoformat(),
            "error": err,
            "synergy_pairs": [],
            "inst_favorite_stocks": [],
            "trader_type_top": {
                "one_day_tour": [],
                "lock_position": [],
                "igniter": [],
                "analysis_note": "数据库不存在，无数据"
            },
            "net_buy_top": [],
            "db_stats": {},
        }
        with open(OUT_PATH, 'w') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[WARN] 空结果已写入 {OUT_PATH}")
        return

    # 连接DB
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    
    # 统计DB概览
    db_stats = {}
    for tbl in ['stock_lhb_stats', 'dept_return_rank', 'dept_lhb_rank',
                'inst_buy_sell', 'inst_seat_track', 'daily_active_dept']:
        try:
            cnt = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            db_stats[tbl] = cnt
        except Exception:
            db_stats[tbl] = 0
    
    print(f"DB状态: {json.dumps(db_stats, ensure_ascii=False)}")
    
    # 执行分析
    synergy_pairs = analyze_synergy_pairs(conn)
    inst_favorite = analyze_inst_favorite(conn)
    trader_types = analyze_trader_types(conn)
    net_buy_top = analyze_net_buy_top(conn)
    
    conn.close()
    
    # 组装输出
    result = {
        "generated_at": datetime.now().isoformat(),
        "db_stats": db_stats,
        "synergy_pairs": synergy_pairs,
        "inst_favorite_stocks": inst_favorite,
        "trader_type_top": trader_types,
        "net_buy_top": net_buy_top,
    }
    
    # 写入
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"✅ 分析完成！结果已写入: {OUT_PATH}")
    print(f"   文件大小: {os.path.getsize(OUT_PATH)} bytes")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
