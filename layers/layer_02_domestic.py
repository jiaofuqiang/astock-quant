"""
🇨🇳 国内宏观层引擎 (Layer 2)
====================================
关注：货币政策、财政政策、产业政策、监管态度、经济数据

每一个国内宏观因素，都要判断：
1. 事件性质（货币政策/财政/产业/监管/经济数据）
2. 影响方向（宽松/收紧/中性、利好/利空）
3. 传导路径（影响哪些板块）
4. 预期差（预期vs现实，超预期/符合/不及预期）
5. 时间尺度（脉冲/短期/中期/长期）

生产环境：需接入财联社/同花顺/东方财富的实时政策推送
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ============= 传导路径数据库 =============

# 货币政策事件 → 影响
MONETARY_POLICY_EVENTS = {
    "降准": {
        "direction": "宽松",
        "intensity_base": 4,
        "chains": [
            {"to": "银行", "reason": "可贷资金增加", "impact": 0.5, "lag_days": 1},
            {"to": "地产", "reason": "融资环境改善", "impact": 0.5, "lag_days": 1},
            {"to": "券商", "reason": "流动性改善提振市场", "impact": 0.6, "lag_days": 0},
            {"to": "大盘", "reason": "释放流动性信号", "impact": 0.4, "lag_days": 0},
        ],
        "note": "降准首次最有效，连续降准递减",
    },
    "降息(LPR/MLF)": {
        "direction": "宽松",
        "intensity_base": 5,
        "chains": [
            {"to": "地产", "reason": "融资成本下降", "impact": 0.7, "lag_days": 0},
            {"to": "高负债行业", "reason": "财务费用下降", "impact": 0.5, "lag_days": 1},
            {"to": "成长股", "reason": "折现率下降估值抬升", "impact": 0.6, "lag_days": 0},
            {"to": "银行", "reason": "息差承压", "impact": -0.3, "lag_days": 2},
            {"to": "大盘", "reason": "强力流动性信号", "impact": 0.5, "lag_days": 0},
        ],
        "note": "非对称降息（1年期vs5年期）影响不同",
    },
    "加息/收紧": {
        "direction": "收紧",
        "intensity_base": 5,
        "chains": [
            {"to": "地产", "reason": "融资成本上升", "impact": -0.6, "lag_days": 0},
            {"to": "成长股", "reason": "估值承压", "impact": -0.5, "lag_days": 0},
            {"to": "银行", "reason": "息差扩大", "impact": 0.2, "lag_days": 3},
            {"to": "大盘", "reason": "流动性收紧", "impact": -0.4, "lag_days": 0},
        ],
    },
    "逆回购/MLF超量续作": {
        "direction": "宽松",
        "intensity_base": 2,
        "chains": [
            {"to": "银行间流动性", "reason": "短期资金面改善", "impact": 0.3, "lag_days": 0},
            {"to": "大盘", "reason": "维稳信号", "impact": 0.2, "lag_days": 0},
        ],
        "note": "公开市场操作的信号意义",
    },
    "创设新货币政策工具": {
        "direction": "宽松",
        "intensity_base": 4,
        "chains": [
            {"to": "股市", "reason": "增量资金入市预期", "impact": 0.6, "lag_days": 0},
            {"to": "券商", "reason": "交易活跃度提升预期", "impact": 0.5, "lag_days": 0},
            {"to": "大盘", "reason": "政策底确认", "impact": 0.5, "lag_days": 0},
        ],
        "note": "如互换便利、SFISF等创新工具"
    },
}

# 财政政策事件
FISCAL_POLICY_EVENTS = {
    "大规模财政刺激": {
        "direction": "扩张",
        "intensity_base": 5,
        "chains": [
            {"to": "基建", "reason": "专项债/特别国债直接受益", "impact": 0.8, "lag_days": 0},
            {"to": "建材", "reason": "需求拉动", "impact": 0.7, "lag_days": 1},
            {"to": "工程机械", "reason": "开工率提升", "impact": 0.6, "lag_days": 2},
            {"to": "消费", "reason": "补贴/发钱刺激", "impact": 0.5, "lag_days": 2},
            {"to": "大盘", "reason": "增长预期改善", "impact": 0.4, "lag_days": 0},
        ],
    },
    "减税降费": {
        "direction": "扩张",
        "intensity_base": 3,
        "chains": [
            {"to": "中小盘", "reason": "利润弹性更大", "impact": 0.5, "lag_days": 2},
            {"to": "消费", "reason": "可支配收入提升预期", "impact": 0.3, "lag_days": 3},
            {"to": "制造业", "reason": "成本下降", "impact": 0.4, "lag_days": 2},
        ],
    },
    "专项债加速发行": {
        "direction": "扩张",
        "intensity_base": 3,
        "chains": [
            {"to": "基建", "reason": "资金到位加速", "impact": 0.6, "lag_days": 1},
            {"to": "地方城投", "reason": "融资改善", "impact": 0.4, "lag_days": 2},
        ],
    },
    "消费补贴/以旧换新": {
        "direction": "定向刺激",
        "intensity_base": 3,
        "chains": [
            {"to": "家电", "reason": "换新需求释放", "impact": 0.7, "lag_days": 1},
            {"to": "汽车", "reason": "购置税减免/补贴", "impact": 0.6, "lag_days": 1},
            {"to": "消费电子", "reason": "补贴刺激换机", "impact": 0.5, "lag_days": 1},
        ],
    },
}

# 产业政策
INDUSTRIAL_POLICY_EVENTS = {
    "新能源_重大利好": {
        "intensity_base": 4,
        "chains": [
            {"to": "光伏", "reason": "装机目标提升/补贴", "impact": 0.8},
            {"to": "风电", "reason": "海风规划/补贴", "impact": 0.7},
            {"to": "储能", "reason": "配套政策", "impact": 0.6},
            {"to": "电动车", "reason": "渗透率目标", "impact": 0.5},
        ],
    },
    "半导体_重大利好": {
        "intensity_base": 5,
        "chains": [
            {"to": "半导体设备", "reason": "国产替代资金支持", "impact": 0.8},
            {"to": "半导体材料", "reason": "国产配套", "impact": 0.7},
            {"to": "芯片设计", "reason": "税收优惠", "impact": 0.6},
            {"to": "AI芯片", "reason": "算力基建政策", "impact": 0.7},
        ],
    },
    "AI_重大利好": {
        "intensity_base": 4,
        "chains": [
            {"to": "AI算力", "reason": "算力基建规划", "impact": 0.8},
            {"to": "AI应用", "reason": "行业赋能政策", "impact": 0.6},
            {"to": "光模块", "reason": "算力传输需求", "impact": 0.5},
            {"to": "数据要素", "reason": "数据资产化", "impact": 0.5},
        ],
    },
    "低空经济_利好": {
        "intensity_base": 4,
        "chains": [
            {"to": "eVTOL", "reason": "适航认证加速", "impact": 0.8},
            {"to": "无人机", "reason": "空域开放", "impact": 0.7},
            {"to": "低空基建", "reason": "起降场/通信", "impact": 0.6},
        ],
    },
    "机器人_利好": {
        "intensity_base": 4,
        "chains": [
            {"to": "人形机器人", "reason": "产业化政策", "impact": 0.7},
            {"to": "减速器/执行器", "reason": "核心零部件国产化", "impact": 0.6},
            {"to": "机器视觉", "reason": "AI+机器人", "impact": 0.5},
        ],
    },
    "信创_利好": {
        "intensity_base": 3,
        "chains": [
            {"to": "CPU/GPU", "reason": "国产化替代", "impact": 0.7},
            {"to": "操作系统", "reason": "国产OS推广", "impact": 0.6},
            {"to": "数据库", "reason": "国产数据库替代", "impact": 0.5},
            {"to": "信息安全", "reason": "等保/密评", "impact": 0.5},
        ],
    },
    "房地产_放松限购": {
        "intensity_base": 3,
        "chains": [
            {"to": "地产开发", "reason": "需求端放松", "impact": 0.6},
            {"to": "家居建材", "reason": "后周期需求", "impact": 0.4},
            {"to": "银行", "reason": "贷款风险降低", "impact": 0.3},
        ],
    },
    "老龄化/医疗_利好": {
        "intensity_base": 3,
        "chains": [
            {"to": "创新药", "reason": "审批加速/医保支持", "impact": 0.6},
            {"to": "医疗器械", "reason": "国产替代+集采缓和", "impact": 0.5},
            {"to": "养老", "reason": "银发经济政策", "impact": 0.5},
        ],
    },
}

# 监管事件
REGULATORY_EVENTS = {
    "证监会_强监管/严查": {
        "direction": "收紧",
        "intensity_base": 3,
        "chains": [
            {"to": "大盘", "reason": "短线资金撤退", "impact": -0.3},
            {"to": "ST/问题股", "reason": "退市风险", "impact": -0.5},
            {"to": "次新股/壳资源", "reason": "炒作降温", "impact": -0.4},
            {"to": "绩优蓝筹", "reason": "资金避险流入", "impact": 0.3},
        ],
    },
    "IPO_提速/减速": {
        "direction": "收紧" if False else "宽松",
        "intensity_base": 2,
        "chains": [
            {"to": "大盘", "reason": "抽血效应/减缓", "impact": -0.2 if True else 0.2},
            {"to": "次新股", "reason": "稀缺性/泛滥", "impact": 0.3 if False else -0.2},
        ],
        "note": "IPO节奏对流动性的影响",
    },
    "北向资金_交易限制": {
        "direction": "收紧",
        "intensity_base": 3,
        "chains": [
            {"to": "大盘", "reason": "外资流出风险", "impact": -0.4},
            {"to": "北向重仓股", "reason": "直接受影响", "impact": -0.5},
        ],
    },
    "量化交易_监管": {
        "direction": "收紧",
        "intensity_base": 3,
        "chains": [
            {"to": "大盘", "reason": "高频交易受限利好散户", "impact": 0.2},
            {"to": "券商", "reason": "交易量下降", "impact": -0.3},
        ],
    },
}

# 经济数据 → 市场解读
ECONOMIC_DATA = {
    "PMI超预期": {
        "threshold": 50.5,
        "impact": 0.4,
        "chains": [
            {"to": "顺周期板块", "reason": "经济企稳信号", "impact": 0.5},
            {"to": "大盘", "reason": "基本面改善预期", "impact": 0.4},
        ],
    },
    "PMI不及预期": {
        "threshold": 49.5,
        "impact": -0.3,
        "chains": [
            {"to": "顺周期", "reason": "经济走弱", "impact": -0.4},
            {"to": "债市", "reason": "宽松预期升温", "impact": 0.3},
        ],
    },
    "GDP超预期": {"impact": 0.4, "chains": [{"to": "大盘", "impact": 0.4}]},
    "GDP不及预期": {"impact": -0.3, "chains": [{"to": "大盘", "impact": -0.3}]},
    "社融超预期": {"impact": 0.5, "chains": [{"to": "银行", "impact": 0.4}, {"to": "大盘", "impact": 0.3}]},
    "社融不及预期": {"impact": -0.4, "chains": [{"to": "大盘", "impact": -0.3}]},
    "CPI超预期": {"impact": -0.3, "chains": [{"to": "消费", "impact": 0.3}, {"to": "大盘", "impact": -0.1}]},
    "CPI低于预期": {"impact": 0.2, "chains": [{"to": "消费", "impact": -0.2}]},
}

# 政策预期差评分
EXPECTATION_LEVELS = {
    "远超预期": 1.5,  # 强度乘数
    "略超预期": 1.2,
    "符合预期": 1.0,
    "略不及预期": 0.7,
    "远不及预期": 0.3,
}


def assess_monetary_event(event_name: str, expectation: str = "符合预期",
                          intensity_modifier: float = 1.0) -> Dict:
    """评估货币政策事件"""
    if event_name not in MONETARY_POLICY_EVENTS:
        return {"error": f"未知政策事件: {event_name}"}

    template = MONETARY_POLICY_EVENTS[event_name]
    exp_mult = EXPECTATION_LEVELS.get(expectation, 1.0)
    adjusted_intensity = min(template["intensity_base"] * intensity_modifier * exp_mult, 5.0)

    impacts = []
    for chain in template["chains"]:
        impacts.append({
            "target": chain["to"],
            "impact": round(chain["impact"] * intensity_modifier * exp_mult, 2),
            "direction": "利好" if chain["impact"] > 0 else "利空",
            "reason": chain["reason"],
            "lag_days": chain.get("lag_days", 0),
        })

    # 综合判断
    net_score = round(sum(i["impact"] for i in impacts), 2)

    return {
        "layer": "domestic_macro",
        "type": "monetary_policy",
        "event": event_name,
        "direction": template["direction"],
        "intensity": round(adjusted_intensity, 1),
        "expectation": expectation,
        "net_score": net_score,
        "impacts": impacts,
        "note": template.get("note", ""),
        "timestamp": datetime.now().isoformat(),
    }


def assess_fiscal_event(event_name: str, expectation: str = "符合预期",
                         intensity_modifier: float = 1.0) -> Dict:
    """评估财政政策事件"""
    if event_name not in FISCAL_POLICY_EVENTS:
        return {"error": f"未知财政政策: {event_name}"}

    template = FISCAL_POLICY_EVENTS[event_name]
    exp_mult = EXPECTATION_LEVELS.get(expectation, 1.0)
    adjusted_intensity = min(template["intensity_base"] * intensity_modifier * exp_mult, 5.0)

    impacts = []
    for chain in template["chains"]:
        impacts.append({
            "target": chain["to"],
            "impact": round(chain["impact"] * intensity_modifier * exp_mult, 2),
            "direction": "利好" if chain["impact"] > 0 else "利空",
            "reason": chain["reason"],
            "lag_days": chain.get("lag_days", 0),
        })

    return {
        "layer": "domestic_macro",
        "type": "fiscal_policy",
        "event": event_name,
        "direction": template["direction"],
        "intensity": round(adjusted_intensity, 1),
        "net_score": round(sum(i["impact"] for i in impacts), 2),
        "impacts": impacts,
        "timestamp": datetime.now().isoformat(),
    }


def assess_industrial_policy(event_name: str, expectation: str = "符合预期",
                              intensity_modifier: float = 1.0) -> Dict:
    """评估产业政策事件"""
    if event_name not in INDUSTRIAL_POLICY_EVENTS:
        return {"error": f"未知产业政策: {event_name}"}

    template = INDUSTRIAL_POLICY_EVENTS[event_name]
    exp_mult = EXPECTATION_LEVELS.get(expectation, 1.0)

    impacts = []
    for chain in template["chains"]:
        impacts.append({
            "target": chain["to"],
            "impact": round(chain["impact"] * intensity_modifier * exp_mult, 2),
            "direction": "利好" if chain["impact"] > 0 else "利空",
            "reason": chain.get("reason", ""),
        })

    return {
        "layer": "domestic_macro",
        "type": "industrial_policy",
        "event": event_name,
        "intensity": round(template["intensity_base"] * intensity_modifier * exp_mult, 1),
        "net_score": round(sum(i["impact"] for i in impacts), 2),
        "impacts": impacts,
        "timestamp": datetime.now().isoformat(),
    }


def assess_regulatory_event(event_name: str) -> Dict:
    """评估监管态度事件"""
    if event_name not in REGULATORY_EVENTS:
        return {"error": f"未知监管事件: {event_name}"}

    template = REGULATORY_EVENTS[event_name]
    impacts = []
    for chain in template["chains"]:
        impacts.append({
            "target": chain["to"],
            "impact": chain["impact"],
            "direction": "利好" if chain["impact"] > 0 else "利空",
            "reason": chain.get("reason", ""),
        })

    return {
        "layer": "domestic_macro",
        "type": "regulatory",
        "event": event_name,
        "direction": template.get("direction", "中性"),
        "intensity": template["intensity_base"],
        "net_score": round(sum(i["impact"] for i in impacts), 2),
        "impacts": impacts,
        "note": template.get("note", ""),
        "timestamp": datetime.now().isoformat(),
    }


def assess_economic_data(data_name: str, actual_value: float) -> Dict:
    """评估经济数据"""
    if data_name not in ECONOMIC_DATA:
        return {"error": f"未知经济数据: {data_name}"}

    template = ECONOMIC_DATA[data_name]
    threshold = template.get("threshold")

    # 简单判断是否超预期
    is_surprise = True
    if threshold is not None:
        if "超预期" in data_name:
            is_surprise = actual_value >= threshold
        elif "不及预期" in data_name:
            is_surprise = actual_value <= threshold

    impacts = []
    for chain in template["chains"]:
        impacts.append({
            "target": chain["to"],
            "impact": chain["impact"] if is_surprise else chain["impact"] * -0.5,
            "direction": "利好" if chain["impact"] > 0 else "利空",
        })

    return {
        "layer": "domestic_macro",
        "type": "economic_data",
        "data_name": data_name,
        "actual_value": actual_value,
        "is_confirmed": is_surprise,
        "net_score": round(template["impact"], 2),
        "impacts": impacts,
        "timestamp": datetime.now().isoformat(),
    }


def run_layer(config: Dict = None) -> Dict:
    """
    国内宏观层综合评估
    汇总所有国内宏观信号

    生产环境：从新闻API/财联社获取当天的政策事件
    """
    if config is None:
        config = {}

    all_signals = []
    sector_impacts = {}

    # 货币政策
    for event in config.get("monetary_events", []):
        result = assess_monetary_event(
            event.get("name"),
            event.get("expectation", "符合预期"),
            event.get("intensity_modifier", 1.0),
        )
        all_signals.append(result)
        for impact in result.get("impacts", []):
            target = impact["target"]
            if target not in sector_impacts:
                sector_impacts[target] = {"positive": 0, "negative": 0}
            if impact["impact"] > 0:
                sector_impacts[target]["positive"] += impact["impact"]
            else:
                sector_impacts[target]["negative"] += abs(impact["impact"])

    # 财政政策
    for event in config.get("fiscal_events", []):
        result = assess_fiscal_event(
            event.get("name"),
            event.get("expectation", "符合预期"),
            event.get("intensity_modifier", 1.0),
        )
        all_signals.append(result)
        for impact in result.get("impacts", []):
            target = impact["target"]
            if target not in sector_impacts:
                sector_impacts[target] = {"positive": 0, "negative": 0}
            if impact["impact"] > 0:
                sector_impacts[target]["positive"] += impact["impact"]
            else:
                sector_impacts[target]["negative"] += abs(impact["impact"])

    # 产业政策
    for event in config.get("industrial_events", []):
        result = assess_industrial_policy(
            event.get("name"),
            event.get("expectation", "符合预期"),
            event.get("intensity_modifier", 1.0),
        )
        all_signals.append(result)
        for impact in result.get("impacts", []):
            target = impact["target"]
            if target not in sector_impacts:
                sector_impacts[target] = {"positive": 0, "negative": 0}
            if impact["impact"] > 0:
                sector_impacts[target]["positive"] += impact["impact"]
            else:
                sector_impacts[target]["negative"] += abs(impact["impact"])

    # 监管
    for event in config.get("regulatory_events", []):
        result = assess_regulatory_event(event)
        all_signals.append(result)

    # 经济数据
    for item in config.get("economic_data", []):
        result = assess_economic_data(item["name"], item["value"])
        all_signals.append(result)

    net_score = round(
        sum(v["positive"] - v["negative"] for v in sector_impacts.values()),
        2
    )

    return {
        "layer": "domestic_macro",
        "type": "composite",
        "signals_count": len(all_signals),
        "net_score": net_score,
        "overall": "政策友好" if net_score > 1.5 else "政策中性" if net_score > -1.5 else "政策压力",
        "sector_impacts": dict(sorted(
            sector_impacts.items(),
            key=lambda x: x[1]["positive"] - x[1]["negative"],
            reverse=True
        )[:8]),
        "all_signals": all_signals,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    print("=== 国内宏观层引擎测试 ===")

    print("\n1. 降准测试:")
    r = assess_monetary_event("降准", "略超预期")
    print(json.dumps(r, indent=2, ensure_ascii=False))

    print("\n2. AI产业政策测试:")
    r = assess_industrial_policy("AI_重大利好", "远超预期", 1.2)
    print(json.dumps(r, indent=2, ensure_ascii=False))

    print("\n3. 综合测试:")
    r = run_layer({
        "monetary_events": [{"name": "降准", "expectation": "符合预期"}],
        "industrial_events": [{"name": "半导体_重大利好", "expectation": "略超预期"}],
        "regulatory_events": ["证监会_强监管/严查"],
        "economic_data": [{"name": "PMI超预期", "value": 51.2}],
    })
    print(json.dumps(r, indent=2, ensure_ascii=False))
