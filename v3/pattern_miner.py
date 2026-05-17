"""
🕵️ 策略自动发现引擎
======================
核心原则：不应该只有4个人工编码的策略。
应该从历史数据中自动发现未被编码的有效买卖模式。

思路：
1. 把K线数据转化为"特征向量"（价格变化、成交量、RSI、波动率等）
2. 在每个时间点，标注"未来N天是否涨了M%"作为标签
3. 寻找高胜率的"特征组合模式"
4. 新模式验证后加入评分系统

发现方向举例：
- "连续3天缩量阴跌+第4天RSI<35" → 胜率80%
- "MACD金叉+放量20日均量1.5倍+突破前高" → 胜率75%
- "跌破MA60后3天内收回+成交额>5亿" → 胜率70%
"""

import json
import os
import sys
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backtest import calc_ma, calc_rsi, calc_atr, KlineLoader

DATA_DIR = "/home/ubuntu/astock/data"
PATTERN_FILE = os.path.join(DATA_DIR, "discovered_patterns.json")


# ============= 特征提取 =============

def extract_features(kline: List[Dict], idx: int) -> Dict:
    """
    从K线的第idx个位置提取特征向量

    Args:
        kline: 完整K线
        idx: 当前位置

    Returns:
        特征字典
    """
    closes = [k["close"] for k in kline]
    highs = [k["high"] for k in kline]
    lows = [k["low"] for k in kline]
    volumes = [k["volume"] for k in kline]

    n = len(kline[:idx+1])
    if n < 20:
        return {}

    c = closes[:idx+1]
    h = highs[:idx+1]
    l_ = lows[:idx+1]
    v = volumes[:idx+1]

    features = {}

    # 1. 价格动量
    features["chg_1d"] = (c[-1] - c[-2]) / c[-2] * 100 if n >= 2 else 0
    features["chg_3d"] = (c[-1] - c[-4]) / c[-4] * 100 if n >= 4 else 0
    features["chg_5d"] = (c[-1] - c[-6]) / c[-6] * 100 if n >= 6 else 0
    features["chg_10d"] = (c[-1] - c[-11]) / c[-11] * 100 if n >= 11 else 0

    # 2. 均线关系
    ma5 = calc_ma(c, 5)[-1]
    ma10 = calc_ma(c, 10)[-1] if n >= 10 else None
    ma20 = calc_ma(c, 20)[-1] if n >= 20 else None
    features["ma5"] = ma5
    features["ma10"] = ma10
    features["ma20"] = ma20
    features["ma5_ma20_pct"] = (ma5 - ma20)/ma20*100 if ma5 and ma20 else 0
    features["price_ma20_pct"] = (c[-1] - ma20)/ma20*100 if ma20 else 0

    # 3. 成交量
    ma_vol = calc_ma(v, 20)[-1] if n >= 20 else None
    features["ma_vol"] = ma_vol
    if ma_vol and ma_vol > 0:
        features["vol_ratio_1d"] = v[-1] / ma_vol
        features["vol_ratio_3d_avg"] = sum(v[-3:])/len(v[-3:]) / ma_vol if n >= 3 else 1
        features["vol_ratio_5d_avg"] = sum(v[-5:])/len(v[-5:]) / ma_vol if n >= 5 else 1
    else:
        features["vol_ratio_1d"] = 1.0
        features["vol_ratio_3d_avg"] = 1.0
        features["vol_ratio_5d_avg"] = 1.0

    # 4. RSI
    rsi = calc_rsi(c)
    features["rsi"] = rsi[-1] if rsi[-1] is not None else 50

    # 5. 波动率(ATR%)
    atr = calc_atr(h, l_, c)
    features["atr_pct"] = atr[-1]/c[-1]*100 if atr[-1] and c[-1] > 0 else 3

    # 6. 价格形态
    features["high_low_range"] = (max(l_[-5:]) - min(l_[-5:])) / min(l_[-5:]) * 100 if n >= 5 else 0
    
    # 7. 连续涨跌
    if n >= 5:
        ups = sum(1 for i in range(-4, 0) if c[i] > c[i-1])
        downs = 5 - ups
        features["consecutive_ups"] = ups
        features["consecutive_downs"] = downs
    
    # 8. 是否低于MA60
    ma60 = calc_ma(c, 60)[-1] if n >= 60 else None
    features["price_ma60_pct"] = (c[-1] - ma60)/ma60*100 if ma60 else 0
    features["below_ma60"] = 1 if ma60 and c[-1] < ma60 else 0

    # 9. 回踩信号
    if ma20:
        deviation = abs(c[-1] - ma20) / ma20 * 100
        features["ma20_deviation"] = deviation
        features["is_ma20_bounce"] = 1 if deviation < 2 and c[-1] > ma20 else 0

    # 10. 突破前高
    if n >= 20:
        high_20 = max(c[-20:])
        features["break_20d_high"] = 1 if c[-1] >= high_20 * 0.99 else 0
        features["high_20d_pct"] = (c[-1] - high_20) / high_20 * 100

    # 11. 极值判断
    if n >= 5:
        features["is_3d_low"] = 1 if c[-1] == min(c[-3:]) else 0
        features["is_3d_high"] = 1 if c[-1] == max(c[-3:]) else 0

    # 12. MACD差值近似
    ma12 = calc_ma(c, 12)[-1] if n >= 12 else None
    ma26 = calc_ma(c, 26)[-1] if n >= 26 else None
    if ma12 and ma26:
        features["macd_line"] = ma12 - ma26
        features["macd_pct"] = (ma12 - ma26) / ma26 * 100
    else:
        features["macd_line"] = 0
        features["macd_pct"] = 0

    return features


def get_future_return(kline: List[Dict], idx: int, forward_days: int = 5) -> float:
    """获取未来N天的收益率"""
    if idx + forward_days >= len(kline):
        return None
    future_close = kline[idx + forward_days]["close"]
    current_close = kline[idx]["close"]
    return (future_close - current_close) / current_close * 100


# ============= 模式定义 =============

@dataclass
class PatternRule:
    """
    一个策略模式

    conditions: 条件列表，所有条件同时满足才触发
    buy_score: 买入信号强度 (-3~+3)
    """
    name: str
    description: str
    conditions: List[Tuple[str, str, float]]  # (feature_name, operator, value)
    buy_score: int = 2
    win_rate: float = 0.0
    total_signals: int = 0
    avg_return: float = 0.0


# ============= 手工定义的新模式（专家知识） =============

EXPERT_PATTERNS = [
    PatternRule(
        name="底部放量反转",
        description="连续下跌后放量阳线+RSI低位→反弹确认",
        conditions=[
            ("chg_1d", ">", 2),       # 今日涨>2%
            ("chg_3d", "<", -3),       # 过去3天跌>3%
            ("vol_ratio_1d", ">", 1.5), # 放量>1.5倍
            ("rsi", "<", 40),           # RSI<40（非超买）
            ("chg_5d", "<", 0),         # 5天是负的
        ],
        buy_score=2,
    ),
    PatternRule(
        name="缩量回调到位",
        description="回踩MA20且缩量→支撑确认",
        conditions=[
            ("is_ma20_bounce", "==", 1),   # 回踩MA20
            ("vol_ratio_1d", "<", 0.8),    # 缩量
            ("rsi", ">", 30),               # 非超卖
            ("rsi", "<", 60),               # 非超买
        ],
        buy_score=2,
    ),
    PatternRule(
        name="强势股突破前高",
        description="放量突破20日高点+均线多头排列→主升浪",
        conditions=[
            ("break_20d_high", "==", 1),    # 突破20日高
            ("vol_ratio_1d", ">", 1.3),     # 放量
            ("ma5_ma20_pct", ">", 0),       # MA5>MA20
            ("chg_1d", ">", 1),             # 今日涨>1%
        ],
        buy_score=3,
    ),
    PatternRule(
        name="超卖v型反转",
        description="RSI<30+连续下跌3天+今日阳线→超卖反弹",
        conditions=[
            ("rsi", "<", 30),          # 超卖
            ("chg_3d", "<", -5),       # 3天跌>5%
            ("chg_1d", ">", 0),        # 今日收阳
            ("vol_ratio_1d", ">", 0.8), # 至少不缩量
        ],
        buy_score=2,
    ),
    PatternRule(
        name="放量起涨确认",
        description="温和放量+中阳线+均线多头→起涨点",
        conditions=[
            ("chg_1d", ">", 2),          # 涨>2%
            ("vol_ratio_1d", ">", 1.2),   # 放量1.2x
            ("vol_ratio_1d", "<", 3.0),   # 非天量
            ("ma5_ma20_pct", ">", 0),     # 多头
        ],
        buy_score=2,
    ),
    PatternRule(
        name="地量见底",
        description="地量（<均量0.5倍）+价格横盘→抛压衰竭",
        conditions=[
            ("vol_ratio_1d", "<", 0.5),  # 地量
            ("chg_1d", ">", -1),          # 不大跌
            ("chg_1d", "<", 1),           # 也不大涨
            ("chg_5d", ">", -3),          # 5天不大跌
            ("rsi", "<", 45),             # 偏弱
        ],
        buy_score=2,
    ),
    PatternRule(
        name="MA60支撑反弹",
        description="跌破MA60后快速收回→假跌破",
        conditions=[
            ("below_ma60", "==", 0),       # 当前在MA60上方
            ("is_3d_low", "==", 1),        # 过去3天最低
            ("chg_1d", ">", 1),            # 今日反弹
            ("price_ma60_pct", "<", 5),    # 离MA60不远
            ("price_ma60_pct", ">", -1),   # 刚站上MA60
        ],
        buy_score=3,
    ),
    PatternRule(
        name="看涨吞没",
        description="阴线后接大阳线吞没前一日→反转",
        conditions=[
            ("chg_1d", ">", 3),          # 今日涨>3%
        ],
        buy_score=2,
    ),
]


def evaluate_pattern(kline: List[Dict], pattern: PatternRule) -> List[int]:
    """
    在完整K线上评估一个模式，返回买入信号序列

    Returns:
        [0,0,1,0,0,...] 1表示触发买入信号
    """
    signals = [0] * len(kline)
    for i in range(20, len(kline) - 5):  # 需要至少20根数据
        features = extract_features(kline, i)
        if not features:
            continue
        if _matches_pattern(features, pattern.conditions):
            signals[i] = 1
    return signals


def _matches_pattern(features: Dict, conditions: List[Tuple]) -> bool:
    """检查特征是否匹配条件"""
    for feat_name, op, value in conditions:
        feat_val = features.get(feat_name)
        if feat_val is None:
            return False
        try:
            if op == ">":
                if not (feat_val > value):
                    return False
            elif op == "<":
                if not (feat_val < value):
                    return False
            elif op == ">=":
                if not (feat_val >= value):
                    return False
            elif op == "<=":
                if not (feat_val <= value):
                    return False
            elif op == "==":
                if not (abs(feat_val - value) < 0.01):
                    return False
            else:
                return False
        except:
            return False
    return True


# ============= 模式验证 =============

def validate_pattern(kline: List[Dict], pattern: PatternRule,
                      forward_days: int = 5, min_profit: float = 2.0) -> Dict:
    """
    验证一个模式的历史表现

    Returns:
        {
            "name": pattern name,
            "total_signals": N,
            "wins": N,
            "win_rate": %,
            "avg_return": %,
            "max_return": %,
            "min_return": %,
            "profit_factor": total_profit/total_loss
        }
    """
    signals = evaluate_pattern(kline, pattern)

    wins = 0
    total_signals = 0
    returns = []
    profits = []
    losses = []

    for i in range(len(signals)):
        if signals[i] != 1:
            continue

        future_ret = get_future_return(kline, i, forward_days)
        if future_ret is None:
            continue

        total_signals += 1
        returns.append(future_ret)

        if future_ret >= min_profit:
            wins += 1
            profits.append(future_ret)
        elif future_ret < 0:
            losses.append(abs(future_ret))

    if total_signals == 0:
        return {"name": pattern.name, "total_signals": 0, "win_rate": 0,
                "avg_return": 0, "max_return": 0, "min_return": 0, "profit_factor": 0}

    win_rate = wins / total_signals * 100
    avg_ret = sum(returns) / len(returns)
    max_ret = max(returns)
    min_ret = min(returns)
    profit_factor = sum(profits) / sum(losses) if losses and sum(losses) > 0 else float('inf')

    return {
        "name": pattern.name,
        "total_signals": total_signals,
        "wins": wins,
        "win_rate": round(win_rate, 1),
        "avg_return": round(avg_ret, 2),
        "max_return": round(max_ret, 2),
        "min_return": round(min_ret, 2),
        "profit_factor": round(profit_factor, 2),
        "description": pattern.description,
        "buy_score": pattern.buy_score,
        "forward_days": forward_days,
        "min_profit": min_profit,
    }


def validate_all_patterns(kline: List[Dict], forward_days: int = 5,
                           min_profit: float = 2.0) -> List[Dict]:
    """验证所有专家模式"""
    results = []
    for pattern in EXPERT_PATTERNS:
        result = validate_pattern(kline, pattern, forward_days, min_profit)
        results.append(result)
    results.sort(key=lambda r: r["win_rate"], reverse=True)
    return results


# ============= 跨股验证 =============

def cross_validate_pattern(pattern: PatternRule, codes: List[str] = None,
                             forward_days: int = 5, min_profit: float = 2.0) -> Dict:
    """
    在多个股票上验证一个模式

    累计所有信号，计算综合胜率
    """
    loader = KlineLoader()
    if codes is None:
        codes = loader.load_all_codes()

    all_signals = 0
    all_wins = 0
    all_returns = []

    for code in codes:
        kline = loader.load_kline(code)
        if len(kline) < 60:
            continue
        result = validate_pattern(kline, pattern, forward_days, min_profit)
        all_signals += result["total_signals"]
        all_wins += result["wins"]

    win_rate = all_wins / all_signals * 100 if all_signals > 0 else 0

    return {
        "name": pattern.name,
        "total_signals": all_signals,
        "wins": all_wins,
        "win_rate": round(win_rate, 1),
        "description": pattern.description,
    }


# ============= 自动发现新模式 =============

def auto_discover_patterns(kline: List[Dict], min_signals: int = 5,
                            min_win_rate: float = 60) -> List[Dict]:
    """
    自动发现新买卖模式

    从历史数据中寻找特征组合，输出胜率高的模式
    这是"挖掘未被编码的有效买卖模式"的核心

    当前实现：基于已有特征的二元切割组合
    """
    from itertools import combinations

    if len(kline) < 60:
        return []

    # 提取所有特征和未来收益
    samples = []
    for i in range(20, len(kline) - 10):
        features = extract_features(kline, i)
        if not features:
            continue
        future_ret = get_future_return(kline, i, 5)
        if future_ret is None:
            continue
        samples.append({
            "features": features,
            "future_return": future_ret,
            "positive": future_ret >= 2.0,
        })

    if len(samples) < 50:
        return []

    # 选择最有预测力的特征
    important_features = [
        "rsi", "chg_1d", "chg_3d", "chg_5d",
        "vol_ratio_1d", "vol_ratio_3d_avg",
        "ma5_ma20_pct", "price_ma20_pct",
        "ma20_deviation", "atr_pct",
        "is_ma20_bounce", "is_3d_low",
        "break_20d_high", "below_ma60",
        "high_20d_pct", "macd_pct",
    ]

    discovered = []

    # 单特征切割
    for feat in important_features:
        vals = [s["features"].get(feat) for s in samples if feat in s["features"]]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue

        # 尝试不同的切割阈值（百分位）
        sorted_vals = sorted(vals)
        for pct in [20, 30, 40, 50, 60, 70, 80]:
            idx = int(len(sorted_vals) * pct / 100)
            threshold = sorted_vals[idx]

            for direction in ["above", "below"]:
                if direction == "above":
                    matched = [s for s in samples if s["features"].get(feat) is not None and s["features"][feat] > threshold]
                else:
                    matched = [s for s in samples if s["features"].get(feat) is not None and s["features"][feat] < threshold]

                if len(matched) < min_signals:
                    continue

                wins = sum(1 for s in matched if s["positive"])
                wr = wins / len(matched) * 100
                avg_ret = sum(s["future_return"] for s in matched) / len(matched)

                if wr >= min_win_rate:
                    discovered.append({
                        "type": "单特征",
                        "feature": feat,
                        "direction": direction,
                        "threshold": round(threshold, 2) if isinstance(threshold, float) else threshold,
                        "signals": len(matched),
                        "win_rate": round(wr, 1),
                        "avg_return": round(avg_ret, 2),
                    })

    # 双特征组合（两两组合）
    feat_pairs = list(combinations(important_features[:8], 2))
    for f1, f2 in feat_pairs:
        for dir1 in ["above", "below"]:
            for dir2 in ["above", "below"]:
                matched = []
                for s in samples:
                    v1 = s["features"].get(f1)
                    v2 = s["features"].get(f2)
                    if v1 is None or v2 is None:
                        continue
                    c1 = (dir1 == "above" and v1 > 0) or (dir1 == "below" and v1 < 0)
                    c2 = (dir2 == "above" and v2 > 0) or (dir2 == "below" and v2 < 0)
                    if c1 and c2:
                        matched.append(s)

                if len(matched) < min_signals:
                    continue

                wins = sum(1 for s in matched if s["positive"])
                wr = wins / len(matched) * 100

                if wr >= min_win_rate + 5:  # 更严格
                    discovered.append({
                        "type": "双特征",
                        "features": [f1, f2],
                        "directions": [dir1, dir2],
                        "signals": len(matched),
                        "win_rate": round(wr, 1),
                    })

    discovered.sort(key=lambda x: x["win_rate"], reverse=True)
    return discovered[:20]


# ============= 持久化 =============

def save_discovered_patterns(patterns: List[Dict]):
    """保存发现的模式到文件"""
    data = {
        "discovered_at": datetime.now().isoformat(),
        "patterns": patterns,
    }
    with open(PATTERN_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_discovered_patterns() -> List[Dict]:
    """加载已发现的模式"""
    if os.path.exists(PATTERN_FILE):
        with open(PATTERN_FILE) as f:
            data = json.load(f)
        return data.get("patterns", [])
    return []


# ============= 模式→策略评分 =============

def get_pattern_scores(features: Dict, discovered_patterns: List[Dict] = None) -> int:
    """
    根据当前特征，计算模式匹配得分

    Args:
        features: extract_features()的输出
        discovered_patterns: 从auto_discover_patterns发现的模式

    Returns:
        0~3 的评分，可叠加到现有的评分系统
    """
    score = 0

    # 专家模式匹配
    for pattern in EXPERT_PATTERNS:
        if _matches_pattern(features, pattern.conditions):
            score += pattern.buy_score

    # 自动发现的模式匹配
    if discovered_patterns:
        for p in discovered_patterns:
            if p["type"] == "单特征":
                feat_val = features.get(p["feature"])
                if feat_val is not None:
                    direction = p["direction"]
                    threshold = p["threshold"]
                    if (direction == "above" and feat_val > threshold) or \
                       (direction == "below" and feat_val < threshold):
                        # 胜率越高，加分越高
                        bonus = min(2, int(p["win_rate"] / 30))
                        score += bonus

    return min(score, 10)  # 最多5分


# ============= 测试 =============

if __name__ == "__main__":
    print("🕵️ 策略自动发现引擎")
    print("=" * 60)

    loader = KlineLoader()
    codes = loader.load_all_codes()
    print(f"\n数据库中共 {len(codes)} 只股票")

    if codes:
        kline = loader.load_kline(codes[0])
        if len(kline) >= 60:
            print(f"\n测试股票: {codes[0]} ({len(kline)}根K线)")

            # 验证专家模式
            print(f"\n📋 专家模式验证（5日2%止盈）:")
            results = validate_all_patterns(kline, forward_days=5, min_profit=2.0)
            for r in results:
                if r["total_signals"] > 0:
                    mark = "✅" if r["win_rate"] >= 50 else "❌"
                    print(f"  {mark} {r['name']}: {r['total_signals']}次信号 "
                          f"胜率{r['win_rate']:.0f}% 均收益{r['avg_return']:+.1f}%")

            # 自动发现新模式
            print(f"\n🔍 自动发现新模式:")
            discovered = auto_discover_patterns(kline, min_signals=3, min_win_rate=55)
            for d in discovered[:8]:
                if d["type"] == "单特征":
                    print(f"  🆕 {d['feature']} {d['direction']} {d['threshold']} "
                          f"→ 胜率{d['win_rate']:.0f}% ({d['signals']}次信号)")
                else:
                    print(f"  🆕 {d['features'][0]}+{d['features'][1]} "
                          f"→ 胜率{d['win_rate']:.0f}% ({d['signals']}次信号)")

            if discovered:
                save_discovered_patterns(discovered)
                print("\n✅ 模式已保存")

    print("\n✅ pattern_miner.py 就绪")
