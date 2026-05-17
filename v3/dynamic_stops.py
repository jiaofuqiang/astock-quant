"""
🎯 V3.6 动态止损止盈
======================
核心：固定止损（-8%）和固定止盈（+15%）是粗糙的。
应该基于每只股的：
1. 历史波动率（ATR）——高波动股波动大，止损应该更宽
2. 近期走势强度——强势股回踩幅度小，止损可以更紧
3. 当前位置——靠近支撑还是阻力？

输出：
- 动态止损价（随价格波动更新）
- 动态止盈价（分档：+5%/+10%/+15%分批）
- 预警信号（当价格接近止损/止盈时提醒）
"""

import json
import os
import sys
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backtest import KlineLoader, calc_ma, calc_atr
from data_feed import DataFeed

DATA_DIR = "/home/ubuntu/astock/data"


class DynamicStopLoss:
    """
    动态止损管理器

    对每只持仓股票维护动态止损价和止盈价
    """

    def __init__(self):
        self.loader = KlineLoader()
        self.df = DataFeed()
        self._load_state()

    def _load_state(self):
        self.state_file = os.path.join(DATA_DIR, "dynamic_stops.json")
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    self.stops = json.load(f)
            except:
                self.stops = {}
        else:
            self.stops = {}

    def _save_state(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.stops, f, ensure_ascii=False, indent=2)

    def calculate_stops(self, code: str, buy_price: float) -> Dict:
        """
        计算动态止损止盈价

        Args:
            code: 股票代码
            buy_price: 买入价格

        Returns:
            {
                "stop_loss": 12.50,
                "stop_loss_pct": -6.5,
                "take_profit_1": 14.50,    # 第一档止盈
                "take_profit_2": 16.00,    # 第二档止盈
                "take_profit_3": 18.00,    # 第三档止盈
                "trailing_stop": 13.00,    # 移动止损
                "warning_near_stop": False,
                "reason": "..."
            }
        """
        kline = self.loader.load_kline(code, 60)
        if len(kline) < 20:
            return self._default_stops(code, buy_price)

        closes = [k["close"] for k in kline]
        highs = [k["high"] for k in kline]
        lows = [k["low"] for k in kline]

        current_price = closes[-1]
        if current_price <= 0:
            return self._default_stops(code, buy_price)

        # 1. ATR波动率
        atr = calc_atr(highs, lows, closes, 14)
        atr_value = atr[-1] if atr[-1] else (current_price * 0.03)
        atr_pct = atr_value / current_price * 100

        # 2. 近期最大回撤
        recent_max = max(closes[-20:])
        recent_min = min(closes[-20:])
        max_drawdown = (recent_max - recent_min) / recent_max * 100 if recent_max > 0 else 10

        # 3. MA20支撑位
        ma20 = calc_ma(closes, 20)[-1]
        ma20_support = ma20 * 0.98 if ma20 else 0  # MA20下方2%

        # 4. 最低价支撑（近10日最低）
        support_10d = min(lows[-10:]) if len(lows) >= 10 else 0

        # === 计算止损价 ===
        # 基于ATR（2倍ATR）
        stop_by_atr = current_price - atr_value * 2
        stop_by_atr_pct = -atr_pct * 2

        # 基于MA20
        stop_by_ma20 = ma20 * 0.95 if ma20 else 0  # MA20下方5%
        stop_by_ma20_pct = (stop_by_ma20 - current_price) / current_price * 100 if current_price > 0 else -10

        # 基于买入价
        stop_by_buy = buy_price * 0.92  # 买入价下方8%
        stop_by_buy_pct = (stop_by_buy - current_price) / current_price * 100 if current_price > 0 else -8

        # 取最严格的止损
        stops = [s for s in [stop_by_atr, stop_by_ma20, stop_by_buy] if s > 0]
        if stops:
            stop_loss = max(stops)  # 取最高值（最靠近现价，最严格）
            stop_loss_pct = (stop_loss - current_price) / current_price * 100
        else:
            stop_loss = buy_price * 0.9
            stop_loss_pct = -10.0

        # 防止止损太宽松（不超过-15%）
        stop_loss_pct = max(stop_loss_pct, -15)
        stop_loss = current_price * (1 + stop_loss_pct / 100)

        # === 计算止盈价（3档） ===
        # 基于ATR：1.5x / 2.5x / 4x ATR
        tp1 = current_price + atr_value * 1.5
        tp2 = current_price + atr_value * 3
        tp3 = current_price + atr_value * 5

        tp1_pct = (tp1 - current_price) / current_price * 100
        tp2_pct = (tp2 - current_price) / current_price * 100
        tp3_pct = (tp3 - current_price) / current_price * 100

        # === 移动止损（从高点回落） ===
        trailing_distance = max(atr_pct * 2, 5)  # 从最高点回落至少5%
        trailing_stop = current_price * (1 - trailing_distance / 100)

        # === 检查是否接近止损 ===
        pct_to_stop = abs((current_price - stop_loss) / current_price * 100)
        warning = pct_to_stop < atr_pct  # 距离止损不到1个ATR

        # === 原因说明 ===
        reasons = []
        if stop_by_atr > 0 and abs(stop_loss - stop_by_atr) / current_price * 100 < 1:
            reasons.append(f"ATR={atr_value:.2f}({atr_pct:.1f}%)")
        if ma20 and abs(stop_loss - stop_by_ma20) / current_price * 100 < 1:
            reasons.append(f"MA20支撑={ma20:.2f}")
        if abs(stop_loss - stop_by_buy) / current_price * 100 < 1:
            reasons.append(f"买入成本={buy_price:.2f}")

        result = {
            "code": code,
            "buy_price": buy_price,
            "current_price": round(current_price, 2),
            "stop_loss": round(stop_loss, 2),
            "stop_loss_pct": round(stop_loss_pct, 1),
            "take_profit_1": round(tp1, 2),
            "take_profit_1_pct": round(tp1_pct, 1),
            "take_profit_2": round(tp2, 2),
            "take_profit_2_pct": round(tp2_pct, 1),
            "take_profit_3": round(tp3, 2),
            "take_profit_3_pct": round(tp3_pct, 1),
            "trailing_stop": round(trailing_stop, 2),
            "atr_pct": round(atr_pct, 1),
            "warning_near_stop": warning,
            "reason": " | ".join(reasons) if reasons else "默认设置",
            "updated_at": datetime.now().isoformat(),
        }

        # 保存
        self.stops[code] = result
        self._save_state()

        return result

    def _default_stops(self, code: str, buy_price: float) -> Dict:
        """数据不足时的默认止损"""
        return {
            "code": code,
            "buy_price": buy_price,
            "current_price": buy_price,
            "stop_loss": round(buy_price * 0.9, 2),
            "stop_loss_pct": -10.0,
            "take_profit_1": round(buy_price * 1.05, 2),
            "take_profit_1_pct": 5.0,
            "take_profit_2": round(buy_price * 1.12, 2),
            "take_profit_2_pct": 12.0,
            "take_profit_3": round(buy_price * 1.2, 2),
            "take_profit_3_pct": 20.0,
            "trailing_stop": round(buy_price * 0.92, 2),
            "atr_pct": 3.0,
            "warning_near_stop": False,
            "reason": "默认（数据不足）",
            "updated_at": datetime.now().isoformat(),
        }

    def update_all_stops(self, holdings: List[Dict]) -> List[Dict]:
        """
        更新所有持仓的止损止盈

        Args:
            holdings: [{"code": "300308", "buy_price": 50.0}, ...]

        Returns:
            [stop_result, ...] 包含预警信息的列表
        """
        # 先获取实时价格
        codes = [h["code"] for h in holdings]
        try:
            prices = self.df.fetch(codes)
        except:
            prices = {}

        results = []
        alerts = []

        for h in holdings:
            code = h["code"]
            buy_price = h.get("buy_price", 0)
            if buy_price <= 0:
                continue

            result = self.calculate_stops(code, buy_price)

            # 检查预警
            current_price = prices.get(code, {}).get("price", 0) or result["current_price"]
            if current_price > 0:
                pnl = (current_price - buy_price) / buy_price * 100
                pct_to_stop = (current_price - result["stop_loss"]) / current_price * 100

                if pct_to_stop < result.get("atr_pct", 3):
                    alerts.append(f"🚨 {code}: 距止损仅{pct_to_stop:.1f}% (现价{current_price:.2f} 止损{result['stop_loss']:.2f})")
                if pnl > result["take_profit_2_pct"]:
                    alerts.append(f"🎯 {code}: 已涨{pnl:.1f}%! 建议分批止盈（TP2={result['take_profit_2']:.2f}）")

            results.append(result)

        if alerts:
            for a in alerts:
                results.append({"alert": a})

        return results

    def get_alert_report(self, holdings: List[Dict]) -> str:
        """生成止损止盈报告"""
        results = self.update_all_stops(holdings)

        lines = []
        lines.append(f"🎯 **止损止盈监控 | {datetime.now().strftime('%m-%d %H:%M')}**")
        lines.append("")

        alerts = [r for r in results if "alert" in r]
        for a in alerts:
            lines.append(a["alert"])

        if not alerts:
            lines.append("✅ 所有持仓在安全范围内")

        lines.append("")
        for r in results:
            if "alert" in r:
                continue
            lines.append(f"**{r['code']}:**")
            lines.append(f"  买入价{r['buy_price']:.2f} → 现价{r['current_price']:.2f} ({ (r['current_price']-r['buy_price'])/r['buy_price']*100:+.1f}%)")
            lines.append(f"  🛑 止损: {r['stop_loss']:.2f} ({r['stop_loss_pct']:+.1f}%) | ATR={r['atr_pct']:.1f}%")
            lines.append(f"  🎯 止盈: T1={r['take_profit_1']:.2f}({r['take_profit_1_pct']:+.0f}%) "
                         f"T2={r['take_profit_2']:.2f}({r['take_profit_2_pct']:+.0f}%) "
                         f"T3={r['take_profit_3']:.2f}({r['take_profit_3_pct']:+.0f}%)")
            lines.append(f"  📍 移动止损: {r['trailing_stop']:.2f}")
            lines.append(f"  理由: {r['reason']}")

        return "\n".join(lines)


# ============= 测试 =============

if __name__ == "__main__":
    print("🎯 动态止损止盈")
    print("=" * 60)

    manager = DynamicStopLoss()

    # 模拟计算
    print("\n计算止损止盈（买入价假设为现价）:")
    for code in ["300308", "002371", "300394"]:
        result = manager.calculate_stops(code, buy_price=100)
        print(f"\n  {code}:")
        print(f"    ATR: {result['atr_pct']:.1f}%")
        print(f"    止损: {result['stop_loss']:.2f} ({result['stop_loss_pct']:+.1f}%)")
        print(f"    1档: {result['take_profit_1']:.2f} (+{result['take_profit_1_pct']:.0f}%)")
        print(f"    2档: {result['take_profit_2']:.2f} (+{result['take_profit_2_pct']:.0f}%)")
        print(f"    3档: {result['take_profit_3']:.2f} (+{result['take_profit_3_pct']:.0f}%)")
        print(f"    移动止损: {result['trailing_stop']:.2f}")
        print(f"    接近止损: {'⚠️' if result['warning_near_stop'] else '✅'}")

    print("\n✅ 动态止损止盈就绪")
