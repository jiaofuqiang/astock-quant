"""
🌤️ V3.3 市场状态分类器
========================
核心原则：没有一种策略在所有市场环境里都有效。
系统应该自动识别当前市场状态，然后切换最优策略组合。

市场状态分类：
1. 强趋势上涨 — 金叉死叉+放量突破最有效
2. 震荡偏多 — MA20回踩+RSI低位最有效
3. 震荡偏空 — 空仓或轻仓
4. 下跌趋势 — 空仓
5. 剧烈波动 — RSI超卖反弹最有效
6. 底部企稳 — MA60支撑反弹+地量见底最有效

分类依据：
- 20日涨跌幅
- 60日涨跌幅
- 20日波动率
- 成交量变化
- 涨跌家数比
- 均线排列（MA5 vs MA20 vs MA60）
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backtest import calc_ma

DATA_DIR = "/home/ubuntu/astock/data"
STATE_FILE = os.path.join(DATA_DIR, "market_state.json")


@dataclass
class MarketState:
    """市场状态"""
    regime: str           # 状态名称: strong_up / choppy_up / choppy_down / strong_down / volatile / bottoming
    regime_cn: str        # 中文名
    score: int            # -5~+5 综合评分
    confidence: float     # 0~1 置信度
    best_strategies: List[str]  # 该状态下最优策略
    description: str      # 一句话描述


# ============= 状态定义 =============

STATE_CONFIG = {
    "strong_up": {
        "cn": "🔥 强趋势上涨",
        "best_strategies": ["金叉死叉", "放量突破", "强势股突破前高"],
        "description": "均线多头排列，指数稳步上涨",
    },
    "choppy_up": {
        "cn": "📈 震荡偏多",
        "best_strategies": ["MA20回踩", "缩量回调到位", "RSI"],
        "description": "指数震荡但不创新低，重心上移",
    },
    "choppy_down": {
        "cn": "📉 震荡偏空",
        "best_strategies": ["RSI", "缩量回调到位"],
        "description": "指数震荡重心下移，建议轻仓",
    },
    "strong_down": {
        "cn": "💨 下跌趋势",
        "best_strategies": [],
        "description": "均线空头排列，建议空仓观望",
    },
    "volatile": {
        "cn": "🌪️ 剧烈波动",
        "best_strategies": ["RSI", "超卖v型反转", "底部放量反转"],
        "description": "波动率和成交量同时放大，大小盘分化",
    },
    "bottoming": {
        "cn": "🌱 底部企稳",
        "best_strategies": ["MA60支撑反弹", "地量见底", "底部放量反转"],
        "description": "缩量至地量+RSI低位+不再创新低",
    },
}


class MarketStateClassifier:
    """
    市场状态分类器

    基于多个指标自动判断当前市场状态
    """

    def __init__(self):
        self.history_buf = []  # 状态历史
        self._load_history()

    def _load_history(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    self.history_buf = json.load(f)
            except:
                self.history_buf = []
        if not isinstance(self.history_buf, list):
            self.history_buf = []

    def _save_history(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        # 只保留最近30条
        recent = self.history_buf[-30:] if len(self.history_buf) > 30 else self.history_buf
        with open(STATE_FILE, "w") as f:
            json.dump(recent, f, ensure_ascii=False, indent=2)

    def classify_from_index(self, index_kline: List[Dict]) -> MarketState:
        """
        基于大盘指数的K线判断市场状态

        Args:
            index_kline: 指数K线数据

        Returns:
            MarketState
        """
        if len(index_kline) < 60:
            return MarketState(
                regime="choppy_up", regime_cn="📈 震荡偏多",
                score=0, confidence=0.3,
                best_strategies=STATE_CONFIG["choppy_up"]["best_strategies"],
                description="数据不足，默认中性")

        closes = [k["close"] for k in index_kline]
        n = len(closes)

        # 1. 近期涨跌幅
        chg_5d = (closes[-1] - closes[-6]) / closes[-6] * 100 if n >= 6 else 0
        chg_20d = (closes[-1] - closes[-21]) / closes[-21] * 100 if n >= 21 else 0
        chg_60d = (closes[-1] - closes[-61]) / closes[-61] * 100 if n >= 61 else 0

        # 2. 波动率（20日）
        if n >= 21:
            daily_returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100
                             for i in range(-20, 0)]
            volatility = sum(abs(r) for r in daily_returns) / len(daily_returns)
        else:
            volatility = 2.0

        # 3. 均线排列
        ma5 = calc_ma(closes, 5)[-1] if n >= 5 else None
        ma20 = calc_ma(closes, 20)[-1] if n >= 20 else None
        ma60 = calc_ma(closes, 60)[-1] if n >= 60 else None

        # 多头排列检查
        bullish = bool(ma5 and ma20 and ma5 > ma20)
        strong_bullish = bool(ma5 and ma20 and ma60 and ma5 > ma20 > ma60)
        bearish = bool(ma5 and ma20 and ma5 < ma20)
        strong_bearish = bool(ma5 and ma20 and ma60 and ma5 < ma20 < ma60)

        # 4. 成交量变化（最后20天 vs 前20天成交量）
        volumes = [k.get("volume", 0) or 0 for k in index_kline]
        if n >= 40 and sum(volumes[-20:]) > 0 and sum(volumes[-40:-20]) > 0:
            vol_ratio = sum(volumes[-20:]) / sum(volumes[-40:-20])
        else:
            vol_ratio = 1.0

        # 5. 近期动量（最近5天 vs 前15天）
        if n >= 20:
            recent_momentum = chg_5d
        else:
            recent_momentum = 0

        # ===== 评分计算 =====
        score = 0
        score += min(2, max(-2, chg_20d / 5))  # 20日涨跌幅
        score += min(1, max(-1, recent_momentum / 3))  # 近期动量
        if strong_bullish:
            score += 2
        elif bullish:
            score += 1
        elif strong_bearish:
            score -= 2
        elif bearish:
            score -= 1
        score = max(-5, min(5, score))

        # ===== 状态判断 =====
        regime = "choppy_up"
        confidence = 0.6

        if strong_bullish and chg_20d > 5 and recent_momentum > 0:
            regime = "strong_up"
            confidence = 0.8
        elif strong_bearish and chg_20d < -5:
            regime = "strong_down"
            confidence = 0.8
        elif volatility > 3 and vol_ratio > 1.3:
            regime = "volatile"
            confidence = 0.7
        elif bearish and chg_60d < -8:
            # 可能底部
            if vol_ratio < 0.6 and abs(chg_20d) < 3:
                regime = "bottoming"
                confidence = 0.6
            else:
                regime = "choppy_down"
                confidence = 0.6
        elif bullish and chg_20d > 2:
            regime = "choppy_up"
            confidence = 0.7
        elif chg_20d > 1:
            regime = "choppy_up"
            confidence = 0.5
        elif chg_20d < -1:
            regime = "choppy_down"
            confidence = 0.5

        state = MarketState(
            regime=regime,
            regime_cn=STATE_CONFIG[regime]["cn"],
            score=round(score, 1),
            confidence=round(confidence, 2),
            best_strategies=STATE_CONFIG[regime]["best_strategies"],
            description=STATE_CONFIG[regime]["description"],
        )

        # 保存历史
        self.history_buf.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "regime": regime,
            "score": score,
            "confidence": confidence,
            "indicators": {
                "chg_5d": round(chg_5d, 2),
                "chg_20d": round(chg_20d, 2),
                "volatility": round(volatility, 2),
                "vol_ratio": round(vol_ratio, 2),
                "bullish": bullish,
                "strong_bullish": strong_bullish,
                "bearish": bearish,
                "strong_bearish": strong_bearish,
            },
        })
        self._save_history()

        return state

    def get_strategy_weights_for_state(self, regime: str) -> Dict[str, float]:
        """
        根据市场状态返回策略权重调整建议

        不同市场状态下，各策略的权重应该不同：
        - 强趋势: 金叉死叉权重最高
        - 震荡偏多: MA20回踩权重最高
        - 剧烈波动: RSI权重最高
        """
        weights = {
            "金叉死叉": 1.0,
            "MA20回踩": 1.0,
            "放量突破": 1.0,
            "RSI": 1.0,
        }

        if regime == "strong_up":
            weights["金叉死叉"] = 1.5
            weights["放量突破"] = 1.3
            weights["MA20回踩"] = 0.8
            weights["RSI"] = 0.6
        elif regime == "choppy_up":
            weights["MA20回踩"] = 1.4
            weights["RSI"] = 1.2
            weights["金叉死叉"] = 0.8
            weights["放量突破"] = 0.8
        elif regime == "volatile":
            weights["RSI"] = 1.5
            weights["金叉死叉"] = 0.5
            weights["放量突破"] = 0.5
            weights["MA20回踩"] = 0.7
        elif regime == "bottoming":
            weights["RSI"] = 1.3
            weights["金叉死叉"] = 1.2
            weights["MA20回踩"] = 0.6
            weights["放量突破"] = 0.6
        elif regime in ("choppy_down", "strong_down"):
            weights["金叉死叉"] = 0.3
            weights["MA20回踩"] = 0.3
            weights["放量突破"] = 0.2
            weights["RSI"] = 0.5

        return weights

    def get_history(self) -> List[Dict]:
        """获取状态变化历史"""
        return self.history_buf

    def state_changed(self) -> bool:
        """检测状态是否发生了变化"""
        if len(self.history_buf) < 2:
            return False
        return self.history_buf[-1]["regime"] != self.history_buf[-2]["regime"]

    def report(self, state: MarketState = None) -> str:
        """生成市场状态报告"""
        if state is None:
            # 从历史取最新的
            if self.history_buf:
                last = self.history_buf[-1]
                state = MarketState(
                    regime=last["regime"],
                    regime_cn=STATE_CONFIG.get(last["regime"], {}).get("cn", last["regime"]),
                    score=last["score"],
                    confidence=last["confidence"],
                    best_strategies=STATE_CONFIG.get(last["regime"], {}).get("best_strategies", []),
                    description=STATE_CONFIG.get(last["regime"], {}).get("description", ""),
                )
            else:
                return "暂无市场状态数据"

        indicators = self.history_buf[-1]["indicators"] if self.history_buf else {}

        lines = []
        lines.append(f"🌤️ **市场状态: {state.regime_cn}**")
        lines.append(f"  评分: {state.score:+.1f}/5 | 置信度: {state.confidence:.0%}")
        if indicators:
            lines.append(f"  20日涨跌: {indicators.get('chg_20d', '?'):+.1f}% | "
                         f"波动率: {indicators.get('volatility', '?'):.1f}% | "
                         f"量比: {indicators.get('vol_ratio', '?'):.1f}x")
            lines.append(f"  均线: {'多头📈' if indicators.get('bullish') else '空头📉' if indicators.get('bearish') else '中性'}")
        lines.append(f"  {state.description}")

        if state.best_strategies:
            lines.append(f"  🎯 推荐策略: {' → '.join(state.best_strategies)}")

        # 状态变化提示
        if len(self.history_buf) >= 2:
            prev = self.history_buf[-2]
            if prev["regime"] != state.regime:
                lines.append(f"  ⚠️ 状态从 {STATE_CONFIG.get(prev['regime'], {}).get('cn', prev['regime'])} 切换")

        return "\n".join(lines)


# ============= 集成到V3决策 =============

def get_market_adjusted_weights(market_classifier: MarketStateClassifier = None,
                                 state: MarketState = None) -> Dict[str, float]:
    """
    获取市场状态调整后的策略权重
    这个权重会与V3三维权重矩阵的个股级权重叠加
    """
    if market_classifier is None:
        classifier = MarketStateClassifier()
    else:
        classifier = market_classifier

    if state is None:
        # 用最新的历史状态
        if classifier.history_buf:
            last = classifier.history_buf[-1]
            regime = last["regime"]
        else:
            regime = "choppy_up"
    else:
        regime = state.regime

    return classifier.get_strategy_weights_for_state(regime)


# ============= 测试 =============

if __name__ == "__main__":
    print("🌤️ 市场状态分类器")
    print("=" * 60)

    # 模拟指数K线
    import random
    mock_index = []
    price = 3000
    for i in range(120):
        chg = random.uniform(-1.5, 1.5)
        price *= (1 + chg / 100)
        mock_index.append({
            "date": f"2026-{i//30+1:02d}-{(i%30)+1:02d}",
            "close": round(price, 2),
            "volume": random.randint(100, 500) * 10000,
        })

    classifier = MarketStateClassifier()

    # 最近30天改成强趋势
    price = 3200
    for i in range(30):
        chg = random.uniform(0.5, 2.0)
        price *= (1 + chg / 100)
        mock_index.append({
            "date": f"2026-{i//30+1+3:02d}-{(i%30)+1:02d}",
            "close": round(price, 2),
            "volume": random.randint(200, 800) * 10000,
        })

    state = classifier.classify_from_index(mock_index)
    print(f"\n当前状态: {state.regime_cn}")
    print(f"评分: {state.score:+.1f}")
    print(f"推荐策略: {state.best_strategies}")
    print(f"策略权重调整:")
    weights = classifier.get_strategy_weights_for_state(state.regime)
    for name, w in sorted(weights.items()):
        bar = "█" * int(w * 5)
        print(f"  {name}: {w:.1f}x {bar}")

    print(f"\n{state.description}")

    print("\n✅ 市场状态分类器就绪")
