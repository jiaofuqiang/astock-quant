#!/usr/bin/env python3
"""
🎯 概念纯度评分系统 v1.0
========================
每个股票在每个概念下的「概念纯度」量化评分。

核心逻辑：同一个概念下，不同股票关联度天差地别。
  - 实质性利好（主营匹配+订单验证+供应链绑定）→ 高纯度
  - 想象空间（有布局但没业绩）→ 中纯度
  - 纯蹭概念（只是标签贴上了）→ 低纯度

评分维度（总分100）：
  1. 主营匹配度 (35分) — 主营产品/经营范围是否真做这个
  2. 公告验证度 (20分) — 有没有订单/投资/合作公告佐证
  3. 供应链绑定度 (15分) — 上下游是否在这个领域
  4. 业绩验证度 (15分) — 净利润和毛利率是否受益
  5. 概念时效性 (10分) — 概念的长期性（多个来源→更稳定）
  6. 弹性修正 (5分) — 市值越小弹性越大

用法：
  python3 scripts/concept_purity_scorer.py --concept="康宁概念"
  python3 scripts/concept_purity_scorer.py --concept="AI算力"
  python3 scripts/concept_purity_scorer.py --all

输出：
  - 每只股票的纯度评分 + 6维打分明细
  - 板块内排序（龙一/龙二/核心/跟风/蹭概念）
  - 写入数据库供其他模块调用
"""

import os, sys, json, subprocess, re, time
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.path.expanduser("~/astock")
PROFILE_DB = os.path.join(BASE, "data", "stock_profiles.db")
FUND_DB = os.path.join(BASE, "data", "fundamental.db")
KLINE_DB = os.path.join(BASE, "data", "kline_cache.db")

# ============================================================
# 工具函数
# ============================================================

def sql(db, q):
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', db, q],
                       capture_output=True, timeout=30, text=True)
    return [l.strip() for l in r.stdout.split('\n') if l.strip()]

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', file=sys.stderr)

def sf(v):
    try: return float(v) if v and v != '-' else 0.0
    except: return 0.0

# ============================================================
# 数据加载
# ============================================================

PURITY_RESULTS = {}  # concept -> [(code, score, detail)]


def load_concept_stocks(concept_name):
    """加载一个概念下的所有主板股票"""
    escaped = concept_name.replace("'", "''")
    rows = sql(PROFILE_DB, f"""
        SELECT c.code, sb.name, sb.main_business, sb.product
        FROM concepts c
        JOIN stock_basic sb ON c.code = sb.code
        WHERE c.concept_name LIKE '%{escaped}%'
          AND sb.code LIKE '6%'
          AND sb.code NOT LIKE '688%'
        GROUP BY c.code
        ORDER BY c.rowid
    """)
    stocks = []
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code = parts[0].strip()
            name = parts[1].strip()
            biz = parts[2].strip() if len(parts) >= 3 else ''
            prod = parts[3].strip() if len(parts) >= 4 else ''
            stocks.append({'code': code, 'name': name, 'main_business': biz, 'product': prod})
    return stocks


def load_products_data(codes):
    """加载产品的收入占比"""
    if not codes: return {}
    cl = "','".join(codes)
    pm = {}
    rows = sql(PROFILE_DB,
        f"SELECT code, product_name, revenue_pct, gross_margin FROM products WHERE code IN ('{cl}') ORDER BY code, revenue_pct DESC")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code = parts[0].strip()
            if code not in pm: pm[code] = []
            pm[code].append({
                'product': parts[1].strip(),
                'pct': sf(parts[2]) if len(parts) >= 3 else 0,
                'gm': sf(parts[3]) if len(parts) >= 4 else 0,
            })
    return pm


def load_financial_data(codes):
    """加载财务数据"""
    if not codes: return {}
    cl = "','".join(codes)
    fm = {}
    rows = sql(FUND_DB,
        f"SELECT code, net_profit, gross_profit_margin FROM profit_data WHERE stat_date=(SELECT MAX(stat_date) FROM profit_data) AND code IN ('{cl}')")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            code = parts[0].strip()
            fm[code] = {'net_profit': sf(parts[1]), 'gm': sf(parts[2]) if len(parts) >= 3 else 0}
    return fm


def load_kline_data(codes):
    """加载K线数据：涨停次数+今日涨幅"""
    if not codes: return {}, {}
    cl = "','".join(codes)
    today = datetime.now().strftime('%Y-%m-%d')
    from_date = datetime.now().strftime('%Y') + '-01-01'
    
    lm = {}
    rows = sql(KLINE_DB,
        f"SELECT code, COUNT(*) FROM kline WHERE date>='{from_date}' AND date<='{today}' AND code IN ('{cl}') AND close/prev_close>=1.098 AND volume>0 GROUP BY code")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2: lm[parts[0].strip()] = int(parts[1])
    
    pm = {}
    rows = sql(KLINE_DB,
        f"SELECT code, close, pre_close FROM kline WHERE date='{today}' AND code IN ('{cl}')")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 3:
            code = parts[0].strip()
            close = sf(parts[1]); pre = sf(parts[2])
            if pre > 0: pm[code] = (close - pre) / pre * 100
    
    return lm, pm


def load_stock_business(codes):
    """加载主营构成描述"""
    if not codes: return {}
    cl = "','".join(codes)
    bm = {}
    rows = sql(PROFILE_DB, f"SELECT code, business FROM stock_business WHERE code IN ('{cl}')")
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2: bm[parts[0].strip()] = parts[1].strip()
    return bm


# ============================================================
# 核心：概念纯度评分
# ============================================================

def score_dim1_business_match(code, name, concept_name, products, biz_map):
    """
    维度1：主营匹配度 (35分)
    
    核心逻辑：股票的product/main_business是否和概念名关键词匹配。
    匹配的产品收入占比越高 → 纯度越高。
    
    例如，长飞光纤(601869)在"光纤概念"下：
      - 如果products表有"光纤光缆(85%)" → 35分拿满
      - 即使没有products表，main_business含"光纤" → 高匹配
      
    京东方A(000725)在"光纤概念"下：
      - products表是"显示面板" → 无匹配
      - main_business是"显示器制造" → 无匹配
      - → 0-5分（纯蹭）
    """
    # 概念名提取关键词
    cname = concept_name.lower().replace('概念', '').replace('主题', '').strip()
    
    # 关键词集合（概念名本身 + 概念核心词）
    keywords = set()
    keywords.add(cname)
    
    # 拆词：如"康宁概念"→"康宁"，"光纤光缆"→"光纤"+"光缆"
    if cname:
        keywords.add(cname)
        # 拆分成2字词
        for i in range(0, len(cname)-1):
            keywords.add(cname[i:i+2])
    
    score = 0
    evidence = []
    
    # --- 方法A：products表匹配（最准确）---
    if code in products:
        prods = products[code]
        match_pct = 0
        max_pct = 0
        match_products = []
        for p in prods:
            pname = p['product'].lower()
            pct = p['pct']
            if pct > max_pct: max_pct = pct
            
            # 检查产品名是否包含概念关键词
            matched = False
            for kw in keywords:
                if len(kw) >= 2 and kw in pname:
                    matched = True
                    break
            # 也检查概念词是否包含产品名（如概念"光纤光缆"包含产品"光纤"）
            if not matched:
                for kw in keywords:
                    if len(pname) >= 2 and pname in kw:
                        matched = True
                        break
            
            if matched:
                match_pct += pct
                match_products.append(p['product'][:15])
        
        if match_pct > 0:
            score = min(35, 20 + match_pct * 0.15)
            evidence.append(f'产品匹配{match_pct:.0f}%: {",".join(match_products[:3])}')
        elif max_pct > 0:
            score = 5  # 有产品数据但无匹配 → 大概率蹭概念
            evidence.append(f'主营不相关(最大产品占比{max_pct:.0f}%)')
    
    # --- 方法B：main_business字段匹配 ---
    if score < 30:
        biz = biz_map.get(code, '')
        if not biz:
            # 从stock_basic的product字段拿
            rows = sql(PROFILE_DB, f"SELECT product, main_business FROM stock_basic WHERE code='{code}'")
            if rows:
                parts = rows[0].split('|')
                biz = parts[0].strip() if parts[0].strip() else (parts[1].strip() if len(parts) >= 2 else '')
        
        if biz:
            biz_lower = biz.lower()
            match_count = 0
            for kw in keywords:
                if len(kw) >= 2 and kw in biz_lower:
                    match_count += 1
            
            if match_count >= 2:
                score = max(score, 25)
                evidence.append(f'经营范围含{keywords}')
            elif match_count == 1:
                score = max(score, 15)
                evidence.append(f'经营范围部分相关')
            else:
                evidence.append(f'经营范围不相关')
    
    # --- 方法C：股票名称包含关键词（最低置信度）---
    name_lower = name.lower()
    if score < 10:
        for kw in keywords:
            if len(kw) >= 2 and kw in name_lower:
                score = max(score, 8)
                evidence.append(f'股票名称含「{kw}」')
                break
    
    # 最终分
    score = min(35, max(0, score))
    return {'score': score, 'max': 35, 'evidence': '; '.join(evidence[:3]) if evidence else '无匹配数据'}


def score_dim2_announcement(code, concept_name):
    """
    维度2：公告验证度 (20分)
    
    近6个月公告标题是否包含概念相关关键词。
    有订单公告→加分更多，投资扩产→中加分，其他相关→低加分
    """
    keywords = concept_name.lower().replace('概念', '').replace('主题', '').strip()
    
    score = 0
    evidence = []
    
    six_months_ago = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')
    
    rows = sql(PROFILE_DB,
        f"SELECT title, date FROM news_events WHERE date>='{six_months_ago}' AND code='{code}' AND LENGTH(title)<200 ORDER BY date DESC LIMIT 30")
    
    if not rows:
        return {'score': 0, 'max': 20, 'evidence': '无公告数据'}
    
    order_count = 0
    invest_count = 0
    coop_count = 0
    concept_mention = 0
    
    for r in rows:
        parts = r.split('|')
        if len(parts) < 2: continue
        title = parts[0].strip().lower()
        
        # 概念关键词出现在公告里
        if len(keywords) >= 2 and keywords in title:
            concept_mention += 1
        
        # 订单类
        if any(kw in title for kw in ['订单', '中标', '合同', '签约', '供货']):
            order_count += 1
        # 投资类
        elif any(kw in title for kw in ['投资', '建设', '扩产', '投产', '新建']):
            invest_count += 1
        # 合作类
        elif any(kw in title for kw in ['合作', '战略', '入股', '合资']):
            coop_count += 1
    
    if order_count >= 3:
        score = 20
        evidence.append(f'{order_count}份订单公告🔥')
    elif order_count >= 1:
        score = 15
        evidence.append(f'{order_count}份订单公告')
    elif invest_count >= 2:
        score = 12
        evidence.append(f'{invest_count}份投资公告')
    elif invest_count >= 1:
        score = 8
        evidence.append(f'{invest_count}份投资公告')
    elif coop_count >= 2:
        score = 6
        evidence.append(f'{coop_count}份合作协议')
    elif concept_mention >= 1:
        score = 3
        evidence.append(f'公告提及概念')
    
    return {'score': score, 'max': 20, 'evidence': '; '.join(evidence[:2]) if evidence else '无相关公告'}


def score_dim3_supply_chain(code, concept_name):
    """
    维度3：供应链绑定度 (15分)
    
    上下游客户/供应商是否和概念相关。
    大客户是概念核心公司（如英伟达/华为/康宁）→ 高度绑定
    """
    keywords = concept_name.lower().replace('概念', '').replace('主题', '').strip()
    
    score = 0
    evidence = []
    
    # 通用的核心客户/供应商名称
    CORE_NAMES = {
        '英伟达', 'nvidia', '华为', '康宁', 'corning', '特斯拉', 'tesla',
        '苹果', 'apple', '微软', 'microsoft', '谷歌', 'google', '亚马逊', 'amazon',
        '中国移动', '中国电信', '中国联通', '国家电网', '南方电网',
        '宁德时代', '比亚迪', '中芯国际', '长江存储', '长鑫存储',
    }
    
    # 概念相关的客户模糊匹配
    concept_names = set()
    concept_names.add(keywords)
    # 拆词
    for i in range(0, len(keywords)-1):
        concept_names.add(keywords[i:i+2])
    concept_names.update(CORE_NAMES)
    
    rows = sql(PROFILE_DB,
        f"SELECT partner_name, partner_type, amount_pct FROM supply_chain WHERE code='{code}' ORDER BY amount_pct DESC LIMIT 10")
    
    if not rows:
        return {'score': 0, 'max': 15, 'evidence': '供应链数据无'}
    
    bound_count = 0
    bound_pct = 0
    for r in rows:
        parts = r.split('|')
        if len(parts) < 2: continue
        pname = parts[0].strip().lower()
        ptype = parts[1].strip() if len(parts) >= 2 else ''
        pct = sf(parts[2]) if len(parts) >= 3 else 0
        
        for cn in concept_names:
            if len(cn) >= 2 and cn.lower() in pname:
                bound_count += 1
                bound_pct += pct
                break
    
    if bound_count >= 3 or bound_pct > 50:
        score = 15
        evidence.append(f'深度绑定(占比{bound_pct:.0f}%)🔥')
    elif bound_count >= 2 or bound_pct > 20:
        score = 10
        evidence.append(f'绑定(占比{bound_pct:.0f}%)')
    elif bound_count >= 1:
        score = 5
        evidence.append(f'轻微关联')
    
    return {'score': score, 'max': 15, 'evidence': '; '.join(evidence[:2]) if evidence else '无关联'}


def score_dim4_financial(code):
    """
    维度4：业绩验证度 (15分)
    
    净利润为正+毛利率高→实质性受益
    亏损→可能是纯蹭（还没产生利润）
    """
    rows = sql(FUND_DB,
        f"SELECT net_profit, gross_profit_margin, roe FROM profit_data WHERE code='{code}' AND stat_date=(SELECT MAX(stat_date) FROM profit_data)")
    
    if not rows:
        return {'score': 5, 'max': 15, 'evidence': '财务数据延迟'}
    
    parts = rows[0].split('|')
    np = sf(parts[0]) if len(parts) >= 1 else 0
    gm = sf(parts[1]) if len(parts) >= 2 else 0
    roe = sf(parts[2]) if len(parts) >= 3 else 0
    
    score = 0
    evidence = []
    
    if np > 1e9:  # 净利润>10亿 → 大块头
        score += 5
        evidence.append(f'净利{np/1e8:.1f}亿')
    elif np > 1e8:
        score += 4
        evidence.append(f'净利{np/1e8:.1f}亿')
    elif np > 0:
        score += 2
        evidence.append(f'盈利')
    else:
        evidence.append(f'亏损{abs(np)/1e8:.1f}亿🔴')
    
    if gm > 40:
        score += 5
        evidence.append(f'高毛利率{gm:.1f}%🔥')
    elif gm > 20:
        score += 3
        evidence.append(f'毛利率{gm:.1f}%')
    else:
        evidence.append(f'毛利率{gm:.1f}%')
    
    if roe > 15:
        score += 5
        evidence.append(f'ROE{roe:.1f}%优秀')
    elif roe > 5:
        score += 3
        evidence.append(f'ROE{roe:.1f}%')
    
    return {'score': min(15, score), 'max': 15, 'evidence': '; '.join(evidence[:3])}


def score_dim5_timeliness(concept_name):
    """
    维度5：概念时效性 (10分)
    
    概念被多个来源（em/ths/jyg）覆盖→长期有效概念
    只有一个来源→可能是临时标签
    """
    escaped = concept_name.replace("'", "''")
    rows = sql(PROFILE_DB,
        f"SELECT source, COUNT(*) FROM concepts WHERE concept_name LIKE '%{escaped}%' GROUP BY source")
    
    sources = set()
    total = 0
    for r in rows:
        parts = r.split('|')
        if len(parts) >= 2:
            sources.add(parts[0].strip())
            total += int(parts[1])
    
    score = 0
    evidence = []
    
    if len(sources) >= 4:
        score = 10
    elif len(sources) >= 3:
        score = 8
    elif len(sources) >= 2:
        score = 6
    elif len(sources) >= 1:
        score = 4
    
    evidence.append(f'{len(sources)}个来源({",".join(sources)})')
    evidence.append(f'覆盖{total}只')
    
    return {'score': score, 'max': 10, 'evidence': '; '.join(evidence)}


def score_dim6_elasticity(code):
    """维度6：弹性修正 (5分)"""
    rows = sql(PROFILE_DB, f"SELECT total_shares FROM stock_basic WHERE code='{code}'")
    if not rows:
        return {'score': 2, 'max': 5, 'evidence': '市值未知'}
    
    shares = sf(rows[0].split('|')[0]) / 1e8 if rows[0].split('|')[0] else 0
    est_mcap = shares * 10 if shares <= 100 else shares
    
    if est_mcap <= 0:
        return {'score': 2, 'max': 5, 'evidence': '市值未知'}
    elif est_mcap < 30:
        return {'score': 5, 'max': 5, 'evidence': f'微盘{est_mcap:.0f}亿🔥'}
    elif est_mcap < 80:
        return {'score': 4, 'max': 5, 'evidence': f'小盘{est_mcap:.0f}亿⚡'}
    elif est_mcap < 200:
        return {'score': 3, 'max': 5, 'evidence': f'中盘{est_mcap:.0f}亿'}
    else:
        return {'score': 1, 'max': 5, 'evidence': f'大盘{est_mcap:.0f}亿'}


# ============================================================
# 主评分流程
# ============================================================

def score_concept_stocks(concept_name):
    """对一个概念下的所有股票进行纯度评分"""
    log(f"🎯 评分概念: {concept_name}")
    
    stocks = load_concept_stocks(concept_name)
    if not stocks:
        log(f"   ⚠️ 未找到股票")
        return []
    
    codes = [s['code'] for s in stocks]
    log(f"   📊 {len(stocks)} 只待评分")
    
    # 前置数据加载
    products = load_products_data(codes)
    biz_map = load_stock_business(codes)
    fin_data = load_financial_data(codes)
    limit_map, perf_map = load_kline_data(codes)
    
    # 概念时效性（维度5，所有股票相同）
    dim5 = score_dim5_timeliness(concept_name)
    
    results = []
    
    for s in stocks:
        code, name = s['code'], s['name']
        
        d1 = score_dim1_business_match(code, name, concept_name, products, biz_map)
        d2 = score_dim2_announcement(code, concept_name)
        d3 = score_dim3_supply_chain(code, concept_name)
        d4 = score_dim4_financial(code)
        d6 = score_dim6_elasticity(code)
        
        total = d1['score'] + d2['score'] + d3['score'] + d4['score'] + dim5['score'] + d6['score']
        
        # 评级
        if total >= 80:
            rank = '龙一'
            icon = '🟢'
        elif total >= 60:
            rank = '核心受益'
            icon = '🔵'
        elif total >= 40:
            rank = '间接受益'
            icon = '🟡'
        elif total >= 20:
            rank = '跟风/蹭概念'
            icon = '🟠'
        else:
            rank = '纯蹭概念'
            icon = '🔴'
        
        # 涨停次数
        limit_cnt = limit_map.get(code, 0)
        today_chg = perf_map.get(code, 0)
        
        results.append({
            'code': code,
            'name': name,
            'total_score': total,
            'rank': rank,
            'icon': icon,
            'd1_score': d1['score'], 'd1_max': d1['max'], 'd1_evidence': d1['evidence'],
            'd2_score': d2['score'], 'd2_max': d2['max'], 'd2_evidence': d2['evidence'],
            'd3_score': d3['score'], 'd3_max': d3['max'], 'd3_evidence': d3['evidence'],
            'd4_score': d4['score'], 'd4_max': d4['max'], 'd4_evidence': d4['evidence'],
            'd5_score': dim5['score'], 'd5_max': dim5['max'], 'd5_evidence': dim5['evidence'],
            'd6_score': d6['score'], 'd6_max': d6['max'], 'd6_evidence': d6['evidence'],
            'limit_count': limit_cnt,
            'today_chg': today_chg,
        })
    
    # 按总分排序
    results.sort(key=lambda x: -x['total_score'])
    
    return results


def print_results(concept_name, results):
    """格式化输出评分结果"""
    if not results:
        print(f"❌ 概念「{concept_name}」无数据")
        return
    
    lines = []
    lines.append(f"\n🎯 {concept_name} — 概念纯度评分")
    lines.append(f"{'='*60}")
    lines.append(f"共{len(results)}只 | 评分维度: ①主营35 ②公告20 ③供应链15 ④业绩15 ⑤时效10 ⑥弹性5")
    lines.append(f"{'='*60}")
    
    # 按评级分组
    by_rank = defaultdict(list)
    for r in results:
        by_rank[r['rank']].append(r)
    
    # 龙一
    if '龙一' in by_rank:
        lines.append(f"\n🟢 龙一（≥80分）:")
        for r in by_rank['龙一']:
            lines.append(f"  {r['icon']} {r['name']}({r['code']}) → {r['total_score']}分")
            lines.append(f"    ①主营+{r['d1_score']} ②公告+{r['d2_score']} ③供应链+{r['d3_score']} ④业绩+{r['d4_score']} ⑤时效+{r['d5_score']} ⑥弹性+{r['d6_score']}")
            lines.append(f"    主营:{r['d1_evidence'][:40]}")
            if r['d2_score'] > 0:
                lines.append(f"    公告:{r['d2_evidence'][:40]}")
    
    # 核心受益
    if '核心受益' in by_rank:
        lines.append(f"\n🔵 核心受益（60-79分）:")
        for r in by_rank['核心受益'][:5]:
            lines.append(f"  {r['icon']} {r['name']}({r['code']}) → {r['total_score']}分")
            lines.append(f"    ①主营+{r['d1_score']} ②公告+{r['d2_score']} ③供应链+{r['d3_score']} ④业绩+{r['d4_score']}")
    
    # 间接受益
    if '间接受益' in by_rank:
        lines.append(f"\n🟡 间接受益（40-59分）:")
        for r in by_rank['间接受益'][:3]:
            lines.append(f"  {r['name']}({r['code']}) → {r['total_score']}分")
    
    # 跟风/蹭概念
    for rk in ['跟风/蹭概念', '纯蹭概念']:
        if rk in by_rank:
            lines.append(f"\n{'🟠' if rk == '跟风/蹭概念' else '🔴'} {rk}:")
            for r in by_rank[rk][:3]:
                lines.append(f"  {r['name']}({r['code']}) → {r['total_score']}分 | {r['d1_evidence'][:30]}")
            if len(by_rank[rk]) > 3:
                lines.append(f"  ... 还有{len(by_rank[rk])-3}只")
    
    # 龙头->跟风走势规律
    lines.append(f"\n📊 题材内规律")
    if '龙一' in by_rank and len(by_rank['龙一']) > 0:
        top3 = results[:3]
        names = [f"{r['name']}({r['total_score']}分)" for r in top3]
        lines.append(f"  纯度高: {' < '.join(names)}")
        lines.append(f"  💡 龙一涨幅{top3[0]['today_chg']:+.1f}%" if top3[0]['today_chg'] != 0 else "  💡 龙一今日未交易")
    
    lines.append(f"\n{'-'*60}")
    
    print('\n'.join(lines))


def save_to_db(concept_name, results):
    """将评分写入数据库"""
    if not results:
        return
    
    # 创建纯度表（如果不存在）
    sql(PROFILE_DB, """
        CREATE TABLE IF NOT EXISTS concept_purity (
            concept_name TEXT,
            code TEXT,
            total_score REAL,
            rank TEXT,
            d1_score REAL,
            d2_score REAL, 
            d3_score REAL,
            d4_score REAL,
            d5_score REAL,
            d6_score REAL,
            updated_at TEXT,
            PRIMARY KEY (concept_name, code)
        )
    """)
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    escaped_cn = concept_name.replace("'", "''")
    
    for r in results:
        sql(PROFILE_DB,
            f"INSERT OR REPLACE INTO concept_purity VALUES("
            f"'{escaped_cn}','{r['code']}',{r['total_score']},'{r['rank']}',"
            f"{r['d1_score']},{r['d2_score']},{r['d3_score']},{r['d4_score']},{r['d5_score']},{r['d6_score']},'{now}')")
    
    log(f"✅ 已写入 {len(results)} 条纯度数据到 concept_purity 表")


# ============================================================
# 主入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description='概念纯度评分系统')
    parser.add_argument('--concept', type=str, help='概念名称（如"康宁概念"）')
    parser.add_argument('--all', action='store_true', help='评分所有概念')
    parser.add_argument('--save', action='store_true', help='保存到数据库')
    parser.add_argument('--top', type=int, default=5, help='最多显示的概念数')
    args = parser.parse_args()
    
    if args.concept:
        results = score_concept_stocks(args.concept)
        print_results(args.concept, results)
        if args.save:
            save_to_db(args.concept, results)
    
    elif args.all:
        # 获取所有概念
        rows = sql(PROFILE_DB,
            "SELECT concept_name FROM concept_quality WHERE stock_count>=3 AND stock_count<=100 "
            "ORDER BY stock_count DESC LIMIT {}".format(args.top * 10))
        
        concepts = list(set(r.split('|')[0].strip() for r in rows if r))
        log(f"找到 {len(concepts)} 个概念，评分前{args.top}个")
        
        for cn in concepts[:args.top]:
            results = score_concept_stocks(cn)
            print_results(cn, results)
            if args.save:
                save_to_db(cn, results)
            print()
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
