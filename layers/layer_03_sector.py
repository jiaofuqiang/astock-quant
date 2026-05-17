"""
🏭 板块/题材层引擎 (Layer 3)
=============================
关注：一个题材从"萌芽→资金→订单→业绩"的完整生命周期

核心功能：
1. 题材热度评估（资金集中度、涨停家数、板块指数涨幅）
2. 题材生命周期定位（处于哪个阶段）
3. 题材内部结构（龙头是谁、跟风如何）
4. 题材与其他板块的联动/轮动关系

数据来源：
- 腾讯行情API（板块指数、个股涨幅）
- 龙虎榜数据（游资关注的板块）
- 用户手动输入的题材消息
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ============= 题材生命周期 =============

THEME_LIFECYCLE = {
    "萌芽期": {
        "order": 1,
        "description": "新技术/新路径/新规划刚出现，市场关注度低，少数先知先觉的资金介入",
        "typical_duration": "1-3天",
        "signals": ["个别个股异动", "研报开始覆盖", "初始消息出现"],
    },
    "发酵期": {
        "order": 2,
        "description": "消息扩散，市场开始关注，涨停家数增多，资金开始集中",
        "typical_duration": "3-7天",
        "signals": ["多只涨停", "龙虎榜机构介入", "媒体开始报道"],
    },
    "高潮期": {
        "order": 3,
        "description": "全民热议，涨停潮形成，龙头股打出高度，跟风股也大涨",
        "typical_duration": "5-15天",
        "signals": ["板块涨幅>10%", "成交量暴增", "全市场关注度第1"],
    },
    "分化期": {
        "order": 4,
        "description": "部分个股开始掉队，只剩龙头继续，资金开始分歧",
        "typical_duration": "3-7天",
        "signals": ["连板数降低", "分歧日大跌", "成交量萎缩"],
    },
    "退潮期": {
        "order": 5,
        "description": "龙头补跌，板块大幅回调，资金撤离去新题材",
        "typical_duration": "3-10天",
        "signals": ["板块普跌", "龙头跌停", "成交量持续萎缩"],
    },
    "二波/反抽": {
        "order": 3.5,
        "description": "退潮后出现新催化（新技术/订单/业绩），题材重启",
        "typical_duration": "3-5天",
        "signals": ["新消息催化", "老龙头反包", "新龙头接力"],
    },
}

# 核心题材数据库
MAIN_THEMES = {
    "AI": {
        "sub_domains": ["AI算力", "AI应用", "AI芯片", "AI办公", "AI教育", "AI医疗", "AI机器人"],
        "key_stocks": ["中际旭创", "天孚通信", "新易盛", "工业富联", "科大讯飞", "金山办公"],
        "current_lifecycle": None,  # 需实时判断
        "catalyst_count": 0,  # 消息出现次数
        "note": "2023-2025年最强主线，已完成多轮生命周期循环",
    },
    "光模块": {
        "sub_domains": ["光模块", "光器件", "光芯片", "光通信系统"],
        "key_stocks": ["中际旭创", "天孚通信", "新易盛", "光迅科技", "德科立"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "AI最直接受益环节，业绩最先兑现",
    },
    "半导体": {
        "sub_domains": ["半导体设备", "半导体材料", "芯片设计", "封测", "EDA"],
        "key_stocks": ["北方华创", "中微公司", "海光信息", "中芯国际", "华大九天"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "国产替代+AI芯片双逻辑",
    },
    "低空经济": {
        "sub_domains": ["eVTOL", "无人机", "低空基建", "空管系统"],
        "key_stocks": ["亿航智能", "万丰奥威", "中信海直", "莱斯信息"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "2024年新题材，政策驱动型",
    },
    "机器人": {
        "sub_domains": ["人形机器人", "工业机器人", "核心零部件"],
        "key_stocks": ["拓普集团", "三花智控", "绿的谐波", "鸣志电器"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "AI+制造业，特斯拉带动",
    },
    "新能源": {
        "sub_domains": ["光伏", "风电", "锂电池", "储能", "电动车"],
        "key_stocks": ["宁德时代", "比亚迪", "隆基绿能", "阳光电源"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "成熟板块，周期性特征明显",
    },
    "信创": {
        "sub_domains": ["CPU/GPU", "操作系统", "数据库", "信息安全", "办公软件"],
        "key_stocks": ["中国软件", "金山办公", "中科曙光", "海光信息"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "国产替代逻辑，政策驱动",
    },
    "军工": {
        "sub_domains": ["航空", "航天", "舰船", "电子对抗", "无人机"],
        "key_stocks": ["中航沈飞", "航发动力", "中航光电"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "地缘政治驱动+订单驱动",
    },
    "消费电子": {
        "sub_domains": ["果链", "安卓链", "可穿戴", "AR/VR"],
        "key_stocks": ["立讯精密", "歌尔股份", "蓝思科技"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "手机周期+创新周期叠加",
    },
    "创新药": {
        "sub_domains": ["创新药", "CXO", "生物药"],
        "key_stocks": ["恒瑞医药", "药明康德", "百济神州"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "人口老龄化+出海逻辑",
    },
    "地产": {
        "sub_domains": ["地产开发", "物业管理", "家居", "建材"],
        "key_stocks": ["万科A", "保利发展"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "政策托底，困境反转",
    },
    "证券": {
        "sub_domains": ["券商", "金融科技"],
        "key_stocks": ["中信证券", "东方财富", "同花顺"],
        "current_lifecycle": None,
        "catalyst_count": 0,
        "note": "牛市旗手，大盘先行指标",
    },
}

# 题材间的联动关系
THEME_LINKS = {
    "AI": {"影响": ["光模块", "半导体", "机器人", "消费电子"], "受制于": ["半导体"]},
    "光模块": {"影响": ["AI算力"], "受制于": ["AI"]},
    "半导体": {"影响": ["AI", "消费电子", "信创", "汽车电子"], "受制于": ["AI"]},
    "低空经济": {"影响": ["军工", "新能源"], "受制于": ["政策"]},
    "机器人": {"影响": ["新能源", "AI"], "受制于": ["AI", "新能源"]},
    "新能源": {"影响": ["化工", "有色"], "受制于": ["石油", "碳酸锂"]},
    "军工": {"影响": ["航天", "低空经济"], "受制于": ["地缘政治"]},
    "地产": {"影响": ["银行", "建材", "家居"], "受制于": ["政策"]},
}

# 板块轮动的常见模式
SECTOR_ROTATION_PATTERNS = [
    {"from": "AI/科技高位", "to": "低估值/红利", "condition": "AI涨太多、利率上升"},
    {"from": "避险（黄金/红利）", "to": "成长/科技", "condition": "风险偏好回升"},
    {"from": "地产/金融", "to": "消费/科技", "condition": "经济复苏确认"},
    {"from": "小盘/题材", "to": "大盘/蓝筹", "condition": "监管收紧或流动性收紧"},
    {"from": "大盘/蓝筹", "to": "小盘/题材", "condition": "流动性宽松、风险偏好高"},
]


def get_theme_lifecycle_stage(theme_name: str, signals: Dict = None) -> Dict:
    """
    判断题材当前处于什么生命周期阶段

    Args:
        theme_name: 题材名称
        signals: 实时信号（涨幅、涨停家数、成交量、消息频率等）

    Returns:
        生命周期判断结果
    """
    if signals is None:
        signals = {}

    theme = MAIN_THEMES.get(theme_name, {})
    if not theme:
        return {"theme": theme_name, "error": "未知题材"}

    # 评分判断（从多个维度打分）
    scores = {
        "萌芽期": 0,
        "发酵期": 0,
        "高潮期": 0,
        "分化期": 0,
        "退潮期": 0,
    }

    # 1. 涨幅维度
    day_change = signals.get("day_change_pct", 0)
    week_change = signals.get("week_change_pct", 0)
    month_change = signals.get("month_change_pct", 0)

    if month_change > 30:
        scores["高潮期"] += 3
        scores["分化期"] += 1
    elif month_change > 15:
        scores["高潮期"] += 2
        scores["发酵期"] += 1
    elif month_change > 5:
        scores["发酵期"] += 2
        scores["萌芽期"] += 1
    elif month_change < -10:
        scores["退潮期"] += 3
        scores["分化期"] += 1
    elif month_change < -5:
        scores["退潮期"] += 2
        scores["分化期"] += 1
    else:
        scores["萌芽期"] += 1

    # 2. 涨停家数维度
    limit_up = signals.get("limit_up_count", 0)
    if limit_up >= 10:
        scores["高潮期"] += 3
    elif limit_up >= 5:
        scores["发酵期"] += 3
    elif limit_up >= 1:
        scores["萌芽期"] += 2
    else:
        scores["退潮期"] += 2

    # 3. 成交量维度
    vol_change = signals.get("volume_change_ratio", 0)
    if vol_change > 2.0:
        scores["高潮期"] += 2
    elif vol_change > 1.5:
        scores["发酵期"] += 2
    elif vol_change < 0.5:
        scores["退潮期"] += 2
        scores["分化期"] += 1

    # 4. 消息频率维度
    msg_count = signals.get("message_count_7d", 0)
    if msg_count >= 5:
        scores["高潮期"] += 2
    elif msg_count >= 3:
        scores["发酵期"] += 2
    elif msg_count >= 1:
        scores["萌芽期"] += 1
    else:
        scores["退潮期"] += 1

    # 5. 当前催化类型
    catalyst_type = signals.get("catalyst_type", "")
    if catalyst_type == "new_tech":
        scores["萌芽期"] += 3
    elif catalyst_type == "capital_inflow":
        scores["发酵期"] += 2
    elif catalyst_type == "order_landing":
        scores["高潮期"] += 2
    elif catalyst_type == "earnings":
        scores["分化期"] += 2
    elif catalyst_type == "new_catalyst_after_decline":
        scores["二波/反抽"]  # 特殊处理
        pass

    # 选出最高分阶段
    best_stage = max(scores, key=scores.get)
    best_score = scores[best_stage]
    confidence = "高" if best_score >= 3 else "中" if best_score >= 2 else "低"

    lifecycle = THEME_LIFECYCLE.get(best_stage, {})
    next_stages = {
        "萌芽期": "发酵期",
        "发酵期": "高潮期",
        "高潮期": "分化期",
        "分化期": "退潮期",
        "退潮期": "萌芽期（等新催化）",
    }

    return {
        "theme": theme_name,
        "current_stage": best_stage,
        "stage_description": lifecycle.get("description", ""),
        "confidence": confidence,
        "score": best_score,
        "detail_scores": scores,
        "next_stage": next_stages.get(best_stage, ""),
        "typical_duration_remaining": lifecycle.get("typical_duration", ""),
        "signals_current": lifecycle.get("signals", []),
    }


def assess_theme_momentum(theme_name: str, signals: Dict = None) -> Dict:
    """
    评估题材的资金动量

    核心：资金集中度 = 该题材成交额 / 全市场成交额
    如果集中度快速上升 → 题材加速；如果快速下降 → 题材降温
    """
    if signals is None:
        signals = {}

    concentration = signals.get("capital_concentration", 0)  # 0-1
    concentration_change = signals.get("concentration_change_5d", 0)
    relative_strength = signals.get("relative_strength", 0)  # 相对大盘

    # 动量评分
    momentum = 0
    if concentration > 0.1:  # 占全市场10%+
        momentum += 2
    elif concentration > 0.05:
        momentum += 1

    if concentration_change > 0.02:
        momentum += 2  # 集中度快速上升
    elif concentration_change > 0.01:
        momentum += 1
    elif concentration_change < -0.02:
        momentum -= 2  # 资金快速流出

    if relative_strength > 0.05:
        momentum += 2
    elif relative_strength > 0.02:
        momentum += 1
    elif relative_strength < -0.03:
        momentum -= 2

    direction = "加速" if momentum >= 3 else "维持" if momentum >= 0 else "衰减"
    action = "关注" if momentum >= 3 else "持有" if momentum >= 1 else "观望" if momentum >= -1 else "回避"

    return {
        "theme": theme_name,
        "momentum_score": momentum,
        "direction": direction,
        "action": action,
        "capital_concentration": concentration,
        "relative_strength": relative_strength,
    }


def identify_theme_cluster(theme_name: str) -> Dict:
    """
    识别题材集群——这个题材带动哪些上下游，受哪些影响
    """
    links = THEME_LINKS.get(theme_name, {})
    theme = MAIN_THEMES.get(theme_name, {})

    return {
        "theme": theme_name,
        "sub_domains": theme.get("sub_domains", []),
        "influences": links.get("影响", []),
        "depends_on": links.get("受制于", []),
        "key_stocks": theme.get("key_stocks", []),
    }


def check_sector_rotation(
    top_gainers: List[str],
    top_losers: List[str],
    current_regime: str = "震荡"
) -> Dict:
    """
    检查板块轮动信号

    如果近期上涨板块和之前上涨板块不同，说明轮动开始了
    """
    rotation_signals = []

    for pattern in SECTOR_ROTATION_PATTERNS:
        condition = pattern["condition"]
        # 简化判断：检查是否有符合条件的轮动模式
        rotation_signals.append({
            "pattern": f"{pattern['from']} → {pattern['to']}",
            "condition": condition,
            "possible": "需关注",
        })

    return {
        "type": "sector_rotation",
        "rotation_signals": rotation_signals,
        "current_regime": current_regime,
        "note": "板块轮动是A股重要特征，一个题材高潮后资金会流向其他洼地",
    }


def assess_new_theme_potential(
    news_text: str,
    theme_name: str = None,
    existing_catalyst_count: int = 0,
    tech_type: str = "new_tech",  # new_tech, new_path, new_plan, new_invest
) -> Dict:
    """
    评估一个新消息能不能成为一个新题材的起点

    这是最重要的功能之一——识别"萌芽期"的题材

    Args:
        news_text: 消息文本
        theme_name: 如果已知，直接指定题材
        existing_catalyst_count: 这个题材已经催化了几次
        tech_type: 新技术的类型
    """
    # 首次出现 → 最强信号
    # 第2-3次 → 发酵阶段
    # 第4次+ → 边际递减

    rarity_score = max(5 - existing_catalyst_count, 1)  # 首次=5, 第5次=1

    # 技术类型权重
    type_weights = {
        "new_tech": 5,   # 新新技术——最强
        "new_path": 4,   # 新路径——次强
        "new_plan": 3,   # 新规划——中等
        "new_invest": 3, # 新投资——中等
        "capital_inflow": 3,  # 资金落地
        "order_landing": 4,   # 订单落地
        "earnings": 3,    # 业绩落地
    }

    awakening_power = type_weights.get(tech_type, 3) * (rarity_score / 5)

    is_new_theme = awakening_power >= 3 and existing_catalyst_count <= 1

    return {
        "message": news_text[:100] + "...",
        "theme_name": theme_name or "待分类",
        "tech_type": tech_type,
        "existing_catalyst_count": existing_catalyst_count,
        "rarity_score": rarity_score,
        "awakening_power": round(awakening_power, 1),
        "is_new_theme_start": is_new_theme,
        "verdict": "✨ 新题材潜力大" if is_new_theme else "已有题材的催化延续",
        "recommended": "重点关注，首次催化" if is_new_theme and existing_catalyst_count == 0 else (
            "低吸机会，催化还有空间" if awakening_power >= 3 else (
                "边际递减，谨慎参与"
            )
        ),
    }


def run_layer(config: Dict = None) -> Dict:
    """
    板块题材层主入口
    综合评估当前各题材状态
    """
    if config is None:
        config = {}

    all_assessments = {}

    themes_to_analyze = config.get("themes", list(MAIN_THEMES.keys()))

    for theme_name in themes_to_analyze:
        theme_signals = config.get("theme_signals", {}).get(theme_name, {})

        lifecycle = get_theme_lifecycle_stage(theme_name, theme_signals)
        momentum = assess_theme_momentum(theme_name, theme_signals)
        cluster = identify_theme_cluster(theme_name)

        all_assessments[theme_name] = {
            "lifecycle": lifecycle,
            "momentum": momentum,
            "cluster": cluster,
        }

    # 找出当前最值得关注的题材
    rankings = []
    for theme, data in all_assessments.items():
        momentum_score = data["momentum"]["momentum_score"]
        stage = data["lifecycle"]["current_stage"]
        score = 0
        if stage in ("萌芽期", "发酵期"):
            score = momentum_score * 1.5
        elif stage == "高潮期":
            score = momentum_score * 1.2
        elif stage in ("分化期", "退潮期"):
            score = momentum_score * 0.5
        rankings.append((theme, round(score, 1), stage))

    rankings.sort(key=lambda x: x[1], reverse=True)

    return {
        "layer": "sector_theme",
        "type": "composite",
        "themes_analyzed": len(themes_to_analyze),
        "rankings": rankings,
        "top_themes": rankings[:5],
        "details": all_assessments,
        "rotation": check_sector_rotation([], [], config.get("market_regime", "震荡")),
        "frequency_db": config.get("catalyst_count_db", {}),
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    print("=== 板块题材层引擎测试 ===")

    # 测试1：模拟AI题材处于发酵期的信号
    print("\n1. AI题材生命周期判断（发酵期信号）:")
    signals_ai = {
        "day_change_pct": 3.5,
        "week_change_pct": 8.2,
        "month_change_pct": 12.0,
        "limit_up_count": 6,
        "volume_change_ratio": 1.8,
        "message_count_7d": 3,
        "catalyst_type": "capital_inflow",
    }
    r = get_theme_lifecycle_stage("AI", signals_ai)
    print(json.dumps(r, indent=2, ensure_ascii=False))

    # 测试2：新消息能不能成为新题材
    print("\n2. 新题材潜力评估:")
    r = assess_new_theme_potential(
        "华为发布新一代AI芯片，性能提升300%",
        theme_name="AI芯片",
        existing_catalyst_count=0,
        tech_type="new_tech",
    )
    print(json.dumps(r, indent=2, ensure_ascii=False))

    # 测试3：退潮后新催化剂
    print("\n3. 退潮后新催化:")
    r = assess_new_theme_potential(
        "英伟达B200芯片订单超预期，中际旭创新增1.6T订单",
        theme_name="光模块",
        existing_catalyst_count=8,
        tech_type="order_landing",
    )
    print(json.dumps(r, indent=2, ensure_ascii=False))

    # 测试4：综合
    print("\n4. 综合测试:")
    r = run_layer({
        "themes": ["AI", "光模块", "低空经济", "半导体"],
        "catalyst_count_db": {"AI": 12, "光模块": 8, "低空经济": 3, "半导体": 5},
    })
    print(json.dumps(r, indent=2, ensure_ascii=False))
