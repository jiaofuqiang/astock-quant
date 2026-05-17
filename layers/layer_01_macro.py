"""
🌍 全球宏观层引擎 (Layer 1)
=================================
关注：地缘政治、美元/美债/黄金/石油/美股 → A股传导

每一个外部冲击，都要判断：
1. 冲击强度（1-5）
2. 传导路径（影响哪些A股板块）
3. 时间尺度（短期脉冲 / 中期趋势 / 长期格局）
4. 已price in程度（0-100%）
5. 综合评分（利好/利空/中性 + 置信度）

数据来源：通过查询外部API获取，本地维护关键阈值
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ============= 传导路径数据库 =============

# 地缘政治 → A股板块映射
GEOPOLITICAL_CHAINS = {
    # 中东冲突
    "中东冲突_升级": {
        "intensity_base": 4,
        "chains": [
            {"to": "石油石化", "reason": "油价飙升", "impact": 0.9, "lag_days": 0},
            {"to": "军工", "reason": "地缘紧张提升国防预算预期", "impact": 0.5, "lag_days": 1},
            {"to": "黄金", "reason": "避险资金涌入", "impact": 0.8, "lag_days": 0},
            {"to": "航运", "reason": "航线受阻、运费上涨", "impact": 0.4, "lag_days": 2},
            {"to": "化工", "reason": "油价推高石化原料成本", "impact": 0.3, "lag_days": 3},
        ],
        "risk_assets": -0.6,  # 对风险资产的负面影响
    },
    "中东冲突_缓和": {
        "intensity_base": 3,
        "chains": [
            {"to": "石油石化", "reason": "油价回落预期", "impact": -0.7, "lag_days": 0},
            {"to": "航空", "reason": "燃料成本下降", "impact": 0.5, "lag_days": 1},
            {"to": "黄金", "reason": "避险退潮", "impact": -0.6, "lag_days": 0},
        ],
        "risk_assets": 0.4,
    },
    # 中美关系
    "中美_科技制裁升级": {
        "intensity_base": 4,
        "chains": [
            {"to": "半导体国产替代", "reason": "加速自主可控", "impact": 0.7, "lag_days": 0},
            {"to": "信创", "reason": "国产软件替代加速", "impact": 0.6, "lag_days": 0},
            {"to": "光模块", "reason": "出口管制风险", "impact": -0.3, "lag_days": 1},
            {"to": "消费电子", "reason": "供应链中断风险", "impact": -0.4, "lag_days": 2},
        ],
        "risk_assets": -0.3,
    },
    "中美_缓和/会谈": {
        "intensity_base": 3,
        "chains": [
            {"to": "半导体", "reason": "制裁松动预期", "impact": 0.3, "lag_days": 0},
            {"to": "出口板块", "reason": "贸易预期改善", "impact": 0.5, "lag_days": 1},
            {"to": "光伏", "reason": "出口壁垒降低", "impact": 0.4, "lag_days": 1},
        ],
        "risk_assets": 0.4,
    },
    # 俄乌冲突
    "俄乌_升级": {
        "intensity_base": 3,
        "chains": [
            {"to": "军工", "reason": "地缘紧张", "impact": 0.5, "lag_days": 0},
            {"to": "农产品", "reason": "粮食出口受阻", "impact": 0.4, "lag_days": 2},
            {"to": "石油天然气", "reason": "能源供应担忧", "impact": 0.3, "lag_days": 0},
            {"to": "黄金", "reason": "避险", "impact": 0.5, "lag_days": 0},
        ],
        "risk_assets": -0.3,
    },
    # 台海局势
    "台海_紧张": {
        "intensity_base": 5,
        "chains": [
            {"to": "军工", "reason": "国防需求提升", "impact": 0.8, "lag_days": 0},
            {"to": "半导体", "reason": "台湾供应链中断风险", "impact": -0.6, "lag_days": 0},
            {"to": "大盘", "reason": "系统性风险", "impact": -0.5, "lag_days": 0},
        ],
        "risk_assets": -0.8,
    },
    # 朝鲜半岛
    "朝鲜_紧张": {
        "intensity_base": 2,
        "chains": [
            {"to": "军工", "reason": "地缘不确定性", "impact": 0.3, "lag_days": 0},
            {"to": "大盘", "reason": "短期情绪冲击", "impact": -0.1, "lag_days": 0},
        ],
        "risk_assets": -0.2,
    },
    # 中东_以色列黎巴嫩加沙
    "中东_全面战争": {
        "intensity_base": 5,
        "chains": [
            {"to": "石油石化", "reason": "霍尔木兹海峡威胁", "impact": 0.9, "lag_days": 0},
            {"to": "黄金", "reason": "极度避险", "impact": 0.9, "lag_days": 0},
            {"to": "军工", "reason": "全球国防开支增长", "impact": 0.7, "lag_days": 0},
            {"to": "航运", "reason": "红海/波斯湾航线受阻", "impact": 0.6, "lag_days": 1},
            {"to": "大盘", "reason": "全球风险资产下跌", "impact": -0.5, "lag_days": 0},
        ],
        "risk_assets": -0.7,
    },
    # 选举/政治
    "美国大选_不确定性": {
        "intensity_base": 2,
        "chains": [
            {"to": "出口板块", "reason": "贸易政策不确定", "impact": -0.2, "lag_days": 5},
        ],
        "risk_assets": -0.1,
    },
}

# 美元/美债 → A股传导
DOLLAR_BOND_CHAINS = {
    "美元走强": {
        "threshold": 105,  # 美元指数阈值
        "chains": [
            {"to": "北向资金", "reason": "人民币贬值压力", "impact": -0.5, "note": "北向流出"},
            {"to": "黄金", "reason": "美元计价承压", "impact": -0.4},
            {"to": "出口企业", "reason": "人民币贬值利好", "impact": 0.3},
            {"to": "航空", "reason": "美元债务成本上升", "impact": -0.3},
            {"to": "大盘", "reason": "新兴市场资金流出", "impact": -0.2},
        ],
    },
    "美元走弱": {
        "threshold": 100,
        "chains": [
            {"to": "北向资金", "reason": "人民币升值预期", "impact": 0.5, "note": "北向流入"},
            {"to": "黄金", "reason": "美元计价支撑", "impact": 0.4},
            {"to": "进口企业", "reason": "原材料成本下降", "impact": 0.3},
            {"to": "大盘", "reason": "新兴市场资金流入", "impact": 0.2},
        ],
    },
    "美债收益率飙升": {
        "threshold": 4.5,  # 10年期美债收益率
        "chains": [
            {"to": "成长股", "reason": "高利率压制远期现金流折现", "impact": -0.6},
            {"to": "科技股", "reason": "估值承压", "impact": -0.5},
            {"to": "银行", "reason": "息差扩大利好", "impact": 0.3},
            {"to": "大盘", "reason": "全球资金成本上升", "impact": -0.3},
        ],
    },
    "美债收益率下降": {
        "threshold": 4.0,
        "chains": [
            {"to": "成长股", "reason": "利率预期下降，估值抬升", "impact": 0.5},
            {"to": "科技股", "reason": "远期现金流现值上升", "impact": 0.5},
            {"to": "地产", "reason": "融资成本下降", "impact": 0.3},
        ],
    },
}

# 黄金 → 信号
GOLD_SIGNALS = {
    "黄金暴涨": {
        "threshold_24h": 3.0,  # 单日涨幅超过3%
        "meaning": "深度避险模式，市场极度恐慌",
        "impact_on_equity": -0.6,
        "chains": [
            {"to": "黄金股", "reason": "金价直接驱动", "impact": 0.9},
            {"to": "大盘", "reason": "避险抽血效应", "impact": -0.3},
        ],
    },
    "黄金温和上涨": {
        "threshold_days": [1.0, 3.0],  # 1-3%之间
        "meaning": "温和避险，资产配置轮动",
        "impact_on_equity": -0.1,
        "chains": [
            {"to": "黄金股", "reason": "金价驱动", "impact": 0.5},
        ],
    },
}

# 石油 → A股传导
OIL_CHAINS = {
    "油价暴涨": {
        "threshold_24h": 5.0,
        "chains": [
            {"to": "石油石化", "reason": "直接受益", "impact": 0.8},
            {"to": "新能源", "reason": "替代能源逻辑", "impact": 0.4, "lag_days": 1},
            {"to": "航空航运", "reason": "成本飙升", "impact": -0.6},
            {"to": "化工", "reason": "原料成本上升", "impact": -0.3},
            {"to": "通胀预期", "reason": "输入性通胀", "impact": -0.3},
        ],
    },
    "油价暴跌": {
        "threshold_24h": -5.0,
        "chains": [
            {"to": "石油石化", "reason": "直接受损", "impact": -0.7},
            {"to": "航空", "reason": "燃料成本下降", "impact": 0.6},
            {"to": "化工", "reason": "原料成本下降", "impact": 0.3},
        ],
    },
}

# 美股 → A股映射（基于已验证的correlation）
US_A_MAPPING = {
    "NVDA": {
        "sectors": ["光模块", "AI算力", "半导体"],
        "stocks": ["中际旭创", "天孚通信", "新易盛", "工业富联"],
        "correlation_strength": 0.8,
        "lag": 0,  # 当日联动
        "note": "上一轮回测验证：第1次财报联动极强，当前已边际递减",
    },
    "AAPL": {
        "sectors": ["消费电子", "果链"],
        "stocks": ["立讯精密", "歌尔股份", "蓝思科技"],
        "correlation_strength": 0.6,
        "lag": 0,
    },
    "TSLA": {
        "sectors": ["新能源汽车", "自动驾驶", "机器人"],
        "stocks": ["宁德时代", "拓普集团", "三花智控"],
        "correlation_strength": 0.7,
        "lag": 0,
    },
    "MSFT": {
        "sectors": ["AI", "云计算", "软件"],
        "stocks": ["科大讯飞", "金山办公", "中科创达"],
        "correlation_strength": 0.5,
        "lag": 0,
    },
    "AMD": {
        "sectors": ["半导体", "AI芯片"],
        "stocks": ["海光信息", "寒武纪", "龙芯中科"],
        "correlation_strength": 0.6,
        "lag": 0,
    },
    "SMCI": {
        "sectors": ["AI服务器", "光模块"],
        "stocks": ["工业富联", "中际旭创", "浪潮信息"],
        "correlation_strength": 0.7,
        "lag": 0,
    },
    "PLTR": {
        "sectors": ["军工信息化", "AI应用"],
        "stocks": [],
        "correlation_strength": 0.4,
        "lag": 1,
    },
}

# 中国金龙指数 → A股映射
CHINA_INDEX_MAPPING = {
    "中概股上涨": {"impact_on_a": 0.4, "lag_days": 0, "note": "港股情绪传导至A股"},
    "中概股暴跌": {"impact_on_a": -0.5, "lag_days": 0, "note": "通常带动A股低开"},
}

# ============= 核心分析函数 =============

def assess_geopolitical_event(event_name: str, intensity_modifier: float = 1.0) -> Dict:
    """
    评估地缘政治事件的传导影响

    Args:
        event_name: 事件名称（匹配GEOPOLITICAL_CHAINS中的键）
        intensity_modifier: 强度修正系数（默认1.0，可调0.3-2.0）

    Returns:
        评估结果字典
    """
    if event_name not in GEOPOLITICAL_CHAINS:
        return {
            "event": event_name,
            "error": f"未知事件，已知事件: {list(GEOPOLITICAL_CHAINS.keys())}"
        }

    template = GEOPOLITICAL_CHAINS[event_name]
    adjusted_intensity = min(template["intensity_base"] * intensity_modifier, 5.0)

    impacts = []
    total_impact_score = 0
    for chain in template["chains"]:
        impact = {
            "target": chain["to"],
            "impact": round(chain["impact"] * intensity_modifier, 2),
            "reason": chain["reason"],
            "direction": "利好" if chain["impact"] > 0 else "利空",
            "lag_days": chain.get("lag_days", 0),
        }
        impacts.append(impact)
        total_impact_score += abs(impact["impact"]) * (1.0 if impact["impact"] > 0 else -0.5)

    return {
        "layer": "global_macro",
        "type": "geopolitical",
        "event": event_name,
        "intensity": round(adjusted_intensity, 1),
        "risk_assets_impact": template["risk_assets"] * intensity_modifier,
        "impacts": impacts,
        "total_impact_score": round(total_impact_score, 2),
        "timestamp": datetime.now().isoformat(),
    }


def assess_dollar_index(dxy: float, change_pct: float) -> Dict:
    """
    评估美元指数对A股的影响

    Args:
        dxy: 美元指数当前值
        change_pct: 当日变动百分比
    """
    if dxy > DOLLAR_BOND_CHAINS["美元走强"]["threshold"]:
        regime = "强美元"
        template = DOLLAR_BOND_CHAINS["美元走强"]
    elif dxy < DOLLAR_BOND_CHAINS["美元走弱"]["threshold"]:
        regime = "弱美元"
        template = DOLLAR_BOND_CHAINS["美元走弱"]
    else:
        regime = "中性"
        template = None

    impacts = []
    if template:
        for chain in template["chains"]:
            impacts.append({
                "target": chain["to"],
                "impact": chain["impact"],
                "direction": "利好" if chain["impact"] > 0 else "利空",
                "reason": chain.get("reason", ""),
                "note": chain.get("note", ""),
            })

    return {
        "layer": "global_macro",
        "type": "dollar_index",
        "dxy": dxy,
        "change_pct": change_pct,
        "regime": regime,
        "impacts": impacts,
        "timestamp": datetime.now().isoformat(),
    }


def assess_us_bond_yield(yield_10y: float, change_bps: int) -> Dict:
    """
    评估美债收益率变化的影响

    Args:
        yield_10y: 10年期美债收益率（%）
        change_bps: 当日变动（bps）
    """
    if yield_10y > DOLLAR_BOND_CHAINS["美债收益率飙升"]["threshold"]:
        regime = "高利率"
        template = DOLLAR_BOND_CHAINS["美债收益率飙升"]
    elif yield_10y < DOLLAR_BOND_CHAINS["美债收益率下降"]["threshold"]:
        regime = "低利率"
        template = DOLLAR_BOND_CHAINS["美债收益率下降"]
    else:
        regime = "中性"
        template = None

    impacts = []
    if template:
        for chain in template["chains"]:
            impacts.append({
                "target": chain["to"],
                "impact": chain["impact"],
                "direction": "利好" if chain["impact"] > 0 else "利空",
                "reason": chain.get("reason", ""),
            })

    return {
        "layer": "global_macro",
        "type": "us_bond_yield",
        "yield_10y": yield_10y,
        "change_bps": change_bps,
        "regime": regime,
        "impacts": impacts,
        "timestamp": datetime.now().isoformat(),
    }


def assess_gold(gold_price: float, change_pct_24h: float) -> Dict:
    """
    评估黄金价格变化信号
    """
    signal = None
    impact_on_equity = 0

    if change_pct_24h >= GOLD_SIGNALS["黄金暴涨"]["threshold_24h"]:
        signal = "黄金暴涨"
        impact_on_equity = GOLD_SIGNALS["黄金暴涨"]["impact_on_equity"]
        template = GOLD_SIGNALS["黄金暴涨"]
    elif change_pct_24h >= GOLD_SIGNALS["黄金温和上涨"]["threshold_days"][0]:
        signal = "黄金温和上涨"
        impact_on_equity = GOLD_SIGNALS["黄金温和上涨"]["impact_on_equity"]
        template = GOLD_SIGNALS["黄金温和上涨"]
    else:
        signal = "正常波动"
        template = None

    impacts = []
    if template:
        for chain in template["chains"]:
            impacts.append({
                "target": chain["to"],
                "impact": chain["impact"],
                "direction": "利好" if chain["impact"] > 0 else "利空",
                "reason": chain.get("reason", ""),
            })

    return {
        "layer": "global_macro",
        "type": "gold",
        "gold_price": gold_price,
        "change_pct_24h": change_pct_24h,
        "signal": signal,
        "impact_on_equity": impact_on_equity,
        "impacts": impacts,
        "timestamp": datetime.now().isoformat(),
    }


def assess_oil(oil_price: float, change_pct_24h: float) -> Dict:
    """
    评估石油价格变化的影响
    """
    regime = "正常波动"
    template = None

    if change_pct_24h >= OIL_CHAINS["油价暴涨"]["threshold_24h"]:
        regime = "油价暴涨"
        template = OIL_CHAINS["油价暴涨"]
    elif change_pct_24h <= OIL_CHAINS["油价暴跌"]["threshold_24h"]:
        regime = "油价暴跌"
        template = OIL_CHAINS["油价暴跌"]

    impacts = []
    if template:
        for chain in template["chains"]:
            impacts.append({
                "target": chain["to"],
                "impact": chain["impact"],
                "direction": "利好" if chain["impact"] > 0 else "利空",
                "reason": chain.get("reason", ""),
                "lag_days": chain.get("lag_days", 0),
            })

    return {
        "layer": "global_macro",
        "type": "oil",
        "oil_price": oil_price,
        "change_pct_24h": change_pct_24h,
        "regime": regime,
        "impacts": impacts,
        "timestamp": datetime.now().isoformat(),
    }


def assess_us_stock_ticker(ticker: str, change_pct: float) -> Dict:
    """
    评估美股个股涨跌对A股的映射影响

    Args:
        ticker: 美股代码（如 NVDA, TSLA）
        change_pct: 涨跌百分比
    """
    if ticker not in US_A_MAPPING:
        return {"ticker": ticker, "error": "无映射数据"}

    mapping = US_A_MAPPING[ticker]
    strength = mapping["correlation_strength"]
    # 边际递减：如果消息面出现次数多，联动强度打折
    # （这里暂时用固定值，后续可以对接消息次数数据库）

    directed_impact = change_pct * 0.3 * strength  # 衰减系数0.3

    return {
        "layer": "global_macro",
        "type": "us_stock_mapping",
        "ticker": ticker,
        "change_pct": change_pct,
        "correlation_strength": strength,
        "directed_impact_on_a": round(directed_impact / 10, 2),  # 归一化到[-1,1]
        "affected_sectors": mapping["sectors"],
        "affected_stocks": mapping["stocks"],
        "note": mapping.get("note", ""),
        "timestamp": datetime.now().isoformat(),
    }


def assess_macro_composite(
    dxy: Optional[float] = None,
    us_bond_10y: Optional[float] = None,
    gold_price: Optional[float] = None,
    oil_price: Optional[float] = None,
    geopolitical_events: Optional[List[str]] = None,
    us_stocks: Optional[Dict[str, float]] = None,
) -> Dict:
    """
    宏观层综合评估——汇总所有信号，产出大盘级别的综合判断

    这是外部调用的主入口
    """
    all_signals = []
    sector_impacts = {}  # 累计板块影响
    total_risk_score = 0
    total_opportunity_score = 0

    # 1. 地缘政治
    if geopolitical_events:
        for event in geopolitical_events:
            # 支持 dict 格式传入自定义事件
            if isinstance(event, dict):
                result = assess_geopolitical_event(
                    event["name"],
                    event.get("intensity_modifier", 1.0)
                )
            else:
                result = assess_geopolitical_event(event)
            all_signals.append(result)
            for impact in result.get("impacts", []):
                target = impact["target"]
                if target not in sector_impacts:
                    sector_impacts[target] = {"positive": 0, "negative": 0}
                if impact["impact"] > 0:
                    sector_impacts[target]["positive"] += impact["impact"]
                else:
                    sector_impacts[target]["negative"] += abs(impact["impact"])

    # 2. 美元
    if dxy is not None:
        dollar_result = assess_dollar_index(dxy, 0)  # change_pct 需要外部输入
        all_signals.append(dollar_result)
        for impact in dollar_result.get("impacts", []):
            target = impact["target"]
            if target not in sector_impacts:
                sector_impacts[target] = {"positive": 0, "negative": 0}
            if impact["impact"] > 0:
                sector_impacts[target]["positive"] += impact["impact"]
            else:
                sector_impacts[target]["negative"] += abs(impact["impact"])

    # 3. 美债
    if us_bond_10y is not None:
        bond_result = assess_us_bond_yield(us_bond_10y, 0)
        all_signals.append(bond_result)
        for impact in bond_result.get("impacts", []):
            target = impact["target"]
            if target not in sector_impacts:
                sector_impacts[target] = {"positive": 0, "negative": 0}
            if impact["impact"] > 0:
                sector_impacts[target]["positive"] += impact["impact"]
            else:
                sector_impacts[target]["negative"] += abs(impact["impact"])

    # 4. 黄金
    if gold_price is not None:
        gold_result = assess_gold(gold_price, 0)
        all_signals.append(gold_result)
        total_risk_score += abs(gold_result.get("impact_on_equity", 0))

    # 5. 石油
    if oil_price is not None:
        oil_result = assess_oil(oil_price, 0)
        all_signals.append(oil_result)

    # 6. 美股映射
    if us_stocks:
        for ticker, change in us_stocks.items():
            us_result = assess_us_stock_ticker(ticker, change)
            all_signals.append(us_result)

    # 综合评分
    net_sector_score = sum(
        v["positive"] - v["negative"]
        for v in sector_impacts.values()
    )

    # 判断整体环境
    if net_sector_score > 2:
        overall = "偏利好——全球宏观环境支撑A股"
    elif net_sector_score < -2:
        overall = "偏利空——全球宏观环境压力A股"
    else:
        overall = "中性——全球宏观无明显方向"

    return {
        "layer": "global_macro",
        "type": "composite",
        "signals_count": len(all_signals),
        "net_sector_score": round(net_sector_score, 2),
        "overall_assessment": overall,
        "sector_impacts": dict(sorted(
            sector_impacts.items(),
            key=lambda x: x[1]["positive"] - x[1]["negative"],
            reverse=True
        )),
        "top_positive_sectors": [
            s for s, v in sorted(
                sector_impacts.items(),
                key=lambda x: x[1]["positive"] - x[1]["negative"],
                reverse=True
            )[:5]
            if v["positive"] > v["negative"]
        ],
        "top_negative_sectors": [
            s for s, v in sorted(
                sector_impacts.items(),
                key=lambda x: x[1]["negative"] - x[1]["positive"],
                reverse=True
            )[:5]
            if v["negative"] > v["positive"]
        ],
        "all_signals": all_signals,
        "risk_assets_direction": net_sector_score / 10,
        "timestamp": datetime.now().isoformat(),
    }


# ============= 标准接口（供中央编排器调用） =============

def run_layer(config: Dict = None) -> Dict:
    """
    运行全球宏观层分析
    在无实时数据时使用默认/模板数据返回结构化的框架结果

    生产环境中应从外部API获取真实数据
    """
    if config is None:
        config = {}

    result = assess_macro_composite(
        dxy=config.get("dxy"),
        us_bond_10y=config.get("us_bond_10y"),
        gold_price=config.get("gold_price"),
        oil_price=config.get("oil_price"),
        geopolitical_events=config.get("geopolitical_events"),
        us_stocks=config.get("us_stocks"),
    )
    return result


if __name__ == "__main__":
    # 测试
    print("=== 全球宏观层引擎测试 ===")

    # 测试地缘政治
    print("\n1. 中东冲突升级测试:")
    result = assess_geopolitical_event("中东冲突_升级", 1.2)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n2. 美元指数测试:")
    result = assess_dollar_index(106, 0.5)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n3. 美债测试:")
    result = assess_us_bond_yield(4.7, 15)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n4. 美股NVDA映射测试:")
    result = assess_us_stock_ticker("NVDA", 5.2)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n5. 综合测试:")
    result = assess_macro_composite(
        dxy=106,
        us_bond_10y=4.7,
        geopolitical_events=["中东冲突_升级", "中美_科技制裁升级"],
        us_stocks={"NVDA": 3.5, "TSLA": -2.1},
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
