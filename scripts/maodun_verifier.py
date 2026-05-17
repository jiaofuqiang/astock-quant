#!/usr/bin/env python3
"""
📐 T+1实战验证系统 v2.0 — 矛盾引擎复盘版
========================================
基于《实践论》的「认识→实践→再认识」闭环：
1. 昨日矛盾引擎推荐了哪些策略（甲等/乙等/丙等）
2. 用今日K线验证实际T+1收益
3. 按矛盾评级统计命中率 → 更新红黑榜
4. 红黑榜数据注入次日morning_pipeline（参数调优）
"""
import sqlite3, os, json, sys
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
DB = os.path.join(HOME, "astock/data/kline_cache.db")
VERIFY_DB = os.path.join(HOME, "astock/data/strategy_verify.db")
CONTRADICTION_RESULT = os.path.join(HOME, "astock/data/contradiction_engine_result.json")
REDBLACK_DB = os.path.join(HOME, "astock/data/maodun_redblack.db")

def load_contradiction_signals():
    """从矛盾引擎输出文件读取昨日推荐信号"""
    if not os.path.exists(CONTRADICTION_RESULT):
        print(f"  ⚠️ 矛盾引擎输出不存在: {CONTRADICTION_RESULT}")
        return []
    
    with open(CONTRADICTION_RESULT) as f:
        data = json.load(f)
    
    signals = data.get('realtime_signals', [])
    env_score = data.get('env_score', 0)
    
    # 提取每个策略推荐的个股
    candidates = []
    for sig in signals:
        strategy = sig.get('strategy', '?')
        grade_info = '?'
        s_level = data.get('maodun_top_level', [])
        for sl in s_level:
            sl_strat = sl.get('strategy', '')[:40]
            if sl_strat and (sl_strat in strategy or strategy[:40] in sl_strat):
                grade_info = sl.get('maodun_grade', '?')
                break
        
        for st in sig.get('stocks', [])[:3]:
            candidates.append({
                'code': st.get('code', ''),
                'name': st.get('name', '?'),
                'strategy': strategy,
                'grade': grade_info,
                'expected_ret': sig.get('expected_ret', 0),
                'expected_win': sig.get('expected_win', 0),
                'buy_score': st.get('buy_score', 0),
                'recommend_date': data.get('timestamp', '')[:10],
            })
    
    return candidates


def verify_t1(candidates):
    """对每个候选股验证T+1实际收益"""
    if not candidates:
        return []
    
    if not os.path.exists(DB):
        print(f"  ❌ K线数据库不存在: {DB}")
        return []
    
    conn = sqlite3.connect(DB)
    today = datetime.now().strftime('%Y-%m-%d')
    results = []
    
    for c in candidates:
        code = c['code']
        trade_date = c.get('recommend_date', '')
        
        if not code or not trade_date:
            continue
        
        # T日K线（基准价）
        t_k = conn.execute("""
            SELECT open, close FROM kline WHERE code=? AND date=?
        """, (code, trade_date)).fetchone()
        
        if t_k is None:
            # 可能日期格式问题，尝试前一天
            try:
                dt = datetime.strptime(trade_date, '%Y-%m-%d')
                prev = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
                t_k = conn.execute("""
                    SELECT open, close FROM kline WHERE code=? AND date=?
                """, (code, prev)).fetchone()
                if t_k:
                    trade_date = prev
            except:
                pass
            if t_k is None:
                continue
        
        # T+1日K线
        t1 = conn.execute("""
            SELECT date, open, close, high FROM kline 
            WHERE code=? AND date > ? 
            ORDER BY date LIMIT 1
        """, (code, trade_date)).fetchone()
        
        if t1 is None:
            continue
        
        # 按涨停价计算买入成本（打板只能在涨停价成交）
        prev_close = conn.execute("""
            SELECT close FROM kline WHERE code=? AND date < ? ORDER BY date DESC LIMIT 1
        """, (code, trade_date)).fetchone()
        
        if prev_close and float(t_k[1]) >= float(prev_close[0]) * 1.095:
            buy_price = round(float(prev_close[0]) * 1.10, 2)
        else:
            buy_price = float(t_k[0])
        
        t1_close = float(t1[2])
        t1_open = float(t1[1])
        t1_high = float(t1[3])
        
        actual_close_ret = round((t1_close - buy_price) / buy_price * 100, 2)
        actual_open_ret = round((t1_open - buy_price) / buy_price * 100, 2)
        actual_high_ret = round((t1_high - buy_price) / buy_price * 100, 2)
        
        expected_val = c.get('expected_ret', 0)
        
        results.append({
            'code': code,
            'name': c.get('name', '?'),
            'strategy': c.get('strategy', '?')[:40],
            'grade': c.get('grade', '?'),
            'buy_score': c.get('buy_score', 0),
            'trade_date': trade_date,
            't1_date': t1[0],
            'expected': round(expected_val, 2),
            'actual_close': actual_close_ret,
            'actual_open': actual_open_ret,
            'actual_high': actual_high_ret,
            'verified_at': datetime.now().isoformat(),
        })
    
    conn.close()
    return results


def save_and_report(results):
    """保存验证结果并打印报告"""
    if not results:
        print("  📭 无待验证数据")
        return
    
    # 写入验证数据库
    conn = sqlite3.connect(VERIFY_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS t1_verify_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT, name TEXT, strategy TEXT, grade TEXT,
            buy_score INTEGER, trade_date TEXT, t1_date TEXT,
            expected REAL, actual_close REAL, actual_open REAL, actual_high REAL,
            verified_at TEXT,
            UNIQUE(code, trade_date, strategy)
        )
    """)
    
    saved = 0
    for r in results:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO t1_verify_v2
                (code, name, strategy, grade, buy_score, trade_date, t1_date,
                 expected, actual_close, actual_open, actual_high, verified_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (r['code'], r['name'], r['strategy'], r['grade'],
                  r['buy_score'], r['trade_date'], r['t1_date'],
                  r['expected'], r['actual_close'], r['actual_open'],
                  r['actual_high'], r['verified_at']))
            saved += 1
        except Exception as e:
            pass
    conn.commit()
    
    # ===== 按矛盾评级统计红黑榜 =====
    # 写入maodun_redblack.db
    rbc = sqlite3.connect(REDBLACK_DB)
    rbc.execute("""
        CREATE TABLE IF NOT EXISTS grade_hitrate (
            grade TEXT PRIMARY KEY,
            total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            total_return REAL DEFAULT 0.0,
            last_updated TEXT
        )
    """)
    rbc.execute("""
        CREATE TABLE IF NOT EXISTS trade_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, code TEXT, name TEXT, grade TEXT, 
            strategy TEXT, buy_score INTEGER,
            expected REAL, actual REAL, actual_high REAL
        )
    """)
    rbc.execute("""
        CREATE TABLE IF NOT EXISTS system_calibration (
            key TEXT PRIMARY KEY,
            value REAL,
            note TEXT,
            updated_at TEXT
        )
    """)
    
    # 写入今日记录
    for r in results:
        rbc.execute("""
            INSERT INTO trade_records
            (date, code, name, grade, strategy, buy_score, expected, actual, actual_high)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (r['trade_date'], r['code'], r['name'], r['grade'],
              r['strategy'], r['buy_score'], r['expected'],
              r['actual_close'], r['actual_high']))
    
    # 更新评级命中率
    from collections import defaultdict
    grade_stats = defaultdict(lambda: {'total': 0, 'wins': 0, 'ret': 0.0})
    
    records = rbc.execute("""
        SELECT grade, actual FROM trade_records WHERE grade IS NOT NULL AND grade != '?'
    """).fetchall()
    
    for grade, actual in records:
        grade_stats[grade]['total'] += 1
        if actual > 0:
            grade_stats[grade]['wins'] += 1
        grade_stats[grade]['ret'] += actual
    
    for grade, stats in grade_stats.items():
        total = stats['total']
        wins = stats['wins']
        avg_ret = round(stats['ret'] / total, 2) if total > 0 else 0
        win_rate = round(wins / total * 100, 1) if total > 0 else 0
        
        rbc.execute("""
            INSERT OR REPLACE INTO grade_hitrate
            (grade, total_trades, wins, total_return, last_updated)
            VALUES (?,?,?,?,?)
        """, (grade, total, wins, stats['ret'], datetime.now().isoformat()))
    
    # 系统校准：计算偏差
    calib_total = rbc.execute("""
        SELECT COUNT(*), SUM(expected), SUM(actual) FROM trade_records
    """).fetchone()
    if calib_total and calib_total[0] > 0:
        total_n = calib_total[0]
        total_exp = calib_total[1] or 0
        total_act = calib_total[2] or 0
        avg_bias = round((total_act - total_exp) / total_n, 2)
        
        rbc.execute("""
            INSERT OR REPLACE INTO system_calibration (key, value, note, updated_at)
            VALUES (?,?,?,?)
        """, ('avg_bias', avg_bias, 
              f'平均偏差: 实际{total_act/total_n:+.2f}%-预期{total_exp/total_n:+.2f}%',
              datetime.now().isoformat()))
    
    rbc.commit()
    
    # ===== 打印报告 =====
    print(f"\n{'='*72}")
    print(f"📐 矛盾引擎T+1复盘 — 《实践论》闭环 v2.0")
    print(f"   基于15988个涨停×570天回测的评级体系")
    print(f"   验证日期: {results[0]['trade_date']} → {results[0]['t1_date']}")
    print(f"{'='*72}")
    
    print(f"\n📋 今日验证({len(results)}只):")
    print(f"  {'评级':<6} {'名称':<8} {'代码':<8} {'预期':<8} {'实际':<8} {'最高':<8} {'评分':<6}")
    print(f"  {'-'*48}")
    
    wins = 0
    for r in results:
        g = r.get('grade', '?')
        win_mark = '✅' if r['actual_close'] > 0 else '❌'
        if r['actual_close'] > 0:
            wins += 1
        print(f"  {g:<4}{win_mark} {r['name'][:6]:<6} {r['code']:<8} {r['expected']:+7.2f}% {r['actual_close']:+7.2f}% {r['actual_high']:+7.2f}% {r['buy_score']}")
    
    n = len(results)
    wr_day = wins / n * 100 if n > 0 else 0
    print(f"\n  📊 今日胜率: {wins}/{n} = {wr_day:.1f}%")
    
    # 评级红黑榜
    print(f"  {'='*72}")
    print(f"  📊 矛盾评级红黑榜（累积）")
    print(f"  {'='*72}")
    
    grade_list = rbc.execute("""
        SELECT grade, total_trades, wins, total_return, last_updated 
        FROM grade_hitrate ORDER BY total_trades DESC
    """).fetchall()
    
    if grade_list:
        print(f"  {'评级':<8} {'次数':<6} {'胜率':<8} {'均收':<8} {'回测预期':<10}")
        print(f"  {'-'*42}")
        for grade, total, win_n, total_ret, updated in grade_list:
            avg_ret = round(total_ret / total, 2) if total > 0 else 0
            wr = round(win_n / total * 100, 1) if total > 0 else 0
            # 回测预期（v2.0）
            exp = {'甲等': '+0.95~1.30%', '乙等': '+0.51%', '丙等': '-0.70%', '丁等': '<0%'}.get(grade, '?')
            print(f"  {grade:<8} {total:<6} {wr:<8.1f} {avg_ret:>+7.2f}% {exp:<10}")
    else:
        print(f"  📭 暂无评级数据（周一首次运行将产生第1条记录）")
    
    # 系统校准建议
    calib = rbc.execute("SELECT key, value, note FROM system_calibration").fetchall()
    if calib:
        print(f"\n⚙️ 系统校准:")
        for key, val, note in calib:
            if key == 'avg_bias':
                if abs(val) > 2:
                    print(f"  ⚠️ {note} → 需要调优！")
                else:
                    print(f"  ✅ {note} → 系统可靠")
    
    conn.close()
    rbc.close()
    print(f"\n✅ 已保存{saved}条验证记录")
    return results


if __name__ == '__main__':
    print("=" * 72)
    print("📐 矛盾引擎T+1复盘 v2.0 — 《实践论》闭环")
    print(f"   运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)
    
    print("\n📋 读取昨日矛盾引擎推荐...")
    candidates = load_contradiction_signals()
    print(f"   找到{len(candidates)}个推荐标的")
    if candidates:
        for c in candidates[:5]:
            print(f"   {c['grade']} {c['name']}({c['code']}) 评分{c['buy_score']} 来自「{c['strategy'][:30]}」")
    
    print("\n🔍 验证T+1收益...")
    results = verify_t1(candidates)
    save_and_report(results)
