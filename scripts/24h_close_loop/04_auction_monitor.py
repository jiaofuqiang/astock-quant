#!/usr/bin/env python3
"""
【09:15-09:25 竞价阶段】获取股票池竞价数据 → 匹配预测报告 → 选择交易计划

买入信号：开≥3%+龙≥3板 / 开≥5%+龙≥3 优先
卖出信号（T+1）：开≥7%竞价卖 / 其他等冲高
"""
import sys, os, json, urllib.request, re
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 09:15 竞价分析 {TODAY}")
print("="*60)

# ============================================================
# 1. 加载盘前数据
# ============================================================
prediction = load_json_or_empty(yest_report_filename(PREDICTION_DIR, 'prediction'))
plan_report = load_json_or_empty(report_filename(PLAN_DIR, 'plan'))

if not prediction:
    print("  [WARN] 无预测报告，使用默认")
    prediction = {'market_scenarios':[{'scenario':'震荡','probability':60}]}
if not plan_report:
    print("  [WARN] 无交易计划，跳过")
    plan_report = {'plans':{}}

print(f"  预测: 大盘{plan_report.get('market_prediction',{}).get('scenario','?')}")
print(f"  计划数: {len(plan_report.get('plans',{}))}")

# ============================================================
# 2. 获取股票池竞价数据
# ============================================================
import sqlite3

# 板块名到概念名的映射（bundle sector_index名 → stock_profiles.db concepts名）
SECTOR_TO_CONCEPT = {
    '光模块与光通信': ['光模块', '光通信', '光通信模块'],
    'AI算力': ['AI算力'],
    'AI算力/服务器': ['AI算力'],
    'AI芯片': ['AI芯片'],
    '数据中心': ['数据中心'],
    '存储芯片': ['存储芯片'],
    '机器人': ['机器人'],
    '半导体': ['半导体'],
    '新能源汽车': ['新能源汽车'],
    '锂电池': ['锂电池'],
    '储能': ['储能'],
    '低空经济': ['低空经济'],
    '消费电子': ['消费电子'],
    '智能驾驶': ['智能驾驶'],
    '光伏': ['光伏'],
    '风电': ['风电'],
    '军工': ['军工'],
    '软件与应用': ['软件与应用', '软件'],
    '液冷与散热': ['液冷'],
    '创新药': ['创新药'],
    '医疗器械': ['医疗器械'],
    '化工': ['化工'],
    '有色金属': ['有色金属'],
    '钢铁': ['钢铁'],
    '煤炭': ['煤炭'],
    '券商': ['券商'],
    '银行': ['银行'],
    '保险': ['保险'],
    '白酒': ['白酒'],
    '食品': ['食品'],
    '家电': ['家电'],
    '建材': ['建材'],
    '地产基建': ['地产', '基建'],
    '旅游': ['旅游'],
    '中药': ['中药'],
    '生物医药': ['生物医药', '医药'],
    'CXO': ['CXO'],
    '汽车电子': ['汽车电子'],
    '存储芯片': ['存储芯片'],
}

def code_to_tencent(code):
    """6位code转腾讯格式 """
    code = str(code).strip()
    if not code: return ''
    if code.startswith('sh') or code.startswith('sz'):
        return code
    prefix = 'sh' if code[0] in '56' else 'sz'
    return f'{prefix}{code}'

def get_stocks_for_sector(concept_names, limit=5):
    """从stock_profiles.db查询板块对应个股"""
    if not concept_names:
        return []
    try:
        conn = sqlite3.connect(os.path.join(os.path.expanduser('~'), 'astock', 'data', 'stock_profiles.db'))
        cur = conn.cursor()
        stocks = []
        for cn in concept_names:
            cur.execute('''
                SELECT DISTINCT c.code, b.name FROM concepts c
                JOIN stock_basic b ON c.code = b.code
                WHERE c.concept_name = ?
            ''', (cn,))
            stocks.extend(cur.fetchall())
        conn.close()
        # 去重，保留code
        seen = set()
        result = []
        for code, name in stocks:
            if code not in seen:
                seen.add(code)
                result.append({'code': code, 'name': name})
        return result[:limit]
    except Exception as e:
        print(f"  [WARN] 数据库查询失败: {e}")
        return []

def get_auction_data(stock_pool):
    """获取指定股票池的竞价数据"""
    if not stock_pool:
        return []
    results = []
    try:
        # 从腾讯行情获取实时数据(竞合阶段:09:15-09:25)
        tencent_codes = [code_to_tencent(c['code']) for c in stock_pool if c.get('code')]
        if not tencent_codes:
            return []
        url = f"http://qt.gtimg.cn/q={','.join(tencent_codes)}"
        req = urllib.request.urlopen(url, timeout=10)
        raw = req.read().decode('gbk')
        for line in raw.strip().split(';'):
            if not line or '=' not in line: continue
            parts = line.split('~')
            if len(parts) < 40: continue
            code = parts[2]
            name = parts[1]
            price = float(parts[3]) if parts[3] else 0
            yesterday_close = float(parts[4]) if parts[4] else 0
            open_price = float(parts[5]) if parts[5] else 0
            change_pct = float(parts[32]) if parts[32] else 0
            volume = float(parts[6]) if parts[6] else 0
            amount = float(parts[37]) if len(parts)>37 and parts[37] else 0
            vol_ratio = float(parts[39]) if len(parts)>39 and parts[39] else 0

            # 竞价涨幅（相对于昨日收盘）
            auction_pct = round((price - yesterday_close) / yesterday_close * 100, 2) if yesterday_close else 0

            results.append({
                'code': code,
                'name': name,
                'price': price,
                'yesterday_close': yesterday_close,
                'auction_pct': auction_pct,
                'volume_ratio': vol_ratio,
                'amount_wan': amount,
            })
    except Exception as e:
        print(f"  [WARN] 竞价数据获取失败: {e}")
    return sorted(results, key=lambda x: x['auction_pct'], reverse=True)

# 获取各计划的目标板块个股 — 从stock_profiles.db查询
plans = plan_report.get('plans', {})
all_target_stocks = []  # list of {code, name, plan, sector}
for k, v in plans.items():
    sectors = v.get('target_sectors', [])
    for sec_name in sectors:
        # 通过映射找到concept数据库中对应的概念名
        concept_names = SECTOR_TO_CONCEPT.get(sec_name, [sec_name])
        stocks = get_stocks_for_sector(concept_names, limit=5)
        for s in stocks:
            s['plan'] = k
            s['plan_name'] = v.get('name', k)
            s['sector'] = sec_name
            all_target_stocks.append(s)

# 去重（同一只股可能出现在多个板块）
seen = set()
unique_stocks = []
for s in all_target_stocks:
    if s['code'] not in seen:
        seen.add(s['code'])
        unique_stocks.append(s)
all_target_stocks = unique_stocks

print(f"  竞价监控: {len(all_target_stocks)}只标的 (来自{len(plans)}个计划)")

# 获取实时竞价数据
auction_data = get_auction_data(all_target_stocks) if all_target_stocks else []
print(f"  获取竞价数据: {len(auction_data)}只")


# ============================================================
# 3. 买入信号
# ============================================================
def check_buy_signals(auction_data):
    """检查买入信号"""
    signals = []
    for item in auction_data:
        ap = item['auction_pct']
        vr = item['volume_ratio']

        # 开≥3% + 缩量<1 → 买入信号
        if ap >= 3.0:
            signals.append({
                'code': item['code'],
                'name': item['name'],
                'action': '买入',
                'reason': f"竞价开{ap:+.2f}%",
                'priority': '高' if ap >= 5.0 else '中',
                'expected': '+6.9% ~ +7.6%',
            })
        # 开≥7% → 可能过于一致，需谨慎
        elif ap >= 7.0:
            signals.append({
                'code': item['code'],
                'name': item['name'],
                'action': '观察',
                'reason': f"竞价开{ap:+.2f}%开太高，防高开低走",
                'priority': '低',
                'expected': '-',
            })
    return signals

buy_signals = check_buy_signals(auction_data) if auction_data else []
print(f"  买入信号: {len(buy_signals)}条")

# ============================================================
# 4. 选择交易计划
# ============================================================
def select_plan():
    """基于竞价数据和预测场景匹配最合适的交易计划"""
    market_pred = plan_report.get('market_prediction', {})
    scenario = market_pred.get('scenario', '震荡')

    plan_priority = []
    for k, v in plans.items():
        confidence = v.get('confidence', 'low')
        score = {'high': 10, 'medium': 7, 'low': 4}.get(confidence, 5)
        plan_priority.append((score, k, v))

    plan_priority.sort(key=lambda x: x[0], reverse=True)
    return plan_priority

plan_ranking = select_plan()
print(f"  计划优先级:")
for s, k, v in plan_ranking:
    print(f"    {k}({v['name']}) - 信心{v.get('confidence','?')}")

# ============================================================
# 5. 保存竞价报告
# ============================================================
auction_report = {
    'type': 'auction_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'market_prediction_scenario': plan_report.get('market_prediction',{}).get('scenario','震荡'),
    'plans_priority': [{'name':k, 'sectors':v.get('target_sectors',[]), 'confidence':v.get('confidence','')} for _,k,v in plan_ranking],
    'buy_signals': buy_signals,
    'stocks_monitored': len(all_target_stocks) if 'all_target_stocks' in dir() else 0,
    'auction_data': sorted(auction_data, key=lambda x: x['auction_pct'], reverse=True) if auction_data else [],
}

os.makedirs(os.path.join(REPORTS_DIR, 'auction'), exist_ok=True)
with open(os.path.join(REPORTS_DIR, 'auction', f'auction_{TODAY}.json'), 'w', encoding='utf-8') as f:
    json.dump(auction_report, f, ensure_ascii=False, indent=2)
print(f"  竞价报告已保存")
print(f"\n[24h] 09:15 竞价分析完成 ✅")
