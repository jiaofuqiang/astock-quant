#!/usr/bin/env python3
"""
📈 早盘战情聚合器 Morning Brief
===============================
终极目的：每天输出可直接执行的「今日操作清单」
逻辑：
  1. 从东方财富抓取今日科技/产业新闻
  2. 每条走V2.0引擎 → 提取关键信号
  3. 聚合多消息信号 → 生成可操作标的 Top 5
  4. 微信推送 + 复盘数据库保存

用法：
  python3 scripts/morning_brief.py            # 标准模式
  python3 scripts/morning_brief.py --dry-run  # 只输出不推送
  python3 scripts/morning_brief.py --push     # 推送到微信
"""
import os, sys, subprocess, json, re
from datetime import datetime

PROJECT_DIR = "/home/ubuntu/astock"
SCRIPTS_DIR = f"{PROJECT_DIR}/scripts"
DATA_DIR = f"{PROJECT_DIR}/data"
sys.path.insert(0, PROJECT_DIR)
V2_SCRIPT = f"{SCRIPTS_DIR}/valuation_v2.py"

os.chdir(PROJECT_DIR)

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}')

# ══════════════════════════════════════════════════════════
# 第一关：抓今日重点消息（东方财富科技板块 + 美股锚定）
# ══════════════════════════════════════════════════════════

def fetch_tech_news():
    """从新浪API抓科技/产业今日要闻（静态JSON，非JS渲染）"""
    headlines = []
    
    # 新浪财经新闻API多频道
    channels = {"2509": "综合", "2671": "科技", "2676": "产经", "3318": "证券"}
    
    for lid, ch_name in channels.items():
        try:
            url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&k=&num=10&page=1"
            r = subprocess.run(
                ["curl", "-s", url, "--connect-timeout", "5", "--max-time", "8"],
                capture_output=True, timeout=10
            )
            data = json.loads(r.stdout.decode('utf-8', errors='replace'))
            for item in data.get('result', {}).get('data', []):
                title = item.get('title', '').strip()
                if title and len(title) > 6 and title not in headlines:
                    headlines.append(title)
        except:
            pass
    
    # 过滤：保留科技产业相关
    tech_keywords = [
        "AI", "人工智能", "芯片", "半导体", "算力", "光模块", "存储",
        "英伟达", "NVDA", "Blackwell", "HBM", "大模型", "机器人",
        "新能源", "电池", "固态电池", "低空经济", "飞行汽车",
        "智能驾驶", "华为", "小米", "苹果",
        "量产", "订单", "突破", "合作", "募资", "投产",
        "涨价", "产能", "需求", "政策",
        "数据中心", "服务器", "液冷", "散热",
        "业绩", "财报", "投资",
    ]
    
    tech_news = [h for h in headlines if any(kw in h for kw in tech_keywords)]
    
    # 去重（短前缀）
    seen = set()
    unique = []
    for h in tech_news:
        h_short = h[:20]
        if h_short not in seen:
            seen.add(h_short)
            unique.append(h)
    
    return unique[:10]

def fetch_us_anchors():
    """查美股锚定最新价格变化"""
    symbols = ["usNVDA", "usMU", "usTSLA", "usAVGO", "usAMD", "usDELL", "usVRT"]
    codes = ",".join(f"q_{s}" for s in symbols)
    url = f"http://qt.gtimg.cn/q={codes}"
    
    results = {}
    try:
        r = subprocess.run(
            ["curl", "-s", url, "--connect-timeout", "5", "--max-time", "8"],
            capture_output=True, timeout=10
        )
        raw = r.stdout.decode('gbk', errors='replace')
        for line in raw.split(';'):
            if '~' not in line:
                continue
            parts = line.split('~')
            if len(parts) > 32:
                name = parts[1] if parts[1] else parts[0].split('_')[-1]
                price = parts[3] if parts[3] else "?"
                chg = parts[32] if parts[32] else "0"
                results[name] = {"price": price, "chg_pct": chg}
    except:
        pass
    return results

# ══════════════════════════════════════════════════════════
# 第二关：V2.0引擎分析 + 信号提取
# ══════════════════════════════════════════════════════════

def analyze_message(message):
    """运行V2.0引擎分析一条消息，提取结构化信号"""
    try:
        r = subprocess.run(
            ["python3", V2_SCRIPT, message],
            capture_output=True, timeout=30, text=True
        )
        output = r.stdout
        
        # 从输出中提取关键字段
        result = {
            "message": message,
            "node": "",
            "level": "",
            "eps_delta": "",
            "alert": "未知",
            "bayesian": "",
            "top_stock": "",
            "top_score": "",
            "inst": "",
            "hm": "",
            "decision": "",
        }
        
        # 提取产业链节点
        m = re.search(r'🎯 节点: (\S+)', output)
        if m: result["node"] = m.group(1)
        
        m = re.search(r'ΔEPS直接: \+([\d.]+)%', output)
        if m: result["eps_delta"] = m.group(1) + "%"
        
        m = re.search(r'(🟢.*?|🟡.*?|🔴.*?|⚪.*?)\s*\(', output)
        if m: result["alert"] = m.group(1).strip()
        
        m = re.search(r'贝叶斯更新后: ([\d.]+)/100', output)
        if m: result["bayesian"] = m.group(1) + "/100"
        
        m = re.search(r'决策: (🟢\s*\*\*.*?\*\*|🟡\s*\*\*.*?\*\*|🔴\s*\*\*.*?\*\*|⚪\s*\*\*.*?\*\*)', output)
        if m: result["decision"] = m.group(1)
        
        m = re.search(r'机构[^\d]*([\d.]+)/100', output)
        if m: result["inst"] = m.group(1) + "/100"
        
        m = re.search(r'游资[^\d]*([\d.]+)/100', output)
        if m: result["hm"] = m.group(1) + "/100"
        
        # 提取TOP股票
        m = re.search(r'\*\*(\d{6})\*\*\s+(\S+)\s+综合(\d+)', output)
        if m: 
            result["top_stock"] = f"{m.group(1)} {m.group(2)}"
            result["top_score"] = m.group(3) + "/100"
        
        return result, output
    except Exception as e:
        log(f"  V2.0分析失败: {e}")
        return None, None

# ══════════════════════════════════════════════════════════
def build_decision(all_results, us_data):
    """实战决策聚合（覆盖V2.0引擎的保守阈值）
    
    决策逻辑（越往下越强）：
    1. EPS变化 > 30% → 至少⚪关注（强基本面信号）
    2. 多消息同产业链 → 至少⚪关注（共振确认）
    3. 消息含"重大/里程碑/革命/全球首发" → 🟢推荐（直接忽略贝叶斯）
    4. 美股锚定大涨>3% + 同产业链消息 → 🟢🟢强烈推荐
    5. 同时满足以上多项 → 🟢🟢🔥 最强信号
    """
    
    # 统计各产业链的消息密度和总EPS强度
    chain_counts = {}
    chain_strength = {}
    chain_max_eps = {}
    for r in all_results:
        node = r.get("node", "其他")
        chain_counts[node] = chain_counts.get(node, 0) + 1
        eps_str = r.get("eps_delta", "0%")
        try:
            eps_val = float(eps_str.replace("%", ""))
        except:
            eps_val = 0
        chain_strength[node] = chain_strength.get(node, 0) + eps_val
        chain_max_eps[node] = max(chain_max_eps.get(node, 0), eps_val)
    
    # 美股锚定信号
    us_signal_text = ""
    us_strong_buy = False
    us_strong_buy_node = None  # 大涨的美股对应的产业链
    
    # 美股→产业链映射
    US_TO_CHAIN = {
        "NVDA": "GPU/AI芯片",
        "MU": "HBM存储",
        "TSLA": "大模型/AI应用",
        "AVGO": "光模块",
        "AMD": "GPU/AI芯片",
        "DELL": "AI服务器",
        "VRT": "液冷散热",
    }
    
    for name, info in us_data.items():
        try:
            chg = float(info.get("chg_pct", 0))
            if abs(chg) > 0.5:
                direction = "📈" if chg > 0 else "📉"
                us_signal_text += f"{direction} {name} {chg:+.2f}% "
                if chg > 3:
                    us_strong_buy = True
                    for ticker, chain_node in US_TO_CHAIN.items():
                        if ticker in name.upper():
                            us_strong_buy_node = chain_node
        except:
            pass
    
    # 今日操作清单
    picks = []
    seen_stocks = set()
    
    for r in all_results:
        stock_info = r.get("top_stock", "")
        if not stock_info or stock_info in seen_stocks:
            continue
        seen_stocks.add(stock_info)
        
        message = r.get("message", "")
        node = r.get("node", "?")
        eps = r.get("eps_delta", "?")
        inst = r.get("inst", "0/100")
        hm = r.get("hm", "0/100")
        
        try:
            eps_val = float(eps.replace("%", ""))
        except:
            eps_val = 0
        try:
            inst_val = float(inst.replace("/100", ""))
        except:
            inst_val = 0
        try:
            hm_val = float(hm.replace("/100", ""))
        except:
            hm_val = 0
        
        chain_msg_count = chain_counts.get(node, 0)
        max_eps = chain_max_eps.get(node, 0)
        
        # 判断条件
        is_major_msg = any(kw in message for kw in ["重大", "革命", "里程碑", "历史性", "全球首发", "史诗"])
        is_eps_very_strong = eps_val > 50
        is_eps_strong = eps_val > 20
        is_multi_msg = chain_msg_count >= 2
        is_us_match = us_strong_buy and us_strong_buy_node == node
        is_fund_strong = inst_val > 50 and hm_val > 50
        
        # 评分（累积）
        signal_score = 0
        reasons = []
        
        if is_major_msg:
            signal_score += 4
            reasons.append("重大消息")
        if is_eps_very_strong:
            signal_score += 3
            reasons.append(f"EPS+{eps_val:.0f}%")
        if is_eps_strong:
            signal_score += 2
            if "EPS" not in str(reasons):
                reasons.append(f"EPS+{eps_val:.0f}%")
        if is_multi_msg:
            signal_score += 2
            reasons.append(f"{chain_msg_count}条共振")
        if is_us_match:
            signal_score += 3
            reasons.append("美股大涨锚定")
        if is_fund_strong:
            signal_score += 1
            reasons.append("资金确认")
        
        # 输出
        if signal_score >= 7:
            strength = "🟢🟢🔥"
            action = "🔥强烈买入"
        elif signal_score >= 5:
            strength = "🟢🟢"
            action = "买入/加仓"
        elif signal_score >= 3:
            strength = "🟢"
            action = "关注建仓"
        elif signal_score >= 1:
            strength = "⚪"
            action = "关注等待"
        else:
            continue
        
        picks.append({
            "stock": stock_info,
            "node": node,
            "strength": strength,
            "reason": " | ".join(reasons),
            "action": action,
            "score": signal_score,
            "eps": eps,
            "inst": inst,
            "hm": hm,
        })
    
    # 按信号强度排序
    picks.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    return {
        "picks": picks[:5],
        "chain_summary": chain_counts,
        "chain_eps_strength": chain_strength,
        "us_signal": us_signal_text,
    }

# ══════════════════════════════════════════════════════════
# 输出与推送
# ══════════════════════════════════════════════════════════

def format_report(decision, all_results):
    """生成最终战情报告"""
    lines = []
    now = datetime.now()
    
    lines.append(f"📊 **早盘战情聚合 | {now.strftime('%m/%d %H:%M')}**")
    lines.append("")
    
    # 美股锚定
    us_signal = decision.get("us_signal", "")
    if us_signal:
        lines.append(f"**🌍 美股锚定**")
        lines.append(f"  {us_signal}")
        lines.append("")
    
    # 今日产业链热点
    chain_summary = decision.get("chain_summary", {})
    if chain_summary:
        lines.append(f"**🔥 今日热点产业链**")
        for node, cnt in sorted(chain_summary.items(), key=lambda x: x[1], reverse=True):
            eps_total = decision.get("chain_eps_strength", {}).get(node, 0)
            lines.append(f"  • {node}: {cnt}条消息, ΔEPS合计+{eps_total:.1f}%")
        lines.append("")
    
    # 今日操作清单
    picks = decision.get("picks", [])
    if picks:
        lines.append(f"**🎯 今日可操作标的 (T0-T3)**")
        lines.append("")
        for p in picks:
            lines.append(
                f"{p['strength']} **{p['stock']}** {p['node']}"
            )
            lines.append(f"   📝 理由: {p['reason']}")
            lines.append(f"   🎬 操作: {p['action']}")
            lines.append(f"   📊 EPS: {p.get('eps','?')} 机构: {p.get('inst','?')} 游资: {p.get('hm','?')}")
            lines.append("")
    else:
        lines.append(f"**⚪ 今日无强信号标的**")
        lines.append("  观望或关注美股锚定变化")
        lines.append("")
    
    # 单条消息明细
    lines.append(f"**📋 今日消息分析明细 ({len(all_results)}条)**")
    lines.append("")
    for r in all_results[:5]:
        lines.append(f"  • {r.get('message','')[:50]}")
        lines.append(f"    🎯 {r.get('node','?')} | EPS: {r.get('eps_delta','?')} | 贝叶斯: {r.get('bayesian','?')}")
        lines.append(f"    机构: {r.get('inst','?')} 游资: {r.get('hm','?')} | 决策: {r.get('decision','?')}")
        lines.append("")
    
    lines.append("---")
    lines.append(f"🏦 早盘战情聚合器 | {now.strftime('%m/%d %H:%M')}")
    
    return "\n".join(lines)

def push_to_wechat(report):
    """推送报告到微信"""
    try:
        from hermes_tools import send_message
        send_message(target="weixin", message=report)
        log("✅ 已推送到微信")
    except:
        log("⚠️ 微信推送不可用，仅输出到终端")

# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

def main():
    import sys as _sys
    
    dry_run = "--dry-run" in _sys.argv
    push = "--push" in _sys.argv
    
    log("📊 早盘战情聚合器启动...")
    
    # 1. 抓消息
    log("🔍 抓取今日科技新闻...")
    headlines = fetch_tech_news()
    log(f"  获取{len(headlines)}条科技新闻")
    if not headlines:
        # 兜底：用预设的关键消息
        headlines = [
            "英伟达Blackwell Ultra量产",
            "HBM3E存储芯片需求爆发",
            "光模块800G订单增长",
        ]
        log(f"  使用兜底消息: {headlines}")
    
    # 2. 美股锚定
    log("🌍 美股锚定数据...")
    us_data = fetch_us_anchors()
    for name, info in us_data.items():
        log(f"  {name}: {info.get('price','?')} ({info.get('chg_pct','0')}%)")
    
    # 3. 逐条分析
    log("⚡ V2.0消息分析...")
    all_results = []
    for h in headlines[:5]:
        log(f"  分析: {h[:40]}...")
        result, raw_output = analyze_message(h)
        if result:
            all_results.append(result)
            log(f"    → {result.get('node','?')} | {result.get('eps_delta','?')} | {result.get('decision','?')}")
    
    if not all_results:
        log("❌ 无有效分析结果")
        return
    
    # 4. 聚合决策
    log("🧠 信号聚合智能决策...")
    decision = build_decision(all_results, us_data)
    
    # 5. 生成报告
    log("📝 生成战情报告...")
    report = format_report(decision, all_results)
    print("\n" + "=" * 50)
    print(report)
    
    # 6. 推送
    if push:
        push_to_wechat(report)
    elif not dry_run:
        log("💡 如需微信推送，加 --push 参数")
    
    # 7. 保存复盘
    log("💾 保存复盘记录...")
    for r in all_results:
        try:
            r2 = subprocess.run(
                ["sqlite3", "-noheader", f"{DATA_DIR}/valuation_v2.db",
                 f"INSERT INTO review_records (message, chain_node, msg_type, msg_strength, champion_name, champion_score, alert_level, bayesian_value, avg_signal) VALUES ('{r.get('message','').replace(chr(39),chr(39)+chr(39))}', '{r.get('node','')}', '聚合', 3, '{r.get('top_stock','')}', {r.get('top_score','0/100').replace('/100','')}, '{r.get('decision','')}', {r.get('bayesian','0/100').replace('/100','')}, 55);"],
                capture_output=True, timeout=10, text=True
            )
        except:
            pass
    
    log("✅ 完成")

if __name__ == "__main__":
    main()
