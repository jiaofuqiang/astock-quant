#!/usr/bin/env python3
"""
📊 实盘验证系统 v1.0
====================
追踪决策引擎每次推荐的买入候选，在T+1收盘后自动对账：
  - 预期T+1收益 vs 实际T+1收益
  - 胜率偏差（预期胜率 vs 实际胜率）
  - 信号级别偏差（高置信度实际如何）
  - 每日偏差报告

数据流：
  09:08(引擎运行) → 记录recommendations表
  15:20(收盘后)  → 对账T+1实际表现 → 写入verification表
  每周日         → 偏差分析 → 参数调优建议
"""

import sqlite3, os, json, sys
from datetime import datetime, date, timedelta

BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
DB_PATH = os.path.join(DATA_DIR, 'strategy_verify.db')


def init_db():
    """初始化验证数据库"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 推荐记录：引擎每次输出的买入候选
    c.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rec_date TEXT,           -- 推荐日期
            rec_time TEXT,           -- 推荐时间
            code TEXT,
            name TEXT,
            price REAL,              -- 推荐时价格(涨停价)
            score INTEGER,           -- 引擎评分
            expected_t1 REAL,        -- 预期T+1竞价收益%
            signals TEXT,            -- 信号标签(JSON数组)
            confidence TEXT,         -- 高/中/低
            sector TEXT,             -- 板块
            reason TEXT,             -- 推荐理由
            strategy_type TEXT,      -- 信号类型
            rank INTEGER,            -- 推荐排名
            create_time TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    # 验证记录：T+1后实际表现
    c.execute("""
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rec_id INTEGER,          -- 对应recommendations.id
            rec_date TEXT,
            verify_date TEXT,        -- 验证日期(T+1)
            code TEXT,
            name TEXT,
            expected_t1 REAL,        -- 预期T+1竞价%
            actual_open REAL,        -- T+1实际竞价涨跌幅%
            actual_close REAL,       -- T+1收盘涨跌幅%
            actual_high REAL,        -- T+1最高涨跌幅%
            actual_low REAL,         -- T+1最低涨跌幅%
            diff_open REAL,          -- 偏差(实际-预期)
            is_win BOOLEAN,          -- T+1竞价是否盈利
            score INTEGER,
            confidence TEXT,
            signals TEXT,
            create_time TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    # 每日汇总
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary_date TEXT UNIQUE,
            total_recommendations INTEGER DEFAULT 0,
            verified_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            avg_expected REAL DEFAULT 0,
            avg_actual_open REAL DEFAULT 0,
            avg_actual_close REAL DEFAULT 0,
            avg_diff REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            high_conf_count INTEGER DEFAULT 0,
            high_conf_wins INTEGER DEFAULT 0,
            high_conf_win_rate REAL DEFAULT 0,
            mid_conf_count INTEGER DEFAULT 0,
            mid_conf_wins INTEGER DEFAULT 0,
            mid_conf_win_rate REAL DEFAULT 0,
            total_stock_recommendations INTEGER DEFAULT 0,
            total_unique_stocks INTEGER DEFAULT 0,
            create_time TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    # 参数调优记录
    c.execute("""
        CREATE TABLE IF NOT EXISTS param_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            adjust_date TEXT,
            param_name TEXT,
            old_value REAL,
            new_value REAL,
            reason TEXT,
            effect_expected TEXT,
            create_time TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    conn.commit()
    conn.close()
    print(f"✅ 验证数据库初始化完成: {DB_PATH}")


def log_recommendations(report_data):
    """记录引擎推荐的买入候选"""
    if not report_data or 'buy_candidates' not in report_data:
        return 0
    
    buys = report_data.get('buy_candidates', [])
    if not buys:
        return 0
    
    today_str = date.today().isoformat()
    now_str = datetime.now().strftime('%H:%M:%S')
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    count = 0
    for i, b in enumerate(buys[:10], 1):
        signals_json = json.dumps(b.get('signals', []), ensure_ascii=False)
        c.execute("""
            INSERT INTO recommendations 
            (rec_date, rec_time, code, name, price, score, expected_t1, signals, confidence, sector, reason, strategy_type, rank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today_str, now_str,
            b.get('code', ''), b.get('name', ''),
            b.get('price', 0), b.get('score', 0),
            b.get('expected_t1', 0), signals_json,
            b.get('confidence', ''), b.get('sector', ''),
            b.get('reason', ''), str(b.get('signals', [])), i
        ))
        count += 1
    
    conn.commit()
    conn.close()
    return count


def log_verification(rec_date):
    """T+1验证：查今天所有未验证的推荐，用腾讯行情对比实际表现"""
    from subprocess import run
    
    verify_date_str = date.today().isoformat()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 找到昨天(T-1)所有未验证的记录
    target_date = (date.today() - timedelta(days=1)).isoformat()
    
    c.execute("""
        SELECT r.id, r.code, r.name, r.expected_t1, r.score, r.confidence, r.signals
        FROM recommendations r
        LEFT JOIN verifications v ON r.id = v.rec_id
        WHERE r.rec_date = ? AND v.id IS NULL
    """, (target_date,))
    unverified = c.fetchall()
    
    if not unverified:
        conn.close()
        return 0
    
    print(f"  待验证推荐: {len(unverified)}条（日期{target_date}）")
    
    # 获取全部股票今天的实时行情
    codes = [r[1] for r in unverified]
    
    # 用腾讯行情获取
    verified_count = 0
    for rec_id, code, name, expected_t1, score, confidence, sig_json in unverified:
        try:
            mkt = f"sh{code}" if code[0] in ('6', '5', '9') else f"sz{code}"
            r = run(['curl', '-s', '--connect-timeout', '3', '--max-time', '5',
                     f'https://qt.gtimg.cn/q={mkt}'],
                    capture_output=True, timeout=8)
            if not r.stdout:
                continue
            raw_text = r.stdout.decode('gbk', errors='replace')
            
            line = raw_text.strip()
            if '=' not in line:
                continue
            raw = line.split('=', 1)[1].strip().strip('"').strip(';').strip('"')
            fields = raw.split('~')
            if len(fields) < 35:
                continue
            
            prev_close = float(fields[4]) if fields[4] else 0
            cur_price = float(fields[3]) if fields[3] else 0
            today_open = float(fields[5]) if fields[5] else 0
            
            if prev_close <= 0:
                continue
            
            # 实际T+1表现
            actual_open = round((today_open - prev_close) / prev_close * 100, 2)
            actual_close = round((cur_price - prev_close) / prev_close * 100, 2)
            high = float(fields[33]) if fields[33] else 0
            low = float(fields[34]) if fields[34] else 0
            actual_high = round((high - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
            actual_low = round((low - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
            
            diff_open = round(actual_open - (expected_t1 or 0), 2)
            is_win = actual_open > 0
            
            c.execute("""
                INSERT INTO verifications 
                (rec_id, rec_date, verify_date, code, name,
                 expected_t1, actual_open, actual_close, actual_high, actual_low,
                 diff_open, is_win, score, confidence, signals)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rec_id, target_date, verify_date_str, code, name,
                expected_t1, actual_open, actual_close, actual_high, actual_low,
                diff_open, is_win, score, confidence, sig_json
            ))
            verified_count += 1
        except:
            continue
    
    conn.commit()
    conn.close()
    return verified_count


def generate_daily_summary(summary_date=None):
    """生成每日验证汇总"""
    if summary_date is None:
        summary_date = (date.today() - timedelta(days=1)).isoformat()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 计算汇总
    c.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END) as wins,
            AVG(expected_t1) as avg_exp,
            AVG(actual_open) as avg_open,
            AVG(actual_close) as avg_close,
            AVG(diff_open) as avg_diff,
            SUM(CASE WHEN confidence = '🔥高' THEN 1 ELSE 0 END) as high_cnt,
            SUM(CASE WHEN confidence = '🔥高' AND is_win = 1 THEN 1 ELSE 0 END) as high_wins,
            SUM(CASE WHEN confidence = '✅中' THEN 1 ELSE 0 END) as mid_cnt,
            SUM(CASE WHEN confidence = '✅中' AND is_win = 1 THEN 1 ELSE 0 END) as mid_wins
        FROM verifications WHERE rec_date = ?
    """, (summary_date,))
    row = c.fetchone()
    
    if not row or not row[0]:
        print(f"  ⚠️ {summary_date} 无验证数据")
        conn.close()
        return None
    
    total, wins, avg_exp, avg_open, avg_close, avg_diff = row[0:6]
    high_cnt, high_wins, mid_cnt, mid_wins = row[6:10]
    
    # 去重股票数
    c.execute("SELECT COUNT(DISTINCT code) FROM verifications WHERE rec_date = ?", (summary_date,))
    unique_stocks = c.fetchone()[0] or 0
    
    # stock-level count
    c.execute("SELECT COUNT(*) FROM verifications WHERE rec_date = ?", (summary_date,))
    stock_recs = c.fetchone()[0] or 0
    
    summary = {
        'date': summary_date,
        'total_recommendations': total,
        'verified_count': total,
        'win_count': wins,
        'loss_count': total - wins,
        'avg_expected': round(avg_exp, 2) if avg_exp else 0,
        'avg_actual_open': round(avg_open, 2) if avg_open else 0,
        'avg_actual_close': round(avg_close, 2) if avg_close else 0,
        'avg_diff': round(avg_diff, 2) if avg_diff else 0,
        'win_rate': round(wins / total * 100, 1) if total > 0 else 0,
        'high_conf_count': high_cnt or 0,
        'high_conf_wins': high_wins or 0,
        'high_conf_win_rate': round(high_wins / high_cnt * 100, 1) if high_cnt else 0,
        'mid_conf_count': mid_cnt or 0,
        'mid_conf_wins': mid_wins or 0,
        'mid_conf_win_rate': round(mid_wins / mid_cnt * 100, 1) if mid_cnt else 0,
        'total_stock_recommendations': stock_recs,
        'total_unique_stocks': unique_stocks,
    }
    
    # 写入daily_summary
    c.execute("""
        INSERT OR REPLACE INTO daily_summary 
        (summary_date, total_recommendations, verified_count, win_count, loss_count,
         avg_expected, avg_actual_open, avg_actual_close, avg_diff, win_rate,
         high_conf_count, high_conf_wins, high_conf_win_rate,
         mid_conf_count, mid_conf_wins, mid_conf_win_rate,
         total_stock_recommendations, total_unique_stocks)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        summary_date, total, total, wins, total - wins,
        summary['avg_expected'], summary['avg_actual_open'],
        summary['avg_actual_close'], summary['avg_diff'], summary['win_rate'],
        high_cnt or 0, high_wins or 0, summary['high_conf_win_rate'],
        mid_cnt or 0, mid_wins or 0, summary['mid_conf_win_rate'],
        stock_recs, unique_stocks
    ))
    
    conn.commit()
    conn.close()
    
    print(f"\n📊 {summary_date} 验证汇总:")
    print(f"  推荐: {total}条 | 已验证: {total} | 赢: {wins} | 亏: {total-wins}")
    print(f"  预期均值: +{avg_exp:.2f}% | 实际竞价: {avg_open:+.2f}% | 收盘: {avg_close:+.2f}%")
    print(f"  偏差: {avg_diff:+.2f}% | 胜率: {wins/total*100:.1f}%")
    print(f"  高置信({high_cnt}次): {high_wins}胜 胜率{high_wins/high_cnt*100 if high_cnt else 0:.1f}%")
    
    return summary


def generate_verification_report(days=7):
    """生成最近N天的验证报告"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("""
        SELECT summary_date, total_recommendations, verified_count,
               win_count, loss_count, avg_expected, avg_actual_open,
               avg_actual_close, avg_diff, win_rate,
               high_conf_count, high_conf_wins, high_conf_win_rate,
               mid_conf_count, mid_conf_wins, mid_conf_win_rate
        FROM daily_summary 
        ORDER BY summary_date DESC LIMIT ?
    """, (days,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        print("暂无验证数据")
        return
    
    print(f"\n{'='*80}")
    print(f"📊 实盘验证报告（最近{len(rows)}天）")
    print(f"{'='*80}")
    print(f"{'日期':<12} {'推荐':>4} {'验证':>4} {'赢':>4} {'亏':>4} {'预期%':>7} {'竞价%':>7} {'收盘%':>7} {'偏差%':>7} {'胜率':>6} {'高置信':>8}")
    print(f"{'-'*80}")
    
    total_recs = 0
    total_wins = 0
    total_avg_exp = 0
    total_avg_actual = 0
    
    for r in rows:
        date_str, recs, verified, wins, losses, avg_exp, avg_open, avg_close, diff, wr = r[:10]
        high_c, high_w, high_wr, mid_c, mid_w, mid_wr = r[10:16]
        
        print(f"{date_str:<12} {recs:>4} {verified:>4} {wins:>4} {losses:>4} {avg_exp:>+6.2f}% {avg_open:>+6.2f}% {avg_close:>+6.2f}% {diff:>+6.2f}% {wr:>5.1f}% {f'{high_c}/{high_w}':>8}")
        
        total_recs += recs
        total_wins += wins
        total_avg_exp += avg_exp * recs
        total_avg_actual += avg_open * recs
    
    # 汇总行  
    if total_recs > 0:
        print(f"{'-'*80}")
        print(f"{'合计':<12} {total_recs:>4} {'':>4} {total_wins:>4} {total_recs-total_wins:>4} {total_avg_exp/total_recs:>+6.2f}% {total_avg_actual/total_recs:>+6.2f}% {'':>7} {total_avg_actual/total_recs - total_avg_exp/total_recs:+>+6.2f}% {total_wins/total_recs*100:>5.1f}%")
    
    # 信号级别偏差
    print(f"\n{'='*80}")
    print(f"📊 信号置信度表现")
    
    c2 = sqlite3.connect(DB_PATH)
    cur = c2.cursor()
    
    # 高置信
    cur.execute("""
        SELECT confidence, COUNT(*), 
               SUM(CASE WHEN is_win THEN 1 ELSE 0 END) as wins,
               AVG(expected_t1), AVG(actual_open)
        FROM verifications 
        WHERE verify_date >= date('now', '-14 days')
        GROUP BY confidence ORDER BY COUNT(*) DESC
    """)
    
    print(f"{'置信度':<12} {'次数':>6} {'胜':>4} {'亏':>4} {'预期':>7} {'实际':>7} {'胜率':>6}")
    for row in cur.fetchall():
        conf, cnt, w, _, exp_avg, act_avg = row
        print(f"{conf:<12} {cnt:>6} {w:>4} {cnt-w:>4} {exp_avg or 0:+>+6.2f}% {act_avg or 0:+>+6.2f}% {w/cnt*100 if cnt else 0:>5.1f}%")
    
    c2.close()
    
    # 最佳策略排行
    cur3 = sqlite3.connect(DB_PATH)
    cu = cur3.cursor()
    cu.execute("""
        SELECT signals, COUNT(*) as cnt,
               SUM(CASE WHEN is_win THEN 1 ELSE 0 END) as wins,
               AVG(actual_open) as avg_open,
               AVG(expected_t1) as avg_exp
        FROM verifications
        WHERE verify_date >= date('now', '-14 days')
        GROUP BY signals ORDER BY avg_open DESC LIMIT 8
    """)
    
    print(f"\n{'='*80}")
    print(f"📊 信号类型胜率排行（最近14天）")
    print(f"{'信号类型':<30} {'次数':>4} {'胜':>4} {'实际竞价':>8} {'预期':>7} {'偏差':>7}")
    for row in cu.fetchall():
        sig = row[0][:28] if row[0] else '未知'
        cnt, w, a, e = row[1], row[2], row[3] or 0, row[4] or 0
        d = a - e
        print(f"{sig:<30} {cnt:>4} {w:>4} {a:>+7.2f}% {e:>+6.2f}% {d:>+6.2f}%")
    cur3.close()


def analyze_bias_and_suggest():
    """偏差分析 → 参数调优建议"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 最近14天各信号类型的偏差
    c.execute("""
        SELECT signals, COUNT(*) as cnt,
               AVG(actual_open) as avg_actual,
               AVG(expected_t1) as avg_exp,
               AVG(diff_open) as avg_diff,
               SUM(CASE WHEN is_win THEN 1 ELSE 0 END) as wins
        FROM verifications
        WHERE verify_date >= date('now', '-14 days')
        GROUP BY signals
        HAVING cnt >= 5
        ORDER BY avg_diff DESC
    """)
    rows = c.fetchall()
    
    suggestions = []
    
    for row in rows:
        sig_str, cnt, avg_actual, avg_exp, avg_diff, wins = row
        win_rate = round(wins / cnt * 100, 1) if cnt > 0 else 0
        
        # 系统偏差大于1%时建议调参
        if abs(avg_diff) > 1.0:
            direction = '高估' if avg_diff < -0.5 else '低估'
            if avg_diff < -1.0:
                # 实际低于预期 → 该信号被高估
                suggestions.append({
                    'signal': sig_str or '综合',
                    'count': cnt,
                    'avg_diff': round(avg_diff, 2),
                    'direction': '高估',
                    'suggest': f'降低该信号权重{min(abs(round(avg_diff*5)), 15)}分',
                    'win_rate': win_rate,
                    'actual': round(avg_actual, 2) if avg_actual else 0,
                    'expected': round(avg_exp, 2) if avg_exp else 0,
                })
            elif avg_diff > 1.0:
                suggestions.append({
                    'signal': sig_str or '综合',
                    'count': cnt,
                    'avg_diff': round(avg_diff, 2),
                    'direction': '低估',
                    'suggest': f'提高该信号权重{min(abs(round(avg_diff*5)), 15)}分',
                    'win_rate': win_rate,
                    'actual': round(avg_actual, 2) if avg_actual else 0,
                    'expected': round(avg_exp, 2) if avg_exp else 0,
                })
    
    conn.close()
    
    if suggestions:
        print(f"\n{'='*80}")
        print(f"⚙️ 偏差分析与参数调优建议")
        print(f"{'='*80}")
        for s in suggestions:
            print(f"  {s['signal']:<30} {'|'} {s['count']:>3}次 {'|'} 实际{s['actual']:+.2f}% vs 预期{s['expected']:+.2f}% {'|'} 偏差{s['avg_diff']:+.2f}%")
            print(f"  {'':>30} {'|'} 方向: {s['direction']:<4} {'|'} 胜率{s['win_rate']}% {'|'} 建议: {s['suggest']}")
    else:
        print(f"\n✅ 无显著偏差，参数稳定")
    
    return suggestions


def main():
    import argparse
    parser = argparse.ArgumentParser(description='实盘验证系统')
    parser.add_argument('--init', action='store_true', help='初始化数据库')
    parser.add_argument('--log', help='记录推荐数据(输入JSON文件路径)')
    parser.add_argument('--verify', action='store_true', help='T+1验证(查昨天推荐)')
    parser.add_argument('--report', type=int, nargs='?', const=7, help='生成验证报告(默认7天)')
    parser.add_argument('--analyze', action='store_true', help='偏差分析+调优建议')
    parser.add_argument('--all', action='store_true', help='完整运行: verify+report+analyze')
    
    args = parser.parse_args()
    
    if args.init:
        init_db()
        return
    
    if args.log:
        with open(args.log, encoding='utf-8') as f:
            data = json.load(f)
        count = log_recommendations(data)
        print(f"✅ 记录{count}条推荐")
        return
    
    if args.verify:
        # 默认验证昨日的
        rec_date = (date.today() - timedelta(days=1)).isoformat()
        count = log_verification(rec_date)
        print(f"✅ 完成{count}条T+1验证")
        if count > 0:
            generate_daily_summary(rec_date)
        return
    
    if args.report:
        generate_verification_report(args.report)
        return
    
    if args.analyze:
        analyze_bias_and_suggest()
        return
    
    if args.all:
        init_db()
        rec_date = (date.today() - timedelta(days=1)).isoformat()
        v_count = log_verification(rec_date)
        print(f"  验证: {v_count}条")
        if v_count > 0:
            generate_daily_summary(rec_date)
        generate_verification_report(7)
        analyze_bias_and_suggest()
        return
    
    parser.print_help()


if __name__ == '__main__':
    main()
