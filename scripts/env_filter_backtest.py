#!/usr/bin/env python3
"""
阶段1: 宏观环境分桶回测 v2
从K线数据 + 龙虎榜数据中，按宏观环境分桶回测龙虎榜策略

适配真实数据格式：
- lhb_detail: date,code,direction(买/卖),seq,dealer(营业部名称),buy_amt(万),sell_amt(万),net
- lhb_list: date,code,name,type,price,chg
"""

import sqlite3, json, sys, os
from datetime import datetime, timedelta
from collections import defaultdict

DB = os.path.expanduser('~/astock/data')
KLINE = f'{DB}/kline_cache.db'
LHB = f'{DB}/lhb_cache.db'

def db_connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def compute_env_score(kdata):
    if not kdata or len(kdata) < 50:
        return 50, {}
    
    up_count = sum(1 for r in kdata if r['chg_pct'] and r['chg_pct'] > 0)
    down_count = sum(1 for r in kdata if r['chg_pct'] and r['chg_pct'] <= 0)
    total = up_count + down_count
    zh_ratio = up_count / total * 100 if total > 0 else 50
    
    limit_up = sum(1 for r in kdata if r['chg_pct'] and r['chg_pct'] >= 9.9)
    limit_down = sum(1 for r in kdata if r['chg_pct'] and r['chg_pct'] <= -9.9)
    limit_up_rate = limit_up / total * 100 if total > 0 else 0
    
    big_up = sum(1 for r in kdata if r['chg_pct'] and r['chg_pct'] > 5)
    big_down = sum(1 for r in kdata if r['chg_pct'] and r['chg_pct'] < -5)
    effect_ratio = (big_up / big_down) if big_down > 0 else (10 if big_up > 0 else 1)
    
    avg_chg = sum(r['chg_pct'] or 0 for r in kdata) / total if total > 0 else 0
    
    # 炸板率估算
    limit_range = [r for r in kdata if r['chg_pct'] and abs(r['chg_pct']) > 9.9]
    if limit_range:
        open2low = sum(abs(r.get('open_chg', 0) - r['chg_pct']) for r in limit_range)
        zhaban_rate = min(open2low / len(limit_range) / 5 * 100, 100) if limit_range else 0
    else:
        zhaban_rate = 0
    
    # 1. 涨跌比 (20分)
    ratio_score = 20 if zh_ratio > 60 else (15 if zh_ratio > 45 else (10 if zh_ratio > 30 else (5 if zh_ratio > 20 else 0)))
    
    # 2. 涨停强度 (25分)
    limit_score = 25 if limit_up_rate > 5 else (20 if limit_up_rate > 3 else (15 if limit_up_rate > 1.5 else (8 if limit_up_rate > 0.5 else 0)))
    
    # 3. 赚钱效应 (25分)
    effect_score = 25 if effect_ratio > 3 else (20 if effect_ratio > 1.5 else (15 if effect_ratio > 0.8 else (8 if effect_ratio > 0.3 else 0)))
    
    # 4. 动量 (15分)
    momentum_score = 15 if avg_chg > 0.5 else (10 if avg_chg > 0 else (5 if avg_chg > -0.3 else 0))
    
    # 5. 炸板率 (15分)
    zhaban_score = 15 if zhaban_rate < 15 else (10 if zhaban_rate < 30 else (5 if zhaban_rate < 45 else 0))
    
    return (ratio_score + limit_score + effect_score + momentum_score + zhaban_score,
            {'up_count': up_count, 'down_count': down_count, 'zh_ratio': round(zh_ratio, 1),
             'limit_up': limit_up, 'limit_down': limit_down, 'big_up': big_up, 'big_down': big_down,
             'avg_chg': round(avg_chg, 2), 'components': {'ratio': ratio_score, 'limit': limit_score,
             'effect': effect_score, 'momentum': momentum_score, 'zhaban': zhaban_score}})

def get_kline_snapshot(conn, date):
    rows = conn.execute('SELECT code, open, close, high, low, volume FROM kline WHERE date = ?', (date,)).fetchall()
    if not rows:
        return None
    result = []
    for r in rows:
        if r['open'] and r['open'] > 0 and r['close'] and r['close'] > 0:
            chg = (r['close'] - r['open']) / r['open'] * 100
            open_chg = (r['open'] - r['close']) / r['close'] * 100 if r['close'] > 0 else 0  # 相对昨收的开盘涨幅
            result.append({'code': r['code'], 'chg_pct': chg, 'open_chg': open_chg, 'volume': r['volume']})
    return result

def get_lhb_signals(conn_lhb, conn_kline, date):
    """获取某日龙虎榜信号，返回合并后的列表"""
    # 从lhb_list获取当日上榜股票
    list_rows = conn_lhb.execute(
        'SELECT DISTINCT code, name, type FROM lhb_list WHERE date = ?', (date,)
    ).fetchall()
    
    if not list_rows:
        return []
    
    # 从lhb_detail获取买卖明细
    detail_rows = conn_lhb.execute(
        'SELECT code, direction, dealer, buy_amt, sell_amt FROM lhb_detail WHERE date = ?',
        (date,)
    ).fetchall()
    
    # 按code合并详情
    dealer_by_code = defaultdict(list)
    for r in detail_rows:
        dealer_by_code[r['code']].append({
            'dealer': r['dealer'], 'direction': r['direction'],
            'buy': float(r['buy_amt'] or 0), 'sell': float(r['sell_amt'] or 0),
        })
    
    results = []
    for row in list_rows:
        code = row['code']
        name = row['name']
        
        # 过滤ST/创业板/科创板/北交所
        if code.startswith('300') or code.startswith('688') or code.startswith('920') or code.startswith('8'):
            continue
        if 'ST' in name.upper() or name.startswith('*') or '退' in name:
            continue
        
        dealers = dealer_by_code.get(code, [])
        if not dealers:
            continue
        
        # 合并买卖统计
        total_buy = sum(d['buy'] for d in dealers if d['direction'] == 'buy')
        total_sell = sum(d['sell'] for d in dealers if d['direction'] == 'sell')
        net = total_buy - total_sell
        
        # 判断机构参与
        has_jigou = any('机构专用' in d['dealer'] for d in dealers)
        
        # 买方营业部计数（非机构）
        buy_dealers = [d for d in dealers if d['direction'] == 'buy' and '机构专用' not in d['dealer']]
        buy_dealer_count = len(buy_dealers)
        
        # 找K线
        kline = conn_kline.execute(
            'SELECT close, open FROM kline WHERE code = ? AND date = ?', (code, date)
        ).fetchone()
        
        next_date = _get_next_trade_date(conn_kline, date)
        kline_t1 = None
        if next_date:
            kline_t1 = conn_kline.execute(
                'SELECT close, open FROM kline WHERE code = ? AND date = ?', (code, next_date)
            ).fetchone()
        
        t_open = None
        if kline:
            yesterday = conn_kline.execute(
                'SELECT close FROM kline WHERE code = ? AND date < ? ORDER BY date DESC LIMIT 1',
                (code, date)
            ).fetchone()
            if yesterday and yesterday['close'] and yesterday['close'] > 0:
                t_open = (kline['open'] - yesterday['close']) / yesterday['close'] * 100
        
        t1_open = t1_close = None
        if kline_t1 and kline:
            if kline_t1['open'] and kline['close'] and kline['close'] > 0:
                t1_open = (kline_t1['open'] - kline['close']) / kline['close'] * 100
            if kline_t1['close'] and kline['close'] and kline['close'] > 0:
                t1_close = (kline_t1['close'] - kline['close']) / kline['close'] * 100
        
        results.append({
            'code': code, 'name': name,
            'total_buy_wan': round(total_buy, 1), 'total_sell_wan': round(total_sell, 1),
            'net_wan': round(net, 1), 'has_jigou': has_jigou,
            'buy_dealer_count': buy_dealer_count,
            't_open': round(t_open, 2) if t_open else None,
            't1_open': round(t1_open, 2) if t1_open else None,
            't1_close': round(t1_close, 2) if t1_close else None,
        })
    
    return results

def _get_next_trade_date(conn, date):
    row = conn.execute('SELECT date FROM kline WHERE date > ? ORDER BY date LIMIT 1', (date,)).fetchone()
    return row['date'] if row else None

def main():
    print("=" * 70)
    print("📊 宏观环境分桶回测 v2 (适配真实数据格式)")
    print("=" * 70)
    
    conn = db_connect(LHB)
    conn_k = db_connect(KLINE)
    
    # 1. 获取所有交易日
    trade_dates = [r['date'] for r in conn_k.execute('SELECT DISTINCT date FROM kline ORDER BY date')]
    print(f"共 {len(trade_dates)} 个交易日")
    
    # 2. 计算每个交易日的环境分
    env_scores = {}
    for i, d in enumerate(trade_dates):
        kdata = get_kline_snapshot(conn_k, d)
        if not kdata or len(kdata) < 50:
            continue
        score, detail = compute_env_score(kdata)
        env_scores[d] = (score, detail)
        if i % 100 == 0:
            print(f"  环境分: {i}/{len(trade_dates)} {d} → {score}分")
    
    print(f"\n环境分计算完成: {len(env_scores)} 天")
    
    # 3. 回测每个交易日
    all_buckets = defaultdict(list)
    bkt_names = {0: '🔴冰点(<35)', 1: '🟡震荡(35-49)', 2: '🔵发酵(50-69)', 3: '🟢高潮(≥70)'}
    
    for i, d in enumerate(sorted(env_scores.keys())):
        score, detail = env_scores[d]
        signals = get_lhb_signals(conn, conn_k, d)
        
        if score >= 70:
            bidx = 3
        elif score >= 50:
            bidx = 2
        elif score >= 35:
            bidx = 1
        else:
            bidx = 0
        
        for sig in signals:
            all_buckets[bidx].append({**sig, 'env_score': score, 'env_date': d})
        
        if i % 100 == 0:
            print(f"  回测进度: {i}/{len(env_scores)} {d} → {bkt_names[bidx]} 信号={len(signals)}")
    
    # 4. 输出统计
    print("\n" + "=" * 70)
    print("📊 宏观环境分桶回测结果")
    print("=" * 70)
    
    all_with_t1 = []
    for bidx in [0, 1, 2, 3]:
        items = all_buckets.get(bidx, [])
        if not items:
            continue
        with_t1 = [s for s in items if s['t1_close'] is not None]
        all_with_t1.extend(with_t1)
        n = len(items)
        n_t1 = len(with_t1)
        
        if n_t1 == 0:
            print(f"\n{bkt_names[bidx]}: {n} 信号, 无T+1数据")
            continue
        
        avg_t1_close = sum(s['t1_close'] for s in with_t1) / n_t1
        win = sum(1 for s in with_t1 if s['t1_close'] > 0)
        wr = win / n_t1 * 100
        big_loss = sum(1 for s in with_t1 if s['t1_close'] < -5)
        big_win = sum(1 for s in with_t1 if s['t1_close'] > 5)
        avg_net = sum(s['net_wan'] for s in with_t1) / n_t1
        
        # 子策略：机构参与 vs 无机构
        jigou = [s for s in with_t1 if s['has_jigou']]
        no_jigou = [s for s in with_t1 if not s['has_jigou']]
        
        print(f"\n{'─' * 60}")
        print(f"📊 {bkt_names[bidx]}")
        print(f"{'─' * 60}")
        print(f"  信号: {n} | T+1可用: {n_t1}")
        print(f"  T+1均: {avg_t1_close:+.2f}% | 胜率: {wr:.1f}% ({win}/{n_t1})")
        print(f"  大赚>5%: {big_win}({big_win/n_t1*100:.1f}%) | 大亏<-5%: {big_loss}({big_loss/n_t1*100:.1f}%)")
        print(f"  均净额: {avg_net:+.0f}万")
        
        if jigou:
            j_avg = sum(s['t1_close'] for s in jigou) / len(jigou)
            j_wr = sum(1 for s in jigou if s['t1_close'] > 0) / len(jigou) * 100
            j_bl = sum(1 for s in jigou if s['t1_close'] < -5) / len(jigou) * 100
            print(f"  机构参与({len(jigou)}笔): +{j_avg:+.2f}% 胜{j_wr:.1f}% 大亏{j_bl:.1f}%")
        
        if no_jigou:
            nj_avg = sum(s['t1_close'] for s in no_jigou) / len(no_jigou)
            nj_wr = sum(1 for s in no_jigou if s['t1_close'] > 0) / len(no_jigou) * 100
            print(f"  无机构({len(no_jigou)}笔): +{nj_avg:+.2f}% 胜{nj_wr:.1f}%")
        
        # 净额>100万
        big_net = [s for s in with_t1 if s['net_wan'] > 100]
        if big_net:
            bn_avg = sum(s['t1_close'] for s in big_net) / len(big_net)
            bn_wr = sum(1 for s in big_net if s['t1_close'] > 0) / len(big_net) * 100
            print(f"  净卖>100万({len(big_net)}笔): {bn_avg:+.2f}% 胜{bn_wr:.1f}%")
    
    # 5. 总体对比
    n_total = len(all_with_t1)
    if n_total > 0:
        total_avg = sum(s['t1_close'] for s in all_with_t1) / n_total
        total_wr = sum(1 for s in all_with_t1 if s['t1_close'] > 0) / n_total * 100
        
        print(f"\n{'=' * 70}")
        print(f"📊 全量对比: 不分桶 vs 分桶")
        print(f"{'=' * 70}")
        print(f"不分桶全量: {n_total}笔 均{total_avg:+.2f}% 胜率{total_wr:.1f}%")
        
        print(f"\n  分桶对比:")
        print(f"  {'桶':25s} {'笔数':>6s} {'占比':>6s} {'T+1均':>8s} {'胜率':>6s} {'大亏':>6s} {'大赚':>6s}")
        print(f"  {'─'*65}")
        
        for bidx in [0, 1, 2, 3]:
            items = all_buckets.get(bidx, [])
            items_t1 = [s for s in items if s['t1_close'] is not None]
            if items_t1:
                n = len(items_t1)
                avg = sum(s['t1_close'] for s in items_t1) / n
                wr = sum(1 for s in items_t1 if s['t1_close'] > 0) / n * 100
                bl = sum(1 for s in items_t1 if s['t1_close'] < -5) / n * 100
                bw = sum(1 for s in items_t1 if s['t1_close'] > 5) / n * 100
                pct = n / n_total * 100
                print(f"  {bkt_names[bidx]:25s} {n:>6d} {pct:>5.1f}% {avg:>+7.2f}% {wr:>5.1f}% {bl:>5.1f}% {bw:>5.1f}%")
    
    # 6. 环境分布
    print(f"\n{'=' * 70}")
    print("📊 环境分分布")
    print(f"{'=' * 70}")
    score_counts = defaultdict(int)
    for s, _ in env_scores.values():
        bucket = (s // 10) * 10
        score_counts[bucket] += 1
    for b in sorted(score_counts.keys()):
        cnt = score_counts[b]
        bar = '█' * min(cnt, 20) + '░' * max(0, 20 - min(cnt, 20))
        print(f"  {b:3d}-{b+9:2d}分: {cnt:3d}天  {bar}")
    
    # 保存结果
    result = {'total': n_total, 'total_avg': round(total_avg, 2), 'total_win_rate': round(total_wr, 1),
              'env_distribution': dict(sorted(score_counts.items()))}
    for bidx in [0, 1, 2, 3]:
        items = all_buckets.get(bidx, [])
        items_t1 = [s for s in items if s['t1_close'] is not None]
        if items_t1:
            result[bkt_names[bidx]] = {
                'count': len(items), 't1_count': len(items_t1),
                'avg_t1': round(sum(s['t1_close'] for s in items_t1) / len(items_t1), 2),
                'win_rate': round(sum(1 for s in items_t1 if s['t1_close'] > 0) / len(items_t1) * 100, 1),
                'big_loss_pct': round(sum(1 for s in items_t1 if s['t1_close'] < -5) / len(items_t1) * 100, 1),
            }
    
    with open('/home/ubuntu/V2board/data/env_filter_backtest.json', 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已保存: /home/ubuntu/V2board/data/env_filter_backtest.json")
    
    conn.close()
    conn_k.close()

if __name__ == '__main__':
    main()
