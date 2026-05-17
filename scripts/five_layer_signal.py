#!/usr/bin/env python3
"""
🎯 A股五层递进选股信号系统 v1.0
==============================

决策链：消息面解读 → 主线判定 → 预期差估值 → 利好题材内选股 → 盘中资金验证

每层是一道筛子，层层递进：
  第1层 消息面解读 → 自动判断消息性质（超预期/符合预期/利空/无关）
  第2层 主线判定 → 判断是否属于S/A级主线/支线题材
  第3层 预期差估值 → 计算预期估值空间+当前估值+预期差
  第4层 利好题材内选股 → 在确认的利好题材内筛选最优标的（龙头+补涨+备选）
  第5层 盘中资金验证 → 实时检查主力资金信号→生成买入/观望信号

每交易日运行：
  - 9:20 爬取当日消息面
  - 9:30 自动推进第1-4层，输出候选池
  - 10:00/11:00/14:00 第5层启动，盘中资金验证→买入信号
"""

import os, sys, json, time, subprocess, re
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, 'data', 'stock_profiles.db')
sys.path.insert(0, BASE)

HEADERS = 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

def curl_get(url, timeout=8):
    try:
        r = subprocess.run(
            ['curl', '-s', url, '-H', HEADERS, '--connect-timeout', str(timeout), '--max-time', str(timeout+5)],
            capture_output=True, timeout=timeout+8
        )
        return r.stdout.decode('utf-8', errors='replace')
    except:
        return None

def run_sql(sql):
    r = subprocess.run(['sqlite3', DB_PATH], input=sql.encode(), capture_output=True, timeout=60)
    return r.stdout.decode().strip()

# ============================================================
# 第0层：全局定义 - 产业链知识图谱
# ============================================================

# 产业链阶段权重（决定该阶段买入的成功率）
STAGE_WEIGHTS = {
    '1': {'name': '消息催化初期', 'weight': 1, 'desc': '纯概念炒作，波动大', 'investable': False},
    '1→2': {'name': '消息→订单过渡', 'weight': 2, 'desc': '有政策/产品曝光，等待订单确认', 'investable': False},
    '1→2过渡': {'name': '消息→订单过渡', 'weight': 2, 'desc': '有政策/产品曝光，等待订单确认', 'investable': False},
    '2': {'name': '订单验证期', 'weight': 3, 'desc': '订单陆续落地，确定性提高', 'investable': True},
    '2→3': {'name': '订单→业绩过渡', 'weight': 4, 'desc': '最佳窗口！订单确认+业绩未兑现', 'investable': True},
    '2→3过渡': {'name': '订单→业绩过渡', 'weight': 4, 'desc': '最佳窗口！订单确认+业绩未兑现', 'investable': True},
    '3': {'name': '业绩兑现期', 'weight': 3, 'desc': '业绩披露中，超预期/不及预期都敏感', 'investable': True},
    '3→4': {'name': '业绩→扩散期', 'weight': 2, 'desc': '龙头已涨完，概念扩散到二三线', 'investable': False},
    '3→4过渡': {'name': '业绩→扩散期', 'weight': 2, 'desc': '龙头已涨完，概念扩散到二三线', 'investable': False},
    '3末期': {'name': '业绩兑现末期', 'weight': 1, 'desc': '利好出尽，边际递减严重', 'investable': False},
    '4': {'name': '产业出清期', 'weight': 0, 'desc': '资金撤离，空仓等待', 'investable': False},
    '1初期': {'name': '消息催化初期', 'weight': 1, 'desc': '纯概念炒作，波动大', 'investable': False},
}

# 产业链定义（与valuation_engine.py同步，但加入更多维度）
INDUSTRY_CHAIN = {
    # ===== S级主线：AI算力 =====
    'AI算力': {
        'level': 'S级主线',
        'stage': '2→3过渡',
        'period': '2023.01~至今',
        'us_anchors': ['NVDA', 'AMD', 'AVGO', 'MRVL'],
        'a_anchors': ['光模块', '服务器', '数据中心'],
        'mainboard_picks': ['601138', '603019', '600703', '600487', '600745'],
        'reference_boards': ['300308', '300394', '300502', '688041'],
        'key_message_types': ['NVDA财报', '资本开支数据', '1.6T/3.2T量产', '微软/Meta资本开支'],
        'best_entry': 'NVDA财报前3天',
        'risk': '利好边际递减，第八次NVDA财报后光模块联动已趋零',
        'main_concepts': ['AI芯片', '光模块', '算力概念', '服务器', '数据中心', '半导体概念', '国产芯片'],
        'investable': True,
    },
    # ===== A级主线：存储芯片 =====
    '存储芯片': {
        'level': 'A级主线',
        'stage': '2→3过渡',
        'period': '2023.09~至今',
        'us_anchors': ['MU', 'SAMSUNG(005930)', 'SK하이닉스(000660)'],
        'a_anchors': ['兆易创新', '北京君正'],
        'mainboard_picks': ['603986'],
        'reference_boards': ['300661', '688008', '002371'],
        'key_message_types': ['MU财报', '存储涨价', 'HBM出货量', 'NOR Flash报价'],
        'best_entry': 'MU财报日/次日开盘买入',
        'risk': '存储周期斜率待确认，若AI资本开支放缓则存储需求不达预期',
        'main_concepts': ['存储芯片', '半导体概念', '国产芯片'],
        'investable': True,
    },
    # ===== A级主线：低空经济 =====
    '低空经济': {
        'level': 'A级主线(政策驱动)',
        'stage': '1→2过渡',
        'period': '2023.12~至今',
        'us_anchors': [],
        'a_anchors': ['亿航智能(EH)'],
        'mainboard_picks': ['600862', '600760', '600118', '600372'],
        'reference_boards': ['002097', '300696'],
        'key_message_types': ['中央政策文件', '适航认证', 'eVTOL试飞', '空域开放试点'],
        'best_entry': '政策发布前埋伏（两会/中央经济会议前后）',
        'risk': '纯政策驱动，0业绩支撑，政策递减效应（首次+43%，后续递减）',
        'main_concepts': ['低空经济', '商业航天', '无人机', 'eVTOL', '军工'],
        'investable': True,
    },
    # ===== A级：机器人 =====
    '机器人': {
        'level': 'A级主线(0→1)',
        'stage': '1初期',
        'period': '2024.01~至今',
        'us_anchors': ['TSLA'],
        'a_anchors': [],
        'mainboard_picks': ['600406', '601100', '600580'],
        'reference_boards': ['300124', '002472', '688017', '300503'],
        'key_message_types': ['Optimus量产', 'Figure融资', '国产机器人政策'],
        'best_entry': 'Optimus量产确认节点',
        'risk': '0→1阶段，90%概率伪证',
        'main_concepts': ['机器人概念', '人形机器人', '传感器', '机器视觉'],
        'investable': False,
    },
    # ===== B级支线：商业航天 =====
    '商业航天': {
        'level': 'B级支线',
        'stage': '1',
        'period': '2024.05~至今',
        'us_anchors': ['SPCE', 'RKLB'],
        'a_anchors': [],
        'mainboard_picks': ['600118', '600879', '600391'],
        'reference_boards': ['300342', '300447'],
        'key_message_types': ['火箭发射', '卫星组网', '星链进展'],
        'best_entry': '发射成功前埋伏',
        'risk': 'A股纯概念，无业绩支撑',
        'main_concepts': ['商业航天', '卫星互联网', '航天', '军工'],
        'investable': False,
    },
    # ===== B级支线：固态电池 =====
    '固态电池': {
        'level': 'B级支线',
        'stage': '1→2过渡',
        'period': '2024.04~至今',
        'us_anchors': ['QS'],
        'a_anchors': ['宁德时代', '比亚迪'],
        'mainboard_picks': ['600104', '600733', '600741'],
        'reference_boards': ['300750', '002709', '002812'],
        'key_message_types': ['固态电池量产', '车企定点', 'QS进展'],
        'best_entry': '量产消息确认时',
        'risk': '离量产还有2-3年，纯炒作',
        'main_concepts': ['固态电池', '锂电池', '新能源汽车'],
        'investable': False,
    },
}

# ============================================================
# 第1层：消息面解读
# ============================================================

# 消息类型判断规则
MESSAGE_RULES = [
    # (关键词, 类型, 影响强度1-5)
    ('财报', '财报业绩', 4),
    ('业绩预告', '财报业绩', 4),
    ('营收', '财报业绩', 3),
    ('净利润', '财报业绩', 3),
    ('超预期', '财报业绩', 5),
    ('不及预期', '财报业绩', -5),
    ('量产', '产品/技术', 4),
    ('出货', '产品/技术', 3),
    ('发布', '产品/技术', 3),
    ('上线', '产品/技术', 2),
    ('适航证', '政策/认证', 5),
    ('批准', '政策/认证', 4),
    ('政策', '政策/认证', 4),
    ('试点', '政策/认证', 3),
    ('开放', '政策/认证', 3),
    ('合作', '订单/合作', 3),
    ('订单', '订单/合作', 4),
    ('中标', '订单/合作', 3),
    ('投资', '资本/融资', 3),
    ('融资', '资本/融资', 3),
    ('募资', '资本/融资', 2),
    ('增发', '资本/融资', -2),
    ('减持', '股东行为', -3),
    ('增持', '股东行为', 3),
    ('回购', '股东行为', 3),
    ('立案', '监管/风险', -4),
    ('处罚', '监管/风险', -4),
    ('退市', '监管/风险', -5),
    ('ST', '监管/风险', -3),
]

def classify_message(title):
    """解读消息：类型+影响强度+所属产业链"""
    title_lower = title.lower()
    
    # 确定类型和强度
    msg_type = '其他'
    intensity = 0
    for keyword, mtype, imp in MESSAGE_RULES:
        if keyword.lower() in title_lower:
            if abs(imp) > abs(intensity):
                msg_type = mtype
                intensity = imp
    
    # 如果没有匹配到关键词，强度为0（中性）
    if intensity == 0:
        msg_type = '其他'
        intensity = 0
    
    # 匹配产业链
    matched_chains = []
    for chain_name, chain_info in INDUSTRY_CHAIN.items():
        for concept in chain_info['main_concepts']:
            if concept.lower() in title_lower:
                matched_chains.append(chain_name)
                break
    
    return {
        'title': title,
        'type': msg_type,
        'intensity': intensity,
        'chains': matched_chains,
        'is_positive': intensity > 0,
        'is_negative': intensity < 0,
    }

# ============================================================
# 第2层：主线判定
# ============================================================

def judge_mainline(chain_name):
    """判断产业链阶段是否可投资"""
    chain = INDUSTRY_CHAIN.get(chain_name)
    if not chain:
        return None
    
    stage = chain['stage']
    stage_info = STAGE_WEIGHTS.get(stage, {})
    
    return {
        'name': chain_name,
        'level': chain['level'],
        'stage': stage,
        'stage_name': stage_info.get('name', '未知'),
        'weight': stage_info.get('weight', 0),
        'investable': stage_info.get('investable', False),
        'desc': stage_info.get('desc', ''),
        'mainboard_picks': chain['mainboard_picks'],
        'us_anchors': chain['us_anchors'],
        'risk': chain['risk'],
        'best_entry': chain['best_entry'],
        'main_concepts': chain['main_concepts'],
    }

# ============================================================
# 第3层：预期差估值
# ============================================================

def get_stock_valuation(code):
    """获取股票当前估值数据"""
    # 从valuations表获取最新估值
    out = run_sql(f"""
        SELECT date, pe_ttm, pb, market_cap
        FROM valuations
        WHERE code = '{code}'
        ORDER BY date DESC LIMIT 1
    """)
    val = {}
    for line in out.split('\n'):
        if '|' in line:
            p = line.split('|')
            val = {
                'date': p[0].strip(),
                'pe_ttm': float(p[1]) if p[1] else None,
                'pb': float(p[2]) if p[2] else None,
                'market_cap': float(p[3]) if p[3] else None,
            }
    
    # 从stock_basic获取行业信息
    out2 = run_sql(f"SELECT name, industry_sw, main_business FROM stock_basic WHERE code = '{code}'")
    basic = {}
    for line in out2.split('\n'):
        if '|' in line:
            p = line.split('|')
            basic = {'name': p[0], 'industry': p[1], 'business': p[2] if len(p) > 2 else ''}
    
    return {**val, **basic}

def calc_expected_gap(chain_name, code):
    """计算预期差估值空间"""
    chain = INDUSTRY_CHAIN.get(chain_name)
    if not chain:
        return None
    
    val = get_stock_valuation(code)
    if not val or val.get('pe_ttm') is None:
        return {'has_valuation': False, 'gap_pct': None}
    
    # 各产业链的预期PE锚定
    EXPECTED_PE = {
        'AI算力': 45,      # 高增长，给45倍PE
        '存储芯片': 40,    # 周期性成长，给40倍PE
        '低空经济': None,  # 无业绩，不适用PE
        '机器人': None,    # 无业绩，不适用PE
        '商业航天': None,  # 无业绩，不适用PE
        '固态电池': None,  # 无业绩，不适用PE
    }
    
    expected_pe = EXPECTED_PE.get(chain_name)
    if expected_pe is None or val['pe_ttm'] is None or val['pe_ttm'] == 0:
        return {
            'has_valuation': False,
            'current_pe': val.get('pe_ttm'),
            'expected_pe': expected_pe,
            'gap_pct': None,
            'reason': '无业绩支撑或PE数据不足'
        }
    
    current_pe = val['pe_ttm']
    gap_pct = (expected_pe - current_pe) / current_pe * 100
    
    result = {
        'has_valuation': True,
        'current_pe': round(current_pe, 2),
        'expected_pe': expected_pe,
        'gap_pct': round(gap_pct, 1),
        'market_cap': val.get('market_cap'),
    }
    
    # 判断估值状态
    if gap_pct > 30:
        result['status'] = '低估 ✅ 有上升空间'
    elif gap_pct > 10:
        result['status'] = '合理偏低 ⚠️'
    elif gap_pct > -10:
        result['status'] = '合理估值 ➖'
    elif gap_pct > -30:
        result['status'] = '偏高 ❌ 空间有限'
    else:
        result['status'] = '高估 🚫 回避'
    
    return result

# ============================================================
# 第4层：利好题材内选股
# ============================================================

def get_realtime_prices(codes):
    """获取股票实时价格和涨跌幅"""
    if not codes:
        return {}
    tcodes = []
    for code in codes:
        prefix = 'sh' if code.startswith('6') else 'sz'
        tcodes.append(f"{prefix}{code}")
    
    code_str = ','.join(tcodes)
    url = f"https://qt.gtimg.cn/q={code_str}"
    raw = curl_get(url)
    if not raw:
        return {}
    
    result = {}
    for line in raw.split('\n'):
        if not line.strip():
            continue
        try:
            parts = line.split('"')
            if len(parts) < 2:
                continue
            fields = parts[1].split('~')
            if len(fields) < 40:
                continue
            code = fields[2]
            result[code] = {
                'name': fields[1],
                'price': float(fields[3]) if fields[3] else 0,
                'change_pct': float(fields[32]) if fields[32] else 0,
                'volume_ratio': float(fields[49]) if len(fields) > 49 and fields[49] else 0,
                'turnover_rate': float(fields[38]) if fields[38] else 0,
                'amplitude': float(fields[43]) if fields[43] else 0,
                'amount': float(fields[37]) if fields[37] else 0,
            }
        except (IndexError, ValueError):
            continue
    return result

def get_money_flow(codes):
    """获取主力资金数据"""
    if not codes:
        return {}
    code_str = ','.join(codes)
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secids={code_str}&fields=f62,f64,f66,f69,f72,f75,f78,f84,f87,f184,f185"
    raw = curl_get(url)
    if not raw:
        return {}
    
    try:
        data = json.loads(raw)
        items = data.get('data', []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            return {}
    except:
        return {}
    
    result = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        code = item.get('code', '')
        result[code] = {
            'main_inflow': item.get('f62', 0),        # 主力净流入
            'main_pct': item.get('f184', 0),           # 主力净占比%
            'super_large_inflow': item.get('f66', 0),  # 超大单净流入
            'super_large_pct': item.get('f75', 0),     # 超大单占比%
            'large_inflow': item.get('f69', 0),        # 大单净流入
        }
    return result

def select_stocks_in_chain(chain_name, chain_info):
    """在利好题材内选股：龙头+补涨+备选"""
    picks = chain_info['mainboard_picks']
    refs = chain_info.get('reference_boards', [])
    all_codes = picks + refs
    
    # 获取实时行情
    realtime = get_realtime_prices(all_codes)
    if not realtime:
        return []
    
    # 获取资金流
    flow = get_money_flow(all_codes)
    
    # 获取估值
    results = []
    for code in picks:
        rt = realtime.get(code, {})
        f = flow.get(code, {})
        val = calc_expected_gap(chain_name, code)
        name = rt.get('name', '')
        
        # 估值空间信号
        gap_signal = ''
        if val and val.get('gap_pct') is not None:
            gap = val['gap_pct']
            if gap > 20:
                gap_signal = '低估'
            elif gap > 5:
                gap_signal = '偏低'
            elif gap > -5:
                gap_signal = '合理'
            else:
                gap_signal = '偏高'
        
        results.append({
            'code': code,
            'name': name or run_sql(f"SELECT name FROM stock_basic WHERE code='{code}'").split('\n')[0] if name else code,
            'price': rt.get('price', 0),
            'change_pct': rt.get('change_pct', 0),
            'volume_ratio': rt.get('volume_ratio', 0),
            'amplitude': rt.get('amplitude', 0),
            'main_inflow': f.get('main_inflow', 0),
            'main_pct': f.get('main_pct', 0),
            'super_large_pct': f.get('super_large_pct', 0),
            'pe': val.get('current_pe') if val else None,
            'gap_pct': val.get('gap_pct') if val else None,
            'gap_signal': gap_signal,
            'market_cap': val.get('market_cap') if val else None,
        })
    
    return results

# ============================================================
# 第5层：盘中资金验证→买入信号
# ============================================================

def check_buy_signal(stock):
    """验证盘中资金信号→生成买入/观望"""
    
    signals = []
    signal_count = 0
    
    # 信号1：主力资金净买入
    main_pct = stock.get('main_pct', 0)
    if main_pct > 5:
        signals.append(f'✅ 主力净买入 {main_pct:+.1f}%')
        signal_count += 1
    elif main_pct > 2:
        signals.append(f'⚠️ 主力小幅买入 {main_pct:+.1f}%')
    elif main_pct < -5:
        signals.append(f'❌ 主力净卖出 {main_pct:+.1f}%')
        signal_count -= 1
    elif main_pct < -2:
        signals.append(f'⚠️ 主力小幅卖出 {main_pct:+.1f}%')
    else:
        signals.append(f'➖ 主力资金中性 {main_pct:+.1f}%')
    
    # 信号2：超大单买入
    sl_pct = stock.get('super_large_pct', 0)
    if sl_pct > 10:
        signals.append(f'✅ 超大单强买入 {sl_pct:+.1f}%')
        signal_count += 1
    elif sl_pct > 0:
        signals.append(f'⚠️ 超大单小幅买入 {sl_pct:+.1f}%')
    elif sl_pct < -10:
        signals.append(f'❌ 超大单强卖出 {sl_pct:+.1f}%')
        signal_count -= 1
    
    # 信号3：量比>1.2（放量）
    vol_ratio = stock.get('volume_ratio', 0)
    if vol_ratio > 2:
        signals.append(f'✅ 放量 {vol_ratio:.2f}倍')
        signal_count += 1
    elif vol_ratio > 1.2:
        signals.append(f'⚠️ 小幅放量 {vol_ratio:.2f}倍')
    else:
        signals.append(f'➖ 量能正常 {vol_ratio:.2f}倍')
    
    # 信号4：涨幅区间（不追高，不买跌）
    change = stock.get('change_pct', 0)
    if -2 <= change <= 3:
        signals.append(f'✅ 涨幅适中 {change:+.2f}%')
        signal_count += 1
    elif change > 5:
        signals.append(f'⚠️ 涨幅偏大 {change:+.2f}%（追高风险）')
    elif change > 9:
        signals.append(f'❌ 接近涨停，无法买入')
        signal_count -= 1
    elif change < -4:
        signals.append(f'❌ 跌幅过大 {change:+.2f}%（趋势可能破坏）')
        signal_count -= 1
    
    # 信号5：估值空间
    gap = stock.get('gap_pct')
    if gap is not None:
        if gap > 30:
            signals.append(f'✅ 估值空间 {gap:+.0f}%（低估）')
            signal_count += 1
        elif gap > 10:
            signals.append(f'⚠️ 估值空间 {gap:+.0f}%（偏低）')
        elif gap < -20:
            signals.append(f'❌ 估值偏高 {gap:+.0f}%')
            signal_count -= 1
    
    # 综合决策
    if signal_count >= 3:
        decision = '🟢 果断买入'
    elif signal_count >= 1:
        decision = '🟡 观望（信号不够强）'
    else:
        decision = '🔴 等待（信号为负）'
    
    return {
        'signal_count': signal_count,
        'decision': decision,
        'signals': signals,
    }

# ============================================================
# 主流程
# ============================================================

def main():
    now = datetime.now()
    print("=" * 65)
    print("🎯 A股五层递进选股信号系统")
    print(f"   {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    
    # 第1步：获取今日消息面
    # 如果是人工输入的，直接使用；如果是自动跑，从news_monitor取
    today_messages = []
    
    # 检查是否有新闻监控数据
    news_out = run_sql("""
        SELECT title FROM news_events 
        WHERE date = date('now') AND source IN ('公告', '新闻')
        LIMIT 20
    """)
    for line in news_out.split('\n'):
        if line.strip():
            today_messages.append(line.strip())
    
    # 同时也尝试从新浪财经获取
    sina_news_raw = curl_get("https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=10")
    if sina_news_raw:
        try:
            data = json.loads(sina_news_raw)
            for item in data.get('result', {}).get('data', []):
                title = item.get('title', '')
                if title:
                    today_messages.append(title)
        except:
            pass
    
    print(f"\n📰 【第1层】消息面解读 — 今日 {len(today_messages)} 条消息")
    print("-" * 65)
    
    # 分类消息
    classified_messages = [classify_message(msg) for msg in today_messages[:15]]
    
    # 按产业链分组
    chain_messages = defaultdict(list)
    other_messages = []
    for msg in classified_messages:
        if msg['chains']:
            for chain in msg['chains']:
                chain_messages[chain].append(msg)
        else:
            other_messages.append(msg)
        
        if msg['intensity'] != 0:
            icon = '🟢' if msg['is_positive'] else '🔴'
            bar = '|' * abs(msg['intensity'])
            chains_str = ','.join(msg['chains']) if msg['chains'] else '—'
            print(f"   {icon}{bar} [{msg['type']}] {msg['title'][:50]} → {chains_str}")
    
    # 第2层：主线判定
    print(f"\n🏆 【第2层】主线判定 — 产业链阶段评估")
    print("-" * 65)
    
    investable_chains = []
    chain_assessments = []
    
    for chain_name in INDUSTRY_CHAIN:
        judge = judge_mainline(chain_name)
        if not judge:
            continue
        
        # 判断是否有今日消息催化
        has_news = chain_name in chain_messages
        news_strength = sum(m['intensity'] for m in chain_messages.get(chain_name, []))
        
        icon = '🟢' if judge['investable'] else '🔴'
        news_icon = '📰' if has_news else ''
        
        print(f"   {icon} {judge['level']} {chain_name:12s} | {judge['stage']:10s} | {judge['stage_name']:10s} | 权重{judge['weight']}/4 {news_icon}")
        
        if has_news:
            print(f"      📰 今日消息强度: {news_strength} (『{chain_messages[chain_name][0]['title'][:40]}』)")
        
        if judge['investable']:
            chain_assessments.append({
                **judge,
                'has_news': has_news,
                'news_strength': news_strength,
            })
    
    # 第3层：预期差估值
    print(f"\n💰 【第3层】预期差估值 — 可投资产业链标的估值空间")
    print("-" * 65)
    
    all_candidates = []
    
    for ca in chain_assessments:
        chain_name = ca['name']
        print(f"\n   {ca['level']} {chain_name}")
        
        chain_info = INDUSTRY_CHAIN.get(chain_name, {})
        stocks_data = select_stocks_in_chain(chain_name, chain_info)
        
        for s in stocks_data:
            gap_str = f"空间{s['gap_pct']:+.0f}%" if s.get('gap_pct') is not None else "无PE数据"
            pe_str = f"PE={s['pe']}" if s.get('pe') else ""
            print(f"     {s['name']}({s['code']}) | {s['change_pct']:+.2f}% | {pe_str} | {gap_str}")
            
            all_candidates.append({
                'chain': chain_name,
                'level': ca['level'],
                **s
            })
    
    # 第4+5层：选股+资金验证→买入信号
    print(f"\n🎯 【第4+5层】选股+资金验证 — 买入信号")
    print("-" * 65)
    
    # 获取所有候选股票的实时资金流
    all_pick_codes = list(set(s['code'] for s in all_candidates))
    flow_data = get_money_flow(all_pick_codes)
    realtime_data = get_realtime_prices(all_pick_codes)
    
    # 更新资金流信息
    for s in all_candidates:
        if s['code'] in flow_data:
            s.update(flow_data[s['code']])
        if s['code'] in realtime_data:
            s.update(realtime_data[s['code']])
    
    # 逐只验证信号
    buy_signals = []
    watch_signals = []
    
    for s in all_candidates:
        if not s.get('main_pct') and not s.get('price'):
            continue
        
        signal = check_buy_signal(s)
        s['signal'] = signal
        
        if '果断买入' in signal['decision']:
            buy_signals.append(s)
        else:
            watch_signals.append(s)
    
    # ===== 输出：买入信号 =====
    if buy_signals:
        print(f"\n🟢🟢🟢 ===== 有信号，果断买入 ===== 🟢🟢🟢")
        print("-" * 65)
        for s in buy_signals:
            signal = s['signal']
            gap_str = f"预期差{s['gap_pct']:+.0f}%" if s.get('gap_pct') else ''
            print(f"\n   {s['name']}({s['code']}) [{s['chain']}]")
            print(f"     现价:{s['price']:.2f} 涨幅:{s['change_pct']:+.2f}% {gap_str}")
            print(f"     {' | '.join(signal['signals'])}")
            print(f"     ➡️ {signal['decision']}")
    else:
        print("\n   ⏳ 当前无买入信号")
    
    # ===== 输出：观望信号 =====
    if watch_signals:
        print(f"\n🟡🟡🟡 ===== 等待信号 ===== 🟡🟡🟡")
        print("-" * 65)
        
        # 按信号强度排序
        watch_signals.sort(key=lambda x: -x['signal']['signal_count'])
        for s in watch_signals[:5]:
            signal = s['signal']
            print(f"   {s['name']}({s['code']}) [{s['chain']}] 分数:{signal['signal_count']}")
            print(f"     {signal['decision']}")
            for sig in signal['signals'][:3]:
                print(f"     {sig}")
    
    # ===== 总结 =====
    print(f"\n{'='*65}")
    print(f"📋 今日总结")
    print(f"{'='*65}")
    
    # 按产业链输出推荐
    for ca in chain_assessments:
        chain_name = ca['name']
        chain_buys = [s for s in buy_signals if s['chain'] == chain_name]
        chain_watches = [s for s in watch_signals if s['chain'] == chain_name]
        
        print(f"\n   {ca['level']} {chain_name} (阶段:{ca['stage']} {ca['stage_name']})")
        print(f"     {'🟢 可操作' if ca['investable'] else '🔴 不可操作'}")
        print(f"     最佳入场: {ca['best_entry']}")
        print(f"     风险: {ca['risk'][:40]}...")
        
        if chain_buys:
            names = ' '.join(f"{s['name']}({s['change_pct']:+.1f}%)" for s in chain_buys)
            print(f"     🟢 买入信号: {names}")
        
        if chain_watches:
            top_watch = chain_watches[0]
            print(f"     🟡 关注标的: {top_watch['name']}({top_watch['code']}) — {top_watch['signal']['decision']}")
    
    print(f"\n✅ 分析完成!")

if __name__ == '__main__':
    main()
