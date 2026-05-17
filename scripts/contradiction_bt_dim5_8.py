#!/usr/bin/env python3
"""
矛盾论历史回测 v2.1 — 维度5-8（30秒规则修正版）
==================================================
修正内容（2026-05-17）：
  1. 买入价：T-1日涨停价(yclose*1.10)，非T日open*1.10
  2. 卖出价：T+1开盘卖(open_ret = 30秒规则)，不再用收盘卖(close_ret保留仅供参考)
  3. 涨停检测：用T-1日close >= T-2日close*1.095检测T-1涨停
  4. 环境数据：从market_daily.db的day_full读取涨停数/涨跌比(2800+只全量)，非K线自算
  5. 量比：保持前5日均量计算，但排除新股<i>=5

核心原则（实践论）：没有调查就没有发言权
"""

import sqlite3, json, os, time
from datetime import datetime
from collections import defaultdict

HOME = os.path.expanduser("~")
KLINE_DB = os.path.join(HOME, "astock/data/kline_cache.db")
LHB_DB = os.path.join(HOME, "astock/data/lhb_cache.db")
MARKET_DB = os.path.join(HOME, "astock/data/market_daily.db")
BT_PATH = os.path.join(HOME, "astock/data/lhb_practical_backtest_v2.json")
OUTPUT = os.path.join(HOME, "astock/data/contradiction_bt_dim5_8.json")

START = "2024-01-01"
END = "2026-05-15"


def load_market_env(kc):
    """从market_daily.db读取每日环境数据（涨停数、涨跌比）"""
    print("  加载全市场环境数据...")
    mc = sqlite3.connect(MARKET_DB)
    rows = mc.execute(f"""
        SELECT date, limit_up, limit_down, up_count, down_count, zh_ratio,
               market_mood, zhaban_count
        FROM day_full
        WHERE date >= ? AND date <= ?
        ORDER BY date
    """, (START, END)).fetchall()
    mc.close()
    
    env = {}
    for row in rows:
        date, lu, ld, uc, dc, zhr, mood, zb = row
        z_t = (lu or 0) + (ld or 0)
        total = (uc or 0) + (dc or 0)
        up_ratio = uc / total if total > 0 else 0.5
        
        # 环境分类（用涨停数）
        lu_val = lu or 0
        if lu_val >= 70: env_cat = "高潮"
        elif lu_val >= 40: env_cat = "活跃"
        elif lu_val >= 17: env_cat = "震荡"
        else: env_cat = "冰点"
        
        env[date] = {
            'limit_up': lu_val, 'limit_down': ld or 0,
            'up_count': uc or 0, 'down_count': dc or 0,
            'up_ratio': round(up_ratio, 3),
            'zh_ratio': zhr or 0,
            'env_cat': env_cat,
            'market_mood': mood or '',
        }
    
    print(f"    {len(env)}个交易日环境数据加载完成")
    return env


def main():
    print("=" * 70)
    print("矛盾论历史回测 v2.1 — 维度5-8（30秒规则修正版）")
    print(f"数据范围: {START} ~ {END}")
    print(f"启动: {datetime.now().strftime('%H:%M:%S')}")
    print("核心修正: T-1涨停检测→T日竞价开盘买→T+1开盘30秒卖")
    print("=" * 70)
    
    # 连接数据库
    kc = sqlite3.connect(KLINE_DB)
    lc = sqlite3.connect(LHB_DB)
    
    results = {}
    
    # ================================================================
    # 0. 加载环境数据
    # ================================================================
    market_env = load_market_env(kc)
    
    # ================================================================
    # 1. 读取K线数据
    # ================================================================
    print("\n查询K线数据...")
    start_t = time.time()
    
    rows = kc.execute(f"""
        SELECT code, date, open, close, high, volume FROM kline
        WHERE date >= ? AND date <= ?
          AND (code LIKE '6%' OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%' OR code LIKE '003%')
        ORDER BY code, date
    """, (START, END)).fetchall()
    print(f"  读取{len(rows)}条K线 ({time.time()-start_t:.1f}s)")
    
    # 构建索引
    code_data = defaultdict(list)
    for code, date, open_, close, high, vol in rows:
        code_data[code].append((date, open_, close, high, vol))
    
    # ================================================================
    # 各维度统计容器
    # ================================================================
    dim5_vol = defaultdict(lambda: {'samples': 0, 'total_open_ret': 0.0, 'total_close_ret': 0.0, 'wins_open': 0, 'wins_close': 0})
    dim6_board = defaultdict(lambda: {'samples': 0, 'total_open_ret': 0.0, 'total_close_ret': 0.0, 'wins_open': 0, 'wins_close': 0})
    dim8_env_t1 = defaultdict(lambda: {'samples': 0, 'total_open_ret': 0.0, 'wins_open': 0})
    
    # ================================================================
    # 2. 核心修正逻辑：T-1涨停检测 → T开盘买 → T+1开盘卖
    # ================================================================
    print("\n计算（30秒规则修正）...")
    limit_count = 0
    checked_open = 0
    checked_close = 0
    
    for code, records in code_data.items():
        for i in range(1, len(records) - 1):  # 需要T-1、T、T+1三天数据
            # --- T-1日: 涨停检测 ---
            t1_date, t1_open, t1_close, t1_high, t1_vol = records[i-1]
            
            # T-2日收盘价（涨停基准价）
            if i < 2:
                continue
            t2_close = records[i-2][2]
            
            # 涨停检测：T-1日收盘 >= T-2日收盘 * 1.095
            if t2_close <= 0 or t1_close < t2_close * 1.095:
                continue
            
            # 涨停买入价 = T-1日涨停价（基于T-2收盘 * 1.10）
            buy_price = round(t2_close * 1.10, 2)
            
            # --- T日: 竞价开盘数据（开盘价就是真实买入点） ---
            t_date, t_open, t_close, t_high, t_vol = records[i]
            
            # --- T+1日: 开盘卖出（30秒规则） ---
            t2_date, t2_open, t2_close, t2_high, t2_vol = records[i+1]
            
            # 开盘卖 T+1开盘
            open_ret = round((t2_open - t_open) / t_open * 100, 2)
            # 收盘卖（仅供参考）
            close_ret = round((t2_close - t_open) / t_open * 100, 2)
            # 涨停买入 vs T+1开盘卖（最严格的30秒规则）
            strict_ret = round((t2_open - buy_price) / buy_price * 100, 2)
            
            limit_count += 1
            checked_open += 1
            
            # ============================================================
            # 维度5：量比计算（前5日均量，排除新股）
            # ============================================================
            if i - 1 >= 5:  # 用T-1日往前5天均量
                ma5_vol = sum(records[j][4] for j in range(i-6, i-1)) / 5.0
                vol_ratio = t1_vol / ma5_vol if ma5_vol > 0 else 1.0
            else:
                continue  # 不足5天历史数据，跳过
            
            if vol_ratio < 0.3:
                vcat = "极端缩量<0.3"
            elif vol_ratio < 0.5:
                vcat = "高度缩量0.3-0.5"
            elif vol_ratio < 0.7:
                vcat = "缩量0.5-0.7"
            elif vol_ratio < 1.0:
                vcat = "正常量0.7-1.0"
            elif vol_ratio < 1.5:
                vcat = "放量1.0-1.5"
            else:
                vcat = "暴量>1.5"
            
            dim5_vol[vcat]['samples'] += 1
            dim5_vol[vcat]['total_open_ret'] += open_ret
            dim5_vol[vcat]['total_close_ret'] += close_ret
            if open_ret > 0:
                dim5_vol[vcat]['wins_open'] += 1
            if close_ret > 0:
                dim5_vol[vcat]['wins_close'] += 1
            checked_close += 1
            
            # ============================================================
            # 维度6：连板数（T-1是否连板）
            # ============================================================
            board_count = 1  # T-1已涨停
            j = i - 2
            while j >= 1:
                prev_yclose = records[j-1][2]
                prev_close = records[j][2]
                if prev_yclose > 0 and prev_close >= prev_yclose * 1.095:
                    board_count += 1
                    j -= 1
                else:
                    break
            
            if board_count >= 4:
                bcat = "4板+"
            elif board_count == 3:
                bcat = "3板"
            elif board_count == 2:
                bcat = "2板"
            else:
                bcat = "首板"
            
            dim6_board[bcat]['samples'] += 1
            dim6_board[bcat]['total_open_ret'] += open_ret
            dim6_board[bcat]['total_close_ret'] += close_ret
            if open_ret > 0:
                dim6_board[bcat]['wins_open'] += 1
            if close_ret > 0:
                dim6_board[bcat]['wins_close'] += 1
            
            # ============================================================
            # 维度8：环境×涨停T+1
            # ============================================================
            env_info = market_env.get(t1_date)
            if env_info:
                ecat = env_info['env_cat']
                dim8_env_t1[ecat]['samples'] += 1
                dim8_env_t1[ecat]['total_open_ret'] += open_ret
                if open_ret > 0:
                    dim8_env_t1[ecat]['wins_open'] += 1
        
        if limit_count % 5000 == 0 and limit_count > 0:
            print(f"    已识别{limit_count}个涨停，验证{checked_open}个T+1")
    
    print(f"  总涨停检测: {limit_count}")
    print(f"  有T+1开盘数据(30秒规则): {checked_open}")
    print(f"  有T+1完整数据(收盘参考): {checked_close}")
    print(f"  耗时: {time.time() - start_t:.1f}s")
    
    # ================================================================
    # 3. 输出维度5：量比分析
    # ================================================================
    dim5_result = []
    for k in sorted(dim5_vol.keys()):
        v = dim5_vol[k]
        n = v['samples']
        if n < 10:
            continue
        dim5_result.append({
            "vol_range": k, "samples": n,
            "avg_open_ret_30s": round(v['total_open_ret'] / n, 2),
            "open_win_rate_30s": round(v['wins_open'] / n * 100, 1),
            "avg_close_ret_ref": round(v['total_close_ret'] / n, 2),
            "close_win_rate_ref": round(v['wins_close'] / n * 100, 1),
        })
    results["dim5"] = dim5_result
    
    print("\n📊 维度5：量比×T+1收益（30秒规则：T+1开盘卖）")
    print(f"  {'量比区间':<20} {'样本':>6} {'开盘收益[30s]':>13} {'开盘胜率':>9} {'收盘(参考)':>10} {'收盘胜率':>9}")
    print(f"  {'-' * 67}")
    for r in dim5_result:
        print(f"  {r['vol_range']:<20} {r['samples']:>6} {r['avg_open_ret_30s']:>+11.2f}% {r['open_win_rate_30s']:>8.1f}% {r['avg_close_ret_ref']:>+9.2f}% {r['close_win_rate_ref']:>8.1f}%")
    
    # ================================================================
    # 4. 输出维度6：连板分析
    # ================================================================
    board_order = ["首板", "2板", "3板", "4板+"]
    dim6_result = []
    for k in board_order:
        if k in dim6_board:
            v = dim6_board[k]
            n = v['samples']
            dim6_result.append({
                "board_level": k, "samples": n,
                "avg_open_ret_30s": round(v['total_open_ret'] / n, 2) if n else 0,
                "open_win_rate_30s": round(v['wins_open'] / n * 100, 1) if n else 0,
                "avg_close_ret_ref": round(v['total_close_ret'] / n, 2) if n else 0,
                "close_win_rate_ref": round(v['wins_close'] / n * 100, 1) if n else 0,
            })
    results["dim6"] = dim6_result
    
    print("\n📊 维度6：连板数×T+1收益（30秒规则）")
    print(f"  {'板数':<10} {'样本':>6} {'开盘收益[30s]':>13} {'开盘胜率':>9} {'收盘(参考)':>10} {'收盘胜率':>9}")
    print(f"  {'-' * 57}")
    for r in dim6_result:
        print(f"  {r['board_level']:<10} {r['samples']:>6} {r['avg_open_ret_30s']:>+11.2f}% {r['open_win_rate_30s']:>8.1f}% {r['avg_close_ret_ref']:>+9.2f}% {r['close_win_rate_ref']:>8.1f}%")
    
    # ================================================================
    # 5. 维度7：从v2.json读取各策略的open_ret（本来就是30秒规则数据）
    # ================================================================
    print("\n📊 维度7：策略收益 vs 开盘/收盘差异（来自v2.json严格时间线）")
    if os.path.exists(BT_PATH):
        with open(BT_PATH) as f:
            bt = json.load(f)
        strategies = bt.get('strategies', [])
        
        dim7_result = []
        for s in strategies:
            name = s.get('name', '?')
            n = s.get('n', 0)
            cr = s.get('close_ret', 0)
            op = s.get('open_ret', 0)
            cw = s.get('close_win', 0)
            ow = s.get('open_win', 0)
            diff = round(cr - op, 2)
            
            dim7_result.append({
                "name": name, "samples": n,
                "close_ret": cr, "close_win": cw,
                "open_ret": op, "open_win": ow,
                "close_vs_open_diff": diff,
                "30s_rule_suggestion": "开盘卖" if diff < 0 else "可持有",
            })
        results["dim7"] = dim7_result
        
        print(f"  {'策略名':<30} {'样本':>5} {'开盘收益[30s]':>13} {'开盘胜率':>9} {'收盘(参考)':>10} {'收盘胜率':>9} {'差':>7}")
        print(f"  {'-' * 85}")
        for r in dim7_result:
            print(f"  {r['name']:<30} {r['samples']:>5} {r['open_ret']:>+11.2f}% {r['open_win']:>8.1f}% {r['close_ret']:>+9.2f}% {r['close_win']:>8.1f}% {r['close_vs_open_diff']:>+6.2f}%")
    
    # ================================================================
    # 6. 输出维度8：环境×涨停T+1
    # ================================================================
    dim8_result = []
    for k in ["冰点", "震荡", "活跃", "高潮"]:
        if k in dim8_env_t1:
            v = dim8_env_t1[k]
            n = v['samples']
            dim8_result.append({
                "env": k, "samples": n,
                "avg_open_ret_30s": round(v['total_open_ret'] / n, 2) if n else 0,
                "open_win_rate_30s": round(v['wins_open'] / n * 100, 1) if n else 0,
            })
    results["dim8"] = dim8_result
    
    print("\n📊 维度8：环境×涨停T+1收益（30秒规则，环境来自market_daily 2800+只全量）")
    print(f"  {'环境':<10} {'样本':>8} {'开盘收益[30s]':>13} {'开盘胜率':>9}")
    print(f"  {'-' * 40}")
    for r in dim8_result:
        print(f"  {r['env']:<10} {r['samples']:>8} {r['avg_open_ret_30s']:>+11.2f}% {r['open_win_rate_30s']:>8.1f}%")
    
    # 维度7/8修正标签
    results["correction_notes"] = {
        "version": "v2.1 30秒规则修正",
        "buy_rule": "T-1涨停检测 → T日开盘价(open)买",
        "sell_rule": "T+1开盘卖(open_ret为主数据, close_ret仅为参考)",
        "env_source": "market_daily.db.day_full (2800+只全量, 非K线子集)",
        "stock_filter": "纯主板(6/000/001/002/003, 排除300创业板)",
        "kline_quality": f"{len(code_data)}只股票",
        "changes": [
            "1. 涨停检测用T-2→T-1日close, 非T日close→open",
            "2. 买入价=T-1涨停价(yclose*1.10), 非T日open*1.10",
            "3. 卖出主用T+1开盘价(30秒规则), 收盘仅参考",
            "4. 环境数据从market_daily.db读取(2800+只)",
            "5. 新股不足5天数据跳过, 减少噪声",
        ]
    }
    
    # ================================================================
    # 7. 保存结果
    # ================================================================
    output = {
        "meta": {
            "version": "v2.1 30秒规则修正",
            "start_date": START,
            "end_date": END,
            "generated_at": datetime.now().isoformat(),
        },
        "results": results,
    }
    
    with open(OUTPUT, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'=' * 70}")
    print(f"✅ 维度5-8回测（30秒规则修正版）完成！已保存到 {OUTPUT}")
    print(f"{'=' * 70}")
    
    kc.close()
    lc.close()


if __name__ == "__main__":
    main()
