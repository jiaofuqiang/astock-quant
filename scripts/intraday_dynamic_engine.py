#!/usr/bin/env python3
"""
📡 盘中动态市场分类 + 多策略备选引擎 v2.0
===========================================
核心设计：

1. 每N分钟采集腾讯行情 → 实时计算当前市场9维分类
2. 每种市场类型保留TOP5策略备选（按回测评分排序）
3. 对每个策略检测今天盘中是否有可买标的
4. 和前N次分类对比 → 检测市场切换趋势
5. 切换时推送：旧策略→新策略切换建议 + 具体标的

用法:
  python3 scripts/intraday_dynamic_engine.py --once    # 单次运行（cron模式）
  python3 scripts/intraday_dynamic_engine.py --watch   # 持续监控（每5分钟）
  python3 scripts/intraday_dynamic_engine.py --now     # 查看当前状态
"""

import os, sys, json, subprocess, time, sqlite3, math
from datetime import datetime, date
from collections import defaultdict, Counter

BASE = os.path.expanduser("~/astock")
DATA = os.path.join(BASE, "data")
RESEARCH = os.path.join(BASE, "research")

# ============ 1. 腾讯行情接口 ============

def fetch_tencent(codes, batch_size=80):
    """批量获取腾讯实时行情，返回 {code: {name, chg_pct, ...}}"""
    if not codes: return {}
    def add_prefix(c):
        if c.startswith(('sh','sz')): return c
        return f"sh{c}" if c.startswith(('6','5','9')) else f"sz{c}"
    tcodes = [add_prefix(c) for c in codes if c]
    result = {}
    for i in range(0, len(tcodes), batch_size):
        batch = tcodes[i:i+batch_size]
        url = f"https://qt.gtimg.cn/q={','.join(batch)}"
        try:
            proc = subprocess.Popen(
                ['curl','-s','--connect-timeout','5','--max-time','8',url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            out, _ = proc.communicate(timeout=12)
            raw = out.decode('gbk', errors='replace')
            for line in raw.strip().split('\n'):
                if not line.strip() or '=' not in line: continue
                try:
                    parts = line.split('"')
                    if len(parts) < 2: continue
                    fields = parts[1].split('~')
                    if len(fields) < 50: continue
                    code = fields[2]
                    price = float(fields[3]) if fields[3] else 0
                    prev = float(fields[4]) if fields[4] else price
                    chg = (price - prev) / prev * 100 if prev else 0
                    result[code] = {
                        'name': fields[1], 'price': price, 'prev_close': prev,
                        'chg_pct': round(chg, 2),
                        'open': float(fields[5]) if fields[5] else 0,
                        'high': float(fields[33]) if fields[33] else 0,
                        'low': float(fields[34]) if fields[34] else 0,
                        'volume': float(fields[6]) if fields[6] else 0,
                        'amount_wan': float(fields[37]) if len(fields)>37 and fields[37] else 0,
                        'turnover': float(fields[38]) if len(fields)>38 and fields[38] else 0,
                        'volume_ratio': float(fields[49]) if len(fields)>49 and fields[49] else 0,
                        'is_limit_up': chg >= 9.5 and price > 0,
                        'seal_wan': float(fields[50]) if len(fields)>50 and fields[50] else 0,
                    }
                except (ValueError, IndexError): continue
        except Exception as e:
            print(f"  ⚠️ batch error: {e}")
        time.sleep(0.03)
    return result

# ============ 2. 核心数据加载 ============

def load_main_board():
    path = os.path.join(DATA, "all_main_board.txt")
    if not os.path.exists(path): return []
    return [l.strip() for l in open(path) if l.strip() and not l.startswith('#')]

# 龙头候选池（160只，覆盖各板块核心股）
CORE_CODES = [
    # 券商金融
    '600030','601688','601066','600837','601878','601236','002797','300059','600570','300033',
    # 白酒消费
    '600519','000858','600809','000568','000596','600809','002304','603369',
    # 新能源
    '300750','002594','601127','600104','000625','002460','002466','300014','300274','601012',
    # AI算力
    '601138','603019','688981','002463','603501','600745','603986','002049','300661','688012',
    # 光通信
    '300308','300502','002281','300394','688036','002897','300476',
    # 半导体
    '688981','603986','002049','600745','688008','688126','300054','688396',
    # 医药生物
    '600276','300760','300122','000661','002007','300347','603259','688180',
    # 军工
    '600760','600893','000768','002179','600862','600391',
    # 地产基建
    '000002','600048','001979','601668','601390','601186',
    # 周期资源
    '601899','600547','002155','603993','000630','600362','601168','600516',
    # 汽车链
    '601689','002920','002865','300750','002594','601633','000625',
    # 机械
    '600406','601100','002444','000338','600031','300124','300750',
    # 通信
    '000063','600941','601728','300628','603236','300394',
    # 电力
    '600900','601985','600011','600023','600025','600886',
    # 化工
    '600309','002709','601678','600352','000830',
    # 其他活跃股
    '600171','600584','600460','688041','600703','300223','300458','300782',
    '002371','002409','002129','300433','000725','688126','600036','601166',
]

class IntradayEngine:
    """盘中动态决策引擎"""
    
    def __init__(self):
        self.state_file = os.path.join(DATA, "intraday_state.json")
        self.history_file = os.path.join(DATA, "intraday_history.json")
        
        # 加载策略矩阵
        with open(os.path.join(RESEARCH, "market_strategy_matrix_v2.json")) as f:
            self.matrix = json.load(f)
        with open(os.path.join(RESEARCH, "market_history_v6.json")) as f:
            self.history = json.load(f)
        
        self.all_codes = load_main_board()
        
        # 排除M18（结果型），保留11个独立策略
        self.independent_strategies = [
            'M01隔夜溢价缩量', 'M02总龙头打板', 'M07板块爆发追涨',
            'M08连板接力量缩', 'M10放量换手接力', 'M11一字开板接力',
            'M12超卖反弹首板', 'M13深坑反弹',
        ]
        
        # 策略操作手册
        self.strategy_guide = {
            'M01隔夜溢价缩量': {'desc': '缩量<0.7+涨停+非一字', 'buy': 'T日打板', 'sell': 'T+1竞价卖'},
            'M02总龙头打板': {'desc': '唯一最高板≥3板', 'buy': '09:30~14:00打板', 'sell': 'T+1竞价卖'},
            'M07板块爆发追涨': {'desc': '板块涨停≥3', 'buy': '打板龙一/龙二', 'sell': 'T+1竞价卖'},
            'M08连板接力量缩': {'desc': '2板+缩量<0.7', 'buy': '打板买入', 'sell': 'T+1竞价卖'},
            'M09烂板回封': {'desc': '上影>5%+放量+涨停', 'buy': '打板买入', 'sell': 'T+1竞价卖'},
            'M10放量换手接力': {'desc': '放量板0.7~5+T日竞价买', 'buy': 'T日竞价买入', 'sell': 'T+1竞价卖'},
            'M11一字开板接力': {'desc': '一字涨停次日开板', 'buy': '竞价买入', 'sell': 'T+1竞价卖'},
            'M12超卖反弹首板': {'desc': 'MA20<-20%+首板', 'buy': '打板买入', 'sell': 'T+1竞价卖'},
            'M13深坑反弹': {'desc': '60日回撤>30%+板块≥5涨停', 'buy': '打板买入', 'sell': 'T+1竞价卖'},
        }
    
    # ============ 3. 盘中实时分类 ============
    
    def classify(self):
        """实时扫描→分类→返回状态"""
        t0 = time.time()
        
        # 龙头池快扫
        quotes = fetch_tencent(CORE_CODES, batch_size=80)
        if not quotes:
            return None
        
        now = datetime.now()
        hour = now.hour
        minute = now.minute
        is_trading = (hour == 9 and minute >= 25) or (10 <= hour <= 11) or (hour == 11 and minute <= 30) or \
                     (13 <= hour <= 14) or (hour == 15 and minute <= 0)
        
        # 统计指标
        limit_ups = {c: q for c, q in quotes.items() if q['is_limit_up']}
        limit_up_count = len(limit_ups)
        
        up_count = sum(1 for q in quotes.values() if q['chg_pct'] > 0)
        down_count = sum(1 for q in quotes.values() if q['chg_pct'] < 0)
        total = up_count + down_count
        zh_ratio = round(up_count / max(total, 1) * 100, 1)
        
        vol_ratios = [q['volume_ratio'] for q in quotes.values() if q['volume_ratio'] and q['volume_ratio'] > 0]
        avg_vol = sum(vol_ratios) / max(len(vol_ratios), 1) if vol_ratios else 1.0
        
        # 全量扫描（只在有涨停时做）
        all_limits = {}
        if limit_up_count >= 3 and is_trading:
            print(f"  涨停{limit_up_count}只，全量扫描...")
            all_q = fetch_tencent(self.all_codes, batch_size=80)
            all_limits = {c: q for c, q in all_q.items() if q['is_limit_up']}
            limit_up_count = len(all_limits)
            print(f"  全量涨停: {limit_up_count}只")
        
        # ==== 简化9维分类 ====
        # L1热度
        if limit_up_count >= 80: l1 = "☀️狂热"
        elif limit_up_count >= 30: l1 = "🌤活跃"
        elif limit_up_count >= 10: l1 = "☁️平淡"
        elif limit_up_count >= 3: l1 = "❄️冰点"
        else: l1 = "❄️恐慌冰点"
        
        # L2资金风格
        l2 = "放量游资" if avg_vol > 1.5 else ("机构趋势" if avg_vol > 1.0 else "缩量惜售")
        
        # L3赚钱效应（从涨停结构判断）
        l3 = "板块集群"
        if limit_up_count >= 10 and limit_up_count < 30: l3 = "板块集群" 
        elif limit_up_count >= 3 and limit_up_count < 10: l3 = "首板套利"
        elif limit_up_count < 3: l3 = "轮动打地鼠"
        
        # L4板块结构
        if limit_up_count >= 30: l4 = "🔀双线并行"
        elif limit_up_count >= 10: l4 = "🎯集中主线"
        elif limit_up_count >= 3: l4 = "📊散乱多线"
        else: l4 = "🌫️无主线"
        
        # L5量价
        if avg_vol > 2.0: l5 = "⚠️量价虚胖"
        elif avg_vol > 1.5: l5 = "✅量价健康"
        elif avg_vol > 0.8: l5 = "⚖️量价温和"
        else: l5 = "💎缩量惜售"
        
        # L6趋势
        if zh_ratio >= 55: l6 = "📈强势延续"
        elif zh_ratio >= 40: l6 = "↔️震荡筑底"
        elif zh_ratio >= 20: l6 = "📉超跌反弹"
        else: l6 = "🚀加速冲顶"
        
        # L7情绪（盘中简化）
        if limit_up_count >= 50: l7 = "😊乐观"
        elif limit_up_count >= 20: l7 = "😐平衡"
        elif limit_up_count >= 5: l7 = "😔悲观"
        else: l7 = "😰恐慌"
        
        tag = f"{l1}·{l2}·{l3}·{l4}·{l5}·{l6}·{l7}"
        
        # 提取可做标的（涨停股列表）
        buyable_candidates = list(all_limits.keys()) if all_limits else list(limit_ups.keys())
        buyable_names = {}
        for c in buyable_candidates:
            q = all_limits.get(c) or limit_ups.get(c) or {}
            buyable_names[c] = q.get('name', '')
        
        # 保存涨停股原始行情数据供扫描器
        if all_limits:
            q_path = os.path.join(DATA, 'intraday_raw_quotes.json')
            with open(q_path, 'w') as f:
                json.dump(all_limits, f, ensure_ascii=False)
        
        state = {
            'timestamp': now.strftime('%H:%M:%S'),
            'datetime': now.isoformat(),
            'tag': tag,
            'l1': l1, 'l2': l2, 'l3': l3, 'l4': l4, 'l5': l5, 'l6': l6, 'l7': l7,
            'limit_up': limit_up_count,
            'zh_ratio': zh_ratio,
            'avg_vol_ratio': round(avg_vol, 2),
            'up_count': up_count,
            'down_count': down_count,
            'buyable_count': len(buyable_candidates),
            'buyable_candidates': buyable_candidates,  # 全部涨停股
            'buyable_names': buyable_names,
            'is_trading': is_trading,
            'elapsed': round(time.time() - t0, 1),
        }
        
        return state
    
    # ============ 4. 多策略推荐 + 标的可用性 ============
    
    def get_strategy_pool(self, tag):
        """获取该市场类型下所有可用策略（按评分排序，多策略备选）
        
        降级策略：精确9维 → l1+l3+l6 → l1+l3 → _all_market
        每级选样本量(n)最大的tag，确保策略推荐数据可信。
        """
        # 从精确到模糊的多级降级策略
        def get_tag_data(parts_to_match):
            """在matrix中找匹配指定维度前缀的tag，返回(n最大, tag_data)"""
            candidates = []
            for t, d in self.matrix.items():
                if t.startswith('_'):  # 跳过_meta/_all_market
                    continue
                t_parts = t.split('·')
                if len(t_parts) < len(parts_to_match):
                    continue
                match = True
                for i, p in enumerate(parts_to_match):
                    if p is not None and p != t_parts[i]:
                        match = False
                        break
                if match:
                    # 计算总样本数
                    total_n = 0
                    for sname in self.independent_strategies:
                        s = d.get(sname)
                        if isinstance(s, dict):
                            total_n += s.get('n', 0)
                    candidates.append((total_n, t, d))
            if candidates:
                candidates.sort(key=lambda x: -x[0])
                return candidates[0]  # (total_n, tag_name, tag_data)
            return None
        
        parts = tag.split('·')
        if len(parts) < 7:
            return []  # 标签格式异常
        
        # 四级降级匹配
        levels = [
            parts[:9] if len(parts) >= 9 else parts,  # 精确9维（引擎生成7维但矩阵是9维）
            [parts[0], None, parts[2], None, None, parts[5], None, None, None],  # l1+l3+l6
            [parts[0], None, parts[2], None, None, None, None, None, None],      # l1+l3
            None,  # _all_market 兜底
        ]
        
        td = None
        used_level = "无匹配"
        for level in levels:
            if level is None:
                # _all_market 兜底
                if '_all_market' in self.matrix:
                    td = self.matrix['_all_market']
                    used_level = "_all_market"
                break
            result = get_tag_data(level)
            if result:
                total_n, tag_name, tag_data = result
                # 检查样本量是否足够（精确匹配≥20，降级≥50，再降级≥100）
                min_required = 20 if len([p for p in level if p is not None]) >= 7 else \
                               (50 if len([p for p in level if p is not None]) >= 3 else 100)
                # 检查这个tag下最顶层策略的n
                max_strat_n = 0
                for sname in self.independent_strategies:
                    s = tag_data.get(sname)
                    if isinstance(s, dict):
                        max_strat_n = max(max_strat_n, s.get('n', 0))
                if max_strat_n >= min_required:
                    td = tag_data
                    used_level = tag_name[:50]
                    break
                # 样本不够但比没有好——继续尝试下一级
                if not td:
                    td = tag_data
                    used_level = f"{tag_name[:40]}(n={max_strat_n}<{min_required})"
        
        if not td:
            if '_all_market' in self.matrix:
                td = self.matrix['_all_market']
                used_level = "_all_market(最终兜底)"
            if not td:
                return []
        
        scored = []
        for sname in self.independent_strategies:
            s = td.get(sname)
            if s and isinstance(s, dict) and s.get('n', 0) >= 3:
                score = s['open']['avg'] * 0.3 + s['high']['avg'] * 0.3 + s['open']['win_rate'] * 0.02
                guide = self.strategy_guide.get(sname, {})
                scored.append({
                    'strategy': sname,
                    'score': round(score, 1),
                    'open_avg': s['open']['avg'],
                    'high_avg': s['high']['avg'],
                    'win_rate': s['open']['win_rate'],
                    'n': s['n'],
                    'desc': guide.get('desc', ''),
                    'buy': guide.get('buy', ''),
                    'sell': guide.get('sell', ''),
                })
        
        scored.sort(key=lambda x: -x['score'])
        return scored  # 返回全部，调用方按需取前N个
    
    def check_strategy_availability(self, state, strategy_pool):
        """检测每个策略今天是否有可买标的"""
        if not state or not strategy_pool:
            return strategy_pool
        
        buyable = set(state.get('buyable_candidates', []))
        
        for s in strategy_pool:
            sname = s['strategy']
            s['available'] = len(buyable) > 0  # 今天有涨停就至少可选
            s['available_count'] = len(buyable)
            s['is_buyable'] = len(buyable) > 0 and state.get('is_trading', False)
        
        return strategy_pool
    
    # ============ 5. 趋势变化检测 ============
    
    def detect_trend(self, current_state):
        """检测市场切换趋势（和最近3次对比）"""
        history = []
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file) as f:
                    history = json.load(f)
            except: pass
        
        if not history:
            return {'changed': False, 'trend': '首次运行'}
        
        # 取最近3次
        recent = history[-3:] + [current_state]
        
        # 检查l1变化
        l1s = [s.get('l1', '') for s in recent]
        l1_unique = list(dict.fromkeys(l1s))  # 去重保持顺序
        
        # 检查涨停数趋势
        limits = [s.get('limit_up', 0) for s in recent]
        limit_trend = limits[-1] - limits[0] if len(limits) >= 2 else 0
        
        # 检查涨跌比趋势
        zhs = [s.get('zh_ratio', 50) for s in recent]
        zh_trend = zhs[-1] - zhs[0] if len(zhs) >= 2 else 0
        
        changes = []
        direction = "稳定"
        
        if len(l1_unique) >= 2:
            old_l1, new_l1 = l1_unique[0], l1_unique[-1]
            changes.append(f"热度: {old_l1}→{new_l1}")
            
            # 判断方向
            heat_map = {'❄️恐慌冰点': 0, '❄️冰点': 1, '☁️平淡': 2, '🌤活跃': 3, '☀️狂热': 4}
            old_val = heat_map.get(old_l1, 2)
            new_val = heat_map.get(new_l1, 2)
            if new_val > old_val:
                direction = "📈回暖"
            elif new_val < old_val:
                direction = "📉降温"
        
        if abs(limit_trend) >= 10:
            changes.append(f"涨停{'+' if limit_trend>0 else ''}{limit_trend}")
        
        if abs(zh_trend) >= 10:
            changes.append(f"涨跌比{'↑' if zh_trend>0 else '↓'}{abs(zh_trend):.0f}%")
        
        return {
            'changed': len(changes) > 0,
            'changes': changes,
            'direction': direction,
            'limit_trend': limit_trend,
            'zh_trend': zh_trend,
            'old_l1': l1_unique[0] if len(l1_unique) >= 1 else '',
            'new_l1': l1_unique[-1] if len(l1_unique) >= 1 else '',
        }
    
    # ============ 6. 主流程 ============
    
    def run_once(self):
        """单次运行"""
        now = datetime.now()
        print(f"\n{'='*45}")
        print(f"📡 盘中动态决策 @ {now.strftime('%H:%M:%S')}")
        print(f"{'='*45}")
        
        # 分类
        state = self.classify()
        if not state:
            print("❌ 无法获取行情数据")
            return None
        
        print(f"  🏷️  {state['tag']}")
        print(f"  🔥 涨停{state['limit_up']} 涨跌{state['zh_ratio']}% 量比{state['avg_vol_ratio']}")
        
        # 判断行情模式：切换 vs 延续
        trend = self.detect_trend(state)
        market_mode = "延续"
        if trend.get('changed'):
            if trend.get('direction') == '📈回暖':
                market_mode = "切换升温"
            elif trend.get('direction') == '📉降温':
                market_mode = "切换降温"
            else:
                market_mode = "切换稳定"
        print(f"  📌 行情模式: {market_mode} | {trend.get('direction','稳定')}")
        
        # 策略推荐
        pool = self.get_strategy_pool(state['tag'])
        pool = self.check_strategy_availability(state, pool)
        
        # 根据行情模式重排策略优先级
        if len(pool) >= 2:
            # 切换升温：板块爆发优先
            if market_mode == "切换升温":
                pool.sort(key=lambda s: (
                    s['open_avg'] * 0.3 + (s.get('high_avg', s['open_avg']) - s['open_avg']) * 0.5 + s['win_rate'] * 0.02
                ), reverse=True)
            # 切换降温：隔夜溢价+竞价高价优先
            elif market_mode == "切换降温":
                # 把M01和M18提权
                def score_fn(s):
                    base = s['open_avg'] * 0.4 + s['win_rate'] * 0.02
                    if '隔夜' in s['strategy'] or '竞价' in s['strategy'] or '连板' in s['strategy']:
                        base *= 1.3  # 提权30%
                    return base
                pool.sort(key=score_fn, reverse=True)
            # 延续：连板接力优先
            else:
                def score_fn(s):
                    base = s['open_avg'] * 0.5 + (s.get('high_avg', s['open_avg']) - s['open_avg']) * 0.3 + s['win_rate'] * 0.01
                    if '连板' in s['strategy'] or '板块' in s['strategy'] or '隔夜' in s['strategy']:
                        base *= 1.2
                    return base
                pool.sort(key=score_fn, reverse=True)
        
        # ===== 一句话选股：从matrix_v2匹配TOP策略 → 实时选股 =====
        buy_signals = []
        buyable = list(state.get('buyable_candidates', []))
        buyable_names = state.get('buyable_names', {})
        
        # 加载腾讯实时行情
        realtime_quotes = {}
        quotes_path = os.path.join(DATA, 'intraday_raw_quotes.json')
        if os.path.exists(quotes_path):
            try:
                with open(quotes_path) as f:
                    realtime_quotes = json.load(f)
            except:
                pass
        
        if pool and buyable:
            # ===== 加载辅助数据：板块分组 + 连板数 + MA20乖离 =====
            try:
                from intraday_helpers import get_top_sectors, calculate_board_counts, calculate_m20_bias, is_new_stock
                helper_loaded = True
            except:
                helper_loaded = False
            
            # 预计算次新股排除名单
            new_stocks = set()
            if helper_loaded:
                for c in buyable:
                    if is_new_stock(c, today=now.strftime('%Y-%m-%d')):
                        new_stocks.add(c)
            if new_stocks:
                print(f"  🗑️ 排除{len(new_stocks)}只次新股: {', '.join(buyable_names.get(c,c) for c in list(new_stocks)[:5])}{'...' if len(new_stocks)>5 else ''}")
            
            sector_info = {}
            board_info = {}
            m20_bias = {}
            top_sectors = []
            if helper_loaded:
                try:
                    board_info = calculate_board_counts(buyable, now.strftime('%Y-%m-%d'))
                    m20_bias = calculate_m20_bias(buyable, now.strftime('%Y-%m-%d'))
                    top_sectors = get_top_sectors(realtime_quotes, top_n=8)
                    from intraday_helpers import get_sectors_for_code
                    for c in buyable:
                        secs = get_sectors_for_code(c)
                        if secs:
                            sector_info[c] = secs
                except Exception as e:
                    print(f"  ⚠️ 辅助数据: {e}")
            
            print(f"  📊 板块爆发: {len(top_sectors)}个 | 连板: {len(board_info)}只")
            
            # ========== 策略选股执行器：遍历所有策略，谁有标的就推谁 ==========
            strategy_ops = {
                'M01隔夜溢价缩量': ('📌 打板策略：等涨停封板后挂单排队，不要追涨。早封(10:00前)优于午封。T+1竞价卖', 3.76, 82.7),
                'M07板块爆发追涨': ('📌 打板策略：追板块内涨停最快的。等封板确认后挂单。T+1竞价卖或等冲高(+6.5%)', 3.08, 73.4),
                'M08连板接力量缩': ('📌 打板策略：连板涨停时打板买入。10:00前封的更安全。T+1竞价卖最优(+7.5%)', 7.54, 92.6),
                'M11一字开板接力': ('⏰ 明早策略（不开新仓）：一字板被打开后，明天竞价0~3%买入。T+1等冲高卖(+7.5%)', 5.22, 73.0),
                'M02总龙头打板': ('📌 打板策略：唯一最高板≥3板。10:00~14:00等换手足够再打。T+1竞价卖', 2.72, 66.7),
                'M18竞价高价接力': ('⏰ 明早策略（不开新仓）：明天竞价高开5~8%时买入。T+1竞价卖', 7.35, 100.0),
                'M10放量换手接力': ('⏰ 明早策略（不开新仓）：明天开盘0~7%竞价买入。T+1等冲高卖(+6.3%)', 2.74, 71.6),
                'M12超卖反弹首板': ('📌 低吸策略：MA20乖离<-20%的首板涨停，打板买入。T+1竞价卖或等T+3高点', 2.43, 89.4),
                'M13深坑反弹': ('📌 低吸策略：深跌反弹涨停，打板买入。T+1竞价卖', 1.88, 70.6),
            }
            
            today = now.strftime('%Y-%m-%d')
            
            for strat_entry in pool:
                if strat_entry.get('n', 0) < 3:
                    continue  # 样本不足跳过
                    
                sname = strat_entry['strategy']
                op_hint, default_avg, default_wr = strategy_ops.get(sname, ('📌 盘中操作：涨停封板后买入', 2.0, 60.0))
                
                # 可做标的 = 涨停股 - 次新股
                pool_buyable = [c for c in buyable if c not in new_stocks]
                if not pool_buyable:
                    continue
                    
                selected = []
                
                # ===== M01 隔夜溢价缩量 =====
                if 'M01隔夜溢价' in sname:
                    for c in pool_buyable:
                        q = realtime_quotes.get(c, {})
                        vr = q.get('volume_ratio', 0)
                        op = q.get('open', 0)
                        prev = q.get('prev_close', 0)
                        open_chg = (op - prev) / prev * 100 if prev > 0 else 0
                        name = buyable_names.get(c, '')
                        if vr > 0 and vr < 0.7 and open_chg < 9.5 and 'ST' not in name:
                            tier = '极缩' if vr < 0.3 else ('缩量' if vr < 0.5 else '微缩')
                            tier_avg = 7.77 if vr < 0.3 else (4.77 if vr < 0.5 else 3.52)
                            tier_wr = 96.0 if vr < 0.3 else (83.0 if vr < 0.5 else 77.0)
                            selected.append({'code': c, 'name': name, 'rank_score': vr,
                                '策略扩展': f'{tier}<{vr:.1f}', '预期收益': tier_avg, '预期胜率': tier_wr,
                                '仓位': '单只≤3万' if vr < 0.3 else ('单只≤2.5万' if vr < 0.5 else '单只≤2万')})
                    selected.sort(key=lambda x: x['rank_score'])
                
                # ===== M07 板块爆发追涨 =====
                elif 'M07板块爆发' in sname:
                    # 板块爆发定义：板块内涨停≥3只
                    hot_sectors = set()
                    if top_sectors:
                        for sec in top_sectors:
                            if sec.get('limit_up_count', 0) >= 3:
                                hot_sectors.add(sec['sector'])
                    for c in pool_buyable:
                        q = realtime_quotes.get(c, {})
                        vr = q.get('volume_ratio', 0)
                        name = buyable_names.get(c, '')
                        seal = q.get('seal_wan', 0)
                        secs = sector_info.get(c, [])
                        # 必须属于爆发板块（涨停≥3只）
                        is_in_hot = bool(set(secs) & hot_sectors) if secs else False
                        if is_in_hot and vr >= 0.7 and 'ST' not in name:
                            sec_str = ','.join(secs[:2]) if secs else ''
                            score = -(vr * 0.6 + min(seal / 10000, 10) * 0.4)
                            selected.append({'code': c, 'name': name, 'rank_score': score,
                                '策略扩展': f'放量{vr:.1f} [{sec_str[:16]}]', '预期收益': default_avg, '预期胜率': default_wr,
                                '仓位': '单只≤3万'})
                    selected.sort(key=lambda x: x['rank_score'])
                
                # ===== M08 连板接力量缩 =====
                elif 'M08连板' in sname:
                    for c in pool_buyable:
                        q = realtime_quotes.get(c, {})
                        vr = q.get('volume_ratio', 0)
                        bc = board_info.get(c, 0)
                        name = buyable_names.get(c, '')
                        secs = sector_info.get(c, [])
                        if bc >= 2 and 'ST' not in name:
                            score = -bc * 10 + vr
                            sec_str = f" [{','.join(secs[:2])}]" if secs else ""
                            selected.append({'code': c, 'name': name, 'rank_score': score,
                                '策略扩展': f'{bc}板{sec_str}', '预期收益': default_avg, '预期胜率': default_wr,
                                '仓位': '单只≤3万' if bc <= 3 else '单只≤2.5万',
                                '板块': secs})
                    selected.sort(key=lambda x: x['rank_score'])
                
                # ===== M11 一字开板接力 =====
                elif 'M11一字开板' in sname:
                    for c in pool_buyable:
                        q = realtime_quotes.get(c, {})
                        op = q.get('open', 0)
                        prev = q.get('prev_close', 0)
                        name = buyable_names.get(c, '')
                        if prev > 0:
                            open_chg = (op - prev) / prev * 100
                            if 0 <= open_chg < 9.5 and 'ST' not in name:
                                selected.append({'code': c, 'name': name, 'rank_score': open_chg,
                                    '策略扩展': f'开{open_chg:.1f}%', '预期收益': default_avg, '预期胜率': default_wr,
                                    '仓位': '单只≤3万' if open_chg < 3 else '单只≤2万'})
                    selected.sort(key=lambda x: x['rank_score'])
                
                # ===== M02 总龙头打板 =====
                elif 'M02总龙头' in sname:
                    if board_info:
                        max_bc = max(board_info.values()) if board_info else 1
                        top_codes = [c for c, bc in board_info.items() if bc == max_bc]
                        # 唯一最高板：连板数最高的只有一只
                        if len(top_codes) == 1:
                            c = top_codes[0]
                            name = buyable_names.get(c, c)
                            secs = sector_info.get(c, [])
                            sec_str = f" [{','.join(secs[:2])}]" if secs else ""
                            limit_up_count = state.get('limit_up', 0)
                            # 首板(1板)且涨停≥10只时才有效
                            if max_bc >= 3 or (max_bc >= 1 and limit_up_count >= 10):
                                selected.append({'code': c, 'name': name, 'rank_score': -max_bc,
                                    '策略扩展': f'{max_bc}板·唯一{sec_str}', '预期收益': default_avg, '预期胜率': default_wr,
                                    '仓位': '单只≤3万', '板块': secs})
                        # 唯一连板≥3板但有多只（平最高板）—不推任何总龙头
                    # 极端情况：涨停很少且唯一涨停的首板在board_info查不到
                    if not selected and state.get('limit_up', 0) <= 3 and buyable:
                        c = list(buyable)[0]
                        name = buyable_names.get(c, c)
                        secs = sector_info.get(c, [])
                        sec_str = f" [{','.join(secs[:2])}]" if secs else ""
                        selected.append({'code': c, 'name': name, 'rank_score': -1,
                            '策略扩展': f'唯一{sec_str}', '预期收益': default_avg, '预期胜率': default_wr,
                            '仓位': '轻仓试探', '板块': secs})
                
                # ===== M18 竞价高价接力 =====
                elif 'M18竞价高价' in sname:
                    for c in pool_buyable:
                        q = realtime_quotes.get(c, {})
                        op = q.get('open', 0)
                        prev = q.get('prev_close', 0)
                        name = buyable_names.get(c, '')
                        if prev > 0:
                            open_chg = (op - prev) / prev * 100
                            if 5 <= open_chg <= 8 and 'ST' not in name:
                                selected.append({'code': c, 'name': name, 'rank_score': -open_chg,
                                    '策略扩展': f'竞{open_chg:.1f}%', '预期收益': default_avg, '预期胜率': default_wr,
                                    '仓位': '单只≤2万'})
                    selected.sort(key=lambda x: x['rank_score'])
                
                # ===== M10 放量换手接力 =====
                elif 'M10放量换手' in sname:
                    for c in pool_buyable:
                        q = realtime_quotes.get(c, {})
                        op = q.get('open', 0)
                        prev = q.get('prev_close', 0)
                        name = buyable_names.get(c, '')
                        if prev > 0:
                            open_chg = (op - prev) / prev * 100
                            if 0 <= open_chg < 7 and 'ST' not in name:
                                selected.append({'code': c, 'name': name, 'rank_score': open_chg,
                                    '策略扩展': f'开{open_chg:.1f}%', '预期收益': default_avg, '预期胜率': default_wr,
                                    '仓位': '单只≤3万' if open_chg < 3 else '单只≤2万'})
                    selected.sort(key=lambda x: x['rank_score'])
                
                # ===== M12/M13 超卖/深坑 =====
                elif 'M12超卖' in sname or 'M13深坑' in sname:
                    for c in pool_buyable:
                        q = realtime_quotes.get(c, {})
                        name = buyable_names.get(c, '')
                        if 'ST' not in name:
                            selected.append({'code': c, 'name': name, 'rank_score': 0,
                                '策略扩展': '首板涨停', '预期收益': default_avg, '预期胜率': default_wr,
                                '仓位': '单只≤3万'})
                    selected.sort(key=lambda x: x['rank_score'])
                
                # ===== 生成该策略的信号 =====
                for i, s in enumerate(selected[:3]):
                    code = s['code']
                    q = realtime_quotes.get(code, {})
                    chg_val = q.get('chg_pct', 0)
                    vr = q.get('volume_ratio', 0)
                    seal = q.get('seal_wan', 0)
                    secs = s.get('板块', sector_info.get(code, []))
                    sec_str = ','.join(secs[:2]) if secs else ''
                    bs = {
                        '策略': sname,
                        '标的': f"{s['name']}({code})",
                        '评分': round(strat_entry.get('score', 0), 0),
                        '仓位': s.get('仓位', '轻仓'),
                        '预期': f"+{s.get('预期收益', default_avg):.1f}% / {s.get('预期胜率', default_wr):.0f}%胜",
                        '置信度': 'HIGH' if strat_entry.get('n', 0) >= 50 else 'MEDIUM',
                        '涨幅%': chg_val,
                        '量比': vr,
                        '封单万': seal,
                        '策略扩展': s.get('策略扩展', ''),
                        '操作': op_hint,
                        '板块': sec_str,
                    }
                    buy_signals.append(bs)
                    print(f"  ✅ {sname:20s} → {bs['标的']} {s.get('策略扩展','')} {bs.get('板块','')}")
        
        # 如果无选股结果但有可用标的，输出提示
        if not buy_signals and buyable:
            top_strat = pool[0]['strategy'] if pool else '未知'
            top_strat_name = pool[0]['strategy'] if pool else '未知'
            print(f"  ⚠️ {top_strat_name}: {len(buyable)}只涨停但无符合条件标的")
        
        # ===== 卖出信号：从持仓判断 =====
        sell_signals = []
        try:
            import sqlite3
            trade_db = os.path.join(DATA, 'trade_sim.db')
            if os.path.exists(trade_db):
                conn = sqlite3.connect(trade_db, timeout=5)
                # positions表字段: code, name, shares, avg_cost, current_price, profit_rate
                positions = conn.execute(
                    "SELECT code, name, shares, avg_cost, profit_rate FROM positions"
                ).fetchall()
                conn.close()
                
                for pos in positions:
                    code, name, shares, avg_cost, profit_rate = pos
                    q = realtime_quotes.get(code, {})
                    if not q or q.get('price', 0) <= 0:
                        continue
                    
                    current_price = q['price']
                    chg_pct = q.get('chg_pct', 0)
                    profit = profit_rate if profit_rate else (current_price - avg_cost) / avg_cost * 100
                    
                    signal = None
                    action = None
                    
                    if profit <= -8:
                        signal = '🔴 止损'
                        action = '立即卖出'
                    elif profit >= 7 and chg_pct < 2:
                        signal = '🟢 止盈(冲高回落)'
                        action = '卖一半'
                    elif profit >= 10:
                        signal = '🟢 止盈'
                        action = '竞价卖一半'
                    elif chg_pct <= -5:
                        signal = '⚡ 主力逆转'
                        action = '立即卖出'
                    
                    if signal:
                        sell_signals.append({
                            '标的': f"{name}({code})",
                            '盈亏': f"{profit:+.1f}%",
                            '信号': signal,
                            '操作': action,
                            '级别': '⚠️' if abs(profit) > 5 else '💡',
                        })
        except:
            pass
        
        state['sell_signals'] = sell_signals[:5]
        if sell_signals:
            print(f"  🔴 卖出信号: {len(sell_signals)}个")
            for s in sell_signals:
                print(f"    {s['标的']} {s['盈亏']} {s['信号']}")
        
        # 记录推荐到验证系统
        if buy_signals:
            try:
                subprocess.run(
                    ['python3', os.path.join(BASE, 'scripts', 'verify_engine.py'), '--log'],
                    capture_output=True, timeout=10, cwd=BASE
                )
                # 同步到作战面板
                subprocess.run(
                    ['python3', os.path.join(BASE, 'scripts', 'intraday_to_panel.py')],
                    capture_output=True, timeout=10, cwd=BASE
                )
            except:
                pass
        
        state['buy_signals'] = buy_signals[:8]
        os.makedirs(DATA, exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        
        # 追加历史
        history = []
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file) as f:
                    history = json.load(f)
            except: history = []
        history.append({
            'timestamp': state['timestamp'],
            'tag': state['tag'],
            'l1': state['l1'],
            'limit_up': state['limit_up'],
            'zh_ratio': state['zh_ratio'],
            'avg_vol_ratio': state['avg_vol_ratio'],
        })
        if len(history) > 168:
            history = history[-168:]
        with open(self.history_file, 'w') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        
        result = {'state': state, 'strategy_pool': pool[:5], 'trend': trend, 'market_mode': market_mode, 'top_sectors': top_sectors if 'top_sectors' in dir() else []}
        
        # ===== 推送去重：和上次推送对比 =====
        push_hash_file = os.path.join(DATA, "intraday_push_hash.txt")
        # 生成当前推送的哈希（基于选股结果+标签+趋势）
        push_content = json.dumps({
            'tag': state['tag'],
            'limit_up': state['limit_up'],
            'zh_ratio': state['zh_ratio'],
            'buy_signals': [(s.get('策略',''), s.get('标的',''), s.get('策略扩展','')) for s in buy_signals[:3]],
            'sell_signals': [s.get('标的','') for s in sell_signals[:3]],
            'trend_changed': trend.get('changed', False),
        }, sort_keys=True)
        current_hash = str(hash(push_content))
        last_hash = ""
        if os.path.exists(push_hash_file):
            try:
                last_hash = open(push_hash_file).read().strip()
            except:
                pass
        # 只在盘面变化时推送：趋势变化、选股变化、或每30分钟强制推一次
        minute_mark = now.hour * 60 + now.minute
        is_force_mark = (minute_mark % 30 < 5) or trend.get('changed', False)
        should_push = (current_hash != last_hash) or is_force_mark
        if should_push:
            with open(push_hash_file, 'w') as f:
                f.write(current_hash)
        result['should_push'] = should_push
        
        # 保存完整结果
        result_path = os.path.join(DATA, "intraday_decision.json")
        with open(result_path, 'w') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        return result
    
    def switch_strategy(self, old_l1, new_l1):
        """市场切换时的策略过渡建议"""
        # 切换矩阵 [old][new] -> (旧策略退出方式, 新策略入场方式)
        matrix = {
            '❄️恐慌冰点': {
                '❄️冰点': ('适当开仓M10换手接力', '从冰点回温，轻仓M10'),
                '☁️平淡': ('M10逐步退出', 'M01隔夜溢价逐步进场'),
                '🌤活跃': ('换手接力全面退出', 'M08连板缩量+M11一字开板双策略'),
            },
            '❄️冰点': {
                '❄️恐慌冰点': ('全部清仓', '空仓观望'),
                '☁️平淡': ('M10换手接力减半', 'M01隔夜溢价试仓'),
                '🌤活跃': ('M01隔夜溢价加仓', 'M08连板缩量开始关注'),
                '☀️狂热': ('全面转向进攻', 'M08连板缩量主力+M11辅助'),
            },
            '☁️平淡': {
                '❄️冰点': ('隔夜溢价退到冰点模式', '只能做M10换手接力'),
                '🌤活跃': ('M01隔夜溢价加仓', 'M08连板缩量开始关注'),
                '☀️狂热': ('M01/M08并行', '全部策略可用'),
            },
            '🌤活跃': {
                '❄️冰点': ('快速减仓到冰点模式', '放弃连板，只做M10换手'),
                '☁️平淡': ('M08连板减半', '回到M01隔夜溢价为主'),
                '☀️狂热': ('M08连板加仓', '加入M11一字开板辅助'),
            },
            '☀️狂热': {
                '🌤活跃': ('M08连板减半，M11一字退出', 'M01隔夜溢价为主'),
                '☁️平淡': ('全面收缩', 'M01隔夜溢价轻仓'),
                '❄️冰点': ('全部清仓', '空仓或极轻仓M10'),
            },
        }
        return matrix.get(old_l1, {}).get(new_l1, ('保持原有策略', '保持原有策略'))
    
    def format_push(self, result):
        """格式化为推送"""
        if not result: return "❌ 数据不可用"
        state = result['state']
        pool = result['strategy_pool']
        trend = result['trend']
        
        lines = []
        lines.append(f"📡 盘面快照 {state['timestamp']}")
        lines.append(f"{'─'*30}")
        lines.append(f"🔥 涨停{state['limit_up']} | 涨跌{state['zh_ratio']}%")
        lines.append(f"📊 量比{state['avg_vol_ratio']} | 可做{state.get('buyable_count',0)}只")
        lines.append("")
        lines.append(f"🏷️ {state['tag']}")
        
        # 行情模式标签
        market_mode = result.get('market_mode', '延续')
        if market_mode != '延续':
            lines.append(f"📌 {market_mode}")
        
        lines.append("")
        
        if trend.get('changed'):
            if trend.get('direction') in ('📈回暖', '📉降温'):
                lines.append(f"\n⚠️ 趋势{trend['direction']}")
                for c in trend.get('changes', []):
                    lines.append(f"  → {c}")
                
                # 切换建议
                old_l1 = trend.get('old_l1', '')
                new_l1 = trend.get('new_l1', '')
                if old_l1 and new_l1 and old_l1 != new_l1:
                    exit_adv, enter_adv = self.switch_strategy(old_l1, new_l1)
                    lines.append(f"\n  📌 策略切换:")
                    lines.append(f"  🚫 旧策略退出: {exit_adv}")
                    lines.append(f"  🆕 新策略入场: {enter_adv}")
            else:
                lines.append(f"\n✅ 盘面稳定")
        else:
            lines.append(f"\n✅ 盘面稳定")
        
        # 🟢 选股结果（按策略分组）
        if state.get('buy_signals'):
            # 按策略分组
            strat_groups = {}
            for s in state['buy_signals']:
                sn = s['策略']
                if sn not in strat_groups: strat_groups[sn] = []
                strat_groups[sn].append(s)
            
            lines.append("")
            for sn, sigs in strat_groups.items():
                bs0 = sigs[0]
                is_tonight = '⏰' in bs0.get('操作', '')
                lines.append(f"  {sn}")
                lines.append(f"    {bs0['预期']} | 仓{bs0['仓位']} | {bs0['置信度']}")
                if is_tonight:
                    lines.append(f"    ⏰ 明早策略，今天不买")
                lines.append(f"    {bs0['操作']}")
                for i, s in enumerate(sigs[:3], 1):
                    ext = s.get('策略扩展', '')
                    seal = s.get('封单万', 0)
                    sec = s.get('板块', '')
                    seal_str = f" 封{seal:.0f}万" if seal > 0 else ""
                    sec_str = f" [{sec[:16]}]" if sec else ""
                    line = f"    #{i} {s['标的']} {ext}{sec_str}{seal_str}"
                    lines.append(line[:80])
                lines.append("")  # 策略之间空一行
        
        # 🟡 板块爆发排名
        top_sectors = result.get('top_sectors', [])
        if top_sectors and len(top_sectors) >= 2:
            lines.append("")
            lines.append(f"  📊 板块爆发:")
            for sec in top_sectors[:5]:
                limit_cnt = sec['limit_up_count']
                stocks_str = ', '.join([f"{s['name']}" for s in sec['stocks'][:3]])
                icon = "🔥" if limit_cnt >= 5 else "💡"
                line = f"    {icon} {sec['sector']}: {limit_cnt}只涨停"
                lines.append(line)
        
        # 🟡 备选策略
        filtered_pool = [s for s in pool if s.get('n', 0) >= 20]
        if len(filtered_pool) >= 2:
            lines.append("")
            lines.append(f"  📋 备选:")
            for i, s in enumerate(filtered_pool[1:4], 2):
                flag = "🟢" if s.get('is_buyable') else "🔴"
                cur_strat = state['buy_signals'][0]['策略'] if state.get('buy_signals') else ''
                if s.get('strategy') != cur_strat:
                    lines.append(f"    {flag} #{i} {s['strategy']} +{s['open_avg']:.1f}% / {s['win_rate']:.0f}%胜")
        
        # 卖出信号
        sell_sigs = state.get('sell_signals', [])
        if sell_sigs:
            lines.append("")
            lines.append(f"  🔴 持仓卖出:")
            for s in sell_sigs[:3]:
                lines.append(f"    {s.get('级别','')} {s['标的']} {s['信号']} → {s['操作']}")
        
        # 策略健康预警
        try:
            health = subprocess.run(
                ['python3', os.path.join(BASE, 'scripts', 'verify_engine.py'), '--check'],
                capture_output=True, text=True, timeout=8, cwd=BASE
            )
            if 'warning' in health.stdout.lower() or 'critical' in health.stdout.lower():
                lines.append("")
                lines.append(f"  ⚠️ 策略健康预警:")
                for line in health.stdout.strip().split('\n'):
                    if '连亏' in line or '🔴' in line:
                        lines.append(f"    {line.strip()}")
        except:
            pass
        
        lines.append("")
        lines.append(f"⏱️ {state.get('elapsed',0)}s")
        
        return '\n'.join(lines)


# ============ 主入口 ============

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'once'
    engine = IntradayEngine()
    
    if mode in ('once', '--once'):
        result = engine.run_once()
        if result:
            print(f"\n{engine.format_push(result)}")
    
    elif mode in ('watch', '--watch'):
        print("📡 持续监控 (每5分钟)")
        while True:
            try:
                result = engine.run_once()
                if result:
                    print(f"\n{engine.format_push(result)}")
                    
                    # 如果趋势有变化且变化大
                    trend = result['trend']
                    if trend.get('changed') and trend.get('direction') in ('📈回暖', '📉降温'):
                        print(f"\n⚠️ 趋势{trend['direction']}! 注意策略切换")
                        
                        # 输出切换建议
                        old_l1 = trend.get('old_l1', '')
                        new_l1 = trend.get('new_l1', '')
                        if old_l1 and new_l1 and old_l1 != new_l1:
                            # 查新旧策略
                            old_tag = f"{old_l1}·..."
                            new_tag = f"{new_l1}·..."
                            old_pool = engine.get_strategy_pool(f"{old_l1}·放量游资·板块集群·🔀双线并行·✅量价健康·📈强势延续·😐平衡")
                            new_pool = engine.get_strategy_pool(f"{new_l1}·放量游资·板块集群·🔀双线并行·✅量价健康·📈强势延续·😐平衡")
                            if old_pool and new_pool:
                                print(f"  旧策略建议: {old_pool[0]['strategy']} (+{old_pool[0]['open_avg']:.1f}%)")
                                print(f"  新策略建议: {new_pool[0]['strategy']} (+{new_pool[0]['open_avg']:.1f}%)")
                
                time.sleep(300)
            except KeyboardInterrupt:
                print("\n⏹️ 已停止")
                break
            except Exception as e:
                print(f"❌ {e}")
                import traceback; traceback.print_exc()
                time.sleep(300)
    
    elif mode in ('now', '--now'):
        status_path = os.path.join(DATA, "intraday_decision.json")
        if os.path.exists(status_path):
            with open(status_path) as f:
                result = json.load(f)
            print(engine.format_push(result))
        else:
            print("❌ 无最近状态")
    
    elif mode == 'strategies':
        # 输出所有市场类型的多策略清单
        print(f"\n{'='*60}")
        print(f"📋 全部市场类型 × 多策略备选")
        print(f"{'='*60}")
        
        tag_days = Counter(d['_tag'] for d in engine.history)
        for tag, days in tag_days.most_common(30):
            if days < 5: continue
            pool = engine.get_strategy_pool(tag)
            if not pool: continue
            short = tag[:60]
            print(f"\n  {short} [{days}天]")
            for i, s in enumerate(pool[:5], 1):
                avail = "🟢" if s.get('available') else "🔴"
                print(f"  {avail} #{i} {s['strategy']:20s} 竞价{s['open_avg']:+.1f}% 胜{s['win_rate']:.0f}% ({s['n']}笔)")
    
    else:
        print("用法: once | watch | now | strategies")
