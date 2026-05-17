"""
🔮 V3.4 尾盘预测系统 + 次日方向预测
========================================
核心：A股T+1制度下，买入时机决定胜负。
如果能预测次日涨跌方向，就能在尾盘做出更优决策。

预测维度：
1. 当日涨跌 + 相对均线位置 → 超买超卖
2. 成交量配合 → 是踏空追涨还是主力出货
3. 板块联动强度 → 龙头倒了还是板块内轮动
4. 历史模式匹配 → 过去类似的日K线组合，次日如何走
5. 隔夜风险 → 美股期货/离岸人民币等外部变量

输出：次日上涨概率 + 预期波动范围 + 最佳操作建议
"""

import json
import os
import sys
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backtest import KlineLoader, calc_ma, calc_rsi, calc_atr

DATA_DIR = "/home/ubuntu/astock/data"
PREDICT_DB = os.path.join(DATA_DIR, "eod_predictor.json")


class EODPredictor:
    """
    尾盘预测器

    对每只股票，在尾盘（14:30-15:00）运行：
    1. 提取今日K线特征
    2. 匹配历史相似模式
    3. 输出次日上涨概率
    """

    def __init__(self):
        self.loader = KlineLoader()
        self._load_pattern_db()

    def _load_pattern_db(self):
        """加载历史模式数据库"""
        if os.path.exists(PREDICT_DB):
            try:
                with open(PREDICT_DB) as f:
                    data = json.load(f)
                    self.patterns = data.get("patterns", [])
                    self.last_updated = data.get("last_updated", "")
            except:
                self.patterns = []
                self.last_updated = ""
        else:
            self.patterns = []
            self.last_updated = ""

    def _save_pattern_db(self):
        """保存模式数据库"""
        os.makedirs(os.path.dirname(PREDICT_DB), exist_ok=True)
        with open(PREDICT_DB, "w") as f:
            json.dump({
                "patterns": self.patterns[-200:],  # 最多保存200个
                "total_signals": len(self.patterns),
                "last_updated": datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)

    def learn_from_history(self, code: str):
        """
        从历史K线学习模式

        对每根K线：提取当日特征→记录次日涨跌
        """
        kline = self.loader.load_kline(code, 120)
        if len(kline) < 30:
            return 0

        patterns_learned = 0
        for i in range(20, len(kline) - 1):
            # 当日特征
            features = self._extract_daily_features(kline, i)
            if not features:
                continue

            # 次日收益
            tomorrow_ret = (kline[i + 1]["close"] - kline[i]["close"]) / kline[i]["close"] * 100

            features["_code"] = code
            features["_date"] = kline[i]["date"]
            features["_tomorrow_return"] = round(tomorrow_ret, 2)
            features["_tomorrow_up"] = 1 if tomorrow_ret > 0 else 0

            self.patterns.append(features)
            patterns_learned += 1

        self._save_pattern_db()
        return patterns_learned

    def _extract_daily_features(self, kline: List[Dict], idx: int) -> Dict:
        """提取当日特征（用于模式匹配）"""
        if idx < 20:
            return {}

        closes = [k["close"] for k in kline[:idx + 1]]
        highs = [k["high"] for k in kline[:idx + 1]]
        lows = [k["low"] for k in kline[:idx + 1]]
        volumes = [k["volume"] for k in kline[:idx + 1]]
        n = len(closes)

        today = kline[idx]
        yesterday = kline[idx - 1]

        features = {}

        # 1. 今日涨跌幅
        today_chg = (today["close"] - yesterday["close"]) / yesterday["close"] * 100
        features["today_chg"] = round(today_chg, 2)

        # 2. 今日振幅
        features["today_range"] = round((today["high"] - today["low"]) / today["close"] * 100, 2)

        # 3. 今日成交量倍率
        if n >= 21:
            avg_vol = sum(volumes[-21:-1]) / 20
            features["vol_ratio"] = round(today["volume"] / avg_vol if avg_vol > 0 else 1, 2)
        else:
            features["vol_ratio"] = 1.0

        # 4. 相对均线位置
        ma5 = calc_ma(closes, 5)[-1] if n >= 5 else None
        ma20 = calc_ma(closes, 20)[-1] if n >= 20 else None
        ma60 = calc_ma(closes, 60)[-1] if n >= 60 else None

        features["pct_above_ma5"] = round((today["close"] - ma5) / ma5 * 100, 2) if ma5 else 0
        features["pct_above_ma20"] = round((today["close"] - ma20) / ma20 * 100, 2) if ma20 else 0
        features["ma5_above_ma20"] = 1 if ma5 and ma20 and ma5 > ma20 else 0

        # 5. RSI
        rsi = calc_rsi(closes)[-1]
        features["rsi"] = round(rsi, 1) if rsi is not None else 50

        # 6. 近5日涨跌幅（动量）
        if n >= 6:
            features["mom_5d"] = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2)
        else:
            features["mom_5d"] = 0

        # 7. 是否收阳
        features["is_up_day"] = 1 if today_chg > 0 else 0

        # 8. 是否在区间低位（近3日最低）
        if n >= 3:
            low_3d = min(closes[-3:])
            features["is_3d_low"] = 1 if today["close"] == low_3d else 0
        else:
            features["is_3d_low"] = 0

        # 9. 上影线/下影线比例
        candle_body = abs(today["close"] - today["open"])
        features["upper_shadow"] = round((today["high"] - max(today["open"], today["close"])) / (candle_body + 0.01), 2)
        features["lower_shadow"] = round((min(today["open"], today["close"]) - today["low"]) / (candle_body + 0.01), 2)

        # 10. 是否连续涨/跌
        if n >= 3:
            ups = sum(1 for i in range(-2, 0) if closes[i] > closes[i - 1])
            features["consecutive_ups"] = ups
        else:
            features["consecutive_ups"] = 0

        # 11. ATR波动率
        atr = calc_atr(highs, lows, closes)[-1]
        features["atr_pct"] = round(atr / today["close"] * 100, 2) if atr and today["close"] else 3.0

        return features

    def _similarity(self, a: Dict, b: Dict) -> float:
        """计算两个特征向量的相似度"""
        # 只比较重要特征
        compare_keys = [
            "today_chg", "today_range", "vol_ratio",
            "pct_above_ma5", "pct_above_ma20", "rsi",
            "mom_5d", "atr_pct", "upper_shadow", "lower_shadow",
        ]

        total_diff = 0.0
        weight_sum = 0.0

        for key in compare_keys:
            va = a.get(key, 0)
            vb = b.get(key, 0)
            if va is None or vb is None:
                continue

            # 归一化差异
            max_abs = max(abs(va), abs(vb), 0.1)
            diff = abs(va - vb) / max_abs

            # 不同特征的权重
            weight_map = {
                "today_chg": 3.0,
                "pct_above_ma20": 2.0,
                "vol_ratio": 1.5,
                "rsi": 1.5,
                "mom_5d": 1.5,
                "atr_pct": 1.0,
                "today_range": 1.0,
                "upper_shadow": 0.5,
                "lower_shadow": 0.5,
            }
            w = weight_map.get(key, 1.0)
            total_diff += diff * w
            weight_sum += w

        if weight_sum == 0:
            return 0

        avg_diff = total_diff / weight_sum
        similarity = max(0, 1 - avg_diff)
        return round(similarity, 3)

    def predict_tomorrow(self, code: str) -> Dict:
        """
        预测次日方向

        Args:
            code: 股票代码

        Returns:
            {
                "up_probability": 65.2,    # 次日上涨概率%
                "expected_range": [-1.5, 2.0],  # 预期波动范围%
                "confidence": "high"/"medium"/"low",
                "similar_patterns": N,       # 匹配到的相似历史数
                "action": "买入/观望/卖出",
                "reason": "..."
            }
        """
        kline = self.loader.load_kline(code, 120)
        if len(kline) < 30:
            return {
                "up_probability": 50,
                "expected_range": [-2, 2],
                "confidence": "low",
                "similar_patterns": 0,
                "action": "数据不足",
                "reason": ""
            }

        # 提取今日特征
        today_features = self._extract_daily_features(kline, len(kline) - 1)
        if not today_features:
            return self._default_response()

        # 如果没有历史模式数据库，动态从K线学习
        if len(self.patterns) < 30:
            self.learn_from_history(code)

        if len(self.patterns) < 30:
            # 冷启动：基于概率规则
            return self._rule_based_predict(today_features)

        # 找相似的历史模式
        similar_patterns = []
        for p in self.patterns:
            sim = self._similarity(today_features, p)
            if sim >= 0.7:  # 相似度>0.7
                similar_patterns.append({
                    "pattern": p,
                    "similarity": sim,
                })

        similar_patterns.sort(key=lambda x: x["similarity"], reverse=True)
        top_patterns = similar_patterns[:30]  # 最多取30个最相似的

        if not top_patterns:
            return self._rule_based_predict(today_features)

        # 统计
        total = len(top_patterns)
        ups = sum(1 for p in top_patterns if p["pattern"].get("_tomorrow_up", 0) == 1)

        up_prob = ups / total * 100 if total > 0 else 50

        # 预期波动范围
        tomorrow_rets = [p["pattern"].get("_tomorrow_return", 0) for p in top_patterns]
        avg_ret = sum(tomorrow_rets) / len(tomorrow_rets) if tomorrow_rets else 0
        if len(tomorrow_rets) >= 2:
            variance = sum((r - avg_ret) ** 2 for r in tomorrow_rets) / len(tomorrow_rets)
            std = variance ** 0.5
        else:
            std = 2.0

        # 置信度
        if total >= 20:
            confidence = "high"
        elif total >= 10:
            confidence = "medium"
        else:
            confidence = "low"

        # 操作建议
        if up_prob >= 65:
            action = "✅ 适合买入"
            reason = f"历史相似模式次日上涨率{up_prob:.0f}%，均收益{avg_ret:+.2f}%"
        elif up_prob >= 55:
            action = "👀 可考虑"
            reason = f"历史相似模式次日涨跌不明显，上涨率{up_prob:.0f}%"
        elif up_prob >= 40:
            action = "⚠️ 谨慎"
            reason = f"历史相似模式偏空，上涨率仅{up_prob:.0f}%"
        else:
            action = "❌ 不适合买入"
            reason = f"历史相似模式次日下跌概率高，上涨率{up_prob:.0f}%"

        return {
            "up_probability": round(up_prob, 1),
            "expected_return": round(avg_ret, 2),
            "expected_range": [round(avg_ret - std, 2), round(avg_ret + std, 2)],
            "confidence": confidence,
            "similar_patterns": total,
            "action": action,
            "reason": reason,
        }

    def _default_response(self) -> Dict:
        return {
            "up_probability": 50, "expected_return": 0,
            "expected_range": [-2, 2], "confidence": "low",
            "similar_patterns": 0, "action": "数据不足", "reason": ""
        }

    def _rule_based_predict(self, features: Dict) -> Dict:
        """基于规则的概率预测（冷启动用）"""
        score = 50

        # 规则1: 今日大涨+缩量 → 次日可能回调
        if features.get("today_chg", 0) > 3 and features.get("vol_ratio", 1) < 0.8:
            score -= 10

        # 规则2: 今日大涨+放量 → 趋势延续
        if features.get("today_chg", 0) > 3 and features.get("vol_ratio", 1) > 1.5:
            score += 10

        # 规则3: RSI超卖(<30) → 反弹概率大
        if features.get("rsi", 50) < 30:
            score += 15
        elif features.get("rsi", 50) > 70:
            score -= 10

        # 规则4: 回踩MA20
        pct_ma20 = features.get("pct_above_ma20", 10)
        if -2 < pct_ma20 < 1:
            score += 10

        # 规则5: 连续3天跌 → 可能反弹
        if features.get("consecutive_ups", 0) <= 1 and features.get("today_chg", 0) < 0:
            score += 5

        # 规则6: 远离MA20 >5% → 回调风险
        if pct_ma20 > 8:
            score -= 8

        score = max(10, min(90, score))

        action = "✅ 适合买入" if score >= 65 else "👀 可考虑" if score >= 50 else "⚠️ 谨慎"

        return {
            "up_probability": score,
            "expected_return": round((score - 50) * 0.1, 2),
            "expected_range": [round(-2 + (score - 50) * 0.02, 2),
                                round(2 + (score - 50) * 0.04, 2)],
            "confidence": "medium",
            "similar_patterns": 0,
            "action": action,
            "reason": f"规则评分{score}/100",
        }

    def batch_predict(self, codes: List[str]) -> List[Dict]:
        """对多只股票做尾盘预测"""
        results = []
        for code in codes:
            try:
                pred = self.predict_tomorrow(code)
                pred["code"] = code
                results.append(pred)
            except:
                pass

        results.sort(key=lambda x: x["up_probability"], reverse=True)
        return results

    def eod_report(self, codes: List[str] = None) -> str:
        """生成尾盘预测报告"""
        if codes is None:
            loader = KlineLoader()
            codes = loader.load_all_codes()

        results = self.batch_predict(codes[:30])  # 最多30只

        lines = []
        lines.append(f"🔮 **尾盘预测 | {datetime.now().strftime('%m-%d %H:%M')}**")
        lines.append("")

        if not results:
            lines.append("无预测结果")
            return "\n".join(lines)

        # 高概率买入
        buy = [r for r in results if r["up_probability"] >= 60]
        if buy:
            lines.append(f"**✅ 次日上涨概率>60% ({len(buy)}只):**")
            for r in buy[:5]:
                lines.append(f"  {r['code']}: {r['up_probability']:.0f}% | "
                             f"预期{r['expected_return']:+.1f}% | "
                             f"{r['confidence']}")
            lines.append("")

        # 中等
        watch = [r for r in results if 50 <= r["up_probability"] < 60]
        if watch:
            lines.append(f"**👀 中性 ({len(watch)}只):**")
            for r in watch[:3]:
                lines.append(f"  {r['code']}: {r['up_probability']:.0f}% 预期{r['expected_return']:+.1f}%")

        # 低概率
        bad = [r for r in results if r["up_probability"] < 40]
        if bad:
            lines.append(f"**❌ 不建议买入 ({len(bad)}只):**")
            for r in bad[:3]:
                lines.append(f"  {r['code']}: {r['up_probability']:.0f}%")

        lines.append("")
        lines.append("📌 T+1制度下，尾盘买入后次日走势是关键")
        return "\n".join(lines)


def predict_and_score(code: str) -> Dict:
    """
    尾盘预测+推荐评分（供decision.py调用）
    返回可以直接叠加到复合评分的加分值
    """
    predictor = EODPredictor()
    pred = predictor.predict_tomorrow(code)

    # 预测概率→加分
    up_prob = pred.get("up_probability", 50)
    if up_prob >= 70:
        bonus = 2.0  # 强烈信号
    elif up_prob >= 60:
        bonus = 1.0
    elif up_prob >= 50:
        bonus = 0.0
    elif up_prob >= 40:
        bonus = -0.5
    else:
        bonus = -1.5

    return {
        "eod_bonus": bonus,
        "up_probability": up_prob,
        "expected_return": pred.get("expected_return", 0),
        "confidence": pred.get("confidence", "low"),
        "action": pred.get("action", ""),
    }


# ============= 测试 =============

if __name__ == "__main__":
    print("🔮 尾盘预测系统")
    print("=" * 60)

    loader = KlineLoader()
    codes = loader.load_all_codes()
    test_code = codes[0] if codes else "300308"
    print(f"\n测试股票: {test_code}")

    predictor = EODPredictor()

    # 学习
    print(f"\n1️⃣ 学习历史模式...")
    n = predictor.learn_from_history(test_code)
    print(f"   学习了 {n} 个模式")

    # 预测
    print(f"\n2️⃣ 次日方向预测:")
    pred = predictor.predict_tomorrow(test_code)
    print(f"   上涨概率: {pred['up_probability']:.0f}%")
    print(f"   预期收益: {pred['expected_return']:+.2f}%")
    print(f"   预期波动: {pred['expected_range']}")
    print(f"   置信度: {pred['confidence']}")
    print(f"   操作: {pred['action']}")
    print(f"   理由: {pred['reason']}")

    print("\n3️⃣ 批量预测:")
    print(predictor.eod_report(codes[:10]))

    print("\n✅ EOD预测系统就绪")
