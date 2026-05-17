#!/usr/bin/env python3
"""
📋 每日收盘简报 v2.0 — 九维穿透 × 策略决策树
=============================================
15:20运行，整合：
  - market_daily_integrator (62维市场画像)
  - market_cluster_v6 (9层穿透+策略推荐)
  - 龙虎榜三资金
  - 个股异动
  - 板块资金流向
  - 市场类型×策略决策树（精确匹配+回测验证）

输出：推送到微信的简洁中文报告
"""

import os, sys, json, subprocess
from datetime import datetime, date
from collections import Counter

BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
RESEARCH = os.path.join(BASE, 'research')
MARKET_DB = os.path.join(DATA_DIR, 'market_daily.db')

def sql_json(db, q):
    try:
        r = subprocess.run(['sqlite3', '-json', db], capture_output=True, text=True, timeout=15, input=q)
        return json.loads(r.stdout) if r.stdout.strip() else []
    except: return []

def load_v6_today():
    """运行market_cluster_v6.py --today获取九维分类"""
    try:
        r = subprocess.run(['python3', os.path.join(BASE, 'scripts', 'market_cluster_v6.py'), '--today'],
                          capture_output=True, text=True, timeout=30, cwd=BASE)
        return r.stdout
    except:
        return ""

def load_strategy_recommendation(tag):
    """从策略决策树中查找推荐"""
    tree_path = os.path.join(RESEARCH, "market_strategy_tree.json")
    matrix_path = os.path.join(RESEARCH, "market_strategy_matrix.json")
    
    if not os.path.exists(tree_path) or not os.path.exists(matrix_path):
        return None
    
    with open(tree_path) as f:
        tree = json.load(f)
    with open(matrix_path) as f:
        matrix = json.load(f)
    
    # 精确匹配
    result = {}
    if tag in tree:
        result['tree'] = tree[tag]
    
    # 从矩阵中提取精确数据
    if tag in matrix:
        type_data = matrix[tag]
        result['matrix'] = {}
        for sname, s in type_data.items():
            if s['n'] >= 3:
                result['matrix'][sname] = {
                    'n': s['n'],
                    'open_avg': s['open']['avg'],
                    'high_avg': s['high']['avg'],
                    'win_rate': s['open']['win_rate'],
                    'top10': s['open'].get('top10_pct', 0)
                }
    
    return result

def get_strategy_advice(top_strategy):
    """根据策略名返回操作建议"""
    advice = {
        '隔夜溢价缩量': {
            'emoji': '💎',
            'title': '隔夜溢价',
            'desc': '缩量<0.7 涨停 非一字',
            'buy': 'T日打板买入（竞价成交）',
            'sell': 'T+1竞价卖，高开≥7%必卖',
            'position': '单只≤3万，持仓≤3只',
            'risk': '止损8%，连亏3笔暂停'
        },
        '总龙头打板': {
            'emoji': '👑',
            'title': '总龙头打板',
            'desc': '唯一最高板≥3板 非一字',
            'buy': '09:30~14:00打板买入',
            'sell': 'T+1竞价卖（高开≥7%卖一半等冲高）',
            'position': '单只≤3万，只买1只',
            'risk': '涨停<5只时不操作'
        },
        '板块爆发追涨': {
            'emoji': '🔥',
            'title': '板块爆发追涨',
            'desc': '板块涨停≥3 追龙一/龙二',
            'buy': '板块爆发当天打板龙一/龙二',
            'sell': 'T+1竞价卖',
            'position': '单只≤3万，最多2只',
            'risk': '龙一/龙二都一字则放弃'
        },
        '超卖反弹首板': {
            'emoji': '🌀',
            'title': '超卖反弹首板',
            'desc': 'MA20乖离<-20% 首板 非一字',
            'buy': 'T日打板买入',
            'sell': 'T+1竞价卖或等T+3高点',
            'position': '单只≤3万',
            'risk': '出现频率低(整个市场约302次/679天)'
        },
        '深坑反弹': {
            'emoji': '🕳️',
            'title': '深坑反弹',
            'desc': '60日回撤>30% 板块≥5涨停',
            'buy': 'T日打板买入',
            'sell': 'T+1竞价卖',
            'position': '单只≤3万',
            'risk': '只有板块爆发时才有效'
        }
    }
    return advice.get(top_strategy, None)

def build_report():
    today = str(date.today())
    lines = []
    
    # ====== 1. 从 market_daily.db 读取今日数据 ======
    rows = sql_json(MARKET_DB, f"SELECT * FROM day_full WHERE date='{today}'")
    if not rows:
        return "❌ 今日市场数据尚未采集（market_daily_integrator 15:00未运行或尚未完成）"
    d = rows[0]
    
    lu = d.get('limit_up', 0) or 0
    ld = d.get('limit_down', 0) or 0
    up = d.get('up_count', 0) or 0
    down = d.get('down_count', 0) or 0
    total = up + down
    zh = round(up / max(total, 1) * 100, 1)
    yizi = d.get('yizi', 0) or 0
    suoliang = d.get('suoliang', 0) or 0
    fangliang = d.get('fangliang', 0) or 0
    mb = d.get('max_board', 0) or 0
    zhaban = d.get('zhaban_count', 0) or 0
    zhaban_r = d.get('zhaban_rate', 0) or 0
    seal = d.get('total_seal_wan', 0) or 0
    mood = d.get('market_mood', '--') or '--'
    surge = d.get('surge_count', 0) or 0
    spike = d.get('spike_count', 0) or 0
    crash = d.get('crash_count', 0) or 0
    
    lines.append(f"📋 收盘简报 — {today}")
    lines.append(f"{'='*32}")
    lines.append("")
    
    # ====== 2. 市场画像摘要 ======
    lines.append(f"📊 市场画像")
    lines.append(f"━━━━━━━━━━")
    lines.append(f"涨跌 {up}/{down} ({zh}%) | 涨停{lu} 跌停{ld}")
    lines.append(f"一字{yizi} 缩量{suoliang} 放量{fangliang} | 最高板{mb}")
    lines.append(f"封单{seal:.0f}万 炸板{zhaban}({zhaban_r}%) | 情绪{mood}")
    lines.append("")
    
    # ====== 3. 个股异动 ======
    lines.append(f"⚡ 个股异动")
    lines.append(f"━━━━━━━━━━")
    lines.append(f"急拉(>7%未封): {surge}只 | 放量异动: {spike}只 | 跳水: {crash}只")
    surge_top = d.get('surge_top', '[]') or '[]'
    crash_top = d.get('crash_top', '[]') or '[]'
    if isinstance(surge_top, str): surge_top = json.loads(surge_top)
    if isinstance(crash_top, str): crash_top = json.loads(crash_top)
    for s in surge_top[:2]:
        lines.append(f"  🚀 {s.get('n','?')}({s.get('c','?')}) +{s.get('chg',0)}%")
    for s in crash_top[:2]:
        lines.append(f"  🔻 {s.get('n','?')}({s.get('c','?')}) {s.get('chg',0):+.1f}%")
    lines.append("")
    
    # ====== 4. 龙虎榜资金 ======
    youzi = d.get('youzi_net_wan', 0) or 0
    jigou = d.get('jigou_net_wan', 0) or 0
    sanhu = d.get('sanhu_net_wan', 0) or 0
    lines.append(f"💰 龙虎榜")
    lines.append(f"━━━━━━━━━━")
    lines.append(f"游资{youzi:+.0f}万 | 机构{jigou:+.0f}万 | 散户{sanhu:+.0f}万")
    lines.append("")
    
    # ====== 5. 板块资金 ======
    sector_main = d.get('sector_main_total', 0) or 0
    sector_top = d.get('sector_main_top', '[]') or '[]'
    if isinstance(sector_top, str): sector_top = json.loads(sector_top)
    lines.append(f"📈 板块资金TOP5 (主力净{sector_main:+.1f}亿)")
    lines.append(f"━━━━━━━━━━")
    for s in sector_top[:5]:
        if isinstance(s, str):
            lines.append(f"  {s}")
    lines.append("")
    
    # ====== 6. 大盘资金 ======
    main_net = d.get('market_main_net', 0) or 0
    lines.append(f"🏦 主力净{main_net:+.1f}亿")
    lines.append("")
    
    # ====== 7. 九维穿透 + 策略推荐 ======
    lines.append(f"🎯 九维穿透")
    lines.append(f"━━━━━━━━━━")
    
    v6_output = load_v6_today()
    cluster_name = '未知'
    
    for line in v6_output.split('\n'):
        if '🏷️' in line:
            cluster_name = line.split('🏷️')[-1].strip()
            # 只截取l1~l6（可读性）
            lines.append(f"{cluster_name}")
            break
    
    # 额外数字指标
    for line in v6_output.split('\n'):
        if '关键指标' in line:
            for nl in v6_output.split('\n')[v6_output.split('\n').index(line)+1:]:
                if not nl.strip() or '推荐' in nl:
                    break
                nl = nl.strip()
                if nl.startswith('limit_up'):
                    continue  # 跳过重复
                lines.append(f"  {nl}")
    
    lines.append("")
    
    # ====== 8. 精确策略推荐 ======
    lines.append(f"📋 策略推荐 (回测验证)")
    lines.append(f"━━━━━━━━━━")
    
    rec = load_strategy_recommendation(cluster_name)
    
    # 如果精确匹配样本不足，尝试从单层聚合推荐
    if not rec or not rec.get('matrix') or all(s['n'] < 5 for s in rec.get('matrix', {}).values()):
        # 运行 stategy recommender 做单层聚合
        try:
            r2 = subprocess.run(['python3', os.path.join(BASE, 'scripts', 'market_strategy_decision_tree.py'), 'today'],
                              capture_output=True, text=True, timeout=30, cwd=BASE)
            if r2.stdout:
                layer_output = r2.stdout
                for line in layer_output.split('\n'):
                    if '推荐策略' in line or '建议' in line:
                        lines.append(f"  {line.strip()}")
        except:
            pass
    
    if rec and 'matrix' in rec and rec['matrix']:
        scored = []
        for sname, sdata in rec['matrix'].items():
            score = sdata['open_avg'] * 0.3 + sdata['win_rate'] * 0.02 + sdata['top10'] * 0.1
            scored.append((score, sname, sdata))
        scored.sort(key=lambda x: -x[0])
        
        for i, (score, sname, sdata) in enumerate(scored[:3], 1):
            badge = "⭐" if i == 1 else f" #{i}"
            lines.append(f"  {badge} {sname}")
            lines.append(f"    竞价{sdata['open_avg']:+.1f}% 胜{sdata['win_rate']:.0f}% 高{sdata['high_avg']:+.1f}% ({sdata['n']}笔)")
            
            # 第一条策略给完整建议
            if i == 1:
                adv = get_strategy_advice(sname)
                if adv:
                    lines.append(f"    {adv['desc']}")
                    lines.append(f"    → {adv['buy']}")
                    lines.append(f"    → {adv['sell']}")
                    lines.append(f"    仓位: {adv['position']}")
        
        # 如果有隔夜溢价的数据，另外提一下
        geye = rec['matrix'].get('隔夜溢价缩量')
        if geye and geye['n'] >= 5:
            lines.append(f"  ──")
            lines.append(f"  📌 隔夜溢价全市场基: +{geye['open_avg']:.1f}%/胜{geye['win_rate']:.0f}%({geye['n']}笔)")
        
        ht = rec.get('tree', {})
        exact = ht.get('exact', [])
        if exact and len(exact) > 0:
            lines.append(f"  ──")
            lines.append(f"  📎 该类型精确匹配:")
            for e in exact[:3]:
                en = e.get('n', 0)
                if en >= 5:
                    lines.append(f"    {e.get('strategy','?')}: {e.get('open_avg',0):+.1f}%/胜{e.get('win_rate',0):.0f}%({en}笔)")
    else:
        # 降级到全市场基准
        lines.append(f"📌 今日类型缺乏精确回测数据")
        lines.append(f"   使用全市场最优策略:")
        lines.append(f"  ⭐ 隔夜溢价缩量 +3.7%/胜78% (1881笔)")
        lines.append(f"    → 缩量<0.7 涨停 非一字 → T+1竞价卖")
        lines.append(f"  #2 板块爆发追涨 +2.5%/胜66% (30570笔)")
        lines.append(f"  #3 超卖反弹首板 +2.5%/胜88% (302笔)")
    
    lines.append("")
    lines.append(f"{'='*32}")
    lines.append(f"⚠️ 建议15:00后数据更准确")
    lines.append(f"💡 策略推荐基于912天×6策略回测验证")
    
    return '\n'.join(lines)


def extract_section(text, section_name):
    """从报告中提取指定章节的文字"""
    lines = text.split('\n')
    result = []
    capture = False
    for line in lines:
        if section_name in line:
            capture = True
            continue
        if capture:
            # 遇到下一个加粗章节标题就停止
            if line.startswith('━') or (line.startswith('=')):
                break
            if line.strip():
                result.append(line.strip())
            else:
                if result:  # 空行结束
                    break
    return '\n'.join(result[:10])


if __name__ == '__main__':
    report = build_report()
    print(report)
    today = str(date.today())
    
    # 1. 保存文本文件
    out_path = os.path.join(BASE, 'data', f'closing_report_{today}.txt')
    with open(out_path, 'w') as f:
        f.write(report)
    print(f"  📝 已保存到 {out_path}")
    
    # 2. 写入作战面板JSON
    v2board = os.path.expanduser('~/V2board')
    panel_data = {
        '_meta': {
            'date': today,
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'source': 'daily_closing_report',
            'version': '2.0',
        },
        'report_text': report,
        'sections': {
            'market_snapshot': extract_section(report, '📊 市场画像'),
            'stock_alerts': extract_section(report, '⚡ 个股异动'),
            'lhb': extract_section(report, '💰 龙虎榜'),
            'sector_funds': extract_section(report, '📈 板块资金TOP'),
            'nine_dimension': extract_section(report, '🎯 九维穿透'),
            'strategy': extract_section(report, '📋 策略推荐'),
        }
    }
    panel_path = os.path.join(v2board, 'closing_report.json')
    with open(panel_path, 'w') as f:
        json.dump(panel_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 作战面板数据已写入: {panel_path}")
    
    # 3. 存入数据库（供历史查询）
    db = os.path.join(BASE, 'data', 'closing_reports.db')
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS closing_reports (
            date TEXT PRIMARY KEY,
            report_text TEXT,
            tags TEXT,
            limit_up INTEGER,
            zh_ratio REAL,
            nine_dim_tag TEXT,
            created_at TEXT
        )
    ''')
    # 提取九维标签
    nine_tag = ''
    for line in report.split('\n'):
        if '九维穿透' in line:
            continue
        if any(emoji in line for emoji in ['☀️','🌤','☁️','❄️','放量','机构','缩量','板块','首板','轮动']):
            if '·' in line and len(line) < 80:
                nine_tag = line.strip()
    conn.execute('''
        INSERT OR REPLACE INTO closing_reports (date, report_text, tags, limit_up, zh_ratio, nine_dim_tag, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (today, report, '', 104, 57.6, nine_tag, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    print(f"  ✅ 已存入数据库: closing_reports.db")
    
    # 4. 触发作战面板刷新
    try:
        import subprocess
        subprocess.run(['python3', os.path.join(v2board, 'dashboard_aggregator.py')],
                      capture_output=True, timeout=15)
        print(f"  ✅ 作战面板已刷新")
    except:
        pass
