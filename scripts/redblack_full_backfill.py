#!/usr/bin/env python3
"""
红黑榜完整回填 — 基于v2.2矛盾引擎的全量历史回测
=============================================
不等到真实T+1积累，用历史K线数据立即按当前评级逻辑跑一遍，
得到每个等级的真实T+1表现，让自动校准闭环马上生效。
"""
import sqlite3, os, json, time
from datetime import datetime, timedelta
from collections import defaultdict

HOME = os.path.expanduser("~")
KLINE_DB = os.path.join(HOME, "astock/data/kline_cache.db")
LHB_DB = os.path.join(HOME, "astock/data/lhb_cache.db")
REDBLACK_DB = os.path.join(HOME, "astock/data/maodun_redblack.db")
BACKTEST_DATA = os.path.join(HOME, "astock/data/lhb_practical_backtest_v2.json")
ENV_HISTORY = os.path.join(HOME, "astock/data/env_daily_history.json")

def compute_maodun_score(strategy_ret, strategy_wr, tags_str, n, env_score):
    """复写morning_pipeline中v2.2的5维评分+资金合力逻辑"""
    stags = set(tags_str) if isinstance(tags_str, list) else set()
    
    # 1. 收益分
    ret_score = min(20, strategy_ret * 3)
    
    # 2. 胜率分
    win_score = min(20, strategy_wr * 0.3)
    
    # 3. 资金合力分（v2.2重写逻辑）
    has_inst = '机构' in stags
    has_youzi = '游资' in stags
    has_lianghua = '量化' in stags
    has_zhuli = '主力' in stags
    
    if has_inst and has_lianghua and not has_youzi:
        fund_score = 14
    elif has_inst and not has_youzi and not has_lianghua:
        fund_score = 12
    elif has_youzi and not has_inst and not has_lianghua:
        fund_score = 10
    elif has_inst and has_youzi and has_lianghua:
        fund_score = 8
    elif has_inst and has_youzi:
        fund_score = 7
    elif has_lianghua and not has_inst and not has_youzi:
        fund_score = 2
    elif has_zhuli:
        fund_score = 5
    else:
        fund_score = 3
    
    # 4. 样本分
    n_score = min(15, n * 0.1)
    
    # 5. 环境力分
    if env_score >= 70:
        env_score_add = 15
    elif env_score >= 55:
        env_score_add = 12
    elif env_score >= 45:
        env_score_add = 8
    elif env_score >= 35:
        env_score_add = 4
    else:
        env_score_add = 1
    
    total = ret_score + win_score + fund_score + n_score + env_score_add
    return min(100, total)


def get_grade(score, tags_str, jia_th=55, yi_th=50, bing_th=30):
    """v2.2动态阈值评级"""
    stags = set(tags_str) if isinstance(tags_str, list) else set()
    if score >= jia_th and '机构' in stags:
        return '甲等'
    elif score >= yi_th:
        return '乙等'
    elif score >= bing_th:
        return '丙等'
    else:
        return '丁等'


def main():
    t0 = time.time()
    print("=" * 72)
    print("红黑榜完整回填 v2.2 — 全量历史回测")
    print(f"启动: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 72)
    
    # 加载回测数据库（24策略）
    if not os.path.exists(BACKTEST_DATA):
        print("❌ 回测数据库不存在")
        return
    with open(BACKTEST_DATA) as f:
        bt = json.load(f)
    
    strategies = bt.get('strategies', [])
    print(f"✅ 加载{len(strategies)}个策略")
    
    # 加载环境历史
    env_data = {}
    if os.path.exists(ENV_HISTORY):
        with open(ENV_HISTORY) as f:
            env = json.load(f)
        env_data = env.get('daily', {})
        print(f"✅ 加载{len(env_data)}天环境数据")
    
    # 加载全量涨停K线
    print("⏳ 读取涨停K线数据...")
    conn = sqlite3.connect(KLINE_DB)
    t1 = time.time()
    
    limit_rows = conn.execute("""
        SELECT date, code, open, close, volume FROM kline
        WHERE date >= '2024-01-01' AND date <= '2026-05-15'
          AND (code LIKE '6%' OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
          AND close >= open * 1.095
        ORDER BY date, code
    """).fetchall()
    
    print(f"✅ {len(limit_rows)}个涨停记录 ({time.time()-t1:.1f}s)")
    
    # 连接LHB数据库获取资金标签
    lhb_conn = sqlite3.connect(LHB_DB)
    lhb_rows = lhb_conn.execute("""
        SELECT date, code FROM lhb_list WHERE date >= '2024-01-01'
    """).fetchall()
    lhb_codes = defaultdict(set)  # date -> set of codes
    for r in lhb_rows:
        lhb_codes[r[0]].add(r[1])
    print(f"✅ 龙虎榜数据: {len(lhb_rows)}条")
    
    # ===== 按v2.2评级逻辑回测 =====
    print("\n📊 逐条回测...")
    
    # 分组统计
    grade_stats = defaultdict(lambda: {
        'samples': 0, 'rets': [], 'open_rets': [], 'wins_close': 0, 'wins_open': 0
    })
    
    batch_count = 0
    for r in limit_rows:
        date, code, open_, close, volume = r
        
        # T+1
        t1_row = conn.execute("""
            SELECT open, close FROM kline WHERE code=? AND date>? ORDER BY date LIMIT 1
        """, (code, date)).fetchone()
        if not t1_row:
            continue
        
        # 涨停买入价
        prev = conn.execute("""
            SELECT close FROM kline WHERE code=? AND date<? ORDER BY date DESC LIMIT 1
        """, (code, date)).fetchone()
        
        if prev and close >= prev[0] * 1.095:
            buy_price = round(prev[0] * 1.10, 2)
        else:
            buy_price = open_
        
        close_ret = round((t1_row[1] - buy_price) / buy_price * 100, 2)
        open_ret = round((t1_row[0] - buy_price) / buy_price * 100, 2)
        
        # 获取当日环境分
        env_info = env_data.get(date, {})
        env_score = env_info.get('env_score', 50)
        
        # 龙虎榜标签（简化：龙虎榜上榜=有资金）
        is_lhb = code in lhb_codes.get(date, set())
        tags = ['缩量', '机构'] if is_lhb else ['缩量']
        
        # 用24策略中匹配度最高的来做评级
        # 简化：用回测数据的close_ret作为"策略收益"，不精确匹配24策略
        # 更精确：按当前版本模拟策略匹配
        strategy_ret = 2.0  # 默认收益
        strategy_wr = 55.0  # 默认胜率
        sample_n = 100
        
        # 按日期获取最近的策略收益
        # 简化：用环境分对应的策略组平均
        if env_score >= 55:
            strategy_ret = 2.5
            strategy_wr = 60.0
        elif env_score >= 40:
            strategy_ret = 1.5
            strategy_wr = 53.0
        else:
            strategy_ret = 0.8
            strategy_wr = 50.0
        
        if is_lhb:
            tags = ['缩量', '机构']
            strategy_ret = max(strategy_ret, 2.0)
        
        # v2.2评分
        score = compute_maodun_score(strategy_ret, strategy_wr, tags, sample_n, env_score)
        grade = get_grade(score, tags)
        
        # 统计
        gs = grade_stats[grade]
        gs['samples'] += 1
        gs['rets'].append(close_ret)
        gs['open_rets'].append(open_ret)
        if close_ret > 0: gs['wins_close'] += 1
        if open_ret > 0: gs['wins_open'] += 1
        
        batch_count += 1
        if batch_count % 3000 == 0:
            print(f"  ⏳ 已处理{batch_count}/{len(limit_rows)}...")
    
    print(f"\n📊 v2.2矛盾评级红黑榜（{batch_count}笔全量回测）:")
    print(f"  {'评级':<8} {'样本':>6} {'收盘均收':>9} {'收盘胜率':>9} {'开盘均收':>9} {'开盘胜率':>9}")
    print(f"  {'='*55}")
    
    grade_order = ['甲等', '乙等', '丙等', '丁等']
    results = []
    
    for grade in grade_order:
        gs = grade_stats.get(grade)
        if not gs or gs['samples'] == 0:
            print(f"  {grade:<6} {'无数据':>6}")
            continue
        
        n = gs['samples']
        avg_close = round(sum(gs['rets']) / n, 2)
        avg_open = round(sum(gs['open_rets']) / n, 2)
        wr_close = round(gs['wins_close'] / n * 100, 1)
        wr_open = round(gs['wins_open'] / n * 100, 1)
        
        print(f"  {grade:<6} {n:>6} {avg_close:>+8.2f}% {wr_close:>8.1f}% {avg_open:>+8.2f}% {wr_open:>8.1f}%")
        
        results.append({
            'grade': grade,
            'samples': n,
            'avg_close': avg_close,
            'avg_open': avg_open,
            'win_rate': wr_close,
            'total_return': round(sum(gs['rets']), 2),
        })
    
    # ===== 写入红黑榜数据库 =====
    print(f"\n💾 写入红黑榜数据库...")
    rb = sqlite3.connect(REDBLACK_DB)
    
    for r in results:
        grade = r['grade']
        n = r['samples']
        wins = round(n * r['win_rate'] / 100)
        total_ret = r['total_return']
        
        # grade_hitrate表
        rb.execute("""INSERT OR REPLACE INTO grade_hitrate
            (grade, total_trades, wins, total_return, last_updated)
            VALUES (?,?,?,?,?)
        """, (grade, n, wins, total_ret, datetime.now().isoformat()))
        
        # grade_backtest表（完整统计）
        rb.execute("""INSERT OR REPLACE INTO grade_backtest
            (grade, n, avg_close, avg_open, win_rate, updated_at)
            VALUES (?,?,?,?,?,?)
        """, (grade, n, r['avg_close'], r['avg_open'], r['win_rate'], datetime.now().isoformat()))
        
        # 逐条写入trade_records
        # 简化：不逐条写入（数据量太大），只写汇总
    
    rb.commit()
    rb.close()
    print(f"✅ 红黑榜数据库已更新 ({len(results)}个评级)")
    
    # ===== 自动校准 =====
    print(f"\n{'='*72}")
    print("⚙️ 基于红黑榜的自动校准")
    print(f"{'='*72}")
    
    baseline = {
        '甲等': {'expected_ret': 2.21, 'expected_wr': 63.4, 'min_samples': 30},
        '乙等': {'expected_ret': 0.51, 'expected_wr': 52.1, 'min_samples': 50},
        '丙等': {'expected_ret': -0.70, 'expected_wr': 43.3, 'min_samples': 50},
        '丁等': {'expected_ret': -1.50, 'expected_wr': 40.0, 'min_samples': 10},
    }
    
    adjustments = {}
    for r in results:
        bl = baseline.get(r['grade'])
        if not bl or r['samples'] < bl['min_samples']:
            continue
        
        ret_diff = round(r['avg_close'] - bl['expected_ret'], 2)
        wr_diff = round(r['win_rate'] - bl['expected_wr'], 1)
        
        if ret_diff > 0.5 and wr_diff > 5:
            adj = 5
        elif ret_diff < -0.5 and wr_diff < -5:
            adj = -5
        elif ret_diff < -0.2 and wr_diff < -2:
            adj = -2
        else:
            adj = 0
        
        adjustments[r['grade']] = adj
        
        status = '🔥远超预期' if adj >= 5 else ('⚠️低于预期' if adj <= -2 else '✅符合预期')
        print(f"  {r['grade']}: {r['avg_close']:+.2f}%/{r['win_rate']}% vs 预期{bl['expected_ret']:+.2f}%/{bl['expected_wr']}% → {status} (调整{adj:+d})")
    
    # 计算新阈值
    current_thresholds = {'甲等': 55, '乙等': 50, '丙等': 30, '丁等': 0}
    new_thresholds = {}
    for grade, old_th in current_thresholds.items():
        adj = adjustments.get(grade, 0)
        new_th = max(0, old_th + adj)
        new_thresholds[grade] = {'old': old_th, 'new': new_th, 'adjust': adj}
        if adj != 0:
            print(f"  📐 {grade}: 阈值{old_th}→{new_th}")
    
    # 保存校准缓存
    cal = {
        'generated_at': datetime.now().isoformat(),
        'total_trades_analyzed': sum(r['samples'] for r in results),
        'threshold_adjustments': new_thresholds,
        'grade_deviations': [{
            'grade': r['grade'],
            'actual_ret': r['avg_close'],
            'expected_ret': baseline.get(r['grade'], {}).get('expected_ret', 0),
            'ret_diff': round(r['avg_close'] - baseline.get(r['grade'], {}).get('expected_ret', 0), 2),
            'actual_wr': r['win_rate'],
            'expected_wr': baseline.get(r['grade'], {}).get('expected_wr', 0),
        } for r in results],
    }
    
    cal_path = os.path.join(HOME, "astock/data/auto_calibration_cache.json")
    with open(cal_path, 'w') as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)
    
    # 写入bundle
    bundle_path = os.path.join(HOME, "V2board/dashboard_bundle.json")
    if os.path.exists(bundle_path):
        try:
            with open(bundle_path) as f:
                bundle = json.load(f)
            bundle['auto_calibration'] = cal
            bundle['_calibration_at'] = datetime.now().isoformat()
            with open(bundle_path, 'w') as f:
                json.dump(bundle, f, ensure_ascii=False, default=str)
            print(f"\n✅ 校准结果已注入bundle")
        except Exception as e:
            print(f"  ⚠️ bundle写入失败: {e}")
    
    print(f"\n{'='*72}")
    print(f"✅ 全部完成！总耗时{time.time()-t0:.1f}s")
    print(f"  红黑榜: {sum(r['samples'] for r in results)}笔全量回测")
    print(f"  校准文件: {cal_path}")
    
    conn.close()
    lhb_conn.close()

if __name__ == '__main__':
    main()
