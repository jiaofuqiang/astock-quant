"""
🏭 三段估值链实战引擎
=====================
A股核心机制：
  全球发展趋势 → 消息催化预期估值（股价先涨）
  → 产业订单验证估值（继续涨/回调）
  → 业绩兑现估值（超预期/利好出尽）

每日运行时，自动判断：
  1. 各产业当前处于三段估值链的哪个位置
  2. 基于实时消息+美股锚定+主力资金，判断下一段方向
  3. 输出可操作标的

运行：python3 valuation_engine.py
"""
import os, sys, json, sqlite3, subprocess, time, re
from datetime import datetime, timedelta

PROJECT_DIR = "/home/ubuntu/astock"
DATA_DIR = f"{PROJECT_DIR}/data"
KLINE_DB = f"{DATA_DIR}/kline_cache.db"
sys.path.insert(0, PROJECT_DIR)

from industry_engine_v2 import get_realtime_batch, get_us_anchors
from news_scanner import fetch_all_news, match_news_to_segment

# ===========================================================
# 三段估值链知识图谱（核心框架）
# ===========================================================

VALUATION_CHAIN = {
    "AI算力(光模块+服务器+芯片)": {
        "level": "S级主线",
        "lifecycle": {
            "stage": "3→4过渡",
            "description": "第三阶段（业绩兑现）已完成，正向第四阶段（全面扩散）过渡",
            "phase1_message": "已完成（早期效率极高，近期递减）",
            "phase2_order": "已完成（1.6T量产确认，订单已验证）",
            "phase3_earnings": "进行中（光模块业绩持续超预期，边际递减）",
        },
        "global_driver": "NVDA Blackwell Ultra→算力需求十万倍→光模块1.6T迭代",
        "valuation_basis": "以NVDA资本开支为锚，光模块订单增速→PE",
        "mainboard_codes": ["601138", "603019", "600703"],
        "reference_codes": ["300308", "300394", "300502", "688041", "688256"],
        # 历史量化规律
        "quantitative_patterns": {
            "message_impact": "早期事件后20日+2%胜率67%，1.6T量产提前定价+19%",
            "best_entry": "NVDA财报前3天买入，前1天卖出（提前定价模式）",
            "best_exit": "连续2次事件后涨幅<1%→利好出尽，应转到新方向",
        },
        "risk": "光模块78~98%涨幅后进入个股分化，利好出尽风险增大",
    },
    "存储芯片(HBM+NOR+DRAM)": {
        "level": "A级主线",
        "lifecycle": {
            "stage": "2→3过渡",
            "description": "第二阶段（订单验证）确认，正向第三阶段（业绩兑现）过渡 → 最佳建仓窗口",
            "phase1_message": "已完成（MU多次财报验证存储周期向上）",
            "phase2_order": "确认中（HBM3E出货量倍增，存储涨价传导中）",
            "phase3_earnings": "未到（等待兆易创新业绩验证存储涨价受益）",
        },
        "global_driver": "MU HBM3E出货翻倍→存储整体涨价→NOR Flash量价齐升",
        "valuation_basis": "以MU/三星存储价格为锚→兆易NOR Flash营收→PS估值",
        "mainboard_codes": ["603986"],
        "reference_codes": ["688008", "002371"],
        "quantitative_patterns": {
            "message_impact": "MU财报后5日+12.93%胜率100%，后20日+16.66%胜率100%",
            "best_entry": "MU财报发布日或次日开盘买入，持仓到下一次MU财报前",
            "best_exit": "兆易连续2次业绩增速下滑→退出",
        },
        "risk": "存储周期斜率待确认，若AI资本开支放缓则存储需求不达预期",
    },
    "低空经济(eVTOL+无人机)": {
        "level": "A级主线(政策驱动)",
        "lifecycle": {
            "stage": "1",
            "description": "第一阶段（消息催化）进行中，政策持续推动，订单/业绩均未出现",
            "phase1_message": "进行中（政策持续催化）",
            "phase2_order": "未到",
            "phase3_earnings": "未到",
        },
        "global_driver": "eVTOL适航认证加速，空域开放政策推进",
        "valuation_basis": "以政策频次为锚→PS估值（暂无业绩）",
        "mainboard_codes": ["600862", "600760"],
        "reference_codes": ["002097"],
        "quantitative_patterns": {
            "message_impact": "数据不足（2023.12政策事件在K线覆盖前）",
            "best_entry": "政策密集期（两会/中央经济会议前后）",
            "best_exit": "政策发布后5日内，不论涨跌都走（纯炒作）",
        },
        "risk": "纯政策驱动，0业绩支撑，波动极大",
    },
    "机器人(AI+制造)": {
        "level": "长期主线(0→1)",
        "lifecycle": {
            "stage": "1初期",
            "description": "第一阶段初期，Optimus量产预期+供应链催化中",
            "phase1_message": "初期（Optimus/Figure等催化）",
            "phase2_order": "未到",
            "phase3_earnings": "未到",
        },
        "global_driver": "特斯拉Optimus百万台目标→中国供应链成本优势",
        "valuation_basis": "以Optimus量产节奏为锚→供应链份额→PS",
        "mainboard_codes": ["600406"],
        "reference_codes": ["300124", "002472", "688017"],
        "quantitative_patterns": {
            "message_impact": "数据不足（早期事件在覆盖范围外）",
            "best_entry": "Optimus量产确认节点",
            "best_exit": "概念股不追高，等量产再入场",
        },
        "risk": "0→1阶段，90%概率伪证，只适合极小仓位",
    },
    "新能源(电池+智能驾驶)": {
        "level": "A级主线（调整期）",
        "lifecycle": {
            "stage": "3末期",
            "description": "第三阶段（业绩兑现）末期，消息面已无法带动上涨，进入产业出清期",
            "phase1_message": "已完成→无效（消息无法带动股价）",
            "phase2_order": "已完成（订单已被充分定价）",
            "phase3_earnings": "已完成并已出清（股价从高点大幅回调）",
        },
        "global_driver": "电动化30%→50%，固态电池技术革命，FSD迭代",
        "valuation_basis": "以电池出货GWh和锂价为锚",
        "mainboard_codes": ["600690"],
        "reference_codes": ["300750", "002709", "002812", "002460"],
        "quantitative_patterns": {
            "message_impact": "消息后20日-4.52%胜率0%，消息已完全失效",
            "best_entry": "暂无（等待产能出清信号）",
            "best_exit": "反弹即卖出",
        },
        "risk": "产能过剩未出清，锂价/电池价格持续承压",
    },
}


# ===========================================================
# 实时判断引擎
# ===========================================================

def evaluate_sector(sector_name: str, sector_data: dict, 
                    us_data: dict, realtime: dict) -> dict:
    """评估一个产业的实时状态"""
    
    # 1. 获取美股锚定信号
    us_signal = 0
    us_details = []
    
    for code in sector_data.get("mainboard_codes", []):
        if code in realtime:
            d = realtime[code]
            if d.get("super_large", 0) > 0:
                us_signal += 1
    
    # 2. 美股锚定
    if "NVDA" in str(sector_data.get("global_driver", "")):
        nvda = us_data.get("NVDA", {})
        if nvda.get("chg_pct", 0) > 0:
            us_signal += 1
        
    if "MU" in str(sector_data.get("global_driver", "")):
        mu = us_data.get("MU", {})
        if mu.get("chg_pct", 0) > 0:
            us_signal += 1
    
    # 3. 主力资金判断
    mainboard_flow = []
    for code in sector_data.get("mainboard_codes", []):
        if code in realtime:
            d = realtime[code]
            mainboard_flow.append({
                "code": code,
                "name": d.get("name", ""),
                "sl": d.get("super_large_pct", 0),  # 超大单占比%
                "nr": d.get("main_pct", 0),           # 主力净占比%（正=净买，负=净卖）
                "chg": d.get("chg_pct", 0),
                "vol": d.get("vol_ratio", 0),
            })
    
    buy_stocks = sum(1 for s in mainboard_flow if s["nr"] > 0)
    total_stocks = len(mainboard_flow)
    avg_sl = sum(s["sl"] for s in mainboard_flow) / total_stocks if total_stocks > 0 else 0
    avg_nr = sum(s["nr"] for s in mainboard_flow) / total_stocks if total_stocks > 0 else 0
    
    # 4. 阶段权重
    stage = sector_data["lifecycle"]["stage"]
    stage_weight = {
        "1": 1, "1初期": 1, "1→2": 2,
        "2": 3, "2→3": 4,  # 最佳窗口
        "3": 3, "3→4": 3, "3末期": 1,
    }.get(stage, 2)
    
    # 综合评分
    score = stage_weight * 3  # 阶段权重
    score += us_signal * 5    # 美股锚定
    score += avg_sl * 0.5     # 主力超大单
    score += avg_nr * 0.3     # 净买卖比
    
    return {
        "name": sector_name,
        "level": sector_data["level"],
        "stage": stage,
        "stage_desc": sector_data["lifecycle"]["description"],
        "score": round(score, 1),
        "mainboard_flow": mainboard_flow,
        "avg_sl": avg_sl,
        "avg_nr": avg_nr,
        "buy_ratio": f"{buy_stocks}/{total_stocks}",
        "risk": sector_data["risk"],
    }


def run_daily_valuation() -> str:
    """每日运行，生成三段估值链实战报告"""
    
    now = datetime.now()
    
    # 获取实时数据
    all_codes = set()
    for sname, sdata in VALUATION_CHAIN.items():
        all_codes.update(sdata.get("mainboard_codes", []))
        all_codes.update(sdata.get("reference_codes", []))
    
    realtime = get_realtime_batch(list(all_codes))
    us_data = get_us_anchors()
    
    lines = []
    lines.append(f"🏭 **三段估值链实战报告 | {now.strftime('%m/%d %H:%M')}**")
    lines.append("")
    lines.append("A股 = 全球发展趋势的前置预测性估值投资")
    lines.append("")
    
    # === 美股锚定 ===
    lines.append("**🇺🇸 美股锚定（方向标）**")
    for t in ['NVDA', 'MU', 'TSLA', 'AMD', 'META', 'SMCI', 'MRVL']:
        if t in us_data:
            d = us_data[t]
            icon = "📈" if d['chg_pct'] > 0 else "📉" if d['chg_pct'] < 0 else "➡️"
            lines.append(f"  {icon} {t} ${d['price']:.2f} ({d['chg_pct']:+.2f}%)")
    lines.append("")
    
    # === 各产业三段估值链判断 ===
    lines.append("**📊 各产业估值链状态**")
    lines.append("")
    
    sectors = []
    for sname, sdata in VALUATION_CHAIN.items():
        result = evaluate_sector(sname, sdata, us_data, realtime)
        sectors.append(result)
    
    # 按分值排序
    sectors.sort(key=lambda x: -x["score"])
    
    for s in sectors:
        # 阶段图标
        stage = s["stage"]
        if "2→3" in stage:
            stage_icon = "🟢"  # 最佳窗口（绿色）
        elif "3→4" in stage:
            stage_icon = "🟡"  # 过渡期（黄色）
        elif "3末期" in stage:
            stage_icon = "🔴"  # 末期（红色）
        else:
            stage_icon = "⚪"  # 初期（灰色）
        
        level_icon = "🔥" if "S" in s["level"] else "📈" if "A" in s["level"] else "👀"
        
        lines.append(f"{stage_icon} {level_icon} **{s['name']}** | 评分: {s['score']:.0f}")
        lines.append(f"  三段估值链阶段: **{s['stage']}**")
        lines.append(f"  {s['stage_desc']}")
        
        # 显示该产业的主板标的实时数据
        for st in s["mainboard_flow"]:
            sl_icon = "🔴" if st["sl"] > 10 else "🟢" if st["sl"] > 0 else "⚪"
            lines.append(f"    {sl_icon} {st['code']} {st['name']} | 超大单{st['sl']:+.1f}% 净买卖比{st['nr']:+.1f}% 实时{st['chg']:+.1f}% 量比{st['vol']:.1f}")
        
        lines.append(f"  主力验证: {s['buy_ratio']}只净买入 | 超大单均值{s['avg_sl']:+.1f}%")
        lines.append(f"  ⚠️ {s['risk']}")
        lines.append("")
    
    # === 综合策略 ===
    lines.append("---")
    lines.append("**🎯 三段估值链 → 操作策略**")
    lines.append("")
    
    if sectors:
        best = sectors[0]
        if "2→3" in best["stage"]:
            lines.append(f"🟢 **优先方向：{best['name']}**")
            lines.append(f"  原因：正处于第2→3阶段过渡（订单验证确认中，业绩尚未兑现），是最佳建仓窗口")
            lines.append(f"  历史回测：相关消息发布后5日均值+12.93%，胜率100%")
            lines.append("")
        
        if len(sectors) > 1:
            second = sectors[1]
            lines.append(f"🟡 **关注方向：{second['name']}**")
            lines.append(f"  原因：{second['stage_desc']}")
            lines.append("")
        
        # 找出风险最高的
        worst = sectors[-1]
        if "末期" in worst["stage"]:
            lines.append(f"🔴 **回避方向：{worst['name']}**")
            lines.append(f"  原因：{worst['stage_desc']}")
            lines.append("")
    
    # === 美股锚定 → A股实战 ===
    lines.append("---")
    lines.append("**📡 美股锚定→A股实战**")
    lines.append("")
    
    nvda = us_data.get("NVDA", {})
    mu = us_data.get("MU", {})
    tsla = us_data.get("TSLA", {})
    
    # NVDA → AI算力
    if nvda:
        nvda_chg = nvda.get("chg_pct", 0)
        lines.append(f"**NVDA {nvda_chg:+.2f}% → AI算力**")
        if nvda_chg > 0:
            lines.append(f"  利好光模块/AI服务器，关注工业富联(601138)、中科曙光(603019)")
        elif nvda_chg < -5:
            lines.append(f"  NVDA回调>5%，A股光模块可能补跌，但回调是买入机会")
        else:
            lines.append(f"  微跌不影响，AI算力走自身逻辑")
    
    # MU → 存储芯片
    if mu:
        mu_chg = mu.get("chg_pct", 0)
        lines.append(f"**MU {mu_chg:+.2f}% → 存储芯片**")
        if mu_chg > 0:
            lines.append(f"  利好兆易创新(603986)，存储周期确认向上")
        else:
            lines.append(f"  弱信号，需要更多数据确认存储周期方向")
    
    # TSLA → 新能源/机器人
    if tsla:
        tsla_chg = tsla.get("chg_pct", 0)
        lines.append(f"**TSLA {tsla_chg:+.2f}% → 新能源/机器人**")
        if tsla_chg > 3:
            lines.append(f"  利好新能源和机器人方向，但新能源处于末期需要谨慎")
        else:
            lines.append(f"  影响有限")
    
    lines.append("")
    lines.append("---")
    lines.append(f"三段估值链引擎 | 自动运行 | {now.strftime('%H:%M')}")
    
    report = "\n".join(lines)
    
    os.makedirs(f"{PROJECT_DIR}/output", exist_ok=True)
    path = f"{PROJECT_DIR}/output/valuation_engine_{now.strftime('%Y%m%d_%H%M%S')}.txt"
    with open(path, "w") as f:
        f.write(report)
    
    return report


if __name__ == "__main__":
    report = run_daily_valuation()
    print(report)
