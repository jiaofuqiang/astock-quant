"""
💼 V3.5 组合优化引擎
======================
核心：不是选最好的单只股就能赚最多钱——真正的收益来自组合管理。

数学基础（改进版马科维茨+凯利）：
1. 每只标的的预期收益（来自V3复合评分）
2. 标的之间的相关性矩阵（来自correlation.py）
3. 资金约束：单只>5%且<25%，总仓位由市场状态决定
4. 目标：在给定风险下最大化预期收益

输出：
- 最优持仓比例（基于均值-方差优化）
- 各标的当前权重 vs 目标权重的偏离
- 再平衡建议
"""

import json
import os
import sys
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DATA_DIR = "/home/ubuntu/astock/data"
PORTFOLIO_DB = os.path.join(DATA_DIR, "portfolio_opt.json")


@dataclass
class PortfolioAllocation:
    """组合分配建议"""
    code: str
    expected_return: float  # 预期收益%
    risk: float             # 风险（波动率%）
    weight: float           # 建议权重 0~1
    current_weight: float   # 当前实际权重
    rebalance_action: str   # buy/sell/hold


class PortfolioOptimizer:
    """
    组合优化器

    基于均值-方差优化 + 约束条件：
    - 单只>5%且<25%
    - 总仓位<市场状态上限（e.g. 强趋势80%，震荡50%，下跌20%）
    - 同一板块总权重<40%（避免集中风险）
    """

    def __init__(self, correlation_matrix: Dict[str, Dict[str, float]] = None):
        self.corr_matrix = correlation_matrix or {}

    def set_correlation_matrix(self, matrix: Dict[str, Dict[str, float]]):
        self.corr_matrix = matrix

    def _get_correlation(self, code_a: str, code_b: str) -> float:
        """获取两只股票的相关系数"""
        # 从correlation.db获取
        try:
            import sqlite3
            conn = sqlite3.connect(os.path.join(DATA_DIR, "correlation.db"))
            c = conn.cursor()
            # 从correlation表估算（基于同板块联动）
            c.execute("""
                SELECT AVG(correlation) FROM correlation
                WHERE (leader_code=? AND follower_code=?)
                   OR (leader_code=? AND follower_code=?)
            """, (code_a, code_b, code_b, code_a))
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return row[0] / 100  # 归一化到0~1
        except:
            pass

        # 如果同板块，默认0.6相关
        from v3.correlation import SECTOR_GROUPS
        for sector, codes in SECTOR_GROUPS.items():
            if code_a in codes and code_b in codes:
                return 0.6

        # 不同板块，默认0.3
        return 0.3

    def optimize(self, candidates: List[Dict], max_position: float = 0.8,
                  existing_holdings: Dict[str, float] = None) -> Dict:
        """
        组合优化

        Args:
            candidates: [{code, expected_return, risk}, ...]
            max_position: 总仓位上限 (0~1)
            existing_holdings: {code: current_weight, ...}

        Returns:
            {
                "allocations": [PortfolioAllocation, ...],
                "total_expected_return": ...,
                "total_risk": ...,
                "sharpe_ratio": ...,
            }
        """
        if not candidates:
            return {"allocations": [], "total_expected_return": 0, "total_risk": 0, "sharpe_ratio": 0}

        existing = existing_holdings or {}

        # 如果候选标的太少，简化处理
        if len(candidates) <= 3:
            return self._simple_allocate(candidates, max_position, existing)

        # 排序：按预期收益/风险比
        sorted_candidates = sorted(
            candidates,
            key=lambda x: x.get("expected_return", 0) / max(x.get("risk", 1), 0.1),
            reverse=True,
        )

        # 均值-方差优化（简化版：不求解完整二次规划，用贪心+约束）
        n = len(sorted_candidates)
        weights = [0.0] * n

        # 基于夏普比的排名权重
        total_sharpe = 0
        sharpe_list = []
        for c in sorted_candidates:
            er = c.get("expected_return", 0)
            risk = max(c.get("risk", 1), 0.5)
            sharpe = er / risk
            sharpe_list.append(sharpe)
            if sharpe > 0:
                total_sharpe += sharpe

        if total_sharpe > 0:
            for i in range(n):
                if sharpe_list[i] > 0:
                    weights[i] = sharpe_list[i] / total_sharpe

        # 约束1: 单只>5%且<25%
        min_weight = 0.05
        max_single = 0.25

        for i in range(n):
            weights[i] = max(min_weight, min(max_single, weights[i]))

        # 约束2: 总权重=1，然后乘以总仓位
        total_w = sum(weights)
        if total_w > 0:
            weights = [w / total_w * max_position for w in weights]

        # 约束3: 同板块总权重<40%
        from v3.correlation import SECTOR_GROUPS
        sector_weights = {}
        for sector, codes in SECTOR_GROUPS.items():
            sector_w = sum(
                weights[i] for i, c in enumerate(sorted_candidates)
                if c.get("code") in codes
            )
            if sector_w > 0.4:
                scale = 0.4 / sector_w
                for i, c in enumerate(sorted_candidates):
                    if c.get("code") in codes:
                        weights[i] *= scale

        # 构建分配结果
        allocations = []
        for i, c in enumerate(sorted_candidates):
            code = c.get("code", "")
            current_w = existing.get(code, 0)

            if weights[i] > current_w + 0.02:
                action = "buy"
            elif weights[i] < current_w - 0.02:
                action = "sell"
            else:
                action = "hold"

            allocations.append(PortfolioAllocation(
                code=code,
                expected_return=c.get("expected_return", 0),
                risk=c.get("risk", 0),
                weight=round(weights[i], 3),
                current_weight=round(current_w, 3),
                rebalance_action=action,
            ))

        # 组合统计
        total_expected = sum(a.expected_return * a.weight for a in allocations)
        # 简化风险计算（仅估算）
        total_risk = sum(a.risk * a.weight for a in allocations)
        sharpe = total_expected / total_risk if total_risk > 0 else 0

        return {
            "allocations": allocations,
            "total_expected_return": round(total_expected, 2),
            "total_risk": round(total_risk, 2),
            "sharpe_ratio": round(sharpe, 2),
            "total_position": round(sum(a.weight for a in allocations), 2),
        }

    def _simple_allocate(self, candidates: List[Dict], max_position: float,
                          existing: Dict[str, float]) -> Dict:
        """少于3只标的时的简化分配"""
        sorted_c = sorted(
            candidates,
            key=lambda x: x.get("composite_score", 0) or x.get("expected_return", 0),
            reverse=True,
        )

        n = len(sorted_c)
        allocations = []

        for i, c in enumerate(sorted_c):
            code = c.get("code", "")
            current_w = existing.get(code, 0)

            if n == 1:
                weight = min(max_position, 0.25)  # 单只不超过25%
            elif n == 2:
                weight = min(max_position * 0.6, 0.25)
            else:
                weight = min(max_position / n, 0.25)

            action = "buy" if weight > current_w else "hold" if weight >= current_w * 0.8 else "sell"

            allocations.append(PortfolioAllocation(
                code=code,
                expected_return=c.get("expected_return", 0) or c.get("composite_score", 0),
                risk=c.get("risk", 3) or 5,
                weight=round(weight, 3),
                current_weight=round(current_w, 3),
                rebalance_action=action,
            ))

        return {
            "allocations": allocations,
            "total_expected_return": round(sum(a.expected_return * a.weight for a in allocations), 2),
            "total_risk": round(sum(a.risk * a.weight for a in allocations) / n, 2),
            "sharpe_ratio": 0,
            "total_position": round(sum(a.weight for a in allocations), 2),
        }

    def from_v3_scores(self, scored_results: List[Dict], market_state: str = "choppy_up",
                        existing_holdings: Dict[str, float] = None) -> Dict:
        """
        从V3评分结果直接优化组合

        Args:
            scored_results: V3评分结果 [{code, composite_score, volatility, ...}]
            market_state: 市场状态（决定总仓位上限）
            existing_holdings: {code: weight, ...}

        Returns:
            optimized portfolio
        """
        # 市场状态→总仓位
        position_limits = {
            "strong_up": 0.8,
            "choppy_up": 0.5,
            "volatile": 0.3,
            "bottoming": 0.4,
            "choppy_down": 0.2,
            "strong_down": 0.0,
        }
        max_pos = position_limits.get(market_state, 0.5)

        # 筛选评分>2的标的
        candidates = []
        for r in scored_results:
            score = r.get("composite_score", 0) or r.get("kelly_fraction", 0) * 10
            if score < 2:
                continue

            candidates.append({
                "code": r.get("code", ""),
                "expected_return": score,  # 评分作为预期收益的代理
                "risk": r.get("volatility", 5) or 5,
                "composite_score": score,
            })

        return self.optimize(candidates, max_pos, existing_holdings)

    def report(self, opt_result: Dict) -> str:
        """生成组合优化报告"""
        lines = []
        lines.append("💼 **组合优化建议**")
        lines.append("")

        allocations = opt_result.get("allocations", [])
        if not allocations:
            lines.append("无符合条件标的")
            return "\n".join(lines)

        lines.append(f"**总仓位: {opt_result['total_position']*100:.0f}%**")
        lines.append(f"组合预期收益: {opt_result['total_expected_return']:+.1f} | "
                     f"组合风险: {opt_result['total_risk']:.1f}%")
        if opt_result.get("sharpe_ratio"):
            lines.append(f"夏普比: {opt_result['sharpe_ratio']:.2f}")
        lines.append("")

        # 买入
        buys = [a for a in allocations if a.rebalance_action == "buy" and a.weight > 0]
        if buys:
            lines.append(f"**📈 建议加仓 ({len(buys)}):**")
            for a in sorted(buys, key=lambda x: x.weight, reverse=True):
                lines.append(f"  {a.code}: {a.weight*100:.0f}% → 预期{a.expected_return:+.1f}")
            lines.append("")

        # 持有
        holds = [a for a in allocations if a.rebalance_action == "hold"]
        if holds:
            lines.append(f"**➖ 持有 ({len(holds)}):**")
            for a in holds:
                lines.append(f"  {a.code}: {a.weight*100:.0f}%")

        # 卖出
        sells = [a for a in allocations if a.rebalance_action == "sell" and a.weight == 0]
        if sells:
            lines.append(f"**📉 建议减仓/卖出 ({len(sells)}):**")
            for a in sells:
                lines.append(f"  {a.code}: {a.current_weight*100:.0f}%→0")

        lines.append("")
        lines.append("📌 约束: 单只<25% | 同板块<40% | 总仓位按市场状态")
        return "\n".join(lines)


# ============= 测试 =============

if __name__ == "__main__":
    print("💼 组合优化引擎")
    print("=" * 60)

    optimizer = PortfolioOptimizer()

    # 模拟V3评分结果
    mock_results = [
        {"code": "300308", "composite_score": 4.5, "volatility": 8.0},
        {"code": "002371", "composite_score": 3.8, "volatility": 6.5},
        {"code": "300394", "composite_score": 3.2, "volatility": 9.0},
        {"code": "002281", "composite_score": 2.8, "volatility": 5.0},
        {"code": "603019", "composite_score": 2.5, "volatility": 7.0},
    ]

    existing = {"300308": 0.15, "002371": 0.10}

    print(f"\n模拟组合优化（震荡偏多市场，仓位上限50%）:")
    result = optimizer.from_v3_scores(
        mock_results, market_state="choppy_up", existing_holdings=existing
    )

    print(f"总仓位: {result['total_position']*100:.0f}%")
    print(f"预期收益: {result['total_expected_return']:+.1f}")
    for a in result["allocations"]:
        action_icon = "📈" if a.rebalance_action == "buy" else "➖" if a.rebalance_action == "hold" else "📉"
        print(f"  {action_icon} {a.code}: {a.weight*100:.0f}% (当前{a.current_weight*100:.0f}%) → {a.rebalance_action}")

    print(f"\n{optimizer.report(result)}")
    print("\n✅ 组合优化引擎就绪")
