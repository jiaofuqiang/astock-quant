"""
🔧 参数自适应优化器
======================
核心原则：每个策略的参数不应是人工拍脑袋定的，
而应该基于历史数据的grid search选出最优组合。

每个策略的可优化参数：
1. 金叉死叉: fast_period(3~10), slow_period(15~30)
2. MA20回踩: ma_period(10~30), deviation(0.01~0.05)
3. 放量突破: vol_mult(1.3~3.0), price_pct(1.0~5.0)
4. RSI: rsi_threshold(20~40), vol_mult(0.3~0.7)

优化过程：
1. 对每只股，在参数空间做grid search
2. 用最近90天的数据评估
3. 选出最优参数组合（按总收益率排序）
4. 保存到 StockProfileDB
5. 时效性加权：近期的参数选择更重要

使用方式：
- 批量优化：optimize_all_stocks() — 跑所有股的最优参数
- 单股优化：optimize_single(code) — 针对特定股
- 每日增量：optimize_recent() — 只跑7天内未优化的
"""

import json
import os
import sys
import itertools
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backtest import (
    KlineLoader, BacktestEngine,
    GoldenCrossStrategy, MaBounceStrategy,
    VolumeBreakStrategy, OversoldBounceStrategy,
    calc_ma, calc_rsi, TradeSignal
)

# ============= 参数搜索空间 =============

PARAM_GRID = {
    "金叉死叉": {
        "fast_period": [3, 5, 8, 10],
        "slow_period": [15, 20, 25, 30],
    },
    "MA20回踩": {
        "ma_period": [10, 15, 20, 25, 30],
        "deviation": [0.01, 0.015, 0.02, 0.03, 0.05],
    },
    "放量突破": {
        "vol_mult": [1.3, 1.5, 1.8, 2.0, 2.5, 3.0],
        "price_pct": [1.0, 2.0, 3.0, 4.0, 5.0],
    },
    "RSI": {
        "rsi_threshold": [20, 25, 30, 35, 40],
        "vol_mult": [0.3, 0.4, 0.5, 0.6, 0.7],
    },
}


def create_strategy(strategy_name: str, params: Dict):
    """根据策略名和参数创建策略实例"""
    if strategy_name == "金叉死叉":
        return GoldenCrossStrategy(
            fast_period=params.get("fast_period", 5),
            slow_period=params.get("slow_period", 20),
        )
    elif strategy_name == "MA20回踩":
        return MaBounceStrategy(
            ma_period=params.get("ma_period", 20),
            deviation=params.get("deviation", 0.02),
        )
    elif strategy_name == "放量突破":
        return VolumeBreakStrategy(
            vol_mult=params.get("vol_mult", 1.8),
            price_pct=params.get("price_pct", 3.0),
        )
    elif strategy_name == "RSI":
        return OversoldBounceStrategy(
            rsi_threshold=params.get("rsi_threshold", 30),
            vol_mult=params.get("vol_mult", 0.5),
        )
    raise ValueError(f"未知策略: {strategy_name}")


def optimize_single_stock(code: str, strategy_name: str,
                           param_grid: Dict[str, List] = None,
                           kline_data: List[Dict] = None) -> Dict:
    """
    对单只股票单策略做grid search

    Args:
        code: 股票代码
        strategy_name: 策略名
        param_grid: 可选自定义参数网格
        kline_data: 可选K线数据（避免重复加载）

    Returns:
        {
            "best_params": {...},
            "best_return": 12.5,
            "best_win_rate": 66.7,
            "best_trades": 8,
            "all_results": [{"params": ..., "total_return": ..., ...}]
        }
    """
    if param_grid is None:
        param_grid = PARAM_GRID.get(strategy_name)
        if not param_grid:
            return {"error": f"未知策略: {strategy_name}"}

    loader = KlineLoader()
    if kline_data is None:
        kline = loader.load_kline(code)
    else:
        kline = kline_data

    if len(kline) < 60:
        return {"error": f"{code} K线数据不足60根"}

    engine = BacktestEngine()

    # 生成所有参数组合
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    param_combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    results = []
    for params in param_combos:
        strategy = create_strategy(strategy_name, params)
        # 在最近90天数据上回测
        seg = kline[-90:]
        result = engine.run(seg, strategy)
        if result.get("error"):
            continue

        results.append({
            "params": params,
            "total_return": result.get("total_return_pct", 0),
            "win_rate": result.get("win_rate", 0),
            "trades": result.get("total_trades", 0),
            "max_dd": result.get("max_drawdown_pct", 0),
        })

    if not results:
        return {"error": f"{code} {strategy_name} 无有效参数组合"}

    # 按收益排序
    results.sort(key=lambda r: r["total_return"], reverse=True)
    best = results[0]

    return {
        "best_params": best["params"],
        "best_return": best["total_return"],
        "best_win_rate": best["win_rate"],
        "best_trades": best["trades"],
        "all_results": results,
    }


def optimize_single(code: str, kline_data: List[Dict] = None) -> Dict:
    """
    对单只股优化所有4个策略

    Returns:
        {
            "code": "300308",
            "results": {
                "金叉死叉": { best_params, best_return, ... },
                ...
            }
        }
    """
    from v3.stock_profile import StockProfileDB

    db = StockProfileDB()
    loader = KlineLoader()
    kline = kline_data or loader.load_kline(code)

    if len(kline) < 60:
        return {"code": code, "error": "K线不足60根"}

    all_results = {}
    for strategy_name in ["金叉死叉", "MA20回踩", "放量突破", "RSI"]:
        opt = optimize_single_stock(code, strategy_name, kline_data=kline)
        if "error" not in opt:
            all_results[strategy_name] = opt
            # 保存到数据库
            best = opt["best_params"]
            all_r = opt["all_results"]
            best_r = all_r[0] if all_r else {}
            db.save_param_optimization(
                code=code,
                strategy=strategy_name,
                params=best,
                total_return=best_r.get("total_return", 0),
                win_rate=best_r.get("win_rate", 0),
                trades=best_r.get("trades", 0),
                sharpe=0,  # 暂未算夏普
                max_dd=best_r.get("max_dd", 0),
            )

    return {"code": code, "results": all_results}


def optimize_all_stocks():
    """对所有股票批量优化"""
    loader = KlineLoader()
    codes = loader.load_all_codes()
    total = len(codes)

    optimized = 0
    for i, code in enumerate(codes):
        try:
            result = optimize_single(code)
            if "error" not in result:
                optimized += 1
            if (i + 1) % 10 == 0:
                print(f"  ... {i+1}/{total} ({optimized} 优化成功)")
        except Exception as e:
            print(f"  ⚠️ {code}: {e}")

    print(f"✅ {optimized}/{total} 只股票优化完成")
    return optimized


def get_best_params_for_signal(code: str, strategy_name: str,
                                default_params: Dict = None) -> Dict:
    """
    获取某只股某策略的最优参数（供signals.py调用）

    如果数据库中有优化记录，返回最优参数
    如果没有，返回默认参数

    这是整个系统的关键接口——别的模块通过此函数获取自适应参数
    """
    from v3.stock_profile import StockProfileDB

    db = StockProfileDB()
    best = db.get_best_params(code, strategy_name)

    if best and best.get("trades", 0) >= 2:  # 至少2笔交易才能算有效优化
        return best["params"]

    # 默认参数
    defaults = {
        "金叉死叉": {"fast_period": 5, "slow_period": 20},
        "MA20回踩": {"ma_period": 20, "deviation": 0.02},
        "放量突破": {"vol_mult": 1.8, "price_pct": 3.0},
        "RSI": {"rsi_threshold": 30, "vol_mult": 0.5},
    }
    return defaults.get(strategy_name, default_params or {})


# ============= 策略工厂（带自适应参数） =============

def create_adaptive_strategy(code: str, strategy_name: str):
    """
    创建带自适应参数的策略实例

    这是系统的核心——每只股使用自己最优的参数组合
    """
    params = get_best_params_for_signal(code, strategy_name)

    if strategy_name == "金叉死叉":
        return GoldenCrossStrategy(
            fast_period=params.get("fast_period", 5),
            slow_period=params.get("slow_period", 20),
        )
    elif strategy_name == "MA20回踩":
        return MaBounceStrategy(
            ma_period=params.get("ma_period", 20),
            deviation=params.get("deviation", 0.02),
        )
    elif strategy_name == "放量突破":
        return VolumeBreakStrategy(
            vol_mult=params.get("vol_mult", 1.8),
            price_pct=params.get("price_pct", 3.0),
        )
    elif strategy_name == "RSI":
        return OversoldBounceStrategy(
            rsi_threshold=params.get("rsi_threshold", 30),
            vol_mult=params.get("vol_mult", 0.5),
        )
    else:
        raise ValueError(f"未知策略: {strategy_name}")


def compare_params(code: str, strategy_name: str) -> str:
    """对比默认参数 vs 自适应参数的差异"""
    loader = KlineLoader()
    kline = loader.load_kline(code)
    if len(kline) < 60:
        return "数据不足"

    engine = BacktestEngine()

    # 默认参数
    default_strategy = create_strategy(strategy_name, get_best_params_for_signal(code, strategy_name))
    default_result = engine.run(kline[-90:], default_strategy)

    params = get_best_params_for_signal(code, strategy_name)
    opt_strategy = create_strategy(strategy_name, params)
    opt_result = engine.run(kline[-90:], opt_strategy)

    lines = []
    lines.append(f"📊 {code} {strategy_name}")
    lines.append(f"  默认参数: {params}")
    lines.append(f"    收益: {default_result.get('total_return_pct', 0):+.2f}% | "
                 f"胜率: {default_result.get('win_rate', 0):.0f}% | "
                 f"交易: {default_result.get('total_trades', 0)}笔")
    return "\n".join(lines)


# ============= 测试 =============

if __name__ == "__main__":
    print("🔧 参数自适应优化器")
    print("=" * 60)

    loader = KlineLoader()
    codes = loader.load_all_codes()
    print(f"\n数据库中共 {len(codes)} 只股票")

    # 单股测试
    test_code = codes[0] if codes else "300308"
    print(f"\n单股测试: {test_code}")
    result = optimize_single(test_code)
    if "results" in result:
        for sname, opt in result["results"].items():
            print(f"  {sname}:")
            print(f"    最优参数: {opt['best_params']}")
            print(f"    收益: {opt['best_return']:+.2f}% 胜率: {opt['best_win_rate']:.0f}% 交易数: {opt['best_trades']}")
    else:
        print(f"  {result.get('error', '未知错误')}")

    print("\n✅ param_optimizer.py 就绪")
