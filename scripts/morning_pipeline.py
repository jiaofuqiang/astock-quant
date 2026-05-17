#!/usr/bin/env python3
"""
三刀流早盘引擎 — 单进程全链路 v3
==================================
与之前不同：这是一个完整的单进程脚本，不再用subprocess调4次。
每一步都在同一个Python进程内完成。

执行链路：
  1. 加载LHB回测数据库（24策略）
  2. 运行穿透评分v3（6层26维环境感知）
  3. 分析龙虎榜资金特征 → 策略匹配
  4. 三刀流S级筛选 + 仓位分配
  5. 匹配个股（龙虎榜数据+腾讯实时行情双验证）
  6. 打包输出 + 打印摘要
"""

import os, json, sys, sqlite3, re, subprocess, math
from datetime import datetime as _dt

# ===== 路径 =====
BASE = os.path.expanduser('~/astock')
V2BOARD = os.path.expanduser('~/V2board')
SCRIPTS = os.path.join(BASE, 'scripts')
DATA = os.path.join(V2BOARD, 'data')
KLINE_DB = os.path.join(BASE, 'data', 'kline_cache.db')
BACKTEST_PATH = os.path.join(BASE, 'data', 'lhb_practical_backtest_v2.json')  # v2.0严格时间线回测(替换旧v1)
MARKET_DB = os.path.join(BASE, 'data', 'market_daily.db')


# ====================================================================
# 工具函数
# ====================================================================
def sf(v, default=0):
    try: return float(v) if v else default
    except: return default

def si(v, default=0):
    try: return int(v) if v else default
    except: return default

def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def db_connect(path):
    """只读数据库连接"""
    if not os.path.exists(path): return None
    try:
        c = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        return c
    except:
        return None


# ====================================================================
# 1. 加载回测数据库
# ====================================================================
def load_strategy_db():
    if not os.path.exists(BACKTEST_PATH):
        print(f"⚠️ 回测数据库不存在")
        return {'overall': {'close_mean': 0.26, 'close_win': 48.2}, 'strategies': []}
    with open(BACKTEST_PATH) as f:
        return json.load(f)


def extract_tags(name):
    tags = set()
    if '机构' in name: tags.add('机构')
    if '量化' in name: tags.add('量化')
    if '游资' in name: tags.add('游资')
    if '主力买入' in name or '买力' in name: tags.add('主力')
    if '净买入' in name or '净买' in name: tags.add('净买')
    if '连板' in name or '前日涨停' in name or '前日大涨' in name: tags.add('连板')
    if '缩量' in name: tags.add('缩量')
    if '振幅' in name or '窄幅' in name: tags.add('控盘')
    if '换手' in name: tags.add('换手')
    return tags


def compute_strategy_rankings(strategies):
    """24策略 → 综合分排序"""
    scored = []
    for s in strategies:
        ret = s.get('close_ret', 0)
        wr = s.get('close_win', 0)
        n = s.get('n', 0)
        sample_f = min(math.log(n+1)/math.log(50), 1.0) if n > 0 else 0
        composite = round(ret * 4.0 + wr * 0.3 + sample_f * 15.0, 1)
        scored.append({
            'name': s['name'], 'desc': s.get('desc', ''),
            'composite': composite, 'ret': ret, 'win_rate': wr, 'n': n,
            'big_win_pct': s.get('big_win_pct', 0),
            'big_loss_pct': s.get('big_loss_pct', 0),
            'dist': s.get('dist', {}),
            'tags': extract_tags(s['name']),
            'close_ret': s.get('close_ret', 0),
            'open_ret': s.get('open_ret', 0),
        })
    scored.sort(key=lambda x: -x['composite'])
    return scored


# ====================================================================
# 2. 注入市场数据到bundle（解决早上无数据的问题）
# ====================================================================
def inject_market_data_to_bundle():
    """
    从market_daily.db读取最新一条数据注入到bundle，
    确保穿透评分的大盘层有真实数据（而非默认值）。
    周一08:30时用上周五的缓存数据。
    🆕 v2.0: 增加env_daily_history.json环境历史数据源（570天）
    """
    db_path = os.path.join(BASE, 'data', 'market_daily.db')
    bundle_path = os.path.join(V2BOARD, 'dashboard_bundle.json')
    env_history_path = os.path.join(DATA, 'env_daily_history.json')
    
    if not os.path.exists(bundle_path):
        return
    
    try:
        with open(bundle_path) as f:
            bundle = json.load(f)
        
        # 来源1: market_daily.db（优先，3天精确数据）
        md = None
        if os.path.exists(db_path):
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM day_full ORDER BY date DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                md = dict(row)
        
        market_data = {}
        if md:
            market_data = {
                'zh_ratio': md.get('zh_ratio', 50),
                'up_ratio': md.get('zh_ratio', 50),
                'limit_up': md.get('limit_up', 0),
                'limit_down': md.get('limit_down', 0),
                'max_board': md.get('max_board', 0),
                'zhaban_rate': md.get('zhaban_rate', 30),
                'pretoday_avg_change': md.get('pretoday_avg_change', 0),
                'sh_change': md.get('sh_change', md.get('上证涨幅', 0)),
                '上证涨幅': md.get('上证涨幅', md.get('sh_change', 0)),
                'date': md.get('date', ''),
                'panic_score': md.get('panic_score', 0),
                'boom_score': md.get('boom_score', 0),
                'market_mood': md.get('market_mood', ''),
                'pretoday_lianban_rate': md.get('pretoday_lianban_rate', 50),
                'pretoday_die_gt_5_rate': md.get('pretoday_die_gt_5_rate', 20),
                '_source': f"day_full_{md.get('date','')}",
            }
            bundle['market_daily'] = market_data
            print(f"  ✅ market_daily数据注入: {md.get('date','')} (zh={md.get('zh_ratio',0):.1f}%)")
        else:
            print(f"  ⚠️ market_daily.db无数据，使用默认值")
        
        # 来源2: env_daily_history.json（570天历史环境数据，用于环境参考+周日兜底）
        if os.path.exists(env_history_path):
            with open(env_history_path) as f:
                env_history = json.load(f)
            bundle['env_daily_history'] = env_history.get('daily', {})
            bundle['env_history_summary'] = env_history.get('summary', {})
            bundle['_env_history_days'] = env_history.get('meta', {}).get('total_days', 0)
            print(f"  ✅ 环境历史数据注入: {env_history.get('meta',{}).get('total_days',0)}天")
            
            # 如果没有market_daily数据（周日/周一开盘前），用最近交易日的历史数据兜底
            if not md or not bundle.get('market_daily'):
                daily_data = env_history.get('daily', {})
                # 取最近的交易日
                recent_dates = sorted(daily_data.keys(), reverse=True)[:1]
                if recent_dates:
                    recent = recent_dates[0]
                    rd = daily_data[recent]
                    fallback = {
                        'zh_ratio': rd.get('up_ratio', 50) * 100,
                        'up_ratio': rd.get('up_ratio', 0.5) * 100,
                        'limit_up': rd.get('limit_up', 0),
                        'limit_down': rd.get('limit_down', 0),
                        'sh_change': rd.get('avg_chg', 0),
                        '上证涨幅': rd.get('avg_chg', 0),
                        'date': recent,
                        '_source': f"env_history_fallback_{recent}",
                    }
                    if 'market_daily' not in bundle or not bundle.get('market_daily'):
                        bundle['market_daily'] = fallback
                        print(f"  ✅ 环境历史兜底: {recent} (涨跌比{rd.get('up_ratio',0):.1%})")
        
        with open(bundle_path, 'w') as f:
            json.dump(bundle, f, ensure_ascii=False, default=str)
        
    except Exception as e:
        print(f"  ⚠️ bundle注入失败: {e}")

def load_recent_limit_stocks_from_kline(days=5):
    """从kline_cache.db拉取近N日涨停股，作为补充候选池"""
    path = os.path.join(BASE, 'data', 'kline_cache.db')
    if not os.path.exists(path):
        return []
    since = (_dt.now() - __import__('datetime').timedelta(days=days)).strftime('%Y-%m-%d')
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
        rows = conn.execute("""
            SELECT code, date, close, open, ROUND((close-open)/open*100,2) as chg
            FROM kline
            WHERE date >= ? AND close >= open * 1.095
            AND (code LIKE '6%' OR code LIKE '00%' OR code LIKE '30%')
            ORDER BY date DESC, chg DESC
        """, (since,)).fetchall()
        conn.close()
        seen = set()
        stocks = []
        for code, date, close, open_, chg in rows:
            if code in seen: continue
            seen.add(code)
            stocks.append({
                'code': code, 'date': date,
                'close': close, 'chg': chg,
                'from_kline_pool': True,
            })
        return stocks
    except:
        return []

def load_limit_detail_from_market_db():
    """从market_daily.db读取涨停明细，作为个股匹配的补充来源"""
    path = os.path.join(BASE, 'data', 'market_daily.db')
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
        row = conn.execute("SELECT limit_detail_json FROM day_full ORDER BY date DESC LIMIT 1").fetchone()
        conn.close()
        if row and row[0]:
            data = json.loads(row[0])
            # 去重（同一个code可能因为多个板出现多次）
            seen = set()
            stocks = []
            for item in data:
                code = item.get('c', '')
                if code in seen: continue
                seen.add(code)
                stocks.append({
                    'code': code,
                    'name': item.get('n', ''),
                    'board': item.get('bc', 0),
                    'board_str': item.get('b', '今日首板'),
                    'limit_way': item.get('lw', ''),
                    'limit_time': item.get('ct', ''),
                })
            return stocks
    except:
        pass
    return []


# ====================================================================
# 3. 穿透评分v3（直接导入模块）
# ====================================================================
def run_penetration_scoring():
    """调用穿透评分脚本"""
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, 'penetration_scoring_v1.py')],
            capture_output=True, text=True, timeout=45
        )
        if r.stdout:
            # 只打印最后20行（评分摘要）
            lines = r.stdout.strip().split('\n')
            for line in lines[-20:]:
                print(f"  {line}")
    except Exception as e:
        print(f"  ⚠️ 穿透评分调用异常: {e}")
    
    # 读取输出
    path = os.path.join(DATA, 'contradiction_report.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


# ====================================================================
# 3. 龙虎榜特征提取
# ====================================================================
def extract_lhb_tags():
    """从龙虎榜数据提取资金特征标签"""
    tags = set()
    
    # 来源1: lhb_scoring_cache.json
    lhb_path = os.path.join(DATA, 'lhb_scoring_cache.json')
    if os.path.exists(lhb_path):
        with open(lhb_path) as f:
            lhb = json.load(f)
        for item in lhb.get('actionable', []):
            # 直接读顶层字段
            inst_n = si(item.get('jg', 0))
            quant_n = si(item.get('ql', 0))
            youzi_n = si(item.get('yz', 0))
            sh = si(item.get('sh', 0))
            # 也尝试从detail读
            detail = item.get('detail', {})
            if isinstance(detail, dict):
                dealers = detail.get('dealers', [])
                inst_n = max(inst_n, sum(1 for d in dealers if '机构' in d.get('dealer', '')))
                quant_n = max(quant_n, sum(1 for d in dealers if '量化' in d.get('dealer', '')))
            if inst_n > 0: tags.add('机构')
            if quant_n > 0: tags.add('量化')
            if youzi_n > 0: tags.add('游资')
            if sh > 0: tags.add('量化')  # sh=量化类营业部
            # 主力/净买：靠买入额 > 卖出额判断
            t_buy = sf(item.get('details', ''))  # 不靠谱
            if sh > 0 or youzi_n > 0 or inst_n > 0:
                tags.add('主力')
    
    # 来源2: lhb_selected_detail.json
    detail_path = os.path.join(DATA, 'lhb_selected_detail.json')
    if os.path.exists(detail_path):
        with open(detail_path) as f:
            d = json.load(f)
        ic = si(d.get('inst_count', d.get('机构数量', 0)))
        qc = si(d.get('quant_count', d.get('量化数量', 0)))
        tb = sf(d.get('total_buy', d.get('净买入', 0)))
        if ic > 0: tags.add('机构')
        if qc > 0: tags.add('量化')
        if tb > 0: tags.add('净买')
    
    return tags


# ====================================================================
# 4. 策略匹配
# ====================================================================
def match_strategies(rankings, lhb_tags, env_score, contradiction_type=None, drive_bonus=1.0):
    '''根据资金标签精确匹配策略 + 基于主要矛盾微调（矛盾论v4.1） + 内部/外部驱动力bonus'''
    matched = []
    for s in rankings:
        stags = s['tags']
        if not stags:
            if not lhb_tags:
                match_score = s['composite'] * 0.8
            else:
                continue
        else:
            inter = stags & lhb_tags
            core_tags = stags - {'缩量', '控盘', '换手', '连板'}
            core_hit = core_tags & lhb_tags
            
            if core_hit:
                match_score = s['composite'] * min(1.0, len(core_hit) * 0.5 + 0.3)
            elif not core_tags and lhb_tags:
                match_score = s['composite'] * 0.3
            elif not inter:
                continue
            else:
                match_score = s['composite'] * 0.4
        
        # 🆕 改进2：主要矛盾驱动的策略微调
        # 主要矛盾=主线确认 → 有连板标签的加分
        # 主要矛盾=资金博弈 → 有游资/机构标签的加分
        # 主要矛盾=题材轮动 → 纯缩量策略加分
        if contradiction_type == '主线确认' and '连板' in stags:
            match_score *= 1.15  # 连板策略在主线确认期+15%
        elif contradiction_type == '资金博弈' and ('游资' in stags or '机构' in stags):
            match_score *= 1.10  # 资金博弈期，有资金标签的+10%
        elif contradiction_type == '题材轮动' and not (stags - {'缩量', '控盘', '换手', '连板'}):
            match_score *= 1.08  # 题材轮动期，纯缩量策略+8%
        
        # 🆕 改进4：过热预警
        # 策略composite分>50 → 过热，降权10%
        if s['composite'] > 50:
            overheat_note = f'🔥过热预警:复合分{s["composite"]}>50，注意矛盾转化'
            match_score *= 0.90
        else:
            overheat_note = ''
        
        if env_score < 30 and ('连板' in stags or '换手' in stags):
            match_score *= 0.3
        if match_score > 0:
            match_score = min(100, match_score * drive_bonus) if match_score > 0 else 0
            s['match_score'] = round(match_score, 1)
            entry = {**s, 'match_score': round(match_score, 1)}
            if overheat_note:
                entry['overheat_warning'] = overheat_note
            matched.append(entry)
    matched.sort(key=lambda x: -x['match_score'])
    return matched


# ====================================================================
# 5. 腾讯实时行情 + K线验证
# ====================================================================
def fetch_realtime_quotes(codes):
    """批量获取腾讯实时行情 + K线数据"""
    if not codes: return {}
    
    # 腾讯行情
    try:
        c = ','.join(codes)
        p = subprocess.run(['curl','-s','--connect-timeout','5','--max-time','10',
            f'https://qt.gtimg.cn/q={c}'], capture_output=True, timeout=15)
        t = p.stdout.decode('gbk')
    except:
        return {}
    
    quotes = {}
    for line in t.strip().split('\n'):
        m = re.search(r'"(.+)"', line)
        if not m: continue
        parts = m.group(1).split('~')
        if len(parts) < 48: continue
        code = parts[2]
        def f(i):
            try: return float(parts[i]) if parts[i] and parts[i] != '-' else None
            except: return None
        quotes[code] = {
            'name': parts[1], 'price': f(3), 'chg': f(32),
            'vol_ratio': f(39), 'turnover': f(38),
            'amount_wan': f(37), 'amplitude': f(43),
            'open': f(5),
            'yclose': f(4),
        }
        # 计算开盘涨幅
        if quotes[code]['open'] and quotes[code]['yclose'] and quotes[code]['yclose'] > 0:
            quotes[code]['open_chg'] = round((quotes[code]['open'] - quotes[code]['yclose']) / quotes[code]['yclose'] * 100, 2)
        else:
            quotes[code]['open_chg'] = None
    
    return quotes


def calc_board_count(ck, code, date_str):
    """从K线计算连板数"""
    if not ck: return 0
    rows = ck.execute(
        "SELECT date,close,open FROM kline WHERE code=? AND date<=? ORDER BY date DESC LIMIT 10",
        (code, date_str)
    ).fetchall()
    if len(rows) < 2: return 0
    count = 0
    for i in range(len(rows)-1):
        k = rows[i]
        is_limit = k['close'] >= k['open'] * 1.095 if k['open'] > 0 else False
        if not is_limit: break
        count += 1
    return count


def calc_ma5_signal(ck, code, date_str, price=None):
    """计算MA5偏离度和趋势信号"""
    if not ck or not price: return {'ma5_dist': 0, 'trend': '未知'}
    rows = ck.execute(
        "SELECT close,volume FROM kline WHERE code=? AND date<=? ORDER BY date DESC LIMIT 10",
        (code, date_str)
    ).fetchall()
    if len(rows) < 6: return {'ma5_dist': 0, 'trend': '未知'}
    
    ma5 = sum(rows[i]['close'] for i in range(1, 6)) / 5
    dist = round((price - ma5) / ma5 * 100, 2)
    
    # 判断趋势
    up3 = sum(1 for i in range(1, 4) if rows[i]['close'] > rows[i+1]['close'])
    if up3 >= 2: trend = '上升'
    elif up3 <= 1: trend = '调整'
    else: trend = '震荡'
    
    return {'ma5_dist': dist, 'ma5': round(ma5, 2), 'trend': trend}


# ====================================================================
# 6. 三刀流引擎核心
# ====================================================================
# ====================================================================
# 7. 矛盾论 — 主要矛盾识别与转化预测（v4.0）
# ====================================================================
def identify_main_contradiction(env_score, lhb_tags, s_level, signals, penetration):
    """基于矛盾论，识别当前市场的主要矛盾和矛盾转化方向"""
    contradictions = []
    primary = {}
    
    # 一、大盘层矛盾
    if env_score < 25:
        contradictions.append({
            'layer': '大盘', 'level': '⚡主要',
            'contradiction': '系统性恐慌 vs 抄底冲动',
            'main_aspect': '空方主导 — 环境冰点，矛盾未转化',
            'action': '❌ 空仓 — 主要矛盾未解决，不应主动入市',
        })
    elif env_score < 40:
        contradictions.append({
            'layer': '大盘', 'level': '⚡主要',
            'contradiction': '弱势震荡 vs 结构性机会',
            'main_aspect': '空方略优 — 需等明确转化信号',
            'action': '👀 观望为主 — 只在最强板块做龙头',
        })
    elif env_score < 55:
        contradictions.append({
            'layer': '大盘', 'level': '⚡主要',
            'contradiction': '主线上涨共识 vs 题材快速轮动',
            'main_aspect': '多方发酵 — 主线在形成但未统一',
            'action': '🎯 做S级缩量板 — 矛盾方向明确（缩量=一致），不做跟风',
        })
    elif env_score < 70:
        contradictions.append({
            'layer': '大盘', 'level': '⚡主要',
            'contradiction': '赚钱效应扩散 vs 恐高情绪积累',
            'main_aspect': '多方占优 — 但注意高位股分化',
            'action': '✅ 可加仓 — 优先连板龙头，随时准备兑现',
        })
    else:
        contradictions.append({
            'layer': '大盘', 'level': '⚡主要',
            'contradiction': '追涨狂热 vs 获利兑现压力',
            'main_aspect': '多方极盛 — 矛盾将向空方转化',
            'action': '🔥 警惕高潮转衰退 — 降低仓位，只做首板',
        })

    # 二、资金层矛盾 — 机构/游资/量化三大势力的博弈
    tag_set = set(lhb_tags)
    if '机构' in tag_set and '游资' in tag_set and '量化' in tag_set:
        contradictions.append({
            'layer': '资金', 'level': '🔍重要',
            'contradiction': '机构锁仓 vs 游资打板 vs 量化套利 — 三方合力',
            'main_aspect': '三资金合力 — 多方力量极集中，是最强矛盾信号',
            'action': '✅ 三资金合力=历史最佳策略信号，重仓缩量板',
        })
    elif '机构' in tag_set and '游资' in tag_set:
        contradictions.append({
            'layer': '资金', 'level': '🔍重要',
            'contradiction': '机构锁仓 vs 游资打板博弈',
            'main_aspect': '机构+游资合力 — 多方力量集中',
            'action': '✅ 机构游资合力=高胜率信号，缩量板可做',
        })
    elif '机构' in tag_set:
        contradictions.append({
            'layer': '资金', 'level': '🔍重要',
            'contradiction': '机构买入锁定筹码 vs 市场流动性供给',
            'main_aspect': '机构主导 — 缩量条件好，适合持股到收盘',
            'action': '🎯 机构主导适合缩量策略，持股到收盘最佳',
        })
    elif '量' in tag_set or '量化' in tag_set:
        contradictions.append({
            'layer': '资金', 'level': '🔍重要',
            'contradiction': '量化高频博弈 vs 趋势延续',
            'main_aspect': '量化活跃 — 注意量化一日游，开盘有溢价就走',
            'action': '⚠️ 量化主导易一日游，开盘有溢价建议走人',
        })
    elif '游资' in tag_set:
        contradictions.append({
            'layer': '资金', 'level': '🔍重要',
            'contradiction': '游资快进快出 vs 接力情绪',
            'main_aspect': '游资活跃 — 但需注意一日游，隔日溢价有限',
            'action': '⚠️ 游资主导的票，建议开盘有溢价就卖',
        })
    else:
        contradictions.append({
            'layer': '资金', 'level': '🔍重要',
            'contradiction': '资金观望 vs 存量博弈',
            'main_aspect': '缺少主力资金 — 市场没有主要矛盾方向',
            'action': '📉 无明确资金信号，缩量到极致的票才考虑',
        })
    
    # 三、个股层矛盾（从signals提取）
    if signals:
        for sig in signals:
            top = sig['stocks'][0] if sig['stocks'] else None
            if not top: continue
            code = top.get('code', '')
            name = top.get('name', '?')
            vol_ratio = top.get('vol_ratio')
            inst = top.get('inst_count', 0)
            buy_score = top.get('buy_score', 0)
            board = top.get('board_count', 0)
            
            # 矛盾论四维分析
            # (1) 量比 → 多空同一性程度
            if vol_ratio is None:
                liangbi_mood = '⏳待开盘确认'
                liangbi_action = '开盘后看量比: <0.5=极度同一(多方碾压), <0.7=同一趋强, >1=斗争加剧'
                liangbi_value = '未知'
            elif vol_ratio < 0.3:
                liangbi_mood = '🔥极端同一 — 多方碾压空方'
                liangbi_action = '✅ 多方绝对主导，买入后可安心持股到收盘'
                liangbi_value = '极度缩量'
            elif vol_ratio < 0.5:
                liangbi_mood = '🔥高度同一 — 缩量极端一致'
                liangbi_action = '✅ 缩量极好，买入信号'
                liangbi_value = '极端缩量'
            elif vol_ratio < 0.7:
                liangbi_mood = '✅缩量一致 — 开盘溢价高，但收盘会回落'
                liangbi_action = '✅ 缩量一致可买入，但建议开盘有溢价就卖'
                liangbi_value = '缩量'
            elif vol_ratio < 1.0:
                liangbi_mood = '⚖️正常量平衡 — 最佳买入区间(回测+2.88%/65.8%)'
                liangbi_action = '✅ 正常量+涨停=量能健康，最佳买入窗口'
                liangbi_value = '正常量'
            else:
                liangbi_mood = '⚡放量斗争 — 仍可买入但控制仓位(回测+1.76%/55.6%)'
                liangbi_action = '⚠️ 放量板并非必崩，但仓位应减半'
                liangbi_value = '放量'
            
            # (2) 连板高度 → 对抗烈度
            if board >= 3:
                board_mood = f'🔴高度对抗 — {board}连板，多空均重兵投入'
            elif board >= 2:
                board_mood = f'🟠中级对抗 — {board}连板，分歧加大'
            elif board >= 1:
                board_mood = f'🟢初级对抗 — {board}板，多方初期优势'
            else:
                board_mood = '⚪未形成连板 — 非典型股'
            
            # (3) MA5 → 趋势的矛盾方向
            ma5d = top.get('ma5_dist', 0)
            if ma5d and -2 < ma5d < 5:
                trend_mood = '✅趋势一致 — 价格的MA5合理，趋势方向明确'
            elif ma5d and ma5d > 8:
                trend_mood = '⚠️趋势分化 — 远离MA5，价格与均线的矛盾加大'
            elif ma5d and ma5d < -5:
                trend_mood = '🔴趋势逆转 — 跌破MA5，多头转化为空头'
            else:
                trend_mood = '⚖️趋势中性 — MA5附近博弈'
            
            contradictions.append({
                'layer': '个股', 'level': '🎯操作', 'code': code,
                'name': name,
                'contradiction': f'{name}({code}) — 多空博弈三维度',
                'main_aspect': liangbi_mood,
                'action': liangbi_action,
                'score': buy_score,
                'liangbi': liangbi_value,
                'board_mood': board_mood,
                'trend_mood': trend_mood,
                'stock_detail': {
                    'vol_ratio': vol_ratio,
                    'inst_count': inst,
                    'board_count': board,
                    'ma5_dist': ma5d,
                    'buy_score': buy_score,
                }
            })
    
    # 四、主要矛盾总结
    primary_contradiction = contradictions[0] if contradictions else {}
    
    # 🆕 矛盾转化预警信号 — 基于矛盾论第六章（量变质变）
    # 量比从缩量到放量 = 矛盾从同一转化为斗争
    # 连板从一致到分歧 = 矛盾从对抗转化为瓦解
    transformation_warnings = []
    if signals:
        for sig in signals:
            for st in sig.get('stocks', [])[:2]:
                st_code = st.get('code', '')
                vol_ratio = st.get('vol_ratio')
                board = st.get('board_count', 0)
                
                if vol_ratio is None:
                    continue
                warnings = []
                # 条件1：量比>0.7（同一→斗争）
                if vol_ratio >= 0.8:
                    warnings.append('⚠量比>0.8: 缩量向放量转化 — 同一变斗争')
                # 条件2：放量且板数高（对抗激化）
                if vol_ratio >= 1.0 and board >= 2:
                    warnings.append('🔥连板放量: 对抗激化 — 炸板风险高')
                # 条件3：量比正常但MA5远离（趋势分化）
                ma5d = st.get('ma5_dist', 0)
                if ma5d and (ma5d > 8 or ma5d < -5):
                    warnings.append(f'📊MA5偏离{ma5d:+.1f}%: 价格与均线矛盾加大')
                
                if warnings:
                    transformation_warnings.append({
                        'code': st_code,
                        'name': st.get('name', '?'),
                        'warnings': warnings,
                    })
    
    # 🆕 盲区1修复：每层的矛盾方向
    layer_directions = []
    if penetration:
        layers = penetration.get('layer_scores', {})
        if layers:
            for lname, ldata in layers.items():
                score = ldata if isinstance(ldata, (int, float)) else 0
                if score >= 70: arrow = '↗↗强烈偏多'
                elif score >= 55: arrow = '↗偏多'
                elif score >= 40: arrow = '→中性'
                elif score >= 25: arrow = '↘偏空'
                else: arrow = '↘↘强烈偏空'
                layer_directions.append(f"{lname}={arrow}({score}分)")
    
    # 🆕 盲区2修复：T日竞价 vs T-1收盘的矛盾方向变化
    bid_vs_close_diff = ''
    if signals:
        top_stock = signals[0]['stocks'][0] if signals[0]['stocks'] else None
        if top_stock:
            open_chg = top_stock.get('open_chg')
            if open_chg is not None:
                if open_chg > 3:
                    bid_vs_close_diff = f'🚀竞价高开{open_chg:+.1f}% — 多方延续（T-1的矛盾方向得到强化）'
                elif open_chg > 0:
                    bid_vs_close_diff = f'📈竞价微红{open_chg:+.1f}% — 多方延续但力度减弱'
                elif open_chg > -3:
                    bid_vs_close_diff = f'📉竞价微跌{open_chg:+.1f}% — 空方有反击迹象'
                else:
                    bid_vs_close_diff = f'🔴竞价大跌{open_chg:+.1f}% — 矛盾转化！多方→空方'
            else:
                bid_vs_close_diff = '⏳竞价数据未生成（非交易时间）'
    
    # 🆕 盲区3修复：内因与外因
    # 外因 = 大盘环境/板块氛围/消息面
    # 内因 = 个股资金/量价/技术形态
    neiyin_waiyin = {}
    if signals:
        for sig in signals[:1]:
            top = sig['stocks'][0] if sig['stocks'] else None
            if not top: continue
            # 外因评分 = 环境分
            waiyin = min(100, env_score)
            # 内因评分 = 个股评分（综合资金+技术）
            neiyin = top.get('buy_score', 50)
            # 结论：内因和外因哪个是主要矛盾？
            if env_score < 35:
                conclusion = '❌ 外因（大盘风险）> 内因（个股质量）→ 空仓。环境系统性风险下，个股再强也没用'
            elif neiyin >= 75 and env_score >= 40:
                conclusion = '✅ 内因（个股质量）> 外因（大盘环境）→ 可买入。内因是矛盾的主要方面'
            elif neiyin >= 60:
                conclusion = '⚖️ 内外因平衡 → 谨慎参与。量比<0.5可考虑'
            else:
                conclusion = '⚠️ 内因（个股质量）< 外因（大盘环境）→ 观望。个股自身不强'
            
            neiyin_waiyin = {
                'stock': f'{top.get("name","?")}({top.get("code","")})',
                '外因_大盘环境': f'{env_score}分',
                '内因_个股质量': f'{neiyin}分',
                '内因细项_评分': top.get('buy_score', 0),
                '内因细项_资金': f'机构{top.get("inst_count",0)}家',
                '结论': conclusion,
            }
    
    return {
        'primary_contradiction': primary_contradiction,
        'all_contradictions': contradictions[:6],
        'contradiction_count': len(contradictions),
        'transformation_warnings': transformation_warnings[:5],
        # 🆕 三个新盲区修复
        'layer_directions': layer_directions,
        'bid_vs_close_diff': bid_vs_close_diff,
        'neiyin_waiyin': neiyin_waiyin,
    }


def run_morning_pipeline():
    """完整执行所有步骤"""
    print("=" * 70)
    print("🌅 三刀流早盘引擎 v3 — 单进程全链路")
    print(f"   启动时间: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # ===== 加载校准缓存（实践论G3闭环） =====
    cal_path = os.path.join(DATA, 'auto_calibration_cache.json')
    cal_adjustments = {}
    if os.path.exists(cal_path):
        try:
            with open(cal_path) as f:
                cal = json.load(f)
            cal_adjustments = {g: d['new'] for g, d in cal.get('threshold_adjustments', {}).items()}
            if cal_adjustments:
                msg_parts = []
                for g, d in cal.get('threshold_adjustments', {}).items():
                    if d.get('new') != d.get('old'):
                        msg_parts.append(f"{g}{d['old']}→{d['new']}")
                if msg_parts:
                    print(f"  📐 校准加载: {' | '.join(msg_parts)}")
                else:
                    print(f"  ✅ 校准加载: 无调整")
        except Exception as e:
            print(f"  ⚠️ 校准加载失败: {e}")
    
    # --- 步骤1: 加载回测数据库 ---
    print("\n[1/7] 加载24策略回测数据库...")
    bt_db = load_strategy_db()
    rankings = compute_strategy_rankings(bt_db.get('strategies', []))
    print(f"  ✅ {len(rankings)}个策略加载完成")
    print(f"  🥇 {rankings[0]['name'][:45]:45s} {rankings[0]['composite']}分")
    print(f"  🥈 {rankings[1]['name'][:45]:45s} {rankings[1]['composite']}分")
    print(f"  🥉 {rankings[2]['name'][:45]:45s} {rankings[2]['composite']}分")
    
    # --- 步骤2: 注入市场数据 ---
    print("\n[2/7] 注入市场数据（大盘层缓存）...")
    inject_market_data_to_bundle()

    # --- 步骤3: 穿透评分 ---
    print("\n[3/7] 穿透评分v3 (6层27维环境感知)...")
    penetration = run_penetration_scoring()
    env_score = penetration.get('total_score', 0)
    print(f"  ✅ 环境分: {env_score}/100 → {penetration.get('decision', '未知')}")
    
    # --- 步骤4: 龙虎榜特征 ---
    print("\n[4/7] 龙虎榜资金特征...")
    lhb_tags = extract_lhb_tags()
    print(f"  🏷️ 资金标签: {lhb_tags if lhb_tags else '(无标签 - 周未/休市)'}")
    
    # 矛盾类型推测 ↔ 内部/外部驱动力判断（基于I2回测：内部驱动收益+4.71%远优于外部+1.59%）
    if env_score >= 55:
        contradiction_type = '主线确认'
    elif env_score >= 40:
        contradiction_type = '资金博弈'
    else:
        contradiction_type = '题材轮动'
    
    # 内外部驱动力标记
    drive_type = '内部(技术面)' if lhb_tags and '缩量' in str(lhb_tags) else '外部(消息面)'
    drive_bonus = 1.1 if '内部' in drive_type else 1.0  # 内部驱动策略+10%匹配分
    
    # --- 步骤4: 策略匹配 ---
    print("\n[5/7] 24策略精确匹配...")
    matched = match_strategies(rankings, lhb_tags, env_score, contradiction_type, drive_bonus)
    
    if matched:
        print(f"  🏅 TOP5匹配 (矛盾类型={contradiction_type}):")
        for i, m in enumerate(matched[:5]):
            warn = ' ' + m.get('overheat_warning', '') if m.get('overheat_warning') else ''
            print(f"    {i+1}. {m['name'][:45]:45s} 匹配{m['match_score']:5.1f} | {m['ret']:+.2f}%/{m['win_rate']:.1f}% n={m['n']}{warn}")
    else:
        print(f"  ⚠️ 无匹配策略")
    
    # --- 步骤5: 矛盾引擎评级（取代旧S级规则）---
    print("\n[6/7] 矛盾引擎评级 · 甲乙丙丁戊...")
    
    s_level = []
    s_msg = ''
    cap_base = 0
    contradiction = {}
    
    if env_score < 25:
        cap_base = 8
        final_pos = 8
        decision = '⚠️轻仓摸底 — 冰点期，回测冰点涨停T+1仍有+0.66%正收益'
        pos_str = '0~10%'
        s_msg = f"⚠️ 环境分<25（冰点），保留8%仓位做最强涨停板"
    elif env_score < 40:
        cap_base = 10
        final_pos = 10
        decision = '👀观望为主 — 环境偏弱'
        pos_str = '0~10%'
        s_msg = f"⚠️ 环境<40，策略最高丁级，轻仓观望"
    else:
        # 环境≥40，可以进行矛盾评级
        # 矛盾引擎自己的评级体系（基于15988个涨停样本×570天历史回测v2.0）：
        # 甲等(🔴≥55分+机构): 回测T+1 +1.30%/56.3% → 重仓
        # 乙等(🟠≥50分): 回测T+1 +0.51%/52.1% → 正常买入
        # 丙等(🟢≥30分): 回测T+1 -0.70%/43.3% → 谨慎/观望
        # 丁等(⚪<30分): 盈利能力不足 → 观望
        
        jia_level = []   # 甲等
        yi_level = []    # 乙等
        bing_level = []  # 丙等（含丁等戊等）
        
        # 🆕 修复1：对所有rankings做评级，不只对matched
        all_rated = []  # 存储所有策略的评级信息，用于输出
        matched_names = set(m2['name'] for m2 in matched)  # 获取被匹配的策略名
        for m in rankings:
            stags = m['tags']
            has_inst = '机构' in stags or '量化' in stags
            is_pure_suoliang = bool(stags) and not (stags - {'缩量', '控盘', '换手', '连板'})
            
            # 矛盾引擎评级五维打分（基于8维回测结论优化v2.0）
            # 1. 收益力：回测收益得分
            ret_score = min(20, m['ret'] * 3)  # +4.89%→14.7分
            
            # 2. 胜率力：回测胜率得分
            win_score = min(20, m['win_rate'] * 0.3)  # 68.1%→20.4分
            
            # 3. 矛盾力：资金合力得分（基于J3回测——机构+量化胜率72.9%最优）
            fund_tags = stags & {'机构', '量化', '游资', '主力'}
            # 资金合力因子（从回测数据优化v2.0）：
            # - 机构+量化组合胜率72.9%/收益+1.62% → 最高14分
            # - 仅机构+3.14% → 12分
            # - 仅游资+2.67% → 10分
            # - 机构+游资+量化三力+1.82% → 8分
            # - 仅量化+0.36%/48.4% → 2分（量化一日游）
            has_inst = '机构' in fund_tags
            has_youzi = '游资' in fund_tags
            has_lianghua = '量化' in fund_tags
            if has_inst and has_lianghua and not has_youzi:
                fund_score = 14  # 机构+量化=最强组合
            elif has_inst and not has_youzi and not has_lianghua:
                fund_score = 12  # 仅机构
            elif has_youzi and not has_inst and not has_lianghua:
                fund_score = 10  # 仅游资
            elif has_inst and has_youzi and has_lianghua:
                fund_score = 8   # 三家合力
            elif has_inst and has_youzi:
                fund_score = 7   # 机构+游资
            elif has_lianghua and not has_inst and not has_youzi:
                fund_score = 2   # 仅量化（一日游风险最大）
            elif '主力' in fund_tags:
                fund_score = 5
            else:
                fund_score = 3
            
            # 4. 样本力：样本可信度得分
            n_score = min(15, m['n'] * 0.1)  # 69→6.9分

            # 5. 🆕 环境力：基于维度3回测发现——外因对T+1收益影响远超内因
            # 高潮(60+)→15分, 发酵(50-59)→12分, 震荡(35-49)→8分, 弱势(25-34)→4分, 冰点(<25)→1分
            if env_score >= 70:
                env_score_add = 15
            elif env_score >= 55:
                env_score_add = 12
            elif env_score >= 45:
                env_score_add = 8
            elif env_score >= 35:
                env_score_add = 4
            else:
                env_score_add = 1
            
            # 综合矛盾评分（5维）
            maodun_total = ret_score + win_score + fund_score + n_score + env_score_add
            maodun_total = min(100, maodun_total)
            
            big_loss = m.get('big_loss_pct', 0)
            risk_penalty = 0
            if big_loss >= 30: risk_penalty = 0.5
            elif big_loss >= 25: risk_penalty = 0.25
            elif big_loss >= 20: risk_penalty = 0.1
            
            cap_weight = min(1.0, m.get('match_score', 0) / 55.0) if m.get('match_score', 0) > 0 else 0
            
            # 矛盾等级判定 — 基于15988个涨停样本×570天历史回测的最优阈值v2.0 + 自动校准
            # 回测结论：≥55分+机构=甲等(+1.30%/77笔)，≥50分=乙等(+0.51%/2399笔)，<50分=丙等(-0.70%/97笔)
            jia_threshold = cal_adjustments.get('甲等', 55)
            yi_threshold = cal_adjustments.get('乙等', 50)
            bing_threshold = cal_adjustments.get('丙等', 30)
            
            if maodun_total >= jia_threshold and '机构' in stags:
                grade = '甲等'
                color = '🔴'
            elif maodun_total >= yi_threshold:
                grade = '乙等'
                color = '🟠'
            elif maodun_total >= bing_threshold:
                grade = '丙等'
                color = '🟢'
            else:
                grade = '丁等'
                color = '⚪'
            
            # 过热标记（不降级，仅标注）
            if m.get('overheat_warning'):
                grade = grade  # 维持原级
                color_overheat = f'{color}🔥'  # 加🔥标记
            else:
                color_overheat = color
            
            entry = {
                'strategy': m['name'], 'composite': m['composite'],
                'match_score': m.get('match_score', 0), 'ret': m['ret'],
                'win_rate': m['win_rate'], 'n': m['n'],
                'tags': list(m['tags']), 'cap_weight': round(cap_weight, 2),
                'big_win_pct': m.get('big_win_pct', 0),
                'big_loss_pct': big_loss,
                'risk_penalty': risk_penalty,
                'is_pure_suoliang': is_pure_suoliang,
                'sell_advice': '💹 持股到收盘' if m.get('close_ret', 0) > m.get('open_ret', 0) else '💸 开盘立即卖',
                'sell_diff_pct': round(m.get('close_ret', 0) - m.get('open_ret', 0), 2),
                # 矛盾引擎新增
                'maodun_grade': grade,
                'maodun_color': color,
                'maodun_color_overheat': color_overheat,
                'maodun_score': round(maodun_total, 1),
                'ret_score': round(ret_score, 1),
                'win_score': round(win_score, 1),
                'fund_score': fund_score,
                'n_score': round(n_score, 1),
                # 🆕 标记是否被资金环境匹配
                'is_matched': m['name'] in matched_names,
            }
            
            all_rated.append(entry)
            
            # 只对有match_score>0的策略做等级分类（进入候选）
            if m['name'] in matched_names:
                if grade == '甲等':
                    jia_level.append(entry)
                elif grade == '乙等':
                    yi_level.append(entry)
                elif grade in ('丙等', '丁等', '戊等'):
                    bing_level.append(entry)
        
        # 评级排序：甲→乙→丙各取top，仓位上甲等优先
        jia_level.sort(key=lambda x: -x['maodun_score'])
        yi_level.sort(key=lambda x: -x['maodun_score'])
        bing_level.sort(key=lambda x: -x['maodun_score'])
        
        if jia_level:
            s_level = jia_level[:2] + yi_level[:1]
        elif yi_level:
            s_level = yi_level[:2] + bing_level[:1]
        elif bing_level:
            s_level = bing_level[:3]
        else:
            s_level = []
        s_level = s_level[:3]
        
        if s_level:
            env_factor = env_score / 50.0
            strategy_boost = round(min(25, s_level[0]['match_score'] / 55.0 * 20) * min(env_factor, 1.5))
            
            max_risk_penalty = max(s.get('risk_penalty', 0) for s in s_level)
            
            cap_base = 22 + strategy_boost
            cap_base = int(cap_base * (1.0 - max_risk_penalty))
            cap_base = min(cap_base, 60) if env_score < 55 else min(cap_base, 85)
            cap_base = max(cap_base, 10)
            
            if cap_base <= 10:
                decision, pos_str = '👀观望为主', '0~10%'
            elif cap_base <= 25:
                decision, pos_str = '⚠️轻仓参与', '10~25%'
            elif cap_base <= 50:
                decision, pos_str = '🟢正常操作', '25~50%'
            elif cap_base <= 70:
                decision, pos_str = '✅积极操作', '50~70%'
            else:
                decision, pos_str = '🔥重仓出击', '70~85%'
            
            # 显示评级分布（含全部24策略）
            grades_count = {}
            for e in all_rated:
                g = e.get('maodun_grade', '?')
                grades_count[g] = grades_count.get(g, 0) + 1
            grade_summary = ' | '.join([f'{g}{n}个' for g, n in sorted(grades_count.items(), reverse=True)])
            
            s_msg = f"✅ {grade_summary}, 优选{s_level[0]['maodun_color']}{s_level[0]['maodun_grade']}领衔, 仓位{cap_base}%"
        else:
            cap_base = 15
            decision = '⚪观望 — 无甲等/乙等策略'
            pos_str = '0~15%'
            s_msg = f"⚠️ 环境{env_score}分，无甲等乙等策略，轻仓观望"
    
    print(f"  🌍 环境分: {env_score}/100")
    print(f"  {s_msg}")
    # 打印全策略评级分布（修复1输出）
    if 'all_rated' in dir() or 'all_rated' in locals():
        try:
            gd = {}
            for e in all_rated:
                g = e.get('maodun_grade', '?')
                gd[g] = gd.get(g, 0) + 1
            matched_count = sum(1 for e in all_rated if e.get('is_matched'))
            print(f"  📊 24策略评级: {' | '.join([f'{g}{n}' for g,n in sorted(gd.items(),reverse=True)])} (匹配{matched_count})")
        except:
            pass
    
    if s_level:
        for s in s_level:
            g = s.get('maodun_color_overheat', s.get('maodun_color', '')) + s.get('maodun_grade', '')
            print(f"  {g} {s['strategy'][:48]:48s} 矛盾{s['maodun_score']}分 | {s['ret']:+.2f}%/{s['win_rate']:.1f}% | {'💹持股到收盘' if s.get('sell_advice','').startswith('💹') else '💸开盘卖'}")
            print(f"     收益{s['ret_score']}分+胜率{s['win_score']}分+资金{s['fund_score']}分+样本{s['n_score']}分")
    
    # --- 步骤6: 匹配个股（三刀流·量化排序版）---
    print(f"\n[7/7] 矛盾个股匹配（量化排序）...")
    
    signals = []
    
    if s_level and env_score >= 40:
        # 来源1: 龙虎榜缓存
        lhb_data = {}
        for p in [os.path.join(DATA, 'lhb_scoring_cache.json')]:
            if os.path.exists(p):
                with open(p) as f:
                    lhb_data = json.load(f)
        
        actionable = lhb_data.get('actionable', [])
        
        # 来源2: 涨停板明细（从market_daily.db）
        limit_stocks = load_limit_detail_from_market_db()
        
        # 来源3: K线库近N日涨停股（大幅扩大候选池）
        kline_limit_stocks = load_recent_limit_stocks_from_kline(days=5)
        
        # 建立已存在code集合
        all_candidates = list(actionable)
        existing_codes = set()
        for item in actionable:
            c = item.get('code', '')
            if c: existing_codes.add(c)
        
        # 合并涨停明细
        limit_only_count = 0
        for ls in limit_stocks:
            if ls['code'] not in existing_codes:
                limit_only_count += 1
                existing_codes.add(ls['code'])
                all_candidates.append({
                    'code': ls['code'], 'name': ls['name'],
                    'board': ls['board'], 'board_str': ls['board_str'],
                    'score': 50, 'jg': 0, 'ql': 0, 'yz': 0, 'sh': 0,
                    'tier': 'C', 'from_limit_detail': True,
                })
        
        # 合并K线涨停池
        kline_only_count = 0
        for ks in kline_limit_stocks:
            if ks['code'] not in existing_codes:
                kline_only_count += 1
                existing_codes.add(ks['code'])
                all_candidates.append({
                    'code': ks['code'], 'name': '',
                    'board': 0, 'board_str': '近N日涨停',
                    'score': 45, 'jg': 0, 'ql': 0, 'yz': 0, 'sh': 0,
                    'tier': 'C', 'from_kline_pool': True, 'limit_date': ks.get('date',''),
                })
        
        print(f"  📊 候选池: {len(actionable)}龙虎榜 + {limit_only_count}涨停明细 + {kline_only_count}K线涨停 = {len(all_candidates)}只")
        
        # 连接K线数据库
        conn_k = db_connect(KLINE_DB)
        today = _dt.now().strftime('%Y-%m-%d')
        
        for strategy in s_level:
            needed = set(strategy.get('tags', []))
            raw_matches = []
            is_pure_suoliang = bool(needed) and not (needed - {'缩量', '控盘', '换手', '连板'})
            
            # 策略类型标注（游资/机构视角分类）
            if is_pure_suoliang:
                strategy_type = '📊纯缩量(匹配K线池)'
            elif '机构' in needed:
                strategy_type = '🏛️机构(匹配龙虎榜)'
            else:
                strategy_type = '🤖混合(匹配龙虎榜)'
            
            for item in all_candidates:
                code = item.get('code', '')
                name = item.get('name', '')
                is_from_lhb = not item.get('from_limit_detail', False) and not item.get('from_kline_pool', False)
                is_from_kline = item.get('from_kline_pool', False)
                
                # 资金特征
                inst_n = si(item.get('jg', 0))
                quant_n = si(item.get('ql', 0))
                score = si(item.get('score', 50))
                tier = item.get('tier', 'C')
                board = si(item.get('board', 0))
                
                # 条件检查
                ok = True
                
                if is_pure_suoliang:
                    # 🆕 纯缩量策略：优先选K线池的（137只），龙虎榜股需更严条件
                    if is_from_kline:
                        ok = True  # K线池通过
                    elif is_from_lhb:
                        # 龙虎榜股也允许，但要求量比<0.5（更严）— 开盘后验证
                        ok = True  # 暂放行，量比过滤在后面
                    else:
                        ok = False
                elif '机构' in needed:
                    if is_from_lhb: ok = inst_n > 0
                    elif is_from_kline: ok = False
                    else: ok = False
                elif '量化' in needed:
                    if is_from_lhb: ok = quant_n > 0
                    elif is_from_kline: ok = False
                    else: ok = False
                elif not needed - {'缩量', '连板'}:
                    ok = True
                if not ok: continue
                
                # 读取实时行情
                tcodes = []
                if code.startswith('6'): tcodes.append(f'sh{code}')
                elif code.startswith('0') or code.startswith('3'): tcodes.append(f'sz{code}')
                else: tcodes.append(f'sz{code}')
                quotes = fetch_realtime_quotes(tcodes)
                q = quotes.get(tcodes[0], None) if tcodes else None
                
                vol_ratio = q['vol_ratio'] if q and q.get('vol_ratio') else None
                price = q['price'] if q else None
                chg = q['chg'] if q else None
                
                # 缩量检查 — 纯缩量策略必须量比<0.7（开盘后有实时数据才生效）
                if ok and '缩量' in needed and vol_ratio is not None:
                    if vol_ratio >= 0.7:
                        ok = False  # 实际量比>0.7说明未缩量，排除
                # 开盘后量比缺失时，纯缩量策略按K线量比≈历史均值处理
                # 此时无法确认缩量，但保留候选让用户判断
                
                # K线维度
                k_info = calc_ma5_signal(conn_k, code, today, price)
                ma5_dist = k_info.get('ma5_dist', 0)
                trend = k_info.get('trend', '未知')
                
                if ok:
                    # ===== 量化排序分 v3.2（三刀流精细化） =====
                    # 1. 策略匹配度 (0~40分) — 来自策略回测的胜率×收益（降低权重让个股特征多区分）
                    strat_ret = strategy['ret']
                    strat_wr = strategy['win_rate']
                    match_quality = min(40, strat_ret * 4 + strat_wr * 0.25)
                    
                    # 2. 资金强度 (0~30分) — 多维度评分
                    # 龙虎榜综合评分(权重最大)+机构数+游资数+量化数
                    if is_from_lhb:
                        lhb_score = si(item.get('score', 45))
                        tier_score = {'A': 10, 'B': 5, 'C': 0}.get(item.get('tier', 'C'), 0)
                    else:
                        # K线池/涨停明细无龙虎榜评分，给基础分+涨停天折算
                        lhb_score = 30 + board * 8  # 1板=38, 2板=46
                        tier_score = 5  # 默认B级基础分
                    inst_score = min(8, inst_n * 4)
                    quant_score = min(6, quant_n * 2)
                    youzi_bonus = min(4, si(item.get('yz', 0)) * 1.5)
                    fund_score = min(30, lhb_score * 0.15 + tier_score + inst_score + quant_score + youzi_bonus)
                    
                    # 3. 连板/涨停梯度 (0~15分) — 🔄 重大修正（基于回测：首板+0.84%/50.7% vs 二板+0.10%/49.6%）
                    board_bonus = 0
                    if board >= 1 and board <= 2: board_bonus = 8    # 首板/二板优先（首板T+1最优）
                    elif board >= 3: board_bonus = 6                 # 三板以上风险大
                    # 从K线池来的，用limit_date判断
                    if is_from_kline and item.get('limit_date'):
                        # 近N日涨停过的+5分
                        board_bonus = max(board_bonus, 5)
                    
                    # 4. 技术面 (0~15分) — MA5偏离度+趋势方向+日内强度（降低权重）
                    tech_score = 8  # 基准
                    if trend == '上升': tech_score += 4
                    elif trend == '震荡': tech_score += 2
                    if -3 < ma5_dist < 5: tech_score += 3  # 回踩MA5附近最好
                    elif ma5_dist > 10: tech_score -= 3   # 偏离太多=风险
                    elif ma5_dist < -5: tech_score -= 2   # 破位=风险
                    
                    # 🆕 涨停热度bonus（基于回测：15~29只涨停时T+1 +0.89%/50.9%最优）
                    # 需要从环境数据获取当日涨停数
                    limit_heat_bonus = 0
                    try:
                        md_row = conn.execute("SELECT up_count, limit_up FROM day_full ORDER BY date DESC LIMIT 1").fetchone()
                        if md_row:
                            limit_cnt = md_row[0] or md_row[1] or 0
                            if 15 <= limit_cnt <= 29:
                                limit_heat_bonus = 4   # 最优区间
                            elif 30 <= limit_cnt <= 49:
                                limit_heat_bonus = 2   # 较热
                            elif limit_cnt >= 50:
                                limit_heat_bonus = 1   # 过热但仍有溢价
                    except:
                        pass
                    
                    # 🆕 日内强度bonus（基于回测：高位收盘>80%→T+1 +0.87%/52.4%）
                    if q and q.get('price') and q.get('yclose') and q.get('amplitude'):
                        amp = abs(q['amplitude'])
                        chg_val = q.get('chg', 0) or 0
                        if amp > 5 and chg_val > 5 and chg_val < 9.5:
                            # 高振幅+中高涨幅=高位换手未封死=最佳形态
                            tech_score += 5
                            intraday_strong = True
                    
                    tech_score = clamp(tech_score + limit_heat_bonus, 0, 15)
                    
                    buy_score = round(match_quality + fund_score + board_bonus + tech_score)
                    
                    # ===== 🆕 一字板降级（基于H1回测：一字板T+1+1.53% vs自然涨停+2.23%） =====
                    is_yizi = False
                    if q and q.get('open') and q.get('yclose') and \
                       abs(q['open'] - q['yclose']) < q['yclose'] * 0.005 and \
                       q.get('chg') and q['chg'] > 9.0:
                        is_yizi = True
                    # 也通过K线判断（开盘前）
                    if not is_yizi and k_info:
                        prev_close_row = conn_k.execute(
                            "SELECT close FROM kline WHERE code=? AND date<? ORDER BY date DESC LIMIT 1",
                            (code, today)
                        ).fetchone()
                        if prev_close_row and q and q.get('price'):
                            pass  # 仅做预判，真正判断在开盘后
                    
                    if is_yizi:
                        buy_score = round(buy_score * 0.85)  # 一字板降级15%
                    
                    # ===== 🆕 炸板/回封标记（基于G3回测：炸板T+1 -1.27%/37.7%） =====
                    zhaban_signal = False
                    huifeng_signal = False
                    if q and q.get('amplitude') and q.get('chg') and q.get('price') and q.get('yclose'):
                        # 盘中振幅大但涨幅不高 = 可能有炸板风险
                        amp = abs(q['amplitude'])
                        chg_val = q['chg']
                        if amp > 8 and chg_val < 3:
                            zhaban_signal = True  # 高振幅+低涨幅=炸板
                        elif amp > 8 and chg_val > 8:
                            huifeng_signal = True  # 高振幅+高涨幅=回封板
                    
                    # ===== 竞价淘汰标记（游资视角） =====
                    # 只有开盘后有真实竞价数据才能判断
                    # 标记：低开>3%的要警告
                    open_chg = q['open_chg'] if q and q.get('open_chg') is not None else None
                    bidding_weak = False
                    if open_chg is not None and open_chg < -3:
                        bidding_weak = True  # 竞价弱势，标记但不剔除（留给人判断）
                    
                    raw_matches.append({
                        'code': code, 'name': name,
                        'price': price if price else 0,
                        'chg': chg if chg else 0,
                        'vol_ratio': vol_ratio,
                        'inst_count': inst_n, 'quant_count': quant_n,
                        'board_count': board,
                        'ma5_dist': ma5_dist,
                        'trend': trend,
                        'score': score,
                        'tier': tier,
                        'from_limit_detail': item.get('from_limit_detail', False),
                        'from_kline_pool': item.get('from_kline_pool', False),
                        'buy_score': buy_score,
                        'bidding_weak': bidding_weak,
                        'is_yizi': is_yizi,
                        'zhaban_signal': zhaban_signal,
                        'huifeng_signal': huifeng_signal,
                    })
            
            # 按买入优先级排序
            raw_matches.sort(key=lambda x: -x['buy_score'])
            
            if raw_matches:
                signals.append({
                    'strategy': strategy['strategy'],
                    'strategy_type': strategy_type,
                    'composite': strategy['composite'],
                    'match_score': strategy['match_score'],
                    'expected_ret': strategy['ret'],
                    'expected_win': strategy['win_rate'],
                    'sample_n': strategy['n'],
                    'big_loss_pct': strategy.get('big_loss_pct', 0),
                    'risk_penalty': strategy.get('risk_penalty', 0),
                    'sell_advice': strategy.get('sell_advice', '💹 持股到收盘'),
                    'sell_diff_pct': strategy.get('sell_diff_pct', 0),
                    'stocks': raw_matches[:5],
                    'all_ranked': raw_matches,
                })
        
        if conn_k: conn_k.close()
    
    if signals:
        total_st = sum(len(sig['stocks']) for sig in signals)
        print(f"  ✅ {len(signals)}个策略匹配, {total_st}只优先候选")
        
        # 🆕 机构视角：开盘后二次确认标记
        now_hour = _dt.now().hour
        now_minute = _dt.now().minute
        is_market_open = (now_hour == 9 and now_minute >= 30) or (10 <= now_hour <= 14) or (now_hour == 15 and now_minute == 0)
        
        for sig in signals:
            confirmed = []
            for st in sig['stocks']:
                # 二次确认：真实量比+涨幅验证
                vr = st.get('vol_ratio')
                chg_val = st.get('chg', 0)
                
                confirm_tags = []
                
                # 量比确认
                if is_market_open and vr is not None:
                    if vr < 0.5:
                        confirm_tags.append('🔥极端缩量')
                    elif vr < 0.7:
                        confirm_tags.append('✅缩量确认')
                    elif vr < 1.0:
                        confirm_tags.append('⚠️量比偏高')
                    else:
                        confirm_tags.append('❌放量')
                elif not is_market_open:
                    confirm_tags.append('⏳待开盘确认')
                else:
                    confirm_tags.append('📡无实时量比')
                
                # 涨跌确认
                if is_market_open and chg_val is not None:
                    if chg_val > 3:
                        confirm_tags.append('🔥追高谨慎')
                    elif chg_val > 0:
                        confirm_tags.append('😊红盘')
                    elif chg_val > -2:
                        confirm_tags.append('😐微绿')
                    else:
                        confirm_tags.append('🔴大绿')
                
                # MA5确认
                ma5d = st.get('ma5_dist')
                if ma5d is not None:
                    if -2 < ma5d < 5:
                        confirm_tags.append('✅MA5合理')
                    elif ma5d > 10:
                        confirm_tags.append('⚠️远离MA5')
                    elif ma5d < -5:
                        confirm_tags.append('🔴破MA5')
                
                st['secondary_confirm'] = ' | '.join(confirm_tags) if confirm_tags else '⏳待数据'
                if is_market_open and vr is not None and vr < 0.7 and chg_val is not None and chg_val > -2:
                    confirmed.append(st)
            
            sig['secondary_confirmed'] = confirmed[:3]
        
        # 打印信号（含二次确认标签）
        for sig in signals:
            top = sig['stocks'][0] if sig['stocks'] else {}
            if top.get('from_kline_pool'): src_mark = '📊'
            elif top.get('from_limit_detail'): src_mark = '📋'
            else: src_mark = '🐉'
            weak_mark = '⚠️竞价弱' if top.get('bidding_weak') else ''
            yizi_mark = '📄一字板' if top.get('is_yizi') else ''
            zb_mark = '💥炸板' if top.get('zhaban_signal') else ''
            hf_mark = '🔁回封板' if top.get('huifeng_signal') else ''
            extra = weak_mark + yizi_mark + zb_mark + hf_mark
            stype = sig.get('strategy_type', '')
            print(f"  📌 {sig['strategy'][:30]:30s} {stype} → {top.get('name','?')}({top.get('code','?')}) {src_mark} 评分{top.get('buy_score',0)} {extra}")
            if len(sig['stocks']) > 1:
                others = ', '.join([f"{s['name']}({s['buy_score']})" for s in sig['stocks'][1:3]])
                print(f"     备选: {others}")
            
            # 🆕 二次确认结果（仅开盘后显示）
            if sig.get('secondary_confirmed'):
                print(f"     ✅ 二次确认通过: {', '.join([s['name']+s.get('secondary_confirm','') for s in sig['secondary_confirmed']])}")
            elif sig['stocks'] and sig['stocks'][0].get('vol_ratio') is None:
                print(f"     ⏳ 等待开盘后二次确认（量比+涨幅验证）")
    else:
        print(f"  ⚪ 无匹配个股")
    
    # ===== 分仓策略（机构视角） — 按评级差异化 =====
    # 🆕 修复：甲等拿60%，乙等30%，丙等10%，不按排名按评级
    cap_total = cap_base  # 从矛盾评级来的总仓位%
    
    # ===== 🆕 方向一致性仓位修正（基于维度2回测：一致偏多→T+1 +1.16%/56.1%） =====
    # 当所有层方向一致偏多时，加仓10%提升收益
    consistency_directions = contradiction.get('layer_directions', [])
    if consistency_directions:
        bullish_count = sum(1 for d in consistency_directions if '↗' in d)
        bearish_count = sum(1 for d in consistency_directions if '↘' in d)
        total_layers = len(consistency_directions)
        if total_layers >= 3 and bullish_count >= total_layers * 0.7:
            cap_total = round(cap_total * 1.1)
            cap_total = min(cap_total, 85)
            print(f"  📐 方向一致偏多({bullish_count}/{total_layers})！仓位+10% → {cap_total}%")
    
    # ===== 竞价低开>3%减半（基于维度4回测：低开>3%→T+1 -2.80%/33.6%） =====
    now_hour = _dt.now().hour
    tight_hours = (9 <= now_hour <= 15)
    has_bid_signal = False
    if signals and tight_hours:
        for sig in signals:
            top = sig['stocks'][0] if sig['stocks'] else {}
            if top.get('bidding_weak'):
                has_bid_signal = True
                break
        if has_bid_signal:
            print(f"  🚨 竞价低开>3%信号！仓位减半（回测-2.80%/33.6%胜率）")
            cap_total = round(cap_total * 0.5)
    
    # ===== 🆕 穿透式仓位修正 v1.0（基于1,541笔严格时间线回测） =====
    try:
        _pwd = _dt.now().weekday()
        _pe = env_score
        # 从bundle读涨停数
        _plu = 0
        try:
            _pmd = bundle.get('market_daily', {})
            _plu = int(_pmd.get('limit_up', _pmd.get('up_count', 0)) or 0)
        except: pass
        
        # 环境分层
        if _pe < 25: _pt = '冰点'
        elif _pe < 40: _pt = '震荡'
        elif _pe < 55: _pt = '活跃'
        else: _pt = '高潮'
        
        # 回避区域检测
        is_avoid = False
        avoid_reason = ''
        if _pwd == 2 and _pt == '震荡' and _plu < 25:
            is_avoid = True
            avoid_reason = '周三震荡低涨停(-1.00%/37.5%)'
        elif _pwd == 2 and _pt == '冰点':
            is_avoid = True
            avoid_reason = '周三冰点(-0.38%/47.8%)'
        elif _pwd == 0 and _pt == '活跃' and _plu >= 50:
            is_avoid = True
            avoid_reason = '周一活跃涨停≥50(-0.23%/20.0%)'
        
        if is_avoid:
            old_cap = cap_total
            cap_total = round(cap_total * 0.3)  # 仓位砍到30%
            cap_total = max(cap_total, 10)  # 不低于10%
            print(f"  🚫 穿透回避: {avoid_reason}")
            print(f"     仓位 {old_cap}% → {cap_total}%")
        
        # 黄金区域检测
        is_gold = False
        gold_reason = ''
        if _pwd == 1 and _pt == '活跃' and _plu >= 50:
            is_gold = True
            gold_reason = '周二+活跃+涨停≥50(+5.26%/71.4%)'
        elif _pwd == 1 and _pt == '活跃':
            is_gold = True
            gold_reason = '周二+活跃(+2.59%/66.7%)'
        elif _pwd == 2 and _pt == '高潮':
            is_gold = True
            gold_reason = '周三+高潮(+2.41%/64.3%)'
        elif _pt == '活跃':
            is_gold = True
            gold_reason = '活跃环境(+1.42%/53.9%)'
        
        if is_gold and not is_avoid:
            old_cap = cap_total
            cap_total = round(cap_total * 1.2)  # 仓位加20%
            cap_total = min(cap_total, 85)
            print(f"  🏆 穿透黄金信号: {gold_reason}")
            print(f"     仓位 {old_cap}% → {cap_total}%")
    except Exception as e:
        pass  # 穿透修正失败不影响主流程
    
    cap_split = {}
    if signals and s_level:
        grade_order = {'甲等': 0, '乙等': 1, '丙等': 2, '丁等': 3, '戊等': 4}
        # 按评级排序
        s_sorted = sorted(s_level, key=lambda x: grade_order.get(x.get('maodun_grade', '戊等'), 5))
        n_s = len(s_sorted)
        
        w1, w2, w3 = 0.60, 0.30, 0.10  # 默认
        
        if n_s >= 1 and s_sorted[0].get('maodun_grade') == '甲等':
            w1, w2, w3 = 0.60, 0.30, 0.10  # 甲等60%
        elif n_s >= 1 and s_sorted[0].get('maodun_grade') == '乙等':
            w1, w2, w3 = 0.50, 0.30, 0.20  # 乙等50%，给丙等多一些
        else:
            w1, w2, w3 = 0.40, 0.35, 0.25  # 丙等主导，分散
        
        # 纯缩量策略提权
        if n_s >= 3 and s_sorted[2].get('is_pure_suoliang'):
            w3 = min(w3 + 0.05, 0.30)
            w1 = max(w1 - 0.05, 0.40)
        
        cap_split = {}
        if n_s >= 1:
            cap_split[s_sorted[0]['strategy'][:30]] = round(cap_total * w1, 0)
        if n_s >= 2:
            cap_split[s_sorted[1]['strategy'][:30]] = round(cap_total * w2, 0)
        if n_s >= 3:
            cap_split[s_sorted[2]['strategy'][:30]] = round(cap_total * w3, 0)
    elif len(signals) == 2:
        cap_split = {
            signals[0]['strategy'][:30]: round(cap_total * 0.65, 0),
            signals[1]['strategy'][:30]: round(cap_total * 0.35, 0),
        }
    elif len(signals) == 1:
        cap_split = {signals[0]['strategy'][:30]: cap_total}
    
    if cap_split:
        print(f"  💰 分仓: {', '.join([f'{k}={v}%' for k,v in cap_split.items()])}")
    
    # ===== 矛盾论分析 =====
    print(f"\n{'=' * 70}")
    print(f"📖 矛盾论 — 主要矛盾分析")
    print(f"{'=' * 70}")
    contradiction = identify_main_contradiction(env_score, lhb_tags, s_level, signals, penetration)
    primary = contradiction.get('primary_contradiction', {})
    if primary:
        print(f"\n⚡ 主要矛盾: {primary.get('contradiction', '未知')}")
        print(f"📊 主要方面: {primary.get('main_aspect', '未知')}")
        print(f"🎯 行动: {primary.get('action', '未知')}")
    
    print(f"\n🔍 全部矛盾层级:")
    for c in contradiction.get('all_contradictions', []):
        lvl = c.get('level', '')
        if c.get('layer') == '个股':
            sc = c.get('score', 0)
            lb = c.get('liangbi', '')
            bm = c.get('board_mood', '')
            tm = c.get('trend_mood', '')
            print(f"  {lvl} {c.get('name','?')}({sc}分)")
            print(f"      量比={lb:8s} | {bm:25s} | {tm}")
            print(f"      行动: {c.get('action','')}")
        elif c.get('layer') == '大盘':
            print(f"  {lvl} {c.get('contradiction',''):40s} → {c.get('main_aspect','')[:35]:35s} | {c.get('action','')}")
        else:
            print(f"  {lvl} {c.get('contradiction',''):44s} → {c.get('main_aspect','')}")
    
    # 🆕 矛盾转化预警
    tw = contradiction.get('transformation_warnings', [])
    if tw:
        print(f"\n🚨 矛盾转化预警:")
        for w_item in tw:
            for detail in w_item.get('warnings', []):
                print(f"  {w_item.get('name','?')}({w_item.get('code','')}): {detail}")
    
    # 🆕 每层矛盾方向
    ld = contradiction.get('layer_directions', [])
    if ld:
        print(f"\n📐 各层矛盾方向:")
        for d in ld:
            print(f"  {d}")
    
    # 🆕 内因外因
    nw = contradiction.get('neiyin_waiyin', {})
    if nw:
        print(f"\n🥚 内因vs外因（毛选：外因通过内因而起作用）:")
        print(f"  外因(大盘): {nw.get('外因_大盘环境','')} | 内因(个股): {nw.get('内因_个股质量','')}")
        print(f"  结论: {nw.get('结论','')}")
    
    # 🆕 竞价vs昨收矛盾变化
    bvd = contradiction.get('bid_vs_close_diff', '')
    if bvd:
        print(f"\n⏱️ 开盘矛盾对比昨收: {bvd}")
    
    # ===== 🆕 穿透式策略推荐 v1.0 =====
    _loaded_signal_db = load_signals_database()
    
    if env_score is not None and _loaded_signal_db and _loaded_signal_db.get('gold_signals'):
        # 根据当前环境匹配最佳信号
        from datetime import datetime as _dt_inner
        now_wd = _dt_inner.now().weekday()
        wd_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        env_tier_raw = '🔥高潮' if env_score>=70 else '🟢活跃' if env_score>=55 else '⚪发酵' if env_score>=40 else '🔵震荡' if env_score>=25 else '🔴冰点'
        env_tier = env_tier_raw.replace('🔥','').replace('🟢','').replace('⚪','').replace('🔵','').replace('🔴','').strip()
        
        # 获取当前涨停数(从market_daily)
        cur_limit_up = 0
        try:
            md = bundle.get('market_daily', {})
            cur_limit_up = md.get('limit_up', md.get('up_count', 0)) or 0
        except:
            pass
        
        cur_limit_up = int(cur_limit_up) if cur_limit_up else 0
        
        # 找匹配的黄金信号
        best_signal = None
        for g in _loaded_signal_db.get('gold_signals', []):
            label = g.get('label', '')
            n = g.get('n', 0)
            ret = g.get('ret', 0)
            # 简单匹配：环境关键词
            if env_tier in label:
                if best_signal is None or ret > best_signal.get('ret', -999):
                    best_signal = g
        
        avoid_signal = None
        for a in _loaded_signal_db.get('avoid_signals', []):
            label = a.get('label', '')
            if env_tier in label and now_wd == 2 and cur_limit_up < 25:
                avoid_signal = a
        
        # 打印推荐
        print(f"\n🎯 穿透式策略推荐:")
        if best_signal and best_signal.get('ret', 0) > 1.0:
            print(f"  🟢 当前环境:{wd_names[now_wd]}+{env_tier}(涨停{cur_limit_up}只)")
            print(f"  📊 最佳匹配: {best_signal['label']}")
            print(f"  📈 回测: +{best_signal['ret']:.1f}% / 胜率{best_signal['wr']:.1f}% (样本{best_signal['n']}笔)")
        else:
            print(f"  🟡 当前环境:{wd_names[now_wd]}+{env_tier}(涨停{cur_limit_up}只)")
            print(f"  📊 无强匹配信号，执行默认策略")
        
        if avoid_signal:
            print(f"  🔴 ⚠️警告: 当前处于回避区域「{avoid_signal['label']}」")
            print(f"      回测{avoid_signal['label']}仅{avoid_signal.get('ret', 0):+.1f}%/胜率{avoid_signal.get('wr', 0):.1f}%")
            print(f"      建议严格控仓或空仓等待")
        
        # 仓位建议
        base_pos = 0.5
        if avoid_signal:
            base_pos = 0.1
        elif best_signal and best_signal.get('ret', 0) > 2.0:
            base_pos = 0.7
        elif env_tier in ['活跃', '高潮']:
            base_pos = 0.6
        
        print(f"  💼 信号源:1,541笔严格时间线回测 | 建议仓位:{int(base_pos*100)}%")
    
    # ===== 数据质量因子 =====
    # 周日/无交易数据时降低可信度
    dq = penetration.get('data_quality', {})
    quality_count = sum(1 for v in dq.values() if v)
    quality_total = max(len(dq), 1)
    data_quality_factor = clamp(quality_count / quality_total * 100, 30, 100)
    
    # ===== 打包输出 =====
    result = {
        'engine': 'morning-pipeline-v3',
        'timestamp': _dt.now().strftime('%Y-%m-%d %H:%M:%S'),
        'summary': {
            'env_score': env_score,
            'env_tier': '🔥高潮' if env_score>=70 else '🟢活跃' if env_score>=55 else '⚪发酵' if env_score>=40 else '🔵震荡' if env_score>=25 else '🔴冰点',
            'decision': decision,
            'position': pos_str,
            's_level_count': len(s_level),
            'strategy_tag': list(lhb_tags),
            'total_matched': len(matched),
            'sell_rules': 'T+1清仓 | 止损-5% | 低开-3%集合竞价卖',
            'data_quality_pct': round(data_quality_factor, 1),
            'maodun_grades': {
                'top_grade': s_level[0].get('maodun_grade', '无') if s_level else '无',
                'maodun_count': len(s_level),
            },
        },
        'strategy_db_top3': [
            {'name': rankings[0]['name'][:40], 'ret': rankings[0]['ret'],
             'win_rate': rankings[0]['win_rate'], 'n': rankings[0]['n'],
             'composite': rankings[0]['composite']},
            {'name': rankings[1]['name'][:40], 'ret': rankings[1]['ret'],
             'win_rate': rankings[1]['win_rate'], 'n': rankings[1]['n'],
             'composite': rankings[1]['composite']},
            {'name': rankings[2]['name'][:40], 'ret': rankings[2]['ret'],
             'win_rate': rankings[2]['win_rate'], 'n': rankings[2]['n'],
             'composite': rankings[2]['composite']},
        ],
        'matched_strategies': matched[:10],
        'maodun_top_level': s_level,
        'realtime_signals': signals,
        'layers': penetration.get('layer_scores', {}),
    }
    
    # 写入所有输出文件
    s_level_info = [{'strategy': s['strategy'], 'composite': s['composite'],
                     'match_score': s['match_score'], 'ret': s['ret'],
                     'win_rate': s['win_rate'], 'n': s['n'],
                     'big_win_pct': s.get('big_win_pct',0),
                     'big_loss_pct': s.get('big_loss_pct',0),
                     'risk_penalty': s.get('risk_penalty',0),
                     'is_pure_suoliang': s.get('is_pure_suoliang', False),
                     'sell_advice': s.get('sell_advice', '💹 持股到收盘'),
                     'sell_diff_pct': s.get('sell_diff_pct', 0)} for s in s_level]
    
    for fname, data in [
        ('contradiction_report_v3.json', result),
        ('contradiction_engine_result.json', {
            'engine': 'contradiction-engine-v3',
            'timestamp': result['timestamp'],
            'env_score': env_score,
            'env_tier': result['summary']['env_tier'],
            'decision': decision,
            'position': pos_str,
            'data_quality_pct': round(data_quality_factor, 1),
            'maodun_top_level': s_level_info,
            'maodun_count': len(s_level),
            'strategy_tags': list(lhb_tags),
            'matched_count': len(matched),
            'top3_strategies': result['strategy_db_top3'],
            'realtime_signals': signals,
            'contradiction_analysis': contradiction,
            'sell_rules': {
                't_plus_1': True, 'stop_loss_pct': -5.0, 'open_drop_sell_pct': -3.0},
        }),
    ]:
        path = os.path.join(DATA, fname)
        with open(path, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  ✅ 已保存: {fname}")
    
    # ===== 打印摘要 =====
    print(f"\n{'=' * 70}")
    print(f"📋 三刀流早盘摘要")
    print(f"{'=' * 70}")
    print(f"\n🌍 环境分: {env_score}/100{''.join(['🔥' if env_score>=70 else '🟢' if env_score>=55 else '⚪' if env_score>=40 else '🔵' if env_score>=25 else '🔴'])} [{result['summary']['env_tier']}]")
    print(f"🎯 决策: {decision}  |  仓位: {pos_str}")
    
    top3 = result['strategy_db_top3']
    print(f"\n🏆 策略排行榜:")
    for i, t in enumerate(top3):
        print(f"  {i+1}. {t['name'][:40]:40s} {t['composite']}分 {t['ret']:+.2f}%/{t['win_rate']:.1f}% n={t['n']}")
    
    if s_level:
        print(f"\n⭐ 矛盾引擎优选策略:")
        for s in s_level:
            type_mark = '📊纯缩量' if s.get('is_pure_suoliang') else '🏛️机构'
            sell_adv = s.get('sell_advice', '')
            print(f"  ① {type_mark} {s['strategy'][:48]:48s}")
            print(f"     综合{s['composite']} | {s['ret']:+.2f}%/{s['win_rate']:.1f}% | 样本{s['n']}笔 | {sell_adv}")
    
    if signals:
        print(f"\n📡 可操作标的:")
        for sig in signals:
            print(f"  📌 {sig['strategy'][:40]:40s}")
            for st in sig['stocks'][:3]:
                tags_s = f"机构{st['inst_count']}" if st['inst_count'] else ''
                tags_s += f" 量比{st['vol_ratio']}" if st.get('vol_ratio') else ''
                print(f"     {st['name']}({st['code']}) {st['chg']:+.1f}% {tags_s} MA5偏移{st.get('ma5_dist',0):+.1f}%")
    
    print(f"\n⚙️ 卖出: T+1清仓 | 止损-5% | 低开-3%集合竞价卖")
    print(f"\n✅ 分析完成 ({_dt.now().strftime('%H:%M:%S')})")
    
    return result


def load_signals_database():
    """加载穿透信号库（黄金信号），旧代码从 inline import 提取"""
    import json as _json_loader
    sp = os.path.join(BASE, 'data', os.pardir, 'docs', 'signals_penetration_v1.json')
    if os.path.exists(sp):
        try:
            with open(sp) as f:
                return _json_loader.load(f)
        except:
            pass
    return None


if __name__ == '__main__':
    run_morning_pipeline()
