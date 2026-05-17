"""
🏢 V3.7 L4基本面分析层
=========================
核心：基于腾讯行情接口已经提供的数据，做个股基本面分析。

腾讯接口已提供：
- pe: 市盈率（动态TTM）
- market_cap: 总市值（亿）
- circulating_cap: 流通市值（亿）
- turnover_rate: 换手率
- high_52w / low_52w: 52周高/低

我们还能从K线数据库推导：
- 营收增长率 ≈ 市值变化率 - PE变化率（近似）
- 波动率特征
- 大/中/小市值分类

输出：
1. 估值评分（PE历史分位估算）
2. 市值分类（大/中/小）
3. 成长性打分
4. 综合基本面评分（叠加到V3评分）
"""

import json
import os
import sys
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DATA_DIR = "/home/ubuntu/astock/data"

# ============= 行业估值基准 =============

# A股各行业合理PE范围（基于历史数据）
SECTOR_PE_RANGES = {
    "光模块": (25, 50, 80),      # (低估, 合理, 高估)
    "半导体": (30, 60, 100),
    "AI算力": (25, 50, 80),
    "新能源汽车": (15, 30, 50),
    "光伏": (10, 20, 35),
    "机器人": (30, 55, 90),
    "信创": (30, 55, 85),
    "消费电子": (15, 25, 40),
    "创新药": (25, 50, 80),
    "军工": (30, 50, 80),
    "白酒": (20, 35, 55),
    "银行": (4, 6, 10),
    "证券": (15, 25, 40),
    "地产": (5, 10, 18),
    "周期": (8, 15, 25),
    # 通用默认
    "default": (15, 30, 60),
}

# 市值规模标准（A股）
CAP_THRESHOLDS = {
    "大": 500,      # >500亿
    "中": 100,      # 100-500亿
    "小": 0,        # <100亿
}

# 游资偏好：小市值
# 机构偏好：中大市值


class FundamentalAnalyzer:
    """
    基本面分析器

    基于腾讯接口可获取的数据做基本面评估。
    虽然数据有限（没利润表/资产负债表），但足以做：
    - 估值高/中/低判断
    - 市值类型识别
    - 动量与基本面的背离检测
    """

    def __init__(self):
        self.cache = {}

    def analyze(self, code: str, price_data: Dict = None) -> Dict:
        """
        对单只股票做基本面分析

        Args:
            code: 股票代码
            price_data: 来自DataFeed.fetch()的原始数据，
                        包含pe, market_cap, turnover_rate等

        Returns:
            {
                "code": "300308",
                "name": "中际旭创",
                "pe": 35.2,
                "pe_assessment": "合理/低估/高估",
                "market_cap": 1200,
                "cap_category": "大/中/小",
                "forward_pe": ...,
                "growth_score": 0~10,
                "valuation_score": -5~+5,
                "fundamental_score": -5~+5,
                "suitable_for": "游资/机构/两者皆可",
                "detail": {...}
            }
        """
        if not price_data:
            return self._empty_result(code, "无价格数据")

        pe = price_data.get("pe")
        market_cap = price_data.get("market_cap", 0)
        circulating_cap = price_data.get("circulating_cap", 0)
        turnover = price_data.get("turnover_rate", 0)
        high_52w = price_data.get("high_52w", 0)
        low_52w = price_data.get("low_52w", 0)
        price = price_data.get("price", 0)
        name = price_data.get("name", "")

        if pe is None or pe <= 0:
            pe = None  # 亏损股PE为负，单独处理

        # === 1. 市值分类 ===
        cap = market_cap or circulating_cap or 0
        if cap >= CAP_THRESHOLDS["大"]:
            cap_category = "大"
        elif cap >= CAP_THRESHOLDS["中"]:
            cap_category = "中"
        else:
            cap_category = "小"

        # === 2. 估值评分 ===
        # 查找行业PE基准
        sector = self._guess_sector(code)
        low_pe, fair_pe, high_pe = SECTOR_PE_RANGES.get(sector, SECTOR_PE_RANGES["default"])

        if pe and pe > 0:
            if pe <= low_pe:
                pe_assessment = "低估"
                valuation_score = 3
            elif pe <= fair_pe:
                pe_assessment = "合理偏低"
                valuation_score = 1
            elif pe <= high_pe:
                pe_assessment = "合理偏高"
                valuation_score = -1
            else:
                pe_assessment = "高估"
                valuation_score = -3
        else:
            pe_assessment = "亏损/未知"
            valuation_score = -2

        # === 3. 成长性估算（近似） ===
        # 用52周价格变化 + PE变化 反推营收增长
        # 简化：净利润增速 ≈ (1+price_chg_52w)/(1+pe_chg_52w) - 1
        growth_score = 0
        estimated_growth = None
        if price and high_52w and low_52w and low_52w > 0 and price > 0:
            price_position = (price - low_52w) / (high_52w - low_52w) * 100
            # 52周涨幅估算
            price_chg_52w = (price - low_52w) / low_52w * 100
            # 如果是创新高的，可能成长性好
            if price >= high_52w * 0.9:
                growth_score = 3
            elif price_position > 60:
                growth_score = 1
            elif price_position < 20:
                growth_score = -1
        else:
            price_position = 50

        # === 4. 活跃度分析 ===
        # 换手率高 = 游资关注
        # 换手率低 = 机构锁仓
        if turnover and turnover > 0:
            if turnover > 5:
                activity = "非常活跃（游资主导）"
                activity_score = 2
            elif turnover > 2:
                activity = "活跃（游资+机构）"
                activity_score = 1
            elif turnover > 0.5:
                activity = "正常（机构主导）"
                activity_score = 0
            else:
                activity = "低迷（无人问津）"
                activity_score = -1
        else:
            activity = "未知"
            activity_score = 0

        # === 5. 综合基本面评分 ===
        fundamental_score = valuation_score * 0.4 + growth_score * 0.4 + activity_score * 0.2
        fundamental_score = max(-5, min(5, round(fundamental_score, 1)))

        # === 6. 适合谁 ===
        if cap_category == "小" and turnover and turnover > 3:
            suitable_for = "游资"
        elif cap_category == "大" and turnover and turnover < 2:
            suitable_for = "机构"
        else:
            suitable_for = "两者皆可"

        result = {
            "code": code,
            "name": name or code,
            "sector": sector,
            "pe": pe,
            "pe_assessment": pe_assessment,
            "market_cap": round(market_cap, 1) if market_cap else None,
            "circulating_cap": round(circulating_cap, 1) if circulating_cap else None,
            "cap_category": cap_category,
            "price_position_52w": round(price_position, 1),
            "turnover_rate": turnover,
            "activity": activity,
            "growth_score": growth_score,
            "valuation_score": valuation_score,
            "activity_score": activity_score,
            "fundamental_score": fundamental_score,
            "suitable_for": suitable_for,
        }

        self.cache[code] = result
        return result

    def _guess_sector(self, code: str) -> str:
        """通过代码反推行业"""
        for sector, codes in self._get_sector_map().items():
            if code in codes:
                return sector
        return "default"

    def _get_sector_map(self) -> Dict[str, List[str]]:
        """获取个股→行业映射"""
        try:
            from data_feed import SECTOR_STOCKS
            return SECTOR_STOCKS
        except:
            return {}

    def _empty_result(self, code: str, reason: str) -> Dict:
        return {
            "code": code,
            "name": code,
            "error": reason,
            "pe": None, "pe_assessment": "未知",
            "cap_category": "未知",
            "fundamental_score": 0,
            "suitable_for": "未知",
        }

    def batch_analyze(self, code_data_map: Dict[str, Dict]) -> List[Dict]:
        """批量分析"""
        results = []
        for code, data in code_data_map.items():
            result = self.analyze(code, data)
            results.append(result)
        return results

    def fundamental_score_for_v3(self, code: str, price_data: Dict = None) -> Dict:
        """
        生成可以叠加到V3评分的加分

        Returns:
            {"fundamental_bonus": 1.5, "detail": {估值: 3, 成长: 1, ...}}
        """
        result = self.analyze(code, price_data)
        score = result.get("fundamental_score", 0)
        bonus = score * 0.3  # 基本面评分最高±1.5分

        return {
            "fundamental_bonus": round(bonus, 1),
            "fundamental_score": result["fundamental_score"],
            "sector": result["sector"],
            "cap_category": result["cap_category"],
            "pe_assessment": result["pe_assessment"],
            "suitable_for": result["suitable_for"],
            "detail": {
                "估值": result["valuation_score"],
                "成长": result["growth_score"],
                "活跃度": result.get("activity_score", 0),
            }
        }

    def report(self, codes: List[str] = None) -> str:
        """生成基本面分析报告"""
        from data_feed import DataFeed

        df = DataFeed()

        if codes is None:
            try:
                from backtest import KlineLoader
                codes = KlineLoader.load_all_codes()
            except:
                codes = []

        data = df.fetch(codes)

        all_results = []
        for code in codes:
            if code in data:
                result = self.analyze(code, data[code])
                all_results.append(result)

        # 按基本面评分排序
        all_results.sort(key=lambda x: x.get("fundamental_score", -999), reverse=True)

        lines = []
        lines.append(f"🏢 **基本面分析 | {datetime.now().strftime('%m-%d %H:%M')}**")
        lines.append("")

        if not all_results:
            lines.append("无数据")
            return "\n".join(lines)

        # 高评分
        best = [r for r in all_results if r.get("fundamental_score", 0) >= 3]
        if best:
            lines.append(f"**✅ 基本面优秀 ({len(best)}只):**")
            for r in best[:5]:
                lines.append(f"  {r['code']} {r.get('sector','')} | "
                             f"PE={r.get('pe','?')} | "
                             f"{r.get('pe_assessment','')} | "
                             f"{r.get('cap_category','')}盘 | "
                             f"评分{r['fundamental_score']:+.1f}")
            lines.append("")

        # 低评分
        worst = [r for r in all_results if r.get("fundamental_score", 0) <= -3]
        if worst:
            lines.append(f"**❌ 基本面差 ({len(worst)}只):**")
            for r in worst[:3]:
                lines.append(f"  {r['code']}: PE={r.get('pe','?')} | "
                             f"{r.get('pe_assessment','')} | "
                             f"评分{r['fundamental_score']:+.1f}")
            lines.append("")

        # 游资标的 vs 机构标的
        youzi = [r for r in all_results if r.get("suitable_for") == "游资"]
        jigou = [r for r in all_results if r.get("suitable_for") == "机构"]
        lines.append(f"**📊 风格分类:**")
        lines.append(f"  游资型({len(youzi)}) | 机构型({len(jigou)}) | 通用型({len(all_results)-len(youzi)-len(jigou)})")

        lines.append("")
        lines.append("📌 基于腾讯接口数据的简化估值模型")

        return "\n".join(lines)


# ============= V3评分集成 =============

def integrate_fundamental_into_decision(decision_result: Dict,
                                          all_price_data: Dict[str, Dict]) -> Dict:
    """
    将基本面评分注入DecisionEngine的结果中
    """
    analyzer = FundamentalAnalyzer()

    buy_list = decision_result.get("buy_list", [])
    for b in buy_list:
        code = b.get("code", "")
        data = all_price_data.get(code, {})
        fb = analyzer.fundamental_score_for_v3(code, data)
        b["fundamental"] = fb

    # 重新排序：V3评分 + 基本面加分
    for b in buy_list:
        v3_score = b.get("score", 0)
        fb_bonus = b.get("fundamental", {}).get("fundamental_bonus", 0)
        b["adjusted_score"] = round(v3_score + fb_bonus, 1)

    buy_list.sort(key=lambda x: x.get("adjusted_score", 0), reverse=True)
    decision_result["buy_list"] = buy_list
    decision_result["has_fundamental"] = True

    return decision_result


# ============= 测试 =============

if __name__ == "__main__":
    print("🏢 L4基本面分析层")
    print("=" * 60)

    # 用腾讯接口真实数据
    from data_feed import DataFeed

    df = DataFeed()
    codes = ["300308", "002371", "300394", "002281", "300750",
             "000568", "601398", "688662", "002050", "603019"]

    print(f"\n获取 {len(codes)} 只股票的基本面数据...")
    data = df.fetch(codes)

    analyzer = FundamentalAnalyzer()
    print(f"\n基本面分析结果:")
    for code in codes:
        if code in data:
            result = analyzer.analyze(code, data[code])
            if result.get("error"):
                print(f"  {code}: {result['error']}")
                continue
            mark = "✅" if result["fundamental_score"] >= 2 else "⚠️" if result["fundamental_score"] >= 0 else "❌"
            print(f"  {mark} {code} {result.get('name','')} ({result.get('sector','')})")
            print(f"      PE={result.get('pe','?')} ({result.get('pe_assessment','')}) | "
                  f"{result.get('cap_category','')}盘 | "
                  f"换手{result.get('turnover_rate','?')}%")
            print(f"      基本面评分: {result['fundamental_score']:+.1f} | "
                  f"适合: {result.get('suitable_for','')}")

    print(f"\n{analyzer.report(codes)}")
    print("\n✅ L4基本面分析层就绪")
