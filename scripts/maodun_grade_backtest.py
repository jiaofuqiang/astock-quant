#!/usr/bin/env python3
"""
矛盾评级回测系统 v1.0
基于《实践论》「调查才有发言权」
用历史龙虎榜数据+T日K线 → 计算每个矛盾评级的T+1真实收益
验证评级阈值（甲≥52、乙≥42、丙≥35、丁≥25）是否合理
"""
import sqlite3, os, json, sys
from collections import defaultdict

LHB_DB = os.path.expanduser("~/astock/data/lhb_cache.db")
KLINE_DB = os.path.expanduser("~/astock/data/kline_cache.db")
REDBLACK_DB = os.path.expanduser("~/astock/data/maodun_redblack.db")
OUTPUT = os.path.expanduser("~/astock/data/maodun_grade_backtest.json")

def compute_grade(ret, wr, n, has_inst, has_overheat=False):
    """计算矛盾评级（与morning_pipeline完全一致）"""
    ret_score = min(20, ret * 3)
    win_score = min(20, wr * 0.3)
    fund_tags_count = 1 if has_inst else 0
    fund_score = fund_tags_count * 5
    n_score = min(15, n * 0.1)
    maodun_total = ret_score + win_score + fund_score + n_score
    maodun_total = min(100, maodun_total)
    
    if has_overheat:
        maodun_total *= 0.85
    
    if maodun_total >= 52 and has_inst:
        grade = '甲等'
    elif maodun_total >= 42:
        grade = '乙等'
    elif maodun_total >= 35:
        grade = '丙等'
    elif maodun_total >= 25:
        grade = '丁等'
    else:
        grade = '戊等'
    
    return grade, round(maodun_total, 1)


def main():
    print("=" * 72)
    print("📊 矛盾评级回测 v1.0 — 《实践论》调查")
    print(f"   启动时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)
    
    if not os.path.exists(LHB_DB) or not os.path.exists(KLINE_DB):
        print("❌ 数据库不存在")
        return
    
    lhb = sqlite3.connect(f'file:{LHB_DB}?mode=ro', uri=True)
    kline = sqlite3.connect(f'file:{KLINE_DB}?mode=ro', uri=True)
    
    # 从龙虎榜提取涨停股 — 修正：用K线涨跌幅判断涨停，不是lhb_list.chg
    print(f"\n📋 提取龙虎榜涨停数据...")
    
    # 先用lhb_list取历史上榜记录，再到kline验证是否涨停
    # step1: lhb_list取所有主板记录
    lhb_rows = lhb.execute("""
        SELECT code, name, date
        FROM lhb_list
        WHERE (code LIKE '6%' OR code LIKE '00%')
        ORDER BY date DESC
    """).fetchall()
    
    print(f"   主板龙虎榜记录: {len(lhb_rows)}条")
    
    # step2: 到kline验证涨停（close >= open * 1.095）
    zt_list = []
    for code, name, date in lhb_rows:
        k = kline.execute("""
            SELECT open, close FROM kline WHERE code=? AND date=?
        """, (code, date)).fetchone()
        if k and k[0] > 0 and k[1] >= k[0] * 1.095:
            real_chg = round((k[1] - k[0]) / k[0] * 100, 2)
            zt_list.append((code, name, date, real_chg, k[0], k[1]))
    
    print(f"   确认涨停: {len(zt_list)}条")
    
    # 看细节：能否确定机构/游资席位数？
    # 从lhb_detail查每只股票每日的席位分布
    print(f"\n📋 提取席位数据...")
    
    results = defaultdict(list)
    processed = 0
    
    for code, name, date, real_chg, k_open, k_close in zt_list:
        processed += 1
        if processed % 500 == 0:
            print(f"   已处理{processed}/{len(zt_list)}...")
        
        # 查当日席位：机构数、游资数
        dealers = lhb.execute("""
            SELECT direction, seq, dealer, buy_amt, sell_amt
            FROM lhb_detail
            WHERE code=? AND date=?
            ORDER BY seq
        """, (code, date)).fetchall()
        
        inst_count = 0
        youzi_count = 0
        total_buy = 0
        total_sell = 0
        
        for d in dealers:
            direction = d[0]
            dealer_name = d[2]
            buy = float(d[3] or 0)
            sell = float(d[4] or 0)
            total_buy += buy
            total_sell += sell
            
            if '机构' in dealer_name:
                inst_count += 1
            elif any(yz in dealer_name for yz in ['东方财富', '拉萨', '银河', '中信上海', '华泰']):
                youzi_count += 1
        
        total_detail = dealers
        if not total_detail:
            continue
        
        # 查T+1 K线
        t1 = kline.execute("""
            SELECT open, close, high FROM kline 
            WHERE code=? AND date > ? ORDER BY date LIMIT 1
        """, (code, date)).fetchone()
        
        if t1 is None:
            continue
        
        # 买入成本=涨停价（昨收*1.10）
        prev = kline.execute("""
            SELECT close FROM kline WHERE code=? AND date < ? ORDER BY date DESC LIMIT 1
        """, (code, date)).fetchone()
        
        if prev is None:
            continue
        
        buy_price = round(float(prev[0]) * 1.10, 2)
        t1_close = float(t1[1])
        t1_open = float(t1[0])
        
        t1_close_ret = round((t1_close - buy_price) / buy_price * 100, 2)
        t1_open_ret = round((t1_open - buy_price) / buy_price * 100, 2)
        
        # 计算矛盾评级的模拟得分
        ret = real_chg
        wr = 60.0
        has_inst = inst_count > 0
        n_approx = 69 if inst_count >= 2 else (93 if inst_count >= 1 else 136)
        
        grade, score = compute_grade(ret, wr, n_approx, has_inst)
        
        results[grade].append({
            'code': code, 'name': name, 'date': date,
            'inst': inst_count, 'youzi': youzi_count,
            't1_close': t1_close_ret, 't1_open': t1_open_ret,
            'grade_score': score,
        })
    
    # ===== 按评级统计 =====
    print(f"\n{'='*72}")
    print(f"📊 矛盾评级回测结果")
    print(f"{'='*72}")
    
    grade_order = ['甲等', '乙等', '丙等', '丁等', '戊等']
    all_results = {}
    
    for g in grade_order:
        items = results.get(g, [])
        if not items:
            print(f"\n{g}: 无样本")
            continue
        
        n = len(items)
        t1_close_vals = [i['t1_close'] for i in items]
        t1_open_vals = [i['t1_open'] for i in items]
        
        avg_close = round(sum(t1_close_vals) / n, 2)
        avg_open = round(sum(t1_open_vals) / n, 2)
        wins = sum(1 for v in t1_close_vals if v > 0)
        win_rate = round(wins / n * 100, 1)
        
        # 分布
        p25 = sorted(t1_close_vals)[int(n * 0.25)]
        p50 = sorted(t1_close_vals)[int(n * 0.5)]
        p75 = sorted(t1_close_vals)[int(n * 0.75)]
        
        max_ret = max(t1_close_vals)
        min_ret = min(t1_close_vals)
        
        print(f"\n{g}: {n}笔")
        print(f"  开盘卖: {avg_open:+.2f}% → 收盘卖: {avg_close:+.2f}% | 胜率: {win_rate}%")
        print(f"  四分位: [{p25:+.2f}%, {p50:+.2f}%, {p75:+.2f}%] | 范围: {min_ret:+.2f}% ~ {max_ret:+.2f}%")
        
        # 找最好的和最差的
        best = max(items, key=lambda x: x['t1_close'])
        worst = min(items, key=lambda x: x['t1_close'])
        print(f"  最佳: {best['name']}({best['code']}) {best['date']} +{best['t1_close']:.1f}%")
        print(f"  最差: {worst['name']}({worst['code']}) {worst['date']} {worst['t1_close']:.1f}%")
        
        all_results[g] = {
            'n': n, 'avg_close': avg_close, 'avg_open': avg_open,
            'win_rate': win_rate, 'median': p50,
            'p25': p25, 'p75': p75,
            'max': max_ret, 'min': min_ret,
            'best': best, 'worst': worst,
        }
    
    # ===== 阈值验证 =====
    print(f"\n{'='*72}")
    print(f"🎯 阈值验证")
    print(f"{'='*72}")
    
    # 看看每个等级的平均矛盾分
    print(f"\n评级阈值合理性:")
    for g in grade_order:
        items = results.get(g, [])
        if items:
            avg_score = round(sum(i['grade_score'] for i in items) / len(items), 1)
            avg_ret = round(sum(i['t1_close'] for i in items) / len(items), 2)
            print(f"  {g}: 均分{avg_score} | 均收益{avg_ret:+.2f}%")
    
    # 看看等级之间的区分度
    print(f"\n等级区分度（相邻等级收益差应>0.5%才有意义）:")
    prev_ret = None
    for g in grade_order:
        items = results.get(g, [])
        if items:
            avg_ret = round(sum(i['t1_close'] for i in items) / len(items), 2)
            if prev_ret is not None:
                diff = round(avg_ret - prev_ret, 2)
                ok = '✅' if diff > 0.5 else '⚠️区分度不足'
                print(f"  {g} vs 上一级: 差{diff:+.2f}% {ok}")
            prev_ret = avg_ret
    
    # ===== 写入红黑榜数据库 =====
    rbc = sqlite3.connect(REDBLACK_DB)
    rbc.execute("""
        CREATE TABLE IF NOT EXISTS grade_backtest (
            grade TEXT PRIMARY KEY,
            n INTEGER, avg_close REAL, avg_open REAL,
            win_rate REAL, median REAL,
            p25 REAL, p75 REAL, max_ret REAL, min_ret REAL,
            updated_at TEXT
        )
    """)
    
    for g, stats in all_results.items():
        rbc.execute("""
            INSERT OR REPLACE INTO grade_backtest
            (grade, n, avg_close, avg_open, win_rate, median, p25, p75, max_ret, min_ret, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (g, stats['n'], stats['avg_close'], stats['avg_open'],
              stats['win_rate'], stats['median'],
              stats['p25'], stats['p75'], stats['max'], stats['min'],
              __import__('datetime').datetime.now().isoformat()))
    
    rbc.commit()
    rbc.close()
    
    # 保存到JSON
    with open(OUTPUT, 'w') as f:
        json.dump({
            'generated_at': __import__('datetime').datetime.now().isoformat(),
            'total_trades': sum(len(v) for v in results.values()),
            'grades': all_results,
        }, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n✅ 已保存回测结果到 {OUTPUT}")
    print(f"✅ 已写入红黑榜数据库 {REDBLACK_DB}")
    
    lhb.close()
    kline.close()
    print(f"\n📋 总样本: {processed}笔")


if __name__ == '__main__':
    main()
