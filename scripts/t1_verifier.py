#!/usr/bin/env python3
"""
✅ T+1实战验证系统 v1.0
========================
每天收盘后自动验证：
1. 昨日引擎推荐的票 → 今天的实际T+1收益是多少？
2. 预测 vs 实际 → 偏差多大？
3. 将验证结果写入验证数据库 → 用于策略权重调整
"""
import sqlite3, os, json, sys
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
DB = os.path.join(HOME, "astock/data/kline_cache.db")
VERIFY_DB = os.path.join(HOME, "astock/data/strategy_verify.db")
BUNDLE = os.path.join(HOME, "V2board/dashboard_bundle.json")

def get_yesterday_candidates():
    """从bundle中读取昨天的买入候选"""
    if not os.path.exists(BUNDLE):
        return []
    
    with open(BUNDLE) as f:
        b = json.load(f)
    
    # 从多个数据源找候选
    candidates = []
    
    # 1. buy_candidates from bundle
    for c in b.get('buy_candidates', []):
        if isinstance(c, dict):
            candidates.append(c)
    
    # 2. lhb_4_strategy actionable
    l4 = b.get('lhb_4_strategy', {})
    for c in l4.get('actionable_by_priority', []):
        if isinstance(c, dict) and c.get('code'):
            candidates.append(c)
    
    # 3. decision_report buy_candidates
    dr = b.get('decision_report', {})
    for c in dr.get('buy_candidates', []):
        if isinstance(c, dict):
            candidates.append(c)
    
    return candidates


def verify_t1(candidates):
    """
    对每个候选：
    找到T日 + T+1日的K线
    计算实际收益
    对比预期收益
    """
    if not candidates:
        return []
    
    conn = sqlite3.connect(DB)
    results = []
    
    # bundle时间戳作为T日
    try:
        with open(BUNDLE) as f:
            b = json.load(f)
        bundle_time = b.get('_meta', {}).get('built_at', '')
    except:
        bundle_time = ''
    
    today = bundle_time[:10] if bundle_time else datetime.now().strftime('%Y-%m-%d')
    
    for c in candidates:
        code = c.get('code', c.get('stock_code', ''))
        name = c.get('name', c.get('stock_name', '?'))
        expected = c.get('expected_ret', c.get('tier_avg_ret', c.get('score', 0)))
        
        if not code:
            continue
        
        # 找T日K线
        t_k = conn.execute("""
            SELECT open, close FROM kline WHERE code=? AND date=?
        """, (code, today)).fetchone()
        
        if t_k is None:
            # 可能bundle是昨天的数据
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            t_k = conn.execute("""
                SELECT open, close FROM kline WHERE code=? AND date=?
            """, (code, yesterday)).fetchone()
            if t_k is None:
                continue
            trade_date = yesterday
        else:
            trade_date = today
        
        # 找T+1日K线
        t1 = conn.execute("""
            SELECT code, date, open, close, high FROM kline 
            WHERE code=? AND date > ? 
            ORDER BY date LIMIT 1
        """, (code, trade_date)).fetchone()
        
        if t1 is None:
            continue
        
        # 实际收益（用开盘买入）
        actual_open = (float(t1[3]) - float(t_k[0])) / float(t_k[0]) * 100
        actual_high = (float(t1[4]) - float(t_k[0])) / float(t_k[0]) * 100
        
        # ⭐ 核心修正：买入成本=涨停价（昨收*1.10）
        # 实战打板只能在涨停价成交，不是开盘价
        prev = conn.execute("""
            SELECT close FROM kline WHERE code=? AND date < ? ORDER BY date DESC LIMIT 1
        """, (code, trade_date)).fetchone()
        if prev and float(t_k[1]) >= float(prev[0]) * 1.095:
            # 涨停，买=涨停价
            limit_price = round(float(prev[0]) * 1.10, 2)
            actual_buy = (float(t1[3]) - limit_price) / limit_price * 100
            actual_high_pct = (float(t1[4]) - limit_price) / limit_price * 100
        else:
            actual_buy = actual_open
            actual_high_pct = actual_high
        
        expected_val = float(expected) if expected else 0
        
        results.append({
            'code': code,
            'name': name,
            'trade_date': trade_date,
            't1_date': t1[1],
            'expected': round(expected_val, 2),
            'actual_t1': round(actual_buy, 2),
            'actual_high': round(actual_high_pct, 2),
            'error': round(actual_buy - expected_val, 2),
            'verified_at': datetime.now().isoformat(),
        })
    
    conn.close()
    return results


def save_verification(results):
    """保存到验证数据库"""
    if not results:
        print("❌ 无待验证数据")
        return
    
    conn = sqlite3.connect(VERIFY_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS t1_verify (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            trade_date TEXT,
            t1_date TEXT,
            expected REAL,
            actual_t1 REAL,
            actual_high REAL,
            error REAL,
            verified_at TEXT,
            UNIQUE(code, trade_date)
        )
    """)
    
    saved = 0
    for r in results:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO t1_verify 
                (code, name, trade_date, t1_date, expected, actual_t1, actual_high, error, verified_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (r['code'], r['name'], r['trade_date'], r['t1_date'],
                  r['expected'], r['actual_t1'], r['actual_high'],
                  r['error'], r['verified_at']))
            saved += 1
        except Exception as e:
            print(f"  ❌ 写入失败 {r['code']}: {e}")
    
    conn.commit()
    
    # 统计
    totals = []
    cur = conn.execute("SELECT actual_t1, expected, error FROM t1_verify")
    for row in cur:
        totals.append({'a': row[0], 'e': row[1], 'err': row[2]})
    
    if totals:
        avg_actual = sum(x['a'] for x in totals) / len(totals)
        avg_expected = sum(x['e'] for x in totals) / len(totals)
        avg_error = sum(x['err'] for x in totals) / len(totals)
        wr = sum(1 for x in totals if x['a'] > 0) / len(totals) * 100
        
        print(f"\n📊 总体验证统计（{len(totals)}次）")
        print(f"  平均预期: {avg_expected:+.2f}%")
        print(f"  平均实际: {avg_actual:+.2f}%")
        print(f"  平均偏差: {avg_error:+.2f}%")
        print(f"  实际胜率: {wr:.1f}%")
        
        if avg_error > 2:
            print(f"  ⚠️ 系统偏乐观！实际比预期低{avg_error:.2f}%")
        elif avg_error < -2:
            print(f"  ⚠️ 系统偏保守！实际比预期高{-avg_error:.2f}%")
        else:
            print(f"  ✅ 预期与实际基本一致，偏差{avg_error:.2f}%")
    
    conn.close()
    print(f"\n✅ 已保存{saved}条验证记录到{VERIFY_DB}")


def print_today_results(results):
    """打印今日验证结果"""
    if not results:
        print("\n📭 今日无待验证数据")
        return
    
    print(f"\n{'='*72}")
    print(f"✅ T+1实战验证 — {results[0]['trade_date']}")
    print(f"{'='*72}")
    print(f"{'名称':<8} {'代码':>6} {'预期':>8} {'实际':>8} {'最高':>8} {'偏差':>8}")
    print("-" * 52)
    
    wins = 0
    for r in results:
        name = r['name'][:8] if r['name'] else r['code'][:8]
        status = "✅" if r['actual_t1'] > 0 else "❌"
        if r['actual_t1'] > 0:
            wins += 1
        print(f"{status} {name:<8} {r['code']:>6} {r['expected']:>+7.2f}% {r['actual_t1']:>+7.2f}% {r['actual_high']:>+7.2f}% {r['error']:>+7.2f}%")
    
    n = len(results)
    print(f"\n  今日胜率: {wins}/{n} = {wins/n*100:.1f}%")


if __name__ == '__main__':
    print("=" * 72)
    print("✅ T+1实战验证系统 v1.0")
    print(f"   运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)
    
    print("\n📋 读取昨日候选...")
    candidates = get_yesterday_candidates()
    print(f"   找到{len(candidates)}个候选")
    
    print("\n🔍 验证T+1收益...")
    results = verify_t1(candidates)
    
    print_today_results(results)
    save_verification(results)
