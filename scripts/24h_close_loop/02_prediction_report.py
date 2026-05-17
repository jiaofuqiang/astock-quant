#!/usr/bin/env python3
"""
【17:35 预测报告】龙虎榜+十大流通股+盘后报告 → 多方向预测

大盘预测3方向：强更强/强转弱/震荡 (带概率)
题材预测2方向：主线持续/主线轮动 (带候选板块)
个股预测3方向：强更强/轮动/转弱 (带候选个股)
游资/量化/机构表现预测
"""
import sys, os, json, sqlite3
from datetime import date, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 17:35 预测报告 {TODAY}")
print("="*60)

# ============================================================
# 1. 加载盘后报告
# ============================================================
afternoon = load_json_or_empty(report_filename(AFTERNOON_DIR, 'afternoon'))
if not afternoon:
    # 尝试从文件读取
    afternoon = load_json_or_empty(os.path.join(AFTERNOON_DIR, f'afternoon_{TODAY}.json'))
print(f"  盘后报告加载: {'✅' if afternoon else '❌ 无数据'}")

# ============================================================
# 2. 获取龙虎榜数据
# ============================================================
def get_lhb_data():
    """从bundle或LHB_DB获取龙虎榜数据"""
    bundle = load_json_or_empty(BUNDLE_JSON)
    lhb = bundle.get('lhb', {}) if bundle else {}
    if lhb and lhb.get('stocks'):
        return lhb
    try:
        db = sqlite3.connect(LHB_DB)
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT code, name, reason, buy_amount, sell_amount, net_amount,
                   seat_type, seat_name, date
            FROM lhb_records WHERE date=?
            ORDER BY net_amount DESC LIMIT 30
        """, (TODAY,)).fetchall()
        db.close()
        if rows:
            stocks = defaultdict(lambda: {'code':'','name':'','reasons':[],'seats':[]})
            for r in rows:
                d = dict(r)
                stocks[d['code']] = d
            return {'stocks': list(stocks.values())[:20], 'summary': f'共{len(stocks)}只上龙虎榜'}
    except Exception as e:
        print(f"  [WARN] 龙虎榜DB查询失败: {e}")
    return {}

lhb = get_lhb_data()
lhb_stocks = lhb.get('stocks', [])
print(f"  龙虎榜: {len(lhb_stocks)}只上榜")

# ============================================================
# 3. 获取十大流通股股东新进（游资新进未上龙虎榜）
# ============================================================
def get_holder_new():
    """从holder_new.db获取最新十大流通股股东新进"""
    try:
        db = sqlite3.connect(HOLDER_NEW)
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT h.code, s.name, h.holder_name, h.holder_type, h.amount,
                   h.change_reason, h.date
            FROM holder_new h
            LEFT JOIN stock_names s ON h.code=s.code
            WHERE h.date=?
            ORDER BY h.amount DESC LIMIT 20
        """, (TODAY,)).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"  [WARN] 十大流通股查询失败: {e}")
        return []

holders = get_holder_new()
print(f"  十大流通股新进: {len(holders)}条")

# ============================================================
# 4. 多方向预测
# ============================================================

# ---- 4a. 大盘预测3方向 ----
def predict_market():
    """基于盘后数据预测明日大盘"""
    market_data = afternoon.get('market', {})
    sh = market_data.get('上证', {})
    sz = market_data.get('深证', {})
    sh_pct = sh.get('change_pct', 0)

    # 场景概率计算
    scenarios = []

    # 强更强：今日大涨+量增+涨停多
    if sh_pct > 0.5:
        scenarios.append({
            'scenario': '强更强',
            'probability': max(30, min(70, int(50 + sh_pct * 10))),
            'condition': f'今日+{sh_pct:.2f}%，惯性上冲',
        })
    else:
        scenarios.append({
            'scenario': '强更强',
            'probability': 30,
            'condition': '大盘中性，高开可能冲高',
        })

    # 强转弱：今日大涨后缩量/涨停减少
    total_limit = sum(s.get('limit_up',0) for s in afternoon.get('sectors_top5',[]))
    if total_limit < 3:
        scenarios.append({
            'scenario': '强转弱',
            'probability': 40,
            'condition': f'涨停仅{total_limit}只，热点不足',
        })
    else:
        scenarios.append({
            'scenario': '强转弱',
            'probability': 25,
            'condition': '利好出尽可能调整',
        })

    # 震荡
    scenarios.append({
        'scenario': '震荡',
        'probability': 100 - sum(s['probability'] for s in scenarios),
        'condition': '方向不明，多空平衡',
    })

    # 按概率排序
    scenarios.sort(key=lambda x: x['probability'], reverse=True)
    return scenarios

market_scenarios = predict_market()
print(f"  大盘预测:")
for s in market_scenarios:
    print(f"    {s['scenario']} ({s['probability']}%) - {s['condition']}")

# ---- 4b. 题材预测2方向 ----
def predict_sectors():
    scenarios = []
    # 今日最强题材
    strongest = afternoon.get('strongest_themes', [])
    sectors_top5 = afternoon.get('sectors_top5', [])

    # 主线持续：今日强势且有持续性
    if strongest:
        scenarios.append({
            'scenario': '主线持续',
            'probability': 55,
            'sectors': strongest,
            'condition': f'今日最强题材{",".join(strongest)}可能持续',
        })
    else:
        scenarios.append({
            'scenario': '主线持续',
            'probability': 30,
            'sectors': ['光模块','AI算力','存储芯片'],
            'condition': '无明确强势题材，关注AI主线',
        })

    # 主线轮动
    # 看今日哪些板块有异动但未涨停
    hot_names = [s['name'] for s in sectors_top5 if s['limit_up'] < 3]
    scenarios.append({
        'scenario': '主线轮动',
        'probability': 100 - scenarios[0]['probability'],
        'sectors': hot_names if hot_names else ['机器人','新能源','消费电子'],
        'condition': '高位板块分歧时，低位题材可能接力',
    })

    return scenarios

sector_scenarios = predict_sectors()
print(f"  题材预测:")
for s in sector_scenarios:
    print(f"    {s['scenario']} ({s['probability']}%) - 板块:{s['sectors'][:3]}")

# ---- 4c. 个股预测3方向 ----
def predict_stocks():
    scenarios = []
    stocks = afternoon.get('stocks_top10', [])
    # 强更强：今日涨停龙头明日继续
    if stocks:
        leaders = [s for s in stocks if s.get('board_count',0) >= 3]
        scenarios.append({
            'scenario': '强更强',
            'probability': 40 if leaders else 20,
            'stocks': [s['name'] for s in (leaders or stocks[:3])],
            'condition': '龙头连板持续，缩量加速',
        })
    else:
        scenarios.append({
            'scenario': '强更强',
            'probability': 20,
            'stocks': [],
            'condition': '无明确强势个股',
        })
    # 轮动
    scenarios.append({
        'scenario': '轮动',
        'probability': 35,
        'stocks': [s['name'] for s in stocks[:5]] if stocks else [],
        'condition': '龙头分歧后，新标的接力',
    })
    # 转弱
    scenarios.append({
        'scenario': '转弱',
        'probability': 100 - sum(s['probability'] for s in scenarios),
        'stocks': [],
        'condition': '连续涨停后高位见顶，注意风险',
    })
    return scenarios

stock_scenarios = predict_stocks()
print(f"  个股预测:")
for s in stock_scenarios:
    print(f"    {s['scenario']} ({s['probability']}%) - {s['condition']}")

# ---- 4d. 游资/量化/机构表现预测 ----
def predict_funds():
    """基于今日数据预测明日三资金动机"""
    funds = afternoon.get('funds_performance', {})
    yz = funds.get('youzi', 0)
    lh = funds.get('lianghua', 0)
    jg = funds.get('jigou', 0)
    total_limit = sum(s.get('limit_up',0) for s in afternoon.get('sectors_top5',[]))
    lhb_count = len(lhb_stocks)

    predictions = {}
    # 游资：龙虎榜多→活跃
    if lhb_count >= 10:
        predictions['youzi'] = '活跃打板，重点关注龙虎榜标的'
    elif lhb_count >= 5:
        predictions['youzi'] = '中等活跃，首选首板跟风'
    else:
        predictions['youzi'] = '偏谨慎，减少打板操作'

    # 量化：涨停多+量比大→量化参与度高
    if total_limit >= 5:
        predictions['lianghua'] = '量化积极参与，关注趋势加速标的'
    else:
        predictions['lianghua'] = '量化偏观望，减少追涨操作'

    # 机构：龙虎榜机构席位多→机构活跃
    jigou_seats = sum(1 for s in lhb_stocks if isinstance(s.get('seat_name',''),str) and '机构' in s.get('seat_name',''))
    if jigou_seats >= 3:
        predictions['jigou'] = '机构活跃，关注机构净买入标的'
    else:
        predictions['jigou'] = '机构偏观望，减少趋势持仓'

    return predictions

fund_preds = predict_funds()
print(f"  资金预测:")
for k,v in fund_preds.items():
    print(f"    {k}: {v}")

# ============================================================
# 5. 组装预测报告
# ============================================================
report = {
    'type': 'prediction_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'market_scenarios': market_scenarios,
    'sector_scenarios': sector_scenarios,
    'stock_scenarios': stock_scenarios,
    'fund_predictions': fund_preds,
    'lhb_count': len(lhb_stocks),
    'lhb_stocks': [{'name':s.get('name',''),'reason':s.get('reason','')[:50]} for s in lhb_stocks[:10]],
    'holder_new': [{'name':h.get('name',''),'holder':h.get('holder_name','')} for h in holders[:5]],
    'raw_data': {
        'lhb': lhb_stocks[:5] if lhb_stocks else [],
        'holders': holders[:5],
    }
}

report_file = report_filename(PREDICTION_DIR, 'prediction')
os.makedirs(os.path.dirname(report_file), exist_ok=True)
with open(report_file, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"  预测报告已保存: {report_file}")
print(f"\n[24h] 17:35 预测报告完成 ✅")
