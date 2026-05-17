#!/usr/bin/env python3
"""
📡 消息→新概念自动识别引擎 v1.0

核心功能：
  1. 接收消息 → 识别是否新概念
  2. 自动匹配相关个股（通过主营产品/经营范围/F10）
  3. 新概念写入 stock_profiles.db（concepts表）
  4. 输出推荐标的 + 详细匹配理由

用法：
  # 微信收到消息时调用
  python3 scripts/concept_messenger.py --news="英伟达宣布与康宁合作建厂"
  
  # 指定概念名（如果你已经知道是什么概念）
  python3 scripts/concept_messenger.py --concept="康宁概念" --desc="英伟达与康宁合作，新建3家美国工厂"
  
  # 只扫描已有概念库中的新概念（不匹配个股）
  python3 scripts/concept_messenger.py --concept="康宁概念" --scan-only

输出：
  - 新概念 → 自动写入 stock_profiles.db（source='auto_miner'）
  - 匹配到的个股 → 显示匹配理由
  - 面板数据 → 写入 ~/astock/new_concept_data.txt

数据源：
  - stock_profiles.db.concepts → 35,000+条概念映射（em/ths/auto/jyg/infer/synonym/ann）
  - stock_profiles.db.stock_basic → 股票基本信息+主营产品+经营范围
  - stock_profiles.db.stock_business → 主营构成（行业分类+产品分类）
  - fundamental.db.profit_data → 财务数据
"""

import os, sys, json, re, subprocess, time
from datetime import datetime
from collections import defaultdict

BASE = os.path.expanduser("~/astock")
PROFILE_DB = os.path.join(BASE, "data", "stock_profiles.db")
FUND_DB = os.path.join(BASE, "data", "fundamental.db")
PANEL_DATA = os.path.join(BASE, "new_concept_data.txt")

# ============================================================
# 内置产业关键词库 — 用于消息→概念自动分类
# ============================================================
INDUSTRY_KW = {
    # AI/算力
    'AI算力': ['ai算力', '算力基建', '算力网络', '智算中心', 'nvidia', 'nvda', 'gpu', 'blackwell', 'h100', 'h200', 'b200', '芯片算力'],
    '光模块': ['光模块', '光通信', '光互联', '800g', '1.6t', '硅光', 'cpo', 'lpo', '光引擎', '相干光'],
    '液冷': ['液冷', '浸没式液冷', '冷板液冷', '冷却液', '散热'],
    '服务器': ['ai服务器', '算力服务器', '服务器', '数据中心', 'idc'],
    
    # 存储芯片
    '存储芯片': ['存储芯片', 'hbm', 'dram', 'nand', 'nor flash', '闪存', '内存', '存储器', 'ddr5', '高带宽存储'],
    '半导体设备': ['半导体设备', '光刻机', '刻蚀', '薄膜沉积', '清洗设备', 'cmp', '检测设备', '晶圆'],
    '半导体材料': ['半导体材料', '硅片', '光刻胶', '气体', '靶材', 'cmp浆料', '抛光垫'],
    
    # 机器人
    '人形机器人': ['人形机器人', '人形', '机器人', 'optimus', 't机器人', '灵巧手', '关节', '丝杠', '减速器', '传感器'],
    
    # 低空经济
    '低空经济': ['低空经济', '飞行汽车', 'eVTOL', '无人机', '空管', '适航', '亿航', '苍穹'],
    
    # 新能源
    '固态电池': ['固态电池', '半固态', '固态电解质', '硫化物', '氧化物', '锂金属', '凝聚态电池'],
    '光伏': ['光伏', '太阳能', 'hjt', 'topcon', '钙钛矿', 'bc电池', '逆变器'],
    
    # 光纤/通信
    '康宁概念': ['康宁', 'corning', '光纤', '光缆', '光纤预制棒', '光纤连接器'],
    
    # 汽车
    '智能驾驶': ['智能驾驶', '自动驾驶', '无人驾驶', 'fsd', '智驾', '激光雷达', '毫米波雷达', '线控'],
    
    # AI应用
    'AI应用': ['ai应用', 'aigc', '大模型', 'chatgpt', 'deepseek', 'openai', '文本生成', '视频生成'],
}

# 手动标注的高确定性概念→个股映射（基于产业地位确认）
HIGH_CONFIDENCE_MAP = {
    # 光纤光缆/康宁概念
    '康宁概念': ['601869', '600522', '600487', '600498', '600745', '603083', '300394', '300620'],
    '光纤光缆': ['601869', '600522', '600487', '600498', '600745', '603083', '300394', '300620'],
}

# 全量主板列表（缓存）
ALL_MAINBOARD = []
ALL_MAINBOARD_NAMES = {}


def load_mainboard():
    """加载全量主板股票"""
    global ALL_MAINBOARD, ALL_MAINBOARD_NAMES
    if ALL_MAINBOARD:
        return ALL_MAINBOARD
    
    rows = sql(PROFILE_DB, "SELECT code, name FROM stock_basic WHERE code LIKE '6%' AND market!='科创板' ORDER BY code")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code, name = parts[0].strip(), parts[1].strip()
            ALL_MAINBOARD.append(code)
            ALL_MAINBOARD_NAMES[code] = name
    
    if not ALL_MAINBOARD:
        # 备用：从文本文件加载
        f = os.path.join(BASE, "data", "all_main_board.txt")
        if os.path.exists(f):
            with open(f) as fh:
                for line in fh:
                    code = line.strip()
                    if code:
                        ALL_MAINBOARD.append(code)
    
    return ALL_MAINBOARD


def sql(db, q):
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', db, q],
                       capture_output=True, timeout=30, text=True)
    return [l.strip() for l in r.stdout.split('\n') if l.strip()]


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr)


def sf(v):
    try: return float(v) if v and v != '-' else 0.0
    except: return 0.0


# ============================================================
# 第一步：消息分析 — 识别概念名称
# ============================================================

def extract_concept_from_news(news_text):
    """
    从消息文本中提取概念名称。
    
    策略：
    1. 检测是否包含已有概念（查数据库）
    2. 检测是否包含产业关键词（查INDUSTRY_KW）
    3. 如果都没有 → 生成新概念名称（取最有特色的名词短语）
    """
    text = news_text.lower()
    
    # === 策略0：产业关键词优先检测（防止宽泛概念误匹配） ===
    for industry, keywords in INDUSTRY_KW.items():
        for kw in keywords:
            if kw.lower() in text:
                # 再查数据库是否有这个产业概念（有就是existing，没有才是new）
                db_match = db_has_concept(industry)
                if db_match:
                    return {'type': 'existing', 'concept': industry,
                            'industry': industry, 'confidence': '高',
                            'reason': f'消息匹配到「{kw}」→ 已有概念「{industry}」'}
                return {'type': 'new_industry', 'concept': None,
                        'industry': industry, 'confidence': '高',
                        'keywords': [kw],
                        'reason': f'消息匹配到产业关键词「{kw}」→ 新概念「{industry}」'}
    
    # === 策略1：检测已有概念 ===
    existing = check_existing_concepts(text)
    if existing:
        return {'type': 'existing', 'concept': existing['concept'], 
                'industry': existing['industry'], 'confidence': '高',
                'reason': f'消息匹配已有概念「{existing["concept"]}」({existing["industry"]})'}
    
    # === 策略2：检测产业关键词 ===
    matched_industries = []
    matched_kw = []
    for industry, keywords in INDUSTRY_KW.items():
        for kw in keywords:
            if kw.lower() in text:
                matched_industries.append(industry)
                matched_kw.append(kw)
                break
    
    if matched_industries:
        # 取匹配最长的行业（最具体）
        best = max(set(matched_industries), key=lambda x: len(x))
        return {'type': 'new_industry', 'concept': None,
                'industry': best, 'confidence': '高',
                'keywords': list(set(matched_kw)),
                'reason': f'消息匹配到产业关键词: {", ".join(set(matched_kw))} → {best}'}
    
    # === 策略3：消息太短或无法识别 ===
    # 从消息中提取最有「概念特征」的名词短语：如"康宁"、"XX概念"等
    concept_candidates = extract_concept_phrases(news_text)
    if concept_candidates:
        return {'type': 'new_concept', 'concept': concept_candidates[0],
                'industry': None, 'confidence': '中',
                'reason': f'从消息中提取可能的新概念: {concept_candidates[0]}'}
    
    return {'type': 'unknown', 'concept': None, 'industry': None,
            'confidence': '低', 'reason': '无法从消息中识别概念'}


def db_has_concept(concept_name):
    """查数据库是否已有该概念"""
    escaped = concept_name.replace("'", "''")
    rows = sql(PROFILE_DB, f"SELECT COUNT(*) FROM concepts WHERE concept_name='{escaped}'")
    return rows and rows[0].strip() != '0'


def check_existing_concepts(text):
    """检查消息是否关联已有概念（只查高质量概念）"""
    # 先用产业关键词检测（优先于宽泛的概念匹配）
    text_lower = text.lower()
    for industry, keywords in INDUSTRY_KW.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return None  # 匹配到了产业关键词，让 extract_concept_from_news 走策略2
    
    rows = sql(PROFILE_DB, """
        SELECT DISTINCT c.concept_name, cq.source_type
        FROM concept_quality cq
        JOIN concepts c ON c.concept_name = cq.concept_name
        WHERE cq.stock_count >= 3
        ORDER BY cq.stock_count DESC
        LIMIT 1000
    """)
    
    text_lower = text.lower()
    best_match = None
    best_len = 0
    
    for r in rows:
        parts = r.split('|')
        if not parts:
            continue
        cn = parts[0].strip().lower()
        # 长概念名优先匹配（避免"通信"这种短词误配）
        if len(cn) >= 2 and cn in text_lower:
            if len(cn) > best_len:
                best_match = parts[0].strip()
                best_len = len(cn)
    
    if best_match:
        # 判定所属行业
        industry = classify_to_industry(best_match)
        return {'concept': best_match, 'industry': industry}
    return None


def classify_to_industry(concept):
    """把概念名称归类到产业"""
    c_lower = concept.lower()
    for industry, keywords in INDUSTRY_KW.items():
        for kw in keywords:
            if kw.lower() in c_lower or c_lower in kw.lower():
                return industry
    return None


def extract_concept_phrases(news_text):
    """从消息中提取潜在概念名称"""
    # 常见模式：XX概念
    matches = re.findall(r'([\u4e00-\u9fa5]{2,6})概念', news_text)
    if matches:
        return [m + '概念' for m in matches]
    
    # 模式：XX（公司名/产品名/技术名）+ 相关词
    # 取最长的实体名称
    entities = re.findall(r'([\u4e00-\u9fa5]{2,6})(?:板块|产业链|概念股|大涨|合作|投资|新建)', news_text)
    if entities:
        return [e + '概念' for e in entities]
    
    # 取句中唯一的专有名词（带英文名称或公司名）
    segments = re.split(r'[，。,\.\!\?\s]+', news_text)
    for seg in segments:
        # 包含英文或数字的名词短语
        if re.search(r'[a-zA-Z]', seg) and len(seg) >= 3:
            # 提取中文部分
            cn_part = re.sub(r'[a-zA-Z0-9\s\(\)]+', '', seg).strip()
            if cn_part and len(cn_part) >= 2:
                return [cn_part + '概念']
    
    return []


# ============================================================
# 第二步：概念→个股匹配
# ============================================================

def match_stocks_by_concept(concept_info):
    """
    根据概念信息匹配A股主板股票。
    
    匹配策略（由高到低）：
    1. 高置信度映射 — 手工确认的核心标的
    2. 数据库概念匹配 — 从concepts表查已有概念映射
    3. 主营产品匹配 — 从F10主营产品/经营范围匹配关键词
    4. 公告匹配 — 近7天公告标题含相关关键词
    """
    concept_name = concept_info.get('concept')
    industry = concept_info.get('industry')
    
    matches = []  # [(code, name, reason, confidence)]
    
    # --- 策略1：高置信度映射 ---
    if industry and industry in HIGH_CONFIDENCE_MAP:
        for code in HIGH_CONFIDENCE_MAP[industry]:
            name = get_stock_name(code)
            if name:
                matches.append((code, name, f'高置信度映射·{industry}核心标的', '高'))
    
    # --- 策略2：数据库概念匹配 ---
    if concept_name:
        db_matches = match_from_db(concept_name)
        matches.extend(db_matches)
    elif industry:
        # 用产业名匹配概念库
        for kw in INDUSTRY_KW.get(industry, []):
            db_matches = match_from_db(kw)
            if db_matches:
                matches.extend(db_matches[:5])  # 每个关键词最多5只
                break
        else:
            # 用产业名直接查
            db_matches = match_from_db(industry)
            matches.extend(db_matches)
    
    # --- 策略3：主营产品匹配 ---
    keywords = []
    if concept_name:
        keywords.append(concept_name)
    if industry:
        keywords.extend(INDUSTRY_KW.get(industry, [])[:3])
    
    if keywords:
        biz_matches = match_from_business(keywords)
        matches.extend(biz_matches)
    
    # --- 去重 + 排序 ---
    seen = set()
    unique = []
    conf_order = {'高': 0, '中': 1, '低': 2}
    for code, name, reason, conf in matches:
        if code not in seen:
            seen.add(code)
            unique.append((code, name, reason, conf))
    unique.sort(key=lambda x: (conf_order.get(x[3], 99), x[0]))
    
    return unique


def get_stock_name(code):
    """获取股票名称"""
    if code in ALL_MAINBOARD_NAMES:
        return ALL_MAINBOARD_NAMES[code]
    rows = sql(PROFILE_DB, f"SELECT name FROM stock_basic WHERE code='{code}'")
    if rows:
        name = rows[0].strip()
        ALL_MAINBOARD_NAMES[code] = name
        return name
    return None


def match_from_db(concept):
    """从concepts表匹配概念标签，支持精确+包含匹配"""
    escaped = concept.replace("'", "''")
    
    # 精确匹配（数据库中的概念名=传入的概念名）
    rows = sql(PROFILE_DB, f"""
        SELECT c.code, sb.name
        FROM concepts c
        JOIN stock_basic sb ON c.code = sb.code
        WHERE c.concept_name='{escaped}'
          AND sb.code LIKE '6%'
          AND sb.code NOT LIKE '688%'
        GROUP BY c.code
        ORDER BY c.rowid
        LIMIT 20
    """)
    
    results = []
    seen_codes = set()
    
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code = parts[0].strip()
            name = parts[1].strip()
            if code not in seen_codes:
                seen_codes.add(code)
                results.append((code, name, f'概念标签「{concept}」', '高'))
    
    if results:
        # 精确匹配已命中，继续尝试泛匹配获取更多关联标的
        pass
    
    # 宽泛匹配：数据库概念名包含传入关键词
    # 例如传入"AI算力"，应匹配"算力概念" "AI芯片"等
    rows = sql(PROFILE_DB, f"""
        SELECT c.code, sb.name, c.concept_name
        FROM concepts c
        JOIN stock_basic sb ON c.code = sb.code
        WHERE c.concept_name LIKE '%{escaped}%'
          AND sb.code LIKE '6%'
          AND sb.code NOT LIKE '688%'
        GROUP BY c.code
        ORDER BY c.rowid
        LIMIT 30
    """)
    
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code = parts[0].strip()
            name = parts[1].strip()
            cname = parts[2].strip() if len(parts) >= 3 else concept
            if code not in seen_codes:
                seen_codes.add(code)
                results.append((code, name, f'概念标签「{cname}」', '中'))
    
    if results:
        # 精确匹配已命中，再加试更多关联概念
        pass
    
    # === 泛匹配2：更多关联概念 ===
    # 例如"AI算力"包含"算力"，应匹配"算力概念"
    concept_lower = concept.lower()
    # 提取核心关键词（去掉"概念""主题"后缀）
    # 并且尝试拆分复合词，例如"AI算力"→"算力"，"光模块概念"→"光模块"
    # 方法：取关键词中非AI/GPU/NVDA等英文前缀后的核心部分
    core = concept_lower.replace('概念', '').replace('主题', '').strip()
    # 如果core含英文前缀，尝试取中文核心部分
    cn_core = re.sub(r'^[a-z0-9\s]+', '', core).strip()
    if cn_core and len(cn_core) >= 2 and cn_core != core:
        core_alternatives = [cn_core, core]
    else:
        core_alternatives = [core]
    
    # 查所有概念名中是否包含核心关键词
    for search_core in core_alternatives:
        if not search_core or len(search_core) < 2:
            continue
        escaped_core = search_core.replace("'", "''")
        rows = sql(PROFILE_DB, f"""
            SELECT DISTINCT c.code, sb.name, c.concept_name
            FROM concepts c
            JOIN stock_basic sb ON c.code = sb.code
            WHERE c.concept_name LIKE '%{escaped_core}%'
              AND sb.code LIKE '6%'
              AND sb.code NOT LIKE '688%'
            GROUP BY c.code
            ORDER BY c.rowid
            LIMIT 40
        """)
        for r in rows:
            parts = r.split('|')
            if len(parts) >= 2:
                code = parts[0].strip()
                name = parts[1].strip()
                cname = parts[2].strip() if len(parts) >= 3 else concept
                if code not in seen_codes:
                    seen_codes.add(code)
                    results.append((code, name, f'关联概念「{cname}」', '中'))
        if results:
            break  # 优先使用中文核心词匹配结果
    
    return results


def match_from_business(keywords):
    """从主营业务/经营范围匹配"""
    results = []
    
    # 从stock_basic.main_business匹配（营业范围描述）
    for kw in keywords[:3]:  # 最多3个关键词
        escaped = kw.replace("'", "''")
        rows = sql(PROFILE_DB, f"""
            SELECT code, name, main_business
            FROM stock_basic
            WHERE main_business LIKE '%{escaped}%'
              AND code LIKE '6%'
              AND (market IS NULL OR market != '科创板')
            LIMIT 20
        """)
        for r in rows:
            parts = r.split('|')
            if len(parts) >= 2:
                code = parts[0].strip()
                name = parts[1].strip()
                biz = parts[2].strip() if len(parts) >= 3 else ''
                # 截取匹配内容上下文
                idx = biz.lower().find(kw.lower())
                if idx >= 0:
                    ctx_start = max(0, idx - 10)
                    ctx_end = min(len(biz), idx + len(kw) + 10)
                    ctx = biz[ctx_start:ctx_end]
                else:
                    ctx = biz[:30]
                results.append((code, name, f'经营范围含「{kw}」: ..{ctx}..', '中'))
        
        # 从stock_business匹配（F10主营构成）
        biz_rows = sql(PROFILE_DB, f"""
            SELECT sb.code, sb.name, sbs.business_name, sbs.business_ratio
            FROM stock_business sbs
            JOIN stock_basic sb ON sbs.code = sb.code
            WHERE sbs.business_name LIKE '%{escaped}%'
              AND sb.code LIKE '6%'
              AND sb.code NOT LIKE '688%'
            LIMIT 20
        """)
        for r in biz_rows:
            parts = r.split('|')
            if len(parts) >= 3:
                code = parts[0].strip()
                name = parts[1].strip()
                biz_name = parts[2].strip()
                ratio = parts[3].strip() if len(parts) >= 4 else ''
                results.append((code, name, f'主营构成: {biz_name}({ratio})', '中'))
    
    return results


# ============================================================
# 第三步：概念入库
# ============================================================

def save_concept_to_db(concept_name, stock_codes, source='auto_miner'):
    """把新概念写入数据库"""
    if not concept_name or not stock_codes:
        return 0
    
    inserted = 0
    escaped = concept_name.replace("'", "''")
    
    for code in stock_codes:
        # INSERT OR IGNORE
        r = subprocess.run(['sqlite3', PROFILE_DB, 
            f"INSERT OR IGNORE INTO concepts(code, concept_name, source) VALUES('{code}', '{escaped}', '{source}')"],
            capture_output=True, timeout=10, text=True)
        if not r.stderr:
            inserted += 1
    
    # 更新concept_quality
    subprocess.run(['sqlite3', PROFILE_DB, 
        f"INSERT OR IGNORE INTO concept_quality(concept_name, source_type, stock_count) VALUES('{escaped}', '{source}', {len(stock_codes)})"],
        capture_output=True, timeout=10)
    
    return inserted


def save_to_panel_data(result):
    """保存到面板数据文件"""
    data = {
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'date': datetime.now().strftime('%Y-%m-%d'),
        'type': result['type'],
        'concept': result.get('concept') or result.get('industry', ''),
        'confidence': result.get('confidence', '低'),
        'reason': result.get('reason', ''),
        'stocks': result.get('stocks', []),
    }
    with open(PANEL_DATA, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"✅ 面板数据已写入 {PANEL_DATA}")


# ============================================================
# 第四步：个股深度内因分析（全方位产业定位）
# ============================================================
# 
# 每个股票在它所涉及的领域内，分析：
#   1. 合作关系（上下游/供应商/客户/龙头绑定）
#   2. 供应链角色（主营产品拆解/业务结构）
#   3. 行业地位（市占率/Big3标签/细分领域地位）
#   4. 业绩+毛利率（净利润/毛利率/ROE/EPS/增长趋势）
#   5. 投资/扩产/产能（公告关键词匹配）
#   6. 细分领域排名（同概念内净利润/弹性/涨停/毛利率排名）
#   7. 近期催化（日涨幅/涨停/连板）

def fetch_financial_data(codes):
    """获取财务数据：净利润/毛利率/ROE/EPS"""
    if not codes: return {}
    code_list = "','".join(codes)
    profit_map = {}
    rows = sql(FUND_DB,
        f"SELECT code, net_profit, gross_profit_margin, net_profit_margin, eps, roe "
        f"FROM profit_data WHERE stat_date=(SELECT MAX(stat_date) FROM profit_data) "
        f"AND code IN ('{code_list}')")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code = parts[0].strip()
            profit_map[code] = {
                'net_profit': sf(parts[1]) if len(parts) >= 2 else 0,
                'gross_margin': sf(parts[2]) if len(parts) >= 3 else 0,
                'net_margin': sf(parts[3]) if len(parts) >= 4 else 0,
                'eps': sf(parts[4]) if len(parts) >= 5 else 0,
                'roe': sf(parts[5]) if len(parts) >= 6 else 0,
            }
    return profit_map


def fetch_products_data(codes):
    """获取产品构成：各产品收入/毛利率/占比"""
    if not codes: return {}
    code_list = "','".join(codes)
    prod_map = {}
    rows = sql(PROFILE_DB,
        f"SELECT code, product_name, revenue_pct, gross_margin FROM products "
        f"WHERE code IN ('{code_list}') ORDER BY code, revenue_pct DESC")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code = parts[0].strip()
            if code not in prod_map:
                prod_map[code] = []
            prod_map[code].append({
                'product': parts[1].strip(),
                'revenue_pct': sf(parts[2]) if len(parts) >= 3 else 0,
                'gross_margin': sf(parts[3]) if len(parts) >= 4 else 0,
            })
    return prod_map


def fetch_supply_chain_data(codes):
    """获取供应链关系：供应商/客户/合作伙伴"""
    if not codes: return {}
    code_list = "','".join(codes)
    sc_map = {}
    rows = sql(PROFILE_DB,
        f"SELECT code, partner_name, partner_type, amount_pct FROM supply_chain "
        f"WHERE code IN ('{code_list}') ORDER BY code, amount_pct DESC")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code = parts[0].strip()
            if code not in sc_map:
                sc_map[code] = []
            sc_map[code].append({
                'partner': parts[1].strip(),
                'type': parts[2].strip() if len(parts) >= 3 else '',
                'pct': sf(parts[3]) if len(parts) >= 4 else 0,
            })
    return sc_map


def fetch_announcements_data(codes):
    """获取公告数据，并分类为投资扩产类/订单合同类/合作类"""
    if not codes: return {}
    code_list = "','".join(codes)
    from_date = datetime.now().strftime('%Y-%m-%d')
    ann_map = {}
    
    # 6个月内的公告
    from datetime import timedelta
    six_months_ago = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')
    
    rows = sql(PROFILE_DB,
        f"SELECT code, title, date FROM news_events "
        f"WHERE date>='{six_months_ago}' AND code IN ('{code_list}') "
        f"AND LENGTH(title)<200 ORDER BY date DESC LIMIT 100")
    
    invest_kw = ['投资', '建设', '扩产', '投产', '新建', '产能', '项目', '基金', '设立']
    order_kw = ['订单', '中标', '合同', '签约', '供货', '采购', '供应']
    coop_kw = ['合作', '战略', '协议', '入股', '合资', '联盟', '伙伴']
    
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 3:
            code = parts[0].strip()
            title = parts[1].strip()
            date = parts[2].strip()
            if code not in ann_map:
                ann_map[code] = {'invest': [], 'order': [], 'coop': [], 'other': []}
            
            t = title.lower()
            if any(kw in t for kw in invest_kw):
                ann_map[code]['invest'].append({'title': title, 'date': date})
            elif any(kw in t for kw in order_kw):
                ann_map[code]['order'].append({'title': title, 'date': date})
            elif any(kw in t for kw in coop_kw):
                ann_map[code]['coop'].append({'title': title, 'date': date})
            else:
                ann_map[code]['other'].append({'title': title, 'date': date})
    
    return ann_map


def analyze_stocks_multidimensional(stocks, concept_name):
    """
    个股全方位内因分析 — 7维深度定位
    
    产出每个股票的完整画像：
      - 产业角色：供应链位置+合作关系
      - 产品结构：核心产品+毛利率
      - 业绩画像：净利润/ROE/EPS
      - 赛道排名：同概念内各指标排名
      - 近期催化：公告+涨跌+涨停
    """
    if not stocks:
        return []
    
    codes = [s[0] for s in stocks]
    
    # === 1. 全方位数据采集 ===
    profit_map = fetch_financial_data(codes)
    prod_map = fetch_products_data(codes)
    sc_map = fetch_supply_chain_data(codes)
    ann_map = fetch_announcements_data(codes)
    
    # === 2. 市值/股本 ===
    code_list = "','".join(codes)
    size_map = {}
    rows = sql(PROFILE_DB, f"SELECT code, total_shares FROM stock_basic WHERE code IN ('{code_list}')")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2 and parts[1]:
            try:
                size_map[parts[0].strip()] = sf(parts[1]) / 1e8
            except:
                pass
    
    # === 3. 近期表现 ===
    perf_map, limit_map = {}, {}
    today = datetime.now().strftime('%Y-%m-%d')
    from_date = datetime.now().strftime('%Y') + '-01-01'
    
    rows = sql(os.path.join(BASE, "data", "kline_cache.db"),
        f"SELECT code, close, pre_close FROM kline WHERE date='{today}' AND code IN ('{code_list}')")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 3:
            code = parts[0].strip()
            close = sf(parts[1]); pre = sf(parts[2])
            if pre > 0:
                perf_map[code] = {'chg': (close - pre) / pre * 100}
    
    rows = sql(os.path.join(BASE, "data", "kline_cache.db"),
        f"SELECT code, COUNT(*) FROM kline WHERE date>='{from_date}' AND date<='{today}' "
        f"AND code IN ('{code_list}') AND close/prev_close>=1.098 AND volume>0 GROUP BY code")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            limit_map[parts[0].strip()] = int(parts[1])
    
    rows = sql(os.path.join(BASE, "data", "kline_cache.db"),
        f"SELECT code, MAX(num) FROM (SELECT code, ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) as num, close/prev_close as ratio "
        f"FROM kline WHERE date>='{from_date}' AND date<='{today}' AND code IN ('{code_list}') "
        f"AND close/prev_close>=1.098 AND volume>0) GROUP BY code")
    lianban_map = {}
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            lianban_map[parts[0].strip()] = int(parts[1])
    
    # === 4. 同概念内排名 ===
    codes_in_concept = [s[0] for s in stocks]
    
    # 净利润排名
    profit_ranked = sorted([(c, profit_map.get(c, {}).get('net_profit', 0)) for c in codes_in_concept], key=lambda x: -x[1])
    profit_rank = {c: i+1 for i, (c, _) in enumerate(profit_ranked)}; pt = len(profit_ranked)
    
    # 毛利率排名
    gm_ranked = sorted([(c, profit_map.get(c, {}).get('gross_margin', 0)) for c in codes_in_concept], key=lambda x: -x[1])
    gm_rank = {c: i+1 for i, (c, _) in enumerate(gm_ranked)}; gt = len(gm_ranked)
    
    # 弹性排名（市值越小弹性越大）
    elasti_scores = {}
    for code, mc in size_map.items():
        est = mc * 10 if mc <= 100 else mc
        elasti_scores[code] = 100 if est < 30 else (70 if est < 80 else (40 if est < 200 else 10))
    el_ranked = sorted([(c, elasti_scores.get(c, 0)) for c in codes_in_concept], key=lambda x: -x[1])
    el_rank = {c: i+1 for i, (c, _) in enumerate(el_ranked)}; et = len(el_ranked)
    
    # 涨停活跃排名
    lr_ranked = sorted([(c, limit_map.get(c, 0)) for c in codes_in_concept], key=lambda x: -x[1])
    lr_rank = {c: i+1 for i, (c, _) in enumerate(lr_ranked)}; lt = len(lr_ranked)
    
    # === 5. 组装每个股票的全景画像 ===
    def make_profile(code, name, reason, conf):
        fin = profit_map.get(code, {})
        prods = prod_map.get(code, [])
        sc = sc_map.get(code, [])
        ann = ann_map.get(code, {})
        mc = size_map.get(code, 0)
        est_mcap = mc * 10 if mc <= 100 else mc
        
        # ------- 维度1：业绩画像 -------
        net_p = fin.get('net_profit', 0)
        gm = fin.get('gross_margin', 0)
        nm = fin.get('net_margin', 0)
        eps = fin.get('eps', 0)
        roe = fin.get('roe', 0)
        
        profit_profile = ''
        if net_p > 1e9:
            profit_profile = f'净利润{net_p/1e8:.1f}亿💰'
        elif net_p > 1e7:
            profit_profile = f'净利润{net_p/1e8:.2f}亿'
        elif net_p > 0:
            profit_profile = f'微利{net_p/1e8:.2f}亿'
        elif net_p < 0:
            profit_profile = f'亏损{abs(net_p)/1e8:.1f}亿🔴'
        
        if gm > 40:
            profit_profile += f' | 高毛利率{gm:.1f}%🔥'
        elif gm > 20:
            profit_profile += f' | 毛利率{gm:.1f}%'
        
        if roe > 15:
            profit_profile += f' | ROE{roe:.1f}%优秀'
        elif roe > 5:
            profit_profile += f' | ROE{roe:.1f}%'
        
        # ------- 维度2：产品结构 -------
        product_profile = ''
        if prods:
            top = prods[0]
            product_profile = f'主营:{top["product"]}({top["revenue_pct"]:.0f}%)'
            if top.get('gross_margin'):
                product_profile += f' 毛利率{top["gross_margin"]:.1f}%'
            if len(prods) > 1:
                second = prods[1]
                product_profile += f' 次:{second["product"]}({second["revenue_pct"]:.0f}%)'
        else:
            # 从主营业范围提取
            product_profile = '主营数据待补充'
        
        # ------- 维度3：供应链关系 -------
        supply_profile = ''
        sc_customers = [s for s in sc if s['type'] in ('客户', '下游', '大客户')]
        sc_suppliers = [s for s in sc if s['type'] in ('供应商', '上游')]
        sc_partners = [s for s in sc if s['type'] in ('合作', '战略', '股东')]
        
        if sc_customers:
            supply_profile = f'下游:{"、".join([s["partner"] for s in sc_customers[:3]])}'
        if sc_suppliers:
            if supply_profile: supply_profile += ' | '
            supply_profile += f'上游:{"、".join([s["partner"] for s in sc_suppliers[:3]])}'
        if sc_partners and not supply_profile:
            supply_profile = f'合作:{"、".join([s["partner"] for s in sc_partners[:3]])}'
        
        # ------- 维度4：投资/产能/订单/合作公告 -------
        invest_profile = ''
        if ann.get('invest'):
            invest_profile = f'📢投资:{ann["invest"][0]["title"][:20]}({ann["invest"][0]["date"]})'
        if ann.get('order'):
            if invest_profile: invest_profile += ' | '
            invest_profile += f'📝订单:{ann["order"][0]["title"][:20]}({ann["order"][0]["date"]})'
        if not invest_profile and ann.get('coop'):
            invest_profile = f'🤝合作:{ann["coop"][0]["title"][:20]}({ann["coop"][0]["date"]})'
        
        # ------- 维度5：赛道排名标签 -------
        rank_items = []
        if pt > 1:
            if profit_rank.get(code, pt) <= max(1, pt//3):
                rank_items.append(f'🏆净利{profit_rank[code]}/{pt}')
            if gm_rank.get(code, gt) <= max(1, gt//3):
                rank_items.append(f'💎毛利{gm_rank[code]}/{gt}')
            if el_rank.get(code, et) <= max(1, et//3):
                rank_items.append(f'⚡弹性{el_rank[code]}/{et}')
            if lr_rank.get(code, lt) <= max(1, lt//3):
                rank_items.append(f'🔥涨停{lr_rank[code]}/{lt}')
        
        # ------- 维度6：弹性/活跃度 -------
        if est_mcap <= 0:
            elastic_tag = '市值未知'
        elif est_mcap < 30:
            elastic_tag = f'微盘({est_mcap:.0f}亿)🔥'
        elif est_mcap < 80:
            elastic_tag = f'小盘({est_mcap:.0f}亿)⚡'
        elif est_mcap < 200:
            elastic_tag = f'中盘({est_mcap:.0f}亿)'
        else:
            elastic_tag = f'大盘({est_mcap:.0f}亿)'
        
        limit_cnt = limit_map.get(code, 0)
        if limit_cnt >= 10:
            activity_tag = f'老妖({limit_cnt}次涨停)🔥'
        elif limit_cnt >= 5:
            activity_tag = f'活跃({limit_cnt}次)⚡'
        elif limit_cnt >= 1:
            activity_tag = f'有涨停({limit_cnt}次)'
        else:
            activity_tag = '0次涨停'
        
        lb = lianban_map.get(code, 1)
        if lb >= 3:
            lianban_tag = f'{lb}连板🔥'
        elif lb == 2:
            lianban_tag = f'{lb}连板⚡'
        else:
            lianban_tag = ''
        
        # ------- 维度7：今日表现 -------
        perf = perf_map.get(code, {})
        chg_today = perf.get('chg', 0)
        today_str = f"{chg_today:+.2f}%" if abs(chg_today) > 0 else '未交易'
        
        # ------- 组装全景画像 -------
        # 核心定位一句话
        core_positioning = product_profile.split('主营:')[1] if '主营:' in product_profile else product_profile
        core_positioning = core_positioning[:30]
        
        return {
            'code': code, 'name': name,
            'reason': reason, 'confidence': conf,
            # 核心定位
            'core_positioning': core_positioning,
            # 业绩
            'profit_profile': profit_profile,
            'net_profit': net_p,
            'gross_margin': gm,
            'roe': roe,
            'eps': eps,
            # 产品结构
            'product_profile': product_profile,
            'top_products': [p['product'] for p in prods[:3]],
            # 供应链
            'supply_profile': supply_profile,
            'supply_customers': [s['partner'] for s in sc_customers[:3]],
            'supply_suppliers': [s['partner'] for s in sc_suppliers[:3]],
            # 投资/公告催化
            'invest_profile': invest_profile,
            'announcements': (ann.get('invest', []) + ann.get('order', []) + ann.get('coop', []))[:5],
            'ann_invest': ann.get('invest', []),
            'ann_order': ann.get('order', []),
            'ann_coop': ann.get('coop', []),
            # 赛道排名
            'rank_profit': f'{profit_rank.get(code, pt)}/{pt}' if pt > 0 else '',
            'rank_gross_margin': f'{gm_rank.get(code, gt)}/{gt}' if gt > 0 else '',
            'rank_elasticity': f'{el_rank.get(code, et)}/{et}' if et > 0 else '',
            'rank_limit': f'{lr_rank.get(code, lt)}/{lt}' if lt > 0 else '',
            'rank_labels': ' | '.join(rank_items) if rank_items else '',
            # 弹性/活跃
            'elasticity': elastic_tag,
            'market_cap': est_mcap,
            'activity': activity_tag,
            'lianban': lianban_tag,
            'limit_count': limit_cnt,
            # 今日
            'today_chg': today_str,
        }
    
    enriched = [make_profile(code, name, reason, conf) for code, name, reason, conf in stocks]
    
    enriched.sort(key=lambda x: (
        {'高': 0, '中': 1, '低': 2}.get(x['confidence'], 99),
        -x['net_profit'],  # 净利润高的优先
        x['market_cap'] if x['market_cap'] > 0 else 999
    ))
    
    return enriched


# 保留旧函数名做兼容
analyze_stocks_deep = analyze_stocks_multidimensional


# ============================================================
# 第五步：输出格式化
# ============================================================

def format_output(concept_info, stocks, is_new):
    """格式化为微信友好输出"""
    lines = []
    
    # --- 标题 ---
    concept = concept_info.get('concept') or concept_info.get('industry', '未知概念')
    conf = concept_info.get('confidence', '中')
    conf_icon = '🟢' if conf == '高' else ('🟡' if conf == '中' else '⚪')
    
    if is_new:
        lines.append(f"🆕 **新概念检测: {concept}** {conf_icon}")
    elif concept_info['type'] == 'existing':
        lines.append(f"📡 **{concept}**（已有概念）")
    else:
        lines.append(f"📡 **{concept}**")
    
    lines.append(f"📋 {concept_info.get('reason', '')}")
    
    if not stocks:
        lines.append("")
        lines.append("❌ 未匹配到A股主板个股")
        lines.append("💡 可能原因：概念太新或主营数据未覆盖，需要手工确认")
        return '\n'.join(lines)
    
    # --- 个股明细 ---
    lines.append("")
    lines.append(f"🎯 **匹配 {len(stocks)} 只标的**")
    lines.append("")
    
    for i, s in enumerate(stocks[:15]):  # 最多显示15只
        icon = '🔴' if s['confidence'] == '高' else ('🟠' if s['confidence'] == '中' else '⚪')
        
        # 弹性+活跃度标签
        tags = []
        if '🔥' in s['elasticity']:
            tags.append(s['elasticity'])
        if '🔥' in s['activity']:
            tags.append(s['activity'])
        tag_str = ' | '.join(tags) if tags else ''
        
        # 今日表现
        today = ''
        if s['today_chg'] and s['today_chg'] != '未交易':
            today = f" {s['today_chg']}"
        
        lines.append(f"{i+1}. {icon} **{s['name']}** ({s['code']}){today}")
        # 纯度标签
        purity_tag = ''
        if 'purity_rank' in s and s.get('purity_score', 0) > 0:
            purity_tag = f" {s.get('purity_icon','')}纯度{s['purity_score']:.0f}分·{s['purity_rank']}"
        lines.append(f"   📌 {s['reason'][:50]}{purity_tag}")
        if tag_str:
            lines.append(f"   🏷️ {tag_str}")
        
        # 公告催化
        if s['announcements']:
            ann = s['announcements'][0]
            lines.append(f"   📰 {ann['date']} {ann['title'][:40]}")
        
        # 财务亮点
        if s['net_profit'] and s['net_profit'] > 1e7:
            profit_亿 = s['net_profit'] / 1e8
            lines.append(f"   💰 净利润 {profit_亿:.2f}亿")
        
        lines.append("")
    
    if len(stocks) > 15:
        lines.append(f"... 还有 {len(stocks)-15} 只，完整版见面板")
    
    # --- 操作建议 ---
    lines.append("---")
    lines.append("💡 **操作建议**")
    
    high_conf = [s for s in stocks if s['confidence'] == '高']
    if high_conf:
        top = high_conf[0]
        lines.append(f"🔴 高优先级: {top['name']} ({top['code']}) — {top['reason'][:30]}")
    
    # 弹性标的前3
    elastic = [s for s in stocks if '🔥' in s['elasticity'] or '⚡' in s['elasticity']]
    if elastic and len(elastic) >= 2:
        names = [s['name'] for s in elastic[:3]]
        lines.append(f"⚡ 弹性标的: {'、'.join(names)}")
    
    # --- 概念纯度分区 ---
    if any('purity_rank' in s for s in stocks):
        lines.append("")
        lines.append("🧪 **概念纯度评级**")
        
        rank_order = ['龙一', '核心受益', '间接受益', '跟风/蹭概念', '纯蹭概念']
        rank_icons = {'龙一': '🟢', '核心受益': '🔵', '间接受益': '🟡', '跟风/蹭概念': '🟠', '纯蹭概念': '🔴'}
        
        for rk in rank_order:
            ranked = [s for s in stocks if s.get('purity_rank') == rk]
            if ranked:
                names_scores = [f"{s['name']}({s['purity_score']:.0f}分)" for s in ranked[:3]]
                icon = rank_icons.get(rk, '')
                lines.append(f"  {icon} {rk}: {'、'.join(names_scores)}")
                if len(ranked) > 3:
                    lines.append(f"    ... 还有{len(ranked)-3}只")
    
    lines.append("")
    lines.append("⚠️ 消息初期不可重仓，等3日主力资金验证后再加仓")
    
    return '\n'.join(lines)


# ============================================================
# 主流程
# ============================================================

def process_news(news_text):
    """主流程：消息→概念→个股"""
    load_mainboard()
    
    # 第一步：识别概念
    log(f"📡 分析消息: {news_text[:60]}...")
    concept_info = extract_concept_from_news(news_text)
    log(f"   → 类型={concept_info['type']}, 概念={concept_info.get('concept')}, 产业={concept_info.get('industry')}")
    log(f"   → 置信度={concept_info['confidence']}, 理由={concept_info.get('reason', '')}")
    
    # 第二步：匹配个股
    log(f"   → 开始匹配个股...")
    raw_stocks = match_stocks_by_concept(concept_info)
    log(f"   → 匹配到 {len(raw_stocks)} 只原始标的")
    
    # 第三步：深度分析
    stocks_deep = analyze_stocks_deep(raw_stocks, 
                                       concept_info.get('concept') or concept_info.get('industry', ''))
    
    result = {
        'type': concept_info['type'],
        'concept': concept_info.get('concept'),
        'industry': concept_info.get('industry'),
        'confidence': concept_info.get('confidence'),
        'reason': concept_info.get('reason', ''),
        'stocks': stocks_deep,
    }
    
    # 第四步：新概念入库
    is_new = False
    if concept_info['type'] in ('new_industry', 'new_concept'):
        stock_codes = [s['code'] for s in stocks_deep]
        concept_name = concept_info.get('concept') or concept_info.get('industry', '')
        if concept_name and stock_codes:
            inserted = save_concept_to_db(concept_name, stock_codes)
            if inserted > 0:
                log(f"   → 新概念「{concept_name}」已入库，关联{inserted}只股票")
                is_new = True
            else:
                log(f"   → 概念「{concept_name}」可能已存在")
    
    # 第五步：保存面板数据
    save_to_panel_data(result)
    
    # 第六步：跑概念纯度评分（如果有足够股票）
    purity_results = None
    if len(stocks_deep) >= 3:
        try:
            from concept_purity_scorer import score_concept_stocks
            concept_for_purity = concept_info.get('concept') or concept_info.get('industry', '')
            if concept_for_purity:
                purity_results = score_concept_stocks(concept_for_purity)
                # 把纯度评分合并到深度分析结果中
                purity_map = {r['code']: r for r in purity_results} if purity_results else {}
                for s in stocks_deep:
                    p = purity_map.get(s['code'])
                    if p:
                        s['purity_score'] = p['total_score']
                        s['purity_rank'] = p['rank']
                        s['purity_icon'] = p['icon']
        except Exception as e:
            log(f"   ⚠️ 纯度评分跳过: {e}")
    
    return result, is_new


def main():
    import argparse
    parser = argparse.ArgumentParser(description='消息→新概念自动识别引擎')
    parser.add_argument('--news', type=str, help='消息文本')
    parser.add_argument('--concept', type=str, help='直接指定概念名称')
    parser.add_argument('--desc', type=str, default='', help='概念描述')
    parser.add_argument('--scan-only', action='store_true', help='只扫描不匹配')
    args = parser.parse_args()
    
    if args.concept:
        # 直接指定概念
        load_mainboard()
        concept_info = {'type': 'new_concept', 'concept': args.concept,
                        'industry': classify_to_industry(args.concept),
                        'confidence': '高',
                        'reason': f'用户指定概念: {args.concept}'}
        
        if args.scan_only:
            log(f"📡 扫描概念: {args.concept}")
            print(format_output(concept_info, [], False))
            return
        
        raw_stocks = match_stocks_by_concept(concept_info)
        stocks_deep = analyze_stocks_deep(raw_stocks, args.concept)
        result = {**concept_info, 'stocks': stocks_deep}
        
        # 入库
        stock_codes = [s['code'] for s in stocks_deep]
        inserted = save_concept_to_db(args.concept, stock_codes)
        if inserted > 0:
            log(f"✅ 新概念「{args.concept}」已入库，关联{inserted}只股票")
        
        save_to_panel_data(result)
        print(format_output(concept_info, stocks_deep, True))
        
    elif args.news:
        result, is_new = process_news(args.news)
        output = format_output(result, result['stocks'], is_new)
        print(output)
        
        # JSON模式输出（供程序读取）
        print("\n--- JSON ---")
        print(json.dumps({
            'type': result['type'],
            'concept': result.get('concept'),
            'confidence': result.get('confidence'),
            'stock_count': len(result['stocks']),
        }, ensure_ascii=False), file=sys.stderr)
        
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
