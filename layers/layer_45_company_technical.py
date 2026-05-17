"""
💼 公司/行业层 + 📈 个股技术层 (Layer 4 & 5)
=================================================
两层层合并到一个文件，因为技术面需要公司基本面作为上下文

Layer 4 - 公司行业层：
- 基本面（营收/利润/ROE/现金流）
- 订单/合同/产能
- 技术路线（800G→1.6T→3.2T等）
- 竞争格局（龙头/二线/边缘）
- 估值体系（PE/PB/PS/peg）

Layer 5 - 个股技术层：
- 趋势判断（MA20/MA60排列）
- 位置判断（超买/超卖/突破/回踩）
- 量价关系（放量上涨/缩量调整）
- 筹码分析（集中/分散）

数据来源：腾讯行情、搜狐K线、用户提供
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ============= 个股基本面数据库 =============

STOCK_FUNDAMENTALS = {
    "中际旭创": {
        "code": "300308",
        "sector": "光模块",
        "market_cap": "中小盘",
        "pe_ttm": None,
        "roe": None,
        "revenue_growth": None,
        "product": "高速光模块(800G/1.6T)",
        "tech_roadmap": {"now": "800G", "next": "1.6T(2025H2)", "future": "3.2T(2026-2027)"},
        "competitive_position": "龙头",
        "note": "全球光模块龙头，直接受益AI算力需求",
    },
    "天孚通信": {
        "code": "300394",
        "sector": "光模块",
        "market_cap": "中小盘",
        "pe_ttm": None,
        "roe": None,
        "revenue_growth": None,
        "product": "光器件(FAU/ELS/光引擎)",
        "tech_roadmap": {"now": "800G配套", "next": "1.6T光引擎", "future": "CPO相关"},
        "competitive_position": "龙头",
        "note": "光器件细分龙头，毛利率高",
    },
    "新易盛": {
        "code": "300502",
        "sector": "光模块",
        "market_cap": "中小盘",
        "pe_ttm": None,
        "roe": None,
        "revenue_growth": None,
        "product": "高速光模块",
        "tech_roadmap": {"now": "800G", "next": "1.6T", "future": "LPO"},
        "competitive_position": "二线龙",
        "note": "绑定北美大客户，弹性标的",
    },
    "光迅科技": {
        "code": "002281",
        "sector": "光模块/光器件",
        "market_cap": "大盘",
        "pe_ttm": None,
        "roe": None,
        "revenue_growth": None,
        "product": "光器件/光模块全链条",
        "tech_roadmap": {"now": "400G/800G", "next": "800G/1.6T", "future": "硅光"},
        "competitive_position": "龙头",
        "note": "国家队光通信龙头，央企业务稳健",
    },
    "北方华创": {
        "code": "002371",
        "sector": "半导体设备",
        "market_cap": "大盘",
        "pe_ttm": None,
        "roe": None,
        "revenue_growth": None,
        "product": "刻蚀/薄膜/清洗设备",
        "tech_roadmap": {"now": "28nm", "next": "14nm", "future": "7nm"},
        "competitive_position": "龙头",
        "note": "半导体设备平台型龙头",
    },
    "工业富联": {
        "code": "601138",
        "sector": "AI服务器",
        "market_cap": "大盘",
        "pe_ttm": None,
        "roe": None,
        "revenue_growth": None,
        "product": "AI服务器/交换机",
        "tech_roadmap": {"now": "AI服务器(H100/B200)", "next": "下一代AI服务器", "future": ""},
        "competitive_position": "龙头",
        "note": "英伟达核心代工厂",
    },
    "宁德时代": {
        "code": "300750",
        "sector": "锂电池",
        "market_cap": "大盘",
        "pe_ttm": None,
        "roe": None,
        "revenue_growth": None,
        "product": "动力电池/储能电池",
        "tech_roadmap": {"now": "麒麟电池", "next": "凝聚态/固态", "future": "钠离子"},
        "competitive_position": "全球龙头",
        "note": "全球动力电池霸主",
    },
    "拓普集团": {
        "code": "601689",
        "sector": "汽车零部件/机器人",
        "market_cap": "中盘",
        "pe_ttm": None,
        "roe": None,
        "revenue_growth": None,
        "product": "底盘系统/执行器",
        "tech_roadmap": {"now": "智能底盘", "next": "机器人执行器", "future": "关节模组"},
        "competitive_position": "龙头",
        "note": "车+机器人双赛道标的",
    },
}

# A股主要指数定义
INDEX_COMPONENTS = {
    "上证50": ["中国平安", "贵州茅台", "招商银行", "中信证券", "隆基绿能"],
    "沪深300": ["宁德时代", "贵州茅台", "中国平安", "招商银行", "美的集团"],
    "中证500": ["中际旭创", "天孚通信", "光迅科技", "北方华创"],
}

# ============= 技术指标计算 =============

def assess_trend(ma20: List[float], ma60: List[float]) -> Dict:
    """
    判断趋势

    Args:
        ma20: 最近20个MA20值（最新在最后）
        ma60: 最近20个MA60值

    Returns:
        趋势判断
    """
    if not ma20 or not ma60:
        return {"trend": "未知", "strength": 0}

    current_ma20 = ma20[-1]
    current_ma60 = ma60[-1] if ma60 else current_ma20

    # 均线排列
    if current_ma20 > current_ma60 and len(ma20) >= 5 and len(ma60) >= 5:
        # 多头排列：MA20在MA60上方
        if ma20[-1] > ma20[-2] and ma60[-1] > ma60[-2]:
            trend = "主升浪"
            strength = 3
        else:
            trend = "多头震荡"
            strength = 2
    elif current_ma20 < current_ma60:
        if ma20[-1] < ma20[-2] and ma60[-1] < ma60[-2]:
            trend = "主跌浪"
            strength = -3
        else:
            trend = "空头震荡"
            strength = -2
    else:
        trend = "震荡"
        strength = 1

    # 均线斜率
    ma20_slope = (ma20[-1] - ma20[-5]) / ma20[-5] * 100 if len(ma20) >= 5 else 0
    ma60_slope = (ma60[-1] - ma60[-5]) / ma60[-5] * 100 if len(ma60) >= 5 else 0

    return {
        "trend": trend,
        "strength": strength,
        "ma20_price": round(current_ma20, 2),
        "ma60_price": round(current_ma60, 2),
        "ma20_slope_pct": round(ma20_slope, 2),
        "ma60_slope_pct": round(ma60_slope, 2),
    }


def assess_position(current_price: float,
                    recent_high: float,
                    recent_low: float,
                    bollinger: Dict = None) -> Dict:
    """
    判断价格位置

    Returns:
        rsi_signal: 超买/超卖/正常
        bollinger_signal: 上轨/中轨/下轨
        position_pct: 在近期高低点中的位置百分比
    """
    if not current_price or not recent_high or not recent_low:
        return {"position": "未知"}

    range_size = recent_high - recent_low
    if range_size == 0:
        position_pct = 50
    else:
        position_pct = (current_price - recent_low) / range_size * 100

    if position_pct >= 80:
        zone = "高位区"
        rsi_signal = "可能超买"
    elif position_pct >= 60:
        zone = "中高位"
        rsi_signal = "偏强"
    elif position_pct >= 40:
        zone = "中位区"
        rsi_signal = "中性"
    elif position_pct >= 20:
        zone = "中低位"
        rsi_signal = "偏弱"
    else:
        zone = "低位区"
        rsi_signal = "可能超卖"

    # 布林带信号
    bollinger_signal = "中性"
    if bollinger:
        if current_price >= bollinger.get("upper", float("inf")):
            bollinger_signal = "触及上轨·超买"
        elif current_price <= bollinger.get("lower", 0):
            bollinger_signal = "触及下轨·超卖"
        elif current_price >= bollinger.get("mid", 0):
            bollinger_signal = "中轨以上·偏强"
        else:
            bollinger_signal = "中轨以下·偏弱"

    return {
        "current_price": round(current_price, 2),
        "position_pct": round(position_pct, 1),
        "zone": zone,
        "rsi_signal": rsi_signal,
        "bollinger_signal": bollinger_signal,
        "recent_high": round(recent_high, 2),
        "recent_low": round(recent_low, 2),
    }


def assess_volume(volume_today: float, volume_ma5: float,
                  price_change_pct: float) -> Dict:
    """
    判断量价关系
    """
    vol_ratio = volume_today / volume_ma5 if volume_ma5 > 0 else 1

    if vol_ratio > 2.0 and price_change_pct > 3:
        signal = "放量暴涨·强势突破"
        quality = "极好"
    elif vol_ratio > 1.5 and price_change_pct > 2:
        signal = "放量上涨·健康走势"
        quality = "良好"
    elif vol_ratio > 1.5 and price_change_pct < -2:
        signal = "放量下跌·抛压重"
        quality = "警惕"
    elif vol_ratio < 0.5 and price_change_pct < -1:
        signal = "缩量下跌·阴跌"
        quality = "偏弱"
    elif vol_ratio < 0.5 and price_change_pct > 0:
        signal = "缩量上涨·动力不足"
        quality = "中性偏弱"
    elif 0.7 < vol_ratio < 1.3 and price_change_pct > 0:
        signal = "量价配合·健康"
        quality = "良好"
    elif 0.7 < vol_ratio < 1.3 and price_change_pct < 0:
        signal = "量价正常·正常回调"
        quality = "中性"
    else:
        signal = "量价正常"
        quality = "中性"

    return {
        "volume_ratio": round(vol_ratio, 2),
        "signal": signal,
        "quality": quality,
    }


def assess_valuation(stock_name: str, current_price: float = None,
                     pe: float = None, sector_avg_pe: float = None) -> Dict:
    """
    估值评估
    """
    if stock_name not in STOCK_FUNDAMENTALS:
        return {"stock": stock_name, "error": "无基本面数据"}

    info = STOCK_FUNDAMENTALS[stock_name]

    valuation_status = "未知"
    if pe and sector_avg_pe:
        pe_ratio = pe / sector_avg_pe
        if pe_ratio > 2:
            valuation_status = "高估（PE超行业2倍）"
        elif pe_ratio > 1.3:
            valuation_status = "偏高"
        elif pe_ratio > 0.7:
            valuation_status = "合理"
        elif pe_ratio > 0.5:
            valuation_status = "偏低"
        else:
            valuation_status = "低估"
    else:
        # 无PE数据，用技术位置预估
        pass

    return {
        "stock": stock_name,
        "code": info["code"],
        "sector": info["sector"],
        "product": info["product"],
        "competitive_position": info["competitive_position"],
        "tech_roadmap": info["tech_roadmap"],
        "valuation_status": valuation_status,
        "pe": pe,
        "sector_avg_pe": sector_avg_pe,
    }


def comprehensive_stock_analysis(
    stock_name: str,
    price_data: Dict = None,
) -> Dict:
    """
    个股综合分析——基本面+技术面
    """
    fundamental = STOCK_FUNDAMENTALS.get(stock_name, {})
    if not fundamental:
        return {"stock": stock_name, "error": "无数据"}

    # 基本面
    fundamentals = {
        "sector": fundamental.get("sector"),
        "product": fundamental.get("product"),
        "tech_roadmap": fundamental.get("tech_roadmap"),
        "competitive_position": fundamental.get("competitive_position"),
        "market_cap_category": fundamental.get("market_cap"),
        "note": fundamental.get("note"),
    }

    # 技术面（如有价格数据）
    technical = {}
    if price_data:
        technical["trend"] = assess_trend(
            price_data.get("ma20_list", []),
            price_data.get("ma60_list", []),
        )
        technical["position"] = assess_position(
            price_data.get("current_price", 0),
            price_data.get("recent_high", 0),
            price_data.get("recent_low", 0),
            price_data.get("bollinger"),
        )
        technical["volume"] = assess_volume(
            price_data.get("volume_today", 0),
            price_data.get("volume_ma5", 0),
            price_data.get("price_change_pct", 0),
        )

    return {
        "stock": stock_name,
        "code": fundamental.get("code"),
        "fundamentals": fundamentals,
        "technical": technical,
        "pricing_model": identify_pricing_model(stock_name),
    }


def identify_pricing_model(stock_name: str,
                            market_cap: float = None,
                            current_price: float = None) -> str:
    """
    识别个股的定价模型：游资定价 / 机构定价 / 混合定价
    """
    info = STOCK_FUNDAMENTALS.get(stock_name, {})
    cap_category = info.get("market_cap", "中盘")

    if cap_category == "大盘":
        return "机构定价（基本面/业绩驱动，看PE/ROE）"
    elif cap_category == "中盘":
        return "混合定价（游资情绪+机构估值共同决定）"
    else:
        return "游资定价（小市值、情绪驱动、看预期不看历史业绩）"


def run_layer(config: Dict = None) -> Dict:
    """
    公司+个股技术层主入口
    """
    if config is None:
        config = {}

    stocks = config.get("stocks", [])
    if not stocks:
        stocks = list(STOCK_FUNDAMENTALS.keys())

    results = {}
    for stock_name in stocks:
        price_data = config.get("price_data", {}).get(stock_name)
        results[stock_name] = comprehensive_stock_analysis(stock_name, price_data)

    return {
        "layers": ["company_industry", "stock_technical"],
        "stocks_analyzed": len(results),
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    print("=== 公司+个股技术层测试 ===")

    print("\n1. 中际旭创综合分析:")
    r = comprehensive_stock_analysis("中际旭创")
    print(json.dumps(r, indent=2, ensure_ascii=False))

    print("\n2. 趋势判断测试:")
    r = assess_trend(
        ma20=[100, 102, 105, 108, 110],
        ma60=[95, 96, 97, 98, 100],
    )
    print(json.dumps(r, indent=2, ensure_ascii=False))

    print("\n3. 位置判断测试:")
    r = assess_position(115, 120, 100, {"upper": 118, "mid": 110, "lower": 102})
    print(json.dumps(r, indent=2, ensure_ascii=False))

    print("\n4. 量价关系测试:")
    r = assess_volume(500000, 250000, 4.5)
    print(json.dumps(r, indent=2, ensure_ascii=False))
