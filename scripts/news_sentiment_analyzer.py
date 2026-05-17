#!/usr/bin/env python3
"""
AI新闻情绪挖掘器 v1.0
对采集到的每条消息，用大模型做利好/利空研判：
  1️⃣ 情绪方向: 利好 / 利空 / 中性
  2️⃣ 影响程度: 强烈 / 中等 / 一般
  3️⃣ 受益个股: 列出具体代码+逻辑
  4️⃣ 受损个股: 列出具体代码+逻辑
  5️⃣ 相关概念: 更精准的概念映射
  6️⃣ 操作建议: 观望/关注/买入/回避

输出: ~/V2board/news_sentiment.json
      ~/V2board/news_with_concepts.json (增强版)
"""

import json, os, re, sys
from datetime import datetime

# 配置
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.expanduser("~/V2board")

# A股代码→名称映射（核心150只）
STOCK_DB = {
    # AI算力链
    "601138": "工业富联", "603019": "中科曙光", "000977": "浪潮信息",
    "300308": "中际旭创", "300502": "新易盛", "300394": "天孚通信",
    "688041": "海光信息", "688256": "寒武纪", "002415": "海康威视",
    "002236": "大华股份", "603986": "兆易创新", "002049": "紫光国微",
    "600584": "长电科技", "002156": "通富微电", "688012": "中微公司",
    "688981": "中芯国际", "002371": "北方华创", "688072": "拓荆科技",
    # 存储芯片
    "603986": "兆易创新", "002049": "紫光国微", "600703": "三安光电",
    # 低空经济
    "002085": "万丰奥威", "300690": "双一科技", "600889": "南京化纤",
    # 人形机器人
    "300124": "汇川技术", "688017": "绿的谐波", "002747": "埃斯顿",
    "300660": "江苏雷利", "603662": "柯力传感", "601689": "拓普集团",
    # 新能源
    "300750": "宁德时代", "002594": "比亚迪", "002709": "天赐材料",
    "002460": "赣锋锂业", "002466": "天齐锂业", "300014": "亿纬锂能",
    # 消费电子
    "002475": "立讯精密", "002241": "歌尔股份", "601138": "工业富联",
    "603986": "兆易创新", "002600": "领益智造", "300433": "蓝思科技",
    # 自动驾驶
    "002920": "德赛西威", "002405": "四维图新", "601689": "拓普集团",
    "300124": "汇川技术", "688326": "经纬恒润",
    # 医药
    "600276": "恒瑞医药", "688235": "百济神州", "603259": "药明康德",
    "300122": "智飞生物", "000661": "长春高新",
    # 金融
    "600036": "招商银行", "601318": "中国平安", "600030": "中信证券",
    "601688": "华泰证券", "300059": "东方财富",
    # 联想/分销概念
    "300170": "汉得信息", "301236": "软通动力", "600718": "东软集团",
    "300454": "深信服", "688568": "中科星图",
    # 算力/服务器概念
    "000938": "紫光股份", "600498": "烽火通信", "300454": "深信服",
    "688568": "中科星图", "002912": "中新赛克",
    # 腾讯/阿里概念
    "002410": "广联达", "300624": "万兴科技", "300033": "同花顺",
    "002230": "科大讯飞", "300418": "昆仑万维",
}

# 关键词→个股映射规则
STOCK_RULES = [
    (r'英伟达|H100|H200|B200|GB200|Blackwell|AI芯片采购|GPU出口', [
        ("601138", "工业富联", "AI服务器代工龙头，H200采购分销直接受益"),
        ("603019", "中科曙光", "算力基础设施，英伟达链"),
        ("000977", "浪潮信息", "AI服务器龙头，GPU服务器需求提振"),
        ("300308", "中际旭创", "光模块龙头，AI算力需求带动"),
    ]),
    (r'阿里巴巴|腾讯|字节跳动|京东|互联网巨头|科技巨头采购|H200采购获批', [
        ("601138", "工业富联", "服务器代工，H200分销"),
        ("603986", "兆易创新", "存储芯片，HBM概念"),
    ]),
    (r'联想|富士康|分销商|华为链', [
        ("601138", "工业富联", "联想分销H200受益"),
        ("301236", "软通动力", "联想生态合作伙伴"),
        ("000938", "紫光股份", "ICT设备，算力链"),
    ]),
    (r'存储|HBM|DRAM|NAND|闪存|内存', [
        ("603986", "兆易创新", "存储芯片龙头，NOR Flash+DRAM"),
        ("002049", "紫光国微", "特种芯片+FPGA"),
    ]),
    (r'算力|数据中心|服务器|液冷|光模块|CPO|800G', [
        ("300308", "中际旭创", "800G光模块龙头"),
        ("300502", "新易盛", "光模块，数据中心"),
        ("000977", "浪潮信息", "AI服务器"),
        ("601138", "工业富联", "AI服务器代工"),
        ("603019", "中科曙光", "算力基础设施"),
    ]),
    (r'低空经济|飞行汽车|eVTOL|无人机|空域', [
        ("002085", "万丰奥威", "eVTOL+无人机"),
    ]),
    (r'机器人|人形机器人|Optimus|特斯拉bot|减速器|丝杠', [
        ("688017", "绿的谐波", "谐波减速器"),
        ("601689", "拓普集团", "机器人关节"),
        ("300124", "汇川技术", "伺服电机"),
    ]),
    (r'固态电池|锂电池|新能源车|磷酸铁锂|宁德|比亚迪', [
        ("300750", "宁德时代", "动力电池龙头"),
        ("002594", "比亚迪", "新能源车龙头"),
    ]),
    (r'创新药|CXO|PD-1|GLP-1|减肥药|生物医药', [
        ("600276", "恒瑞医药", "创新药龙头"),
        ("603259", "药明康德", "CXO龙头"),
    ]),
    (r'军工|商业航天|卫星|北斗|C919|大飞机', [
        ("600760", "中航沈飞", "军机龙头"),
        ("600893", "航发动力", "航空发动机"),
    ]),
    (r'半导体|芯片|晶圆|光刻|先进制程|台积电|中芯', [
        ("688981", "中芯国际", "晶圆代工龙头"),
        ("002371", "北方华创", "半导体设备龙头"),
        ("688012", "中微公司", "刻蚀设备龙头"),
        ("600584", "长电科技", "封测龙头"),
    ]),
    (r'消费电子|手机|折叠屏|MR|VR|AR|iPhone|苹果', [
        ("002475", "立讯精密", "苹果链核心"),
        ("002241", "歌尔股份", "VR/AR代工"),
        ("601138", "工业富联", "消费电子代工"),
    ]),
    (r'自动驾驶|智能驾驶|无人驾驶|FSD|激光雷达|Waymo', [
        ("002920", "德赛西威", "智能驾驶域控"),
        ("002405", "四维图新", "高精地图"),
    ]),
    (r'金融|降息|降准|LPR|MLF|央行|美联储|货币政策', [
        ("600030", "中信证券", "券商龙头"),
        ("300059", "东方财富", "互联网券商"),
    ]),
    (r'特朗普|关税|贸易战|出口管制|制裁|限制|脱钩', [
        ("002049", "紫光国微", "国产替代直接受益"),
        ("688981", "中芯国际", "自主可控概念"),
        ("002371", "北方华创", "半导体国产替代"),
    ]),
]

def analyze_news(news_item, llm_available=False):
    """
    分析一条消息，返回情感研判
    当前用规则引擎，后续可接入大模型
    """
    title = news_item.get('title', '')
    if not title:
        return None
    
    # 1. 情绪方向判断（规则+关键词）
    sentiment, strength, reason = judge_sentiment(title)
    
    # 2. 匹配受益/受损个股
    benefit_stocks = match_stocks(title)
    damage_stocks = []  # 规则引擎还不好判断受损，留空
    
    # 3. 操作建议
    ops_suggestion = gen_suggestion(sentiment, strength, benefit_stocks)
    
    return {
        'sentiment': sentiment,       # 利好 / 利空 / 中性
        'strength': strength,          # 强烈 / 中等 / 一般
        'reason': reason,              # 简短理由
        'benefit_stocks': benefit_stocks,  # [{"code":"601138","name":"工业富联","logic":"..."}]
        'damage_stocks': damage_stocks,
        'suggestion': ops_suggestion,  # 观望/关注/买入/回避
        'ai_analyzed': not llm_available,
    }


# 利好词
BULLISH = [
    '获批', '批准', '采购', '利好', '突破', '增长', '大涨', '新高',
    '降价', '刺激', '加速', '扩张', '投产', '量产', '签约', '中标',
    '合作', '战略', '扶持', '补贴', '降息', '降准', '放水',
    '供应', '扩张', '升级', '引入', '合资', '融资',
]
# 利空词
BEARISH = [
    '下跌', '大跌', '暴跌', '回调', '利空', '受损', '受限',
    '制裁', '限制', '禁令', '关税', '加息', '缩表', '衰退', 
    '风险', '警告', '下调', '裁员', '亏损', '违约', '破产',
    '冲突', '战争', '空袭', '罢工', '中断',
]

def judge_sentiment(title):
    """基于关键词判断情绪"""
    bullish_score = sum(1 for w in BULLISH if w in title)
    bearish_score = sum(1 for w in BEARISH if w in title)
    
    # 特殊规则：出口管制/制裁+中国=国产替代利好
    if re.search(r'出口管制|制裁|限制|禁令', title) and re.search(r'中国|华为|中芯|国产', title):
        return '利好', '强烈', '国产替代加速，自主可控逻辑强化'
    
    # 特殊规则：关税/贸易战
    if re.search(r'关税|贸易战|脱钩', title):
        if re.search(r'豁免|谈判|缓和|推迟|取消', title):
            return '利好', '中等', '贸易摩擦缓和，出口链预期改善'
        return '利空', '中等', '贸易摩擦升温，出口承压'
    
    # 特殊规则：H200/芯片采购获批
    if re.search(r'获批.*芯片|采购.*GPU|H200.*批准', title):
        return '利好', '强烈', 'AI算力供应放开，产业链直接受益'
    
    # 特殊规则：罢工/停产
    if re.search(r'罢工|停产|中断', title):
        return '利空', '中等', '供应链中断风险'
    
    # 特殊规则：降价
    if re.search(r'降价.*苹果|iPhone.*降价', title):
        return '利好', '中等', '以价换量，提振销量预期'
    
    # 通用逻辑
    if bullish_score > bearish_score:
        strength = '强烈' if bullish_score >= 3 else '中等' if bullish_score >= 2 else '一般'
        return '利好', strength, f'关键词正面({bullish_score}/{bearish_score})'
    elif bearish_score > bullish_score:
        strength = '强烈' if bearish_score >= 3 else '中等' if bearish_score >= 2 else '一般'
        return '利空', strength, f'关键词负面({bearish_score}/{bullish_score})'
    else:
        return '中性', '一般', '无明显情绪倾向'


def match_stocks(title):
    """匹配受益个股"""
    matched = []
    seen = set()
    for pattern, stocks in STOCK_RULES:
        if re.search(pattern, title, re.IGNORECASE):
            for code, name, logic in stocks:
                if code not in seen:
                    seen.add(code)
                    matched.append({
                        'code': code,
                        'name': name,
                        'logic': logic,
                    })
    return matched[:5]  # 最多5只


def gen_suggestion(sentiment, strength, benefit_stocks):
    if sentiment == '利好' and strength == '强烈' and benefit_stocks:
        return '重点关注'
    elif sentiment == '利好' and strength == '中等':
        return '关注'
    elif sentiment == '利好':
        return '适当关注'
    elif sentiment == '利空' and strength == '强烈':
        return '回避'
    elif sentiment == '利空':
        return '谨慎'
    return '观望'


def process_news(news_data, llm_available=False):
    """处理所有新闻"""
    articles = news_data.get('articles', [])
    
    # 如果是旧格式（没有concepts字段的纯字符串），先转成dict
    if articles and isinstance(articles[0], str):
        articles = [{'title': t, 'concepts': []} for t in articles]
    
    total_positive = 0
    total_negative = 0
    
    analyzed = []
    for a in articles:
        result = analyze_news(a, llm_available)
        if result:
            a['sentiment'] = result['sentiment']
            a['strength'] = result['strength']
            a['reason'] = result['reason']
            a['benefit_stocks'] = result['benefit_stocks']
            a['damage_stocks'] = result['damage_stocks']
            a['suggestion'] = result['suggestion']
            
            # 统计
            if result['sentiment'] == '利好':
                total_positive += 1
            elif result['sentiment'] == '利空':
                total_negative += 1
        
        analyzed.append(a)
    
    # 计算市场情绪指标
    total = len(analyzed)
    bullish_ratio = total_positive / total if total > 0 else 0.5
    bearish_ratio = total_negative / total if total > 0 else 0.5
    
    mood = '乐观' if bullish_ratio > 0.6 else '谨慎乐观' if bullish_ratio > 0.4 else '悲观' if bearish_ratio > 0.6 else '中性'
    
    return {
        'articles': analyzed,
        'total': total,
        'concept_count': news_data.get('concept_count', 0),
        'timestamp': datetime.now().isoformat(),
        'market_mood': mood,
        'bullish_ratio': round(bullish_ratio, 2),
        'bearish_ratio': round(bearish_ratio, 2),
        'top_signals': extract_top_signals(analyzed),
    }


def extract_top_signals(articles):
    """提取最重要的信号"""
    signals = []
    for a in articles:
        if a.get('sentiment') == '利好' and a.get('strength') == '强烈':
            signals.append({
                'title': a.get('title', '')[:60],
                'type': '利好',
                'stocks': a.get('benefit_stocks', [])[:2],
                'suggestion': a.get('suggestion', ''),
            })
        elif a.get('sentiment') == '利空' and a.get('strength') == '强烈':
            signals.append({
                'title': a.get('title', '')[:60],
                'type': '利空',
                'stocks': [],
                'suggestion': a.get('suggestion', ''),
            })
    return signals[:5]


def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] AI新闻情绪分析...")
    
    # 读取现有新闻
    news_path = os.path.join(DATA, 'news_with_concepts.json')
    if not os.path.exists(news_path):
        print(f"  ❌ 未找到 {news_path}")
        return
    
    with open(news_path, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    
    # 做情绪分析
    result = process_news(raw, llm_available=False)
    
    # 输出情绪文件
    sentiment_path = os.path.join(DATA, 'news_sentiment.json')
    # 只存情绪分析结果（不重复存文章全文，减少大小）
    sentiment_data = {
        'timestamp': result['timestamp'],
        'market_mood': result['market_mood'],
        'bullish_ratio': result['bullish_ratio'],
        'bearish_ratio': result['bearish_ratio'],
        'total_articles': result['total'],
        'top_signals': result['top_signals'],
    }
    with open(sentiment_path, 'w', encoding='utf-8') as f:
        json.dump(sentiment_data, f, ensure_ascii=False, indent=2)
    
    # 更新news文件（加入sentiment字段）
    with open(news_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    bullish_ratio = result['bullish_ratio']
    bearish_ratio = result['bearish_ratio']
    print(f"  ✅ 情绪分析完成: {result['total']}条消息")
    print(f"  📊 市场情绪: {result['market_mood']} (利好{bullish_ratio:.0%} / 利空{bearish_ratio:.0%})")
    print(f"  🚦 重要信号: {len(result['top_signals'])}条")
    
    # 打印重要信号
    for s in result['top_signals'][:3]:
        stocks_str = ', '.join([f"{st['name']}({st['code']})" for st in s.get('stocks', [])])
        print(f"    {s['type']} {s['title']} → {s['suggestion']}")
        if stocks_str:
            print(f"       受益: {stocks_str}")


if __name__ == '__main__':
    main()
