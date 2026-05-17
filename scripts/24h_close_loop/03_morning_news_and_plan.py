#!/usr/bin/env python3
"""
【08:00 消息面报告 + 交易计划】盘前新闻采集→打分→题材池→股票池→多方向交易计划

消息来源：华尔街见闻(内部JSON)/新闻_data/dashboard_bundle
输出：《消息面报告》+ 《交易计划》
"""
import sys, os, json, urllib.request, re
from datetime import date, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 08:00 消息面 + 交易计划 {TODAY}")
print("="*60)

# ============================================================
# 1. 加载昨日预测报告
# ============================================================
prediction = load_json_or_empty(yest_report_filename(PREDICTION_DIR, 'prediction'))
if not prediction:
    prediction = {
        'market_scenarios': [{'scenario':'震荡','probability':60,'condition':'默认'}],
        'sector_scenarios': [{'scenario':'主线持续','probability':50,'sectors':['AI算力','光模块','存储芯片']}],
        'stock_scenarios': [{'scenario':'轮动','probability':50,'stocks':[]}],
    }
print(f"  预测报告加载: ✅ (大盘预测:{prediction.get('market_scenarios',[{}])[0].get('scenario','?')})")

# ============================================================
# 2. 采集消息面
# ============================================================
def collect_messages():
    """从bundle和news文件采集盘前消息"""
    articles = []
    concepts = []

    # 来源1：bundle中的news
    bundle = load_json_or_empty(BUNDLE_JSON)
    if bundle:
        news = bundle.get('news', {})
        if isinstance(news, dict):
            articles = news.get('articles', [])
        concepts = bundle.get('news_concepts', [])
        print(f"  从bundle获取: {len(articles)}条消息, {len(concepts)}个概念")

    # 来源2：news_with_concepts.json
    news_json = os.path.join(V2BOARD, 'news_with_concepts.json')
    nc = load_json_or_empty(news_json)
    if nc and isinstance(nc, dict) and nc.get('articles'):
        # 合并去重
        existing_titles = {a.get('title','') for a in articles}
        for a in nc['articles']:
            if a.get('title','') not in existing_titles:
                articles.append(a)
        if nc.get('concepts'):
            concepts = list(set(concepts + nc.get('concepts',[])))

    print(f"  合并后: {len(articles)}条消息, {len(concepts)}个概念")
    return articles, concepts

articles, concepts = collect_messages()

# ============================================================
# 3. 消息打分（-10 ~ +10）
# ============================================================
# 概念→板块映射
CONCEPT_SECTOR_MAP = {
    'AI算力': ['AI算力/服务器', 'AI芯片', '数据中心'],
    'AI芯片': ['AI芯片', '半导体'],
    '光模块': ['光模块与光通信', '液冷与散热'],
    '光通信': ['光模块与光通信'],
    '存储': ['存储芯片', '半导体'],
    '存储芯片': ['存储芯片', '半导体'],
    '机器人': ['机器人', '减速器'],
    '人形机器人': ['机器人', '减速器'],
    '新能源': ['新能源汽车', '锂电池', '储能', '光伏'],
    '新能源车': ['新能源汽车', '锂电池'],
    '锂电池': ['锂电池', '储能'],
    '固态电池': ['锂电池', '固态电池'],
    '汽车': ['新能源汽车', '汽车零部件'],
    '商业航天': ['商业航天', '军工'],
    '航天': ['商业航天', '军工'],
    '军工': ['军工', '低空经济'],
    '低空经济': ['低空经济', '军工'],
    '消费电子': ['消费电子'],
    '半导体': ['半导体', 'AI芯片', '存储芯片'],
    'AI应用': ['软件与应用', 'AI应用'],
    '数据中心': ['数据中心', 'AI算力/服务器'],
    '创新药': ['创新药', '医药'],
    '医药': ['创新药', '医疗器械'],
    '外贸': ['外贸/跨境电商'],
    '贸易': ['外贸/跨境电商'],
    '光伏': ['光伏', '储能'],
    '储能': ['储能', '锂电池'],
}

# 消息打分规则（基于消息内容判断利好/利空）
SCORE_RULES = [
    ('利好', ['突破','创新高','超预期','大增','政策支持','规划建设','加速推进','正式发布','合作','订单','投产','批准','获批','启动','涨价','扩产'], 5),
    ('利好', ['增长','提升','好转','回暖','放量','新高','落地','加码','上调','走强','融资'], 3),
    ('利好', ['关注','聚焦','布局','响应','推进','机会','看好','积极'], 1),
    ('利空', ['大跌','崩盘','下跌','利空','制裁','打压','风险','警告','暂停','放缓','下调','裁员'], -5),
    ('利空', ['回落','减少','下降','走弱','承压','低迷','亏损','违约'], -3),
    ('利空', ['谨慎','担忧','不确定','压力','困难'], -1),
]

def score_article(article):
    """对消息打分 -10 ~ +10"""
    title = article.get('title', '')
    score = 0
    reasons = []
    for direction, keywords, val in SCORE_RULES:
        for kw in keywords:
            if kw in title:
                score += val
                reasons.append(f"{direction}({kw})")
                break  # 每种规则只算一次
    # 识别相关概念
    matched_concepts = []
    for concept, sectors in CONCEPT_SECTOR_MAP.items():
        if concept in title:
            matched_concepts.extend(sectors)
    # 如果有概念但不匹配任何规则，+2基础分
    if matched_concepts and score == 0:
        score = 2
    return max(-10, min(10, score)), list(set(matched_concepts)), reasons

scored_articles = []
for a in articles:
    s, matched_sectors, reasons = score_article(a)
    if s != 0 or matched_sectors:  # 只保留有价值的消息
        a['score'] = s
        a['matched_sectors'] = matched_sectors
        scored_articles.append(a)

# 按分数排序
scored_articles.sort(key=lambda x: x.get('score',0), reverse=True)
print(f"\n  有价值消息: {len(scored_articles)}条")
for a in scored_articles[:10]:
    print(f"    [{a.get('score',0):+d}] {a.get('title','')[:50]}")

# ============================================================
# 4. 建立题材池（1号/2号/N号）
# ============================================================
# 统计各板块的消息热度
sector_heat = defaultdict(lambda: {'score':0, 'count':0, 'articles':[]})
for a in scored_articles:
    for sec in a.get('matched_sectors', []):
        sector_heat[sec]['score'] += a.get('score', 0)
        sector_heat[sec]['count'] += 1
        sector_heat[sec]['articles'].append(a['title'][:30])

# 按消息热度排序
hot_sectors = sorted(sector_heat.items(), key=lambda x: x[1]['score'], reverse=True)
print(f"\n  题材热度:")
pool_1, pool_2, pool_n = [], [], []
for i, (name, data) in enumerate(hot_sectors):
    tag = ''
    if i == 0: tag = '🔥🔥🔥 '
    elif i == 1: tag = '🔥🔥 '
    elif i == 2: tag = '🔥 '
    print(f"    {tag}{name} | 分{data['score']:+d} | {data['count']}条消息")
    if i == 0:
        pool_1.append(name)
    elif i == 1:
        pool_2.append(name)
    else:
        pool_n.append(name)

# ============================================================
# 5. 建立股票池（从bundle板块指数匹配）
# ============================================================
def build_stock_pool(sector_names):
    """从bundle的sector_index提取对应板块的个股"""
    bundle = load_json_or_empty(BUNDLE_JSON)
    si = bundle.get('sector_index', {})
    all_sectors = (si.get('hot_sectors',[]) + si.get('other_sectors',[])) if si else []
    
    pool = {}
    for sec_name in sector_names:
        # 模糊匹配
        for s in all_sectors:
            if sec_name in s.get('name','') or s.get('name','') in sec_name:
                pool[sec_name] = s
                break
    return pool

pool_1_stocks = build_stock_pool(pool_1)
pool_2_stocks = build_stock_pool(pool_2)
pool_n_stocks = build_stock_pool(pool_n)

print(f"\n  股票池:")
print(f"    1号池({pool_1}): {len(pool_1_stocks)}个板块")
print(f"    2号池({pool_2}): {len(pool_2_stocks)}个板块")
print(f"    N号池: {len(pool_n_stocks)}个板块")

# ============================================================
# 6. 多方向交易计划
# ============================================================
def generate_plans():
    plans = {}
    
    # 1号交易计划：打首板（最有把握）
    if pool_1:
        plans['plan_1'] = {
            'name': '打首板',
            'target_sectors': pool_1[:3],
            'target_stocks': [],  # 开盘后实时获取
            'trigger_rule': '板块有≥3只涨停 OR 板块涨幅>3%',
            'buy_condition': '开≥3% + 龙≥3板',
            'expected_return': '+6.9% ~ +7.6%',
            'confidence': 'high',
        }
    
    # 2号交易计划：打2板/3板
    pool_2_target = pool_2[:2] if pool_2 else ['AI算力','光模块']
    plans['plan_2'] = {
        'name': '打2板/3板',
        'target_sectors': pool_2_target,
        'target_stocks': [],
        'trigger_rule': '板块有≥2只涨停 OR 板块涨幅>2%',
        'buy_condition': '开≥5% + 龙≥3板 优先',
        'expected_return': '+7.0%',
        'confidence': 'medium',
    }
    
    # N号交易计划：低位潜伏
    pool_n_target = pool_n[:2] if pool_n else ['消费电子','新能源']
    plans['plan_3'] = {
        'name': '低位潜伏',
        'target_sectors': pool_n_target,
        'target_stocks': [],
        'trigger_rule': '板块有≥1只涨停 OR 龙头开>5%',
        'buy_condition': '缩量<0.7 + 竞价涨幅<2%',
        'expected_return': '+3.5%',
        'confidence': 'low',
    }
    
    return plans

plans = generate_plans()
print(f"\n  交易计划:")
for k, v in plans.items():
    print(f"    {k}({v['name']}): {v['target_sectors']} | {v['trigger_rule']}")

# ============================================================
# 7. 保存
# ============================================================
news_report = {
    'type': 'news_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'total_articles': len(articles),
    'scored_articles': len(scored_articles),
    'concepts': concepts,
    'top_articles': [{
        'title': a['title'][:60],
        'score': a.get('score',0),
        'sectors': a.get('matched_sectors',[]),
    } for a in scored_articles[:15]],
    'sector_heat': [{
        'name': name,
        'score': data['score'],
        'count': data['count'],
    } for name, data in hot_sectors],
}

plan_report = {
    'type': 'trade_plan',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'market_prediction': prediction.get('market_scenarios',[{}])[0],
    'sector_pools': {
        'pool_1': {'sectors': pool_1, 'stocks_count': len(pool_1_stocks)},
        'pool_2': {'sectors': pool_2, 'stocks_count': len(pool_2_stocks)},
        'pool_n': {'sectors': pool_n, 'stocks_count': len(pool_n_stocks)},
    },
    'plans': plans,
}

os.makedirs(NEWS_DIR, exist_ok=True)
os.makedirs(PLAN_DIR, exist_ok=True)
with open(report_filename(NEWS_DIR, 'news'), 'w', encoding='utf-8') as f:
    json.dump(news_report, f, ensure_ascii=False, indent=2)
with open(report_filename(PLAN_DIR, 'plan'), 'w', encoding='utf-8') as f:
    json.dump(plan_report, f, ensure_ascii=False, indent=2)
print(f"\n  消息面报告已保存: {report_filename(NEWS_DIR, 'news')}")
print(f"  交易计划已保存: {report_filename(PLAN_DIR, 'plan')}")
print(f"\n[24h] 08:00 消息面+交易计划完成 ✅")
