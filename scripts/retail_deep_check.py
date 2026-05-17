#!/usr/bin/env python3
"""深化验证散户恐慌后的反转效应"""
import sqlite3
from collections import defaultdict

DB = "/home/ubuntu/astock/data/kline_cache.db"

def future_chg(c, code, date, days):
    c.execute("SELECT close FROM kline WHERE code = ? AND date = ?", (code, date))
    today = c.fetchone()
    if not today:
        return None
    base = today[0]
    if base == 0:
        return None
    c.execute("SELECT close FROM kline WHERE code = ? AND date = date(?, ?)",
              (code, date, f'+{days} days'))
    r = c.fetchone()
    if not r or r[0] is None:
        return None
    return (r[0] - base) / base * 100

def get_ma_vol(c, code, date, window=20):
    c.execute("SELECT AVG(volume) FROM kline WHERE code = ? AND date < ? AND date >= date(?, ?)",
              (code, date, date, f'-{window} days'))
    r = c.fetchone()
    return r[0] if r and r[0] else None

def analyze():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    # 获取近期有数据的股票（3000只够用）
    c.execute("SELECT DISTINCT code FROM kline WHERE date >= '2024-06-01'")
    codes = [r[0] for r in c.fetchall()]
    
    # 验证4个关键信号
    signals = {
        '缩量跌_买点': {'buy': True, 'cond': 'chg_1d < -2 AND vol_ratio < 0.6'},
        '高开低走_卖点': {'buy': False, 'cond': 'open > prev_close AND chg_1d < -1 AND vol_ratio > 1.5'},
        '天量涨停_卖点': {'buy': False, 'cond': 'chg_1d > 9 AND vol_ratio > 2'},
        '无量涨_买点': {'buy': True, 'cond': 'chg_1d > 2 AND vol_ratio < 0.7'},
    }
    
    results = defaultdict(lambda: {'n': 0, 'chg_1d_sum': 0, 'chg_3d_sum': 0, 'chg_5d_sum': 0,
                                    'win_1d': 0, 'win_3d': 0, 'win_5d': 0})
    
    for code in codes:
        c.execute("""SELECT date, open, close, volume FROM kline 
                     WHERE code = ? AND date >= '2024-06-01' AND date <= '2026-04-28'
                     ORDER BY date""", (code,))
        rows = c.fetchall()
        if len(rows) < 25:
            continue

        for i in range(20, len(rows)):
            date, open_p, close, vol = rows[i]
            chg = (close - open_p) / open_p * 100
            
            ma_vol = get_ma_vol(c, code, date, 20)
            if not ma_vol or ma_vol == 0:
                continue
            vol_ratio = vol / ma_vol
            
            prev_close = rows[i-1][2]  # 前一日收盘
            
            # 未来涨跌幅
            f1 = future_chg(c, code, date, 1)
            f3 = future_chg(c, code, date, 3)
            f5 = future_chg(c, code, date, 5)
            
            # 缩量跌 → 买点
            if chg < -2 and vol_ratio < 0.6:
                if f1 is not None:
                    results['缩量跌']['n'] += 1
                    results['缩量跌']['chg_1d_sum'] += f1
                    if f1 > 0: results['缩量跌']['win_1d'] += 1
                if f3 is not None:
                    results['缩量跌']['chg_3d_sum'] += f3
                    if f3 > 0: results['缩量跌']['win_3d'] += 1
                if f5 is not None:
                    results['缩量跌']['chg_5d_sum'] += f5
                    if f5 > 0: results['缩量跌']['win_5d'] += 1
            
            # 高开低走放量 → 卖点
            if open_p > prev_close and chg < -1 and vol_ratio > 1.5:
                if f1 is not None:
                    results['高开低走']['n'] += 1
                    results['高开低走']['chg_1d_sum'] += f1
                    if f1 > 0: results['高开低走']['win_1d'] += 1
                if f3 is not None:
                    results['高开低走']['chg_3d_sum'] += f3
                    if f3 > 0: results['高开低走']['win_3d'] += 1
                if f5 is not None:
                    results['高开低走']['chg_5d_sum'] += f5
                    if f5 > 0: results['高开低走']['win_5d'] += 1
            
            # 天量涨停 → 卖点
            if chg > 9 and vol_ratio > 2:
                if f1 is not None:
                    results['天量涨停']['n'] += 1
                    results['天量涨停']['chg_1d_sum'] += f1
                    if f1 > 0: results['天量涨停']['win_1d'] += 1
                if f3 is not None:
                    results['天量涨停']['chg_3d_sum'] += f3
                    if f3 > 0: results['天量涨停']['win_3d'] += 1
                if f5 is not None:
                    results['天量涨停']['chg_5d_sum'] += f5
                    if f5 > 0: results['天量涨停']['win_5d'] += 1
            
            # 无量涨 → 买点
            if chg > 2 and vol_ratio < 0.7:
                if f1 is not None:
                    results['无量涨']['n'] += 1
                    results['无量涨']['chg_1d_sum'] += f1
                    if f1 > 0: results['无量涨']['win_1d'] += 1
                if f3 is not None:
                    results['无量涨']['chg_3d_sum'] += f3
                    if f3 > 0: results['无量涨']['win_3d'] += 1
                if f5 is not None:
                    results['无量涨']['chg_5d_sum'] += f5
                    if f5 > 0: results['无量涨']['win_5d'] += 1

    conn.close()
    
    print(f"{'='*80}")
    print(f"散户反指深度验证 | 2024-06 → 2026-04 | {len(codes)}只股票")
    print(f"{'='*80}")
    for name in ['缩量跌_买点📗', '无量涨_买点📗', '高开低走_卖点📕', '天量涨停_卖点📕']:
        clean = name.split('_')[0]
        r = results.get(clean)
        if not r or r['n'] == 0:
            continue
        n = r['n']
        a1 = r['chg_1d_sum'] / n
        w1 = r['win_1d'] / n * 100
        a3 = r['chg_3d_sum'] / n if n > 0 else 0
        w3 = r['win_3d'] / n * 100 if r['win_3d'] > 0 else 0
        a5 = r['chg_5d_sum'] / n if n > 0 else 0
        w5 = r['win_5d'] / n * 100 if r['win_5d'] > 0 else 0
        
        print(f"\n{'─'*80}")
        print(f"【{name}】 样本:{n}")
        print(f"  次日: {a1:+.2f}% (胜率{w1:.1f}%)")
        print(f"  3日: {a3:+.2f}% (胜率{w3:.1f}%)")
        print(f"  5日: {a5:+.2f}% (胜率{w5:.1f}%)")
        
        if '买点' in name:
            if w1 > 50:
                print(f"  ✅ 有效买点信号！")
            else:
                print(f"  ⚠️ 买点信号偏弱")
        else:
            if w1 < 50:  # 卖出信号：胜率越低越好
                print(f"  ✅ 有效卖出信号！(跌的概率大)")
            else:
                print(f"  ⚠️ 卖出信号偏弱")

if __name__ == '__main__':
    analyze()
