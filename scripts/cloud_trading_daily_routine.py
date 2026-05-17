#!/usr/bin/env python3
"""
🤖 云盘交易每日例行 v1.0
=========================
每日定时任务：
  - 14:55 → 盘尾扫码+隔夜溢价信号+自动买入（最强策略）
  - 15:00 → 收盘快照+报告推送

用法：
  python3 scripts/cloud_trading_daily_routine.py --scan    # 盘尾扫描（仅输出信号）
  python3 scripts/cloud_trading_daily_routine.py --buy     # 扫描+自动买入
  python3 scripts/cloud_trading_daily_routine.py --report  # 收盘报告
  python3 scripts/cloud_trading_daily_routine.py --daily   # 完整每日例行
"""

import os, sys, json, subprocess, re, time
from datetime import datetime
from collections import defaultdict

BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
SCRIPTS = os.path.join(BASE, 'scripts')

sys.path.insert(0, SCRIPTS)
from cloud_trading import CloudTrader


# ============================================================
# 腾讯行情解析
# ============================================================

def mkt(code):
    return f"sh{code}" if code[0] in ('6', '5', '9') else f"sz{code}"


def fetch_quotes(codes):
    """批量获取腾讯实时行情"""
    quotes = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        codes_str = ','.join(mkt(c) for c in batch)
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://qt.gtimg.cn/q={codes_str}'],
                capture_output=True, text=True, timeout=12
            )
            for line in r.stdout.strip().split('\n'):
                line = line.strip()
                if not line or '=' not in line: continue
                raw = line.split('=', 1)[1].strip().strip('"').strip(';').strip('"')
                fields = raw.split('~')
                if len(fields) < 50: continue
                code = fields[2].strip()
                if not code: continue
                try:
                    cur = float(fields[3]) if fields[3] else 0
                    prev = float(fields[4]) if fields[4] else 0
                    open_p = float(fields[5]) if fields[5] else 0
                    high = float(fields[33]) if fields[33] else 0
                    chg = float(fields[32]) if fields[32] else 0
                    vol_r = float(fields[49]) if len(fields) > 49 and fields[49] else 0
                    name = fields[1].strip()
                    
                    # 上影线比例
                    body_high = max(cur, open_p)
                    upper_shadow = (high - body_high) / (prev or 1) * 100 if body_high > 0 else 0
                    
                    quotes[code] = {
                        'name': name, 'price': cur, 'prev_close': prev,
                        'open': open_p, 'high': high,
                        'change_pct': chg, 'vol_ratio': vol_r,
                        'upper_shadow_pct': round(upper_shadow, 2),
                        'is_limit_up': chg >= 9.5 and cur >= prev * 1.09,
                    }
                except:
                    continue
        except:
            continue
        time.sleep(0.3)
    return quotes


# ============================================================
# 隔夜溢价信号（缩量<0.7 + 极硬板）
# ============================================================

def scan_geye_premium(quotes, limit_stocks):
    """扫描隔夜溢价信号：缩量<0.7 + 上影<0.5%"""
    signals = []
    for code, q in quotes.items():
        if not q.get('is_limit_up'):
            continue
        if not (code.startswith('6') or code.startswith('0')):
            continue
        if code.startswith('688'):
            continue
        vr = q.get('vol_ratio', 0)
        us = q.get('upper_shadow_pct', 100)
        if vr > 0 and vr < 0.7 and us < 0.5:
            signals.append({
                'code': code, 'name': q['name'],
                'price': q['price'],
                'vol_ratio': vr,
                'upper_shadow': us,
                'type': '隔夜溢价缩量板',
                'expected': '+3.51%竞价卖',
                'confidence': '🔥高',
            })
        elif vr > 0 and vr < 1.0 and us < 0.5:
            signals.append({
                'code': code, 'name': q['name'],
                'price': q['price'],
                'vol_ratio': vr,
                'upper_shadow': us,
                'type': '隔夜溢价宽版',
                'expected': '+2.17%竞价卖',
                'confidence': '✅中',
            })
    return signals


# ============================================================
# 板块爆发信号（板块内涨停≥3只）
# ============================================================

SECTORS = {
    'chip': {'name': '存储芯片/AI芯片', 'codes': ['603986','603019','600584','603005','603160',
              '002049','600171','603893','002185','300655','300672','300661','688525','688110']},
    'gpu': {'name': 'AI算力/服务器', 'codes': ['601138','603019','000977','600498','000063',
            '002916','300308','688041']},
    'semicon': {'name': '半导体设备/材料', 'codes': ['688981','688012','688008','688126','688396',
               '002371','688072','688120','688037','300661','688019','688200']},
    'robot': {'name': '人形机器人', 'codes': ['002472','002896','300124','688160','300660',
             '688017','300580','601689','603662']},
    'ai_app': {'name': 'AI应用/AIGC', 'codes': ['300624','002230','300418','603533','002555',
              '300058','300315','300624','002517','688111']},
    'low_alt': {'name': '低空经济/飞行汽车', 'codes': ['002085','600580','300177','688070','688568',
               '002111','002023','603885','000099','600391']},
    'battery': {'name': '固态电池/新能源', 'codes': ['300750','002074','300014','002460','002709',
               '600884','300073','300568','002812','300769']},
}


def scan_sector_boom(quotes):
    """扫描板块爆发信号：涨停≥3只"""
    sector_results = []
    for sk, sv in SECTORS.items():
        limit_codes = []
        for c in sv['codes']:
            q = quotes.get(c)
            if q and q.get('is_limit_up'):
                limit_codes.append((c, q))
        if len(limit_codes) >= 3:
            sector_results.append({
                'key': sk,
                'name': sv['name'],
                'limit_stocks': [{'code': c, 'name': q['name'], 'price': q['price']}
                                for c, q in limit_codes],
                'limit_count': len(limit_codes),
            })
    return sector_results


# ============================================================
# 主函数
# ============================================================

def format_decision(signals, sectors, has_buy):
    """格式化决策报告"""
    now = datetime.now()
    lines = []
    lines.append(f"{'='*70}")
    lines.append(f"🔥 {now.strftime('%Y-%m-%d %H:%M')} 盘尾交易决策")
    lines.append(f"{'='*70}")
    
    # 板块爆发
    if sectors:
        lines.append(f"\n📊 板块爆发（涨停≥3只）:")
        for s in sectors:
            lines.append(f"  🔥 {s['name']}: {s['limit_count']}只涨停")
            tops = [f"{st['name']}(涨停)" for st in s['limit_stocks'][:3]]
            lines.append(f"    标的: {', '.join(tops)}")
    else:
        lines.append(f"\n📊 板块爆发: 无 (涨停<3只)")
    
    # 隔夜溢价
    high_conf = [s for s in signals if s['confidence'] == '🔥高']
    mid_conf = [s for s in signals if s['confidence'] == '✅中']
    
    if high_conf or mid_conf:
        lines.append(f"\n🌅 隔夜溢价信号:")
        for s in high_conf[:5]:
            lines.append(f"  🔥 {s['name']}({s['code']}) 量比{s['vol_ratio']:.2f} 上影{s['upper_shadow']:.2f}% → {s['expected']}")
        for s in mid_conf[:3]:
            lines.append(f"  ✅ {s['name']}({s['code']}) 量比{s['vol_ratio']:.2f} 上影{s['upper_shadow']:.2f}% → {s['expected']}")
    else:
        lines.append(f"\n🌅 隔夜溢价: 无符合条件的缩量板")
    
    # 执行建议
    lines.append(f"\n{'─'*70}")
    if has_buy:
        lines.append(f"🟢 自动买入已执行 → 查看报告:")
        lines.append(f"  python3 scripts/cloud_trading_daily_routine.py --report")
    else:
        lines.append(f"💡 仓位已满或无信号，本日未买入")
    lines.append(f"\n🔔 明日操作:")
    lines.append(f"  隔夜溢价持仓 → T+1竞价卖（09:25前）")
    lines.append(f"  板块爆发持仓 → T+3~5盘中最高卖（冲高≥5%止盈）")
    
    return '\n'.join(lines)


def do_scan():
    """盘尾扫描模式（仅输出信号，不买入）"""
    # 加载全量主板
    try:
        with open(os.path.join(DATA_DIR, 'all_main_board.txt')) as f:
            all_codes = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    except:
        print("❌ 无法读取股票列表")
        return
    
    print("📡 盘尾扫描（14:55）...")
    quotes = fetch_quotes(all_codes)
    if not quotes:
        print("❌ 无法获取行情")
        return
    
    print(f"✅ 获取 {len(quotes)} 只股票数据")
    
    # 筛选涨停股
    limit_stocks = {c: q for c, q in quotes.items() if q.get('is_limit_up')}
    print(f"📈 涨停股: {len(limit_stocks)}只")
    
    signals = scan_geye_premium(quotes, limit_stocks)
    sectors = scan_sector_boom(quotes)
    
    print("\n" + format_decision(signals, sectors, has_buy=False))
    return signals, sectors, limit_stocks


def do_buy():
    """扫描+自动买入云盘"""
    from strategy_matrix import StrategyScorer
    
    trader = CloudTrader(initial_capital=100000)
    scorer = StrategyScorer()
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    
    # 加载K线
    print("⏳ 加载K线...", end=' ', flush=True)
    kline_path = os.path.join(DATA_DIR, 'kline_cache.db')
    scorer._load_kline_full(kline_path)
    print("OK")
    
    # 扫描
    try:
        with open(os.path.join(DATA_DIR, 'all_main_board.txt')) as f:
            all_codes = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    except:
        print("❌ 无法读取股票列表")
        return
    
    print("📡 盘尾扫描...")
    quotes = fetch_quotes(all_codes)
    if not quotes:
        print("❌ 无法获取行情")
        return
    
    limit_stocks = {c: q for c, q in quotes.items() if q.get('is_limit_up')}
    print(f"📈 涨停股: {len(limit_stocks)}只")
    
    signals = scan_geye_premium(quotes, limit_stocks)
    sectors = scan_sector_boom(quotes)
    
    # 评分 + 买入
    positions = trader.get_positions()
    if len(positions) >= 3:
        print(f"⚠️ 仓位已满({len(positions)}只)，跳过买入")
        print(format_decision(signals, sectors, has_buy=False))
        return
    
    # 对高置信度隔夜溢价信号评分
    buy_targets = []
    for s in signals:
        if s['confidence'] == '🔥高':
            # 查板块排名
            try:
                conn = sqlite3.connect(os.path.join(DATA_DIR, 'sector_indexes.db'))
                sr = conn.execute(
                    "SELECT sector_rank, limit_up_count FROM sector_stock_daily ss "
                    "JOIN sector_daily_index si ON ss.date=si.date AND ss.sector_name=si.sector_name "
                    "WHERE ss.date=? AND ss.code=? LIMIT 1",
                    (today, s['code'])
                ).fetchone()
                conn.close()
                sector_rank = sr[0] if sr else 99
                sector_limit = sr[1] if sr else 0
            except:
                sector_rank, sector_limit = 99, 0
            
            result = scorer.score(
                code=s['code'], name=s['name'],
                board_count=1, limit_stat='normal',
                sector_rank=sector_rank, sector_limit=sector_limit, vr=s['vol_ratio'],
                holder_db_path=os.path.join(DATA_DIR, 'holder_cache.db'),
                date=today,
            )
            if result['score'] >= 80:
                buy_targets.append({
                    **s,
                    'score': result['score'],
                    'strategy': result['best'],
                    'predicted': result['predicted_high'],
                })
        
        # 板块爆发也加入
        for sec in sectors:
            for st in sec['limit_stocks']:
                # 避免重复
                if any(t['code'] == st['code'] for t in buy_targets):
                    continue
                try:
                    conn = sqlite3.connect(os.path.join(DATA_DIR, 'sector_indexes.db'))
                    sr = conn.execute(
                        "SELECT sector_rank FROM sector_stock_daily WHERE date=? AND code=? ORDER BY sector_rank LIMIT 1",
                        (today, st['code'])
                    ).fetchone()
                    conn.close()
                    board_rank = sr[0] if sr else 99
                except:
                    board_rank = 99
                
                result = scorer.score(
                    code=st['code'], name=st['name'],
                    board_count=1, limit_stat='normal',
                    sector_rank=board_rank, sector_limit=sec['limit_count'], vr=1.0,
                    holder_db_path=os.path.join(DATA_DIR, 'holder_cache.db'),
                    date=today,
                )
                if result['score'] >= 80:
                    buy_targets.append({
                        **st,
                        'vol_ratio': 0,
                        'upper_shadow': 0,
                        'type': '板块爆发跟风',
                        'expected': 'T+5最高+9.96%',
                        'confidence': '🔥高',
                        'score': result['score'],
                        'strategy': result['best'],
                        'predicted': result['predicted_high'],
                    })
    
    buy_targets.sort(key=lambda x: -x['score'])
    print(f"\n🟢 可买入标的(评分≥80): {len(buy_targets)}只")
    
    bought = 0
    for target in buy_targets:
        positions = trader.get_positions()
        if len(positions) >= 3:
            break
        
        # 用腾讯行情当前价格
        q = quotes.get(target['code'])
        if not q or q['price'] <= 0:
            continue
        cur_price = q['price']
        
        cash = trader._get_cash()
        buy_amount = min(30000, cash * 0.9)
        shares = int(buy_amount / cur_price / 100) * 100
        if shares < 100:
            continue
        
        result = trader.buy(
            code=target['code'], price=cur_price,
            shares=shares, name=target['name'],
            strategy=target['type'],
            score=target['score'],
            reason=f'{target["type"]}({target["score"]}分) 预期{target["expected"]}',
            trade_date=today,
        )
        if result['success']:
            bought += 1
            print(f"  ✅ 买入 #{bought} {target['name']}({target['code']}) {shares}股 @{cur_price:.2f} 现金剩余{result['cash_remain']:.2f}")
    
    print(f"\n{'='*70}")
    print(f"  📊 盘尾交易结果: 买入{bought}只")
    trader.take_snapshot(prices={c: q['price'] for c, q in quotes.items() if c in [t['code'] for t in buy_targets]})
    print(format_decision(signals, sectors, has_buy=bought > 0))


def do_report():
    """收盘报告"""
    trader = CloudTrader(initial_capital=100000)
    trader.report()
    
    # 策略统计
    conn = sqlite3.connect(os.path.join(DATA_DIR, 'trade_sim.db'))
    c = conn.cursor()
    c.execute("""
        SELECT strategy, COUNT(*) as cnt,
               ROUND(AVG(profit_rate),2) as avg_pnl,
               SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END)*1.0/COUNT(*) as win_rate
        FROM trades WHERE direction='sell' AND strategy != ''
        GROUP BY strategy ORDER BY cnt DESC
    """)
    rows = c.fetchall()
    print(f"\n{'='*70}")
    print(f"  📈 策略表现:")
    print(f"  {'策略':<28} {'次数':>5} {'均值':>8} {'胜率':>7}")
    for r in rows:
        print(f"  {r[0]:<28} {r[1]:>5} {r[2]:>+7.2f}% {r[3]*100 if r[3] else 0:>6.1f}%")
    
    # 风控检查
    print(f"\n{'='*70}")
    print(f"  🛡️ 盘中风控检查:")
    positions = trader.get_positions()
    if positions:
        # 拉所有持仓的实时行情
        codes = [p['code'] for p in positions]
        quotes = fetch_quotes(codes)
        risk_signals = trader.check_risk_and_sell(quotes)
        if risk_signals:
            for s in risk_signals:
                print(f"  {s['urgency']} {s['name']}({s['code']}) → {s['reason']}")
                # 有止损信号立即卖出
                if '止损' in s['reason']:
                    result = trader.sell(s['code'], s['price'], reason=s['reason'])
                    if result['success']:
                        print(f"    ✅ 已自动卖出")
        else:
            print(f"  ✅ 无风控信号，持仓安全")
    else:
        print(f"  ✅ 空仓，无需检查")
    
    conn.close()


if __name__ == '__main__':
    import sqlite3
    
    if '--buy' in sys.argv:
        do_buy()
    elif '--report' in sys.argv:
        do_report()
    elif '--daily' in sys.argv:
        do_buy()
        print(f"\n{'='*70}")
        do_report()
    else:
        do_scan()
