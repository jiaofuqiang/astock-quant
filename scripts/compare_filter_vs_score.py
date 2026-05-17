#!/usr/bin/env python3
"""
龙虎榜评分系统 vs 硬性过滤 对比回测 v1.0
========================================
用完全相同的数据和框架，对比两种方法：
  方案A（硬性过滤）：首板+游资+竞价-3~7%+无散户+量比>=0.5+知名游资 → 买
  方案B（评分系统）：极简权重评分≥80（S级）→ 买30%，≥65（A级）→20%，≥50（B级）→10%
  
对比指标：
  - 总收益、胜率、大亏率
  - 最大回撤
  - 交易信号数量
  - 夏普比率（简易版）
  - 每日仓位占用
  - 大赚/大亏分布
"""

import sqlite3, os, json, math
from collections import defaultdict, Counter

DB = os.path.expanduser("~/astock/data/kline_cache.db")
LHB_DB = os.path.expanduser("~/astock/data/lhb_cache.db")

QUANT_KW = ['东方财富证券股份有限公司拉萨','中国国际金融股份有限公司上海分公司','中国国际金融股份有限公司北京建国门外大街','中信证券股份有限公司总部','瑞银证券有限责任公司','摩根大通证券有限责任公司','高盛(中国)证券有限责任公司','华泰证券股份有限公司总部']
YOUZI_KW = ['东方财富','华泰证券','中信证券','国泰君安','招商证券','银河证券','广发证券','国泰海通','中金财富','财通证券','平安证券']
LASA_KW = ['拉萨','东环路','团结路','金融城南环路','香曲东路','昌都','江苏大道']

def classify(dealer):
    if '机构专用' in dealer: return '机构'
    if '股通' in dealer: return '北上'
    for q in QUANT_KW:
        if q in dealer: return '量化'
    for l in LASA_KW:
        if l in dealer: return '散户'
    for y in YOUZI_KW:
        if y in dealer: return '游资'
    return '其他'

FAMOUS_ALIAS = {
    '国泰海通证券股份有限公司武汉紫阳东路证券营业部': '武汉紫阳',
    '国泰海通证券股份有限公司成都北一环路证券营业部': '成都北一环',
    '国泰海通证券股份有限公司总部': '国泰海通总部',
    '中信证券股份有限公司上海分公司': '中信上海',
    '华鑫证券有限责任公司上海宛平南路证券营业部': '炒股养家',
    '华鑫证券有限责任公司上海分公司': '养家分仓',
    '国泰海通证券股份有限公司南京太平南路证券营业部': '南京太平南',
    '国泰海通证券股份有限公司上海长宁区江苏路证券营业部': '章盟主',
    '平安证券股份有限公司杭州曙光路证券营业部': '平安曙光路',
    '中国银河证券股份有限公司绍兴证券营业部': '绍兴',
    '华泰证券股份有限公司总部': '华泰总部',
    '开源证券股份有限公司西安太华路证券营业部': '开源太华路',
    '开源证券股份有限公司西安西大街证券营业部': '开源西大街',
    '中泰证券股份有限公司常州惠国路证券营业部': '中泰常州',
}
FAMOUS_DEALERS = list(FAMOUS_ALIAS.keys())

def is_valid(code, name=''):
    if not (code.startswith('60') or code.startswith('00')): return False
    if 'ST' in name or '*ST' in name: return False
    return True

def detect_board(arr, idx):
    cnt = 0
    for i in range(idx, -1, -1):
        k = arr[i]; kp = arr[i-1] if i > 0 else arr[0]
        chg = (k['close'] - kp['close']) / max(kp['close'], 0.01) * 100
        if chg >= 9.8: cnt += 1
        else: break
    return cnt

def get_famous_set(dealers):
    return set(FAMOUS_ALIAS.get(d, '') for d in dealers if d in FAMOUS_ALIAS)

def compute_score(sig, board, dealer_histories):
    """极简权重评分系统"""
    yz = sig['types'].get('游资', 0)
    jg = sig['types'].get('机构', 0)
    sh = sig['types'].get('散户', 0)
    famous_set = get_famous_set(sig['dealers'])
    
    score = 0
    
    # 游资分（满分40）
    yz_score = 0
    if '武汉紫阳' in famous_set: yz_score += 20
    if '成都北一环' in famous_set: yz_score += 20
    if '中信上海' in famous_set: yz_score += 13
    if '国泰海通总部' in famous_set: yz_score += 13
    if '华泰总部' in famous_set: yz_score += 10
    for fb in ['炒股养家','养家分仓','南京太平南','章盟主','绍兴','开源太华路','开源西大街','中泰常州']:
        if fb in famous_set: yz_score += 8
    if '平安曙光路' in famous_set: yz_score += 3
    if jg >= 1: yz_score += 7
    if yz >= 1: yz_score += 5
    yz_score = min(yz_score, 40)
    score += yz_score
    
    # 板数分（满分20）
    score += 20 if board == 1 else (12 if board == 2 else 8 if board == 3 else 5)
    
    # 协同分（满分20）
    combo = 0
    if '武汉紫阳' in famous_set and '成都北一环' in famous_set: combo += 13
    if '中信上海' in famous_set and '国泰海通总部' in famous_set: combo += 10
    if yz >= 1 and jg >= 1:
        if '平安曙光路' in famous_set: combo -= 13
        else: combo += 10
    combo = max(-5, min(20, combo))
    score += combo
    
    # 散户扣分（满分-10）
    if sh >= 1: score -= 7
    if sh >= 2: score -= 3
    if sh >= 1 and yz_score <= 5: score -= 10
    
    # 历史表现分（满分10）
    for d in set(sig['dealers']):
        t = classify(d)
        if t != '游资': continue
        h = dealer_histories.get(d)
        if h and h['count'] >= 5:
            win_rate = h['win'] / h['count'] * 100
            if win_rate >= 70 and h['total_ret']/h['count'] > 0:
                score += min(4, win_rate / 20)
            elif win_rate < 50 and h['total_ret']/h['count'] < 0:
                score -= 3
    
    return max(0, min(100, round(score, 1))), famous_set


def main():
    print("=" * 100)
    print("🔥 龙虎榜评分系统 vs 硬性过滤 终极对比回测")
    print("=" * 100)
    
    # 加载数据
    conn = sqlite3.connect(LHB_DB)
    conn.row_factory = sqlite3.Row
    lrows = conn.execute("SELECT d.date,d.code,d.direction,d.dealer,d.net,l.name as sname FROM lhb_detail d JOIN lhb_list l ON d.date=l.date AND d.code=l.code ORDER BY d.date,d.code").fetchall()
    conn.close()
    filtered = [r for r in lrows if is_valid(r['code'], r['sname'] or '')]
    
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    krows = conn.execute("SELECT code,date,open,close,high,low,volume FROM kline ORDER BY code,date").fetchall()
    conn.close()
    
    kb = defaultdict(list)
    for r in krows:
        if is_valid(r['code']): kb[r['code']].append(r)
    ki = {}
    for code, arr in kb.items():
        ki[code] = {arr[i]['date']: i for i in range(len(arr))}
    
    # 聚合信号
    signals = {}
    for r in filtered:
        if r['direction'] != 'buy': continue
        key = (r['date'], r['code'])
        if key not in signals:
            signals[key] = {'types': Counter(), 'dealers': [], 'net': 0, 'name': r['sname']}
        t = classify(r['dealer'])
        signals[key]['types'][t] += 1
        signals[key]['dealers'].append(r['dealer'])
        signals[key]['net'] += (r['net'] or 0)
    
    # 营业部历史
    dealer_histories = defaultdict(lambda: {'count': 0, 'win': 0, 'total_ret': 0.0})
    for (date, code), sig in sorted(signals.items()):
        arr = kb.get(code, [])
        if not arr: continue
        idx = ki.get(code, {}).get(date)
        if idx is None or idx < 1 or idx+1 >= len(arr): continue
        board = detect_board(arr, idx)
        if board < 1: continue
        k = arr[idx]; kp = arr[idx-1]; k1 = arr[idx+1]
        try: pc = kp['close']; op = k['open']
        except: continue
        if pc <= 0 or op <= 0: continue
        try: t1_cp = k1['close']
        except: continue
        if t1_cp <= 0: continue
        ret_oc = (t1_cp - op) / op * 100
        for d in set(sig['dealers']):
            t = classify(d)
            if t in ('游资', '量化', '机构'):
                dealer_histories[d]['count'] += 1
                if ret_oc > 0: dealer_histories[d]['win'] += 1
                dealer_histories[d]['total_ret'] += ret_oc
    
    # 构建所有信号的完整记录
    all_records = []
    for (date, code), sig in sorted(signals.items()):
        arr = kb.get(code, [])
        if not arr: continue
        idx = ki.get(code, {}).get(date)
        if idx is None or idx < 1 or idx+1 >= len(arr): continue
        board = detect_board(arr, idx)
        if board < 1: continue
        k = arr[idx]; kp = arr[idx-1]; k1 = arr[idx+1]
        try: pc = kp['close']; op = k['open']; cp = k['close']
        except: continue
        if pc <= 0 or op <= 0: continue
        t_open = (op - pc) / pc * 100
        t_low = (k['low'] - pc) / pc * 100
        t_vol_r = k['volume'] / max(kp['volume'] or 1, 1)
        is_yziban = t_open >= 9.5 and t_low >= 9.0
        try: t1_op = k1['open']; t1_cp = k1['close']; t1_hp = k1['high']
        except: continue
        if t1_op <= 0 or t1_cp <= 0: continue
        ret_oc = (t1_cp - op) / op * 100
        ret_oo = (t1_op - op) / op * 100
        ret_oh = (t1_hp - op) / op * 100
        
        score, famous_set = compute_score(sig, board, dealer_histories)
        
        yz = sig['types'].get('游资', 0)
        sh = sig['types'].get('散户', 0)
        has_famous = any(d in FAMOUS_DEALERS for d in sig['dealers'])
        
        all_records.append({
            'date': date, 'code': code, 'name': sig['name'],
            'board': board, 't_open': t_open, 't_vol_r': t_vol_r,
            'is_yziban': is_yziban,
            'score': score,
            'famous_set': famous_set,
            'has_famous': has_famous,
            'yz': yz, 'sh': sh,
            'ret_oc': ret_oc, 'ret_oo': ret_oo, 'ret_oh': ret_oh,
            't1_open': (t1_op - pc) / pc * 100,
        })
    
    # ============================================================
    # 方案A：硬性过滤
    # 条件：首板 + 有游资 + 竞价-3~7% + 无散户 + 量比>=0.5 + 知名游资 + 非一字板
    # ============================================================
    scheme_a_records = [r for r in all_records if (
        r['yz'] >= 1 and
        not r['is_yziban'] and
        r['t_open'] < 7 and
        r['t_open'] > -3 and
        r['board'] == 1 and
        r['sh'] == 0 and
        r['has_famous']
    )]
    
    # ============================================================
    # 方案B：评分系统（无乘子）
    # 条件：评分>=某个阈值，非一字板
    # 多个阈值对比
    # ============================================================
    scheme_b_thresholds = {
        '评分≥80(S级)': 80,
        '评分≥65(A级)': 65,
        '评分≥50(B级)': 50,
    }
    
    def analyze_scheme(name, records, compute_positions=False):
        """分析一种方案的各项指标"""
        if not records:
            return {'name': name, 'count': 0}
        
        n = len(records)
        avg_oc = sum(r['ret_oc'] for r in records) / n
        avg_oo = sum(r['ret_oo'] for r in records) / n
        avg_oh = sum(r['ret_oh'] for r in records) / n
        win = sum(1 for r in records if r['ret_oc'] > 0) / n * 100
        big_win = sum(1 for r in records if r['ret_oc'] > 10) / n * 100
        big_loss = sum(1 for r in records if r['ret_oc'] < -3) / n * 100
        loss_5 = sum(1 for r in records if r['ret_oc'] < -5) / n * 100
        max_gain = max(r['ret_oc'] for r in records)
        max_loss = min(r['ret_oc'] for r in records)
        
        # 简易夏普比：平均收益 / 收益标准差
        returns = [r['ret_oc'] for r in records]
        avg_r = sum(returns) / n
        var_r = sum((r - avg_r) ** 2 for r in returns) / n
        std_r = math.sqrt(var_r)
        sharpe = avg_r / std_r if std_r > 0 else 0
        
        # 每日信号数分布
        date_counts = Counter(r['date'] for r in records)
        trade_days = len(date_counts)
        avg_per_day = n / trade_days if trade_days > 0 else 0
        
        # 日收益序列（假设每天平均分配仓位）
        daily_rets = []
        for d, cnt in sorted(date_counts.items()):
            day_recs = [r for r in records if r['date'] == d]
            if compute_positions:
                # 按评分分仓位
                total_pos = 0
                total_ret = 0
                for r in day_recs:
                    if r['score'] >= 80:
                        pos = 0.30
                    elif r['score'] >= 65:
                        pos = 0.20
                    elif r['score'] >= 50:
                        pos = 0.10
                    else:
                        pos = 0.05
                    total_pos += pos
                    total_ret += r['ret_oc'] * pos
                if total_pos > 1.0:  # 仓位上限1.0
                    total_ret = total_ret / total_pos
                    total_pos = 1.0
                daily_rets.append(total_ret / total_pos if total_pos > 0 else 0)
            else:
                avg_day_ret = sum(r['ret_oc'] for r in day_recs) / len(day_recs)
                daily_rets.append(avg_day_ret)
        
        # 仓位估算（基于每日信号数）
        if compute_positions:
            cum_ret = [r['ret_oc'] for r in records]
        else:
            cum_ret = daily_rets
        
        total_return = sum(cum_ret) / len(cum_ret) * len(cum_ret) if cum_ret else 0
        
        # 最大回撤（基于日收益序列）
        if daily_rets:
            peak = 0
            max_dd = 0
            cum = 0
            for r in daily_rets:
                cum += r
                if cum > peak:
                    peak = cum
                dd = peak - cum
                if dd > max_dd:
                    max_dd = dd
        
        # T+1卖出分析
        sell_analysis = {}
        for label, lo, hi in [('<-3%', -999, -3), ('-3~0%', -3, 0), ('0~3%', 0, 3), ('3~7%', 3, 7), ('>7%', 7, 999)]:
            recs = [r for r in records if lo <= r['t1_open'] < hi]
            if len(recs) < 3: continue
            s_oo = sum(r['ret_oo'] for r in recs)/len(recs)
            s_oc = sum(r['ret_oc'] for r in recs)/len(recs)
            s_oh = sum(r['ret_oh'] for r in recs)/len(recs)
            better = "开盘卖" if s_oo > s_oc else "等冲高" if s_oh > s_oc else "扛盘尾"
            sell_analysis[label] = {'开盘卖': round(s_oo,2), '盘尾卖': round(s_oc,2), '最高卖': round(s_oh,2), '建议': better}
        
        return {
            'name': name,
            'count': n,
            'trade_days': trade_days,
            'avg_per_day': round(avg_per_day, 2),
            'avg_oc': round(avg_oc, 2),
            'avg_oo': round(avg_oo, 2),
            'avg_oh': round(avg_oh, 2),
            'win_rate': round(win, 1),
            'big_win_rate': round(big_win, 1),
            'big_loss_rate': round(big_loss, 1),
            'loss_5_rate': round(loss_5, 1),
            'max_gain': round(max_gain, 2),
            'max_loss': round(max_loss, 2),
            'sharpe': round(sharpe, 3),
            'max_drawdown': round(max_dd, 2) if daily_rets else 0,
            'sell_analysis': sell_analysis,
            'records': records,
        }
    
    # ============================================================
    # 分析
    # ============================================================
    scheme_a = analyze_scheme('A: 硬性过滤', scheme_a_records, compute_positions=False)
    
    scheme_b_results = []
    for label, th in scheme_b_thresholds.items():
        recs = [r for r in all_records if r['score'] >= th and not r['is_yziban']]
        scheme_b_results.append(analyze_scheme(f'B: {label}', recs, compute_positions=True))
    
    # 也加一个"评分≥70"的版本
    recs70 = [r for r in all_records if r['score'] >= 70 and not r['is_yziban']]
    scheme_b_results.append(analyze_scheme('B: 评分≥70', recs70, compute_positions=True))
    
    # ============================================================
    # 输出对比
    # ============================================================
    print(f"\n{'='*100}")
    print("📊 核心指标对比")
    print(f"{'='*100}")
    print(f"{'方案':30s} {'样本':>5s} {'天数':>5s} {'日均':>5s} {'T+1':>8s} {'胜率':>6s} {'大赚>10%':>8s} {'大亏>3%':>8s} {'大亏>5%':>8s} {'夏普':>6s} {'最大回撤':>8s}")
    print("-" * 100)
    
    print(f"\n{'方案A-硬性过滤':-^98s}")
    a = scheme_a
    print(f"{'A: 硬性过滤(全条件)':30s} {a['count']:5d} {a['trade_days']:5d} {a['avg_per_day']:5.1f} {a['avg_oc']:+7.2f}% {a['win_rate']:5.1f}% {a['big_win_rate']:7.1f}% {a['big_loss_rate']:7.1f}% {a['loss_5_rate']:7.1f}% {a['sharpe']:6.3f} {a['max_drawdown']:+7.2f}%")
    
    print(f"\n{'方案B-评分系统':-^98s}")
    for b in scheme_b_results:
        print(f"{b['name']:30s} {b['count']:5d} {b['trade_days']:5d} {b['avg_per_day']:5.1f} {b['avg_oc']:+7.2f}% {b['win_rate']:5.1f}% {b['big_win_rate']:7.1f}% {b['big_loss_rate']:7.1f}% {b['loss_5_rate']:7.1f}% {b['sharpe']:6.3f} {b['max_drawdown']:+7.2f}%")
    
    # ============================================================
    # 详细对比：重叠vs特有信号
    # ============================================================
    a_set = set((r['date'], r['code']) for r in scheme_a_records)
    
    print(f"\n{'='*100}")
    print("🔍 信号重叠分析")
    print(f"{'='*100}")
    
    for b in scheme_b_results:
        b_set = set((r['date'], r['code']) for r in b['records'])
        overlap = a_set & b_set
        only_a = a_set - b_set
        only_b = b_set - a_set
        
        overlap_recs = [r for r in scheme_a_records if (r['date'], r['code']) in overlap]
        only_a_recs = [r for r in scheme_a_records if (r['date'], r['code']) in only_a]
        only_b_recs = [r for r in b['records'] if (r['date'], r['code']) in only_b]
        
        oa_avg = sum(r['ret_oc'] for r in overlap_recs)/len(overlap_recs) if overlap_recs else 0
        oa_win = sum(1 for r in overlap_recs if r['ret_oc']>0)/len(overlap_recs)*100 if overlap_recs else 0
        onlya_avg = sum(r['ret_oc'] for r in only_a_recs)/len(only_a_recs) if only_a_recs else 0
        onlya_win = sum(1 for r in only_a_recs if r['ret_oc']>0)/len(only_a_recs)*100 if only_a_recs else 0
        onlyb_avg = sum(r['ret_oc'] for r in only_b_recs)/len(only_b_recs) if only_b_recs else 0
        onlyb_win = sum(1 for r in only_b_recs if r['ret_oc']>0)/len(only_b_recs)*100 if only_b_recs else 0
        
        print(f"\n  {b['name']}:")
        print(f"    共同信号: {len(overlap)}次  {oa_avg:+.2f}%  胜{oa_win:.1f}%")
        print(f"    A特有(硬性过滤多买的): {len(only_a_recs)}次  {onlya_avg:+.2f}%  胜{onlya_win:.1f}%")
        print(f"    B特有(评分多买的):    {len(only_b_recs)}次  {onlyb_avg:+.2f}%  胜{onlyb_win:.1f}%")
    
    # ============================================================
    # 方案A的漏网之鱼：哪些亏钱/赚钱？
    # ============================================================
    print(f"\n{'='*100}")
    print("🔬 硬性过滤漏掉的好票 vs 逃过的亏票")
    print(f"{'='*100}")
    
    # A没买但评分≥65的（漏掉的好票）
    b65_set = set((r['date'], r['code']) for r in [r for r in all_records if r['score'] >= 65 and not r['is_yziban']])
    missed = a_set - b65_set  # A没买，但评分也没≥65... 
    # 更准确：A没买的所有信号中，评分≥65的就是"漏掉的好票"
    missed_good = [(r['date'], r['code'], r['score'], r['ret_oc'], r['board'], r['famous_set']) for r in all_records 
                   if (r['date'], r['code']) not in a_set and r['score'] >= 65 and not r['is_yziban']]
    # A没买的所有信号中，T+1大亏的
    missed_bad = [(r['date'], r['code'], r['score'], r['ret_oc'], r['board'], r['famous_set']) for r in all_records 
                  if (r['date'], r['code']) in a_set and r['ret_oc'] < -3]
    
    missed_good.sort(key=lambda x: -x[3])
    missed_bad.sort(key=lambda x: x[3])
    
    print(f"\n  📈 硬性过滤**漏掉的好票**（A没买但评分≥65，T+1大涨的TOP10）:")
    print(f"  {'日期':12s} {'代码':8s} {'名称':8s} {'评分':>4s} {'板':>3s} {'T+1':>8s} {'原因':30s}")
    print(f"  {'-'*75}")
    for m in missed_good[:10]:
        date, code, score, ret, board, famous = m
        name = next((r['name'] for r in all_records if r['date']==date and r['code']==code), '')
        # 为什么没通过
        r = next((r for r in all_records if r['date']==date and r['code']==code), None)
        reasons = []
        if r:
            if r['board'] != 1: reasons.append(f"非首板({r['board']}板)")
            if r['sh'] > 0: reasons.append(f"有散户({r['sh']})")
            if r['t_vol_r'] < 0.5: reasons.append(f"量比<{r['t_vol_r']:.1f}")
            if not r['has_famous']: reasons.append("无知名游资")
            if r['t_open'] >= 7: reasons.append(f"高开{r['t_open']:.1f}%")
            if r['t_open'] <= -3: reasons.append(f"低开{r['t_open']:.1f}%")
        reason_str = ','.join(reasons[:3]) if reasons else '评分高但其他条件不符'
        print(f"  {date:12s} {code:8s} {(name or '')[:6]:6s} {score:4.0f} {board:3d} {ret:+7.2f}% {reason_str:30s}")
    
    print(f"\n  📉 硬性过滤**选到的亏票**（A买但T+1大亏>3%）:")
    print(f"  {'日期':12s} {'代码':8s} {'名称':8s} {'评分':>4s} {'板':>3s} {'T+1':>8s}")
    print(f"  {'-'*55}")
    for m in missed_bad[:10]:
        date, code, score, ret, board, famous = m
        name = next((r['name'] for r in all_records if r['date']==date and r['code']==code), '')
        print(f"  {date:12s} {code:8s} {(name or '')[:6]:6s} {score:4.0f} {board:3d} {ret:+7.2f}%")
    
    # ============================================================
    # 仓位模拟对比
    # ============================================================
    print(f"\n{'='*100}")
    print("💰 仓位占用模拟对比")
    print(f"{'='*100}")
    
    print(f"\n  方案A（硬性过滤）: 每个信号等仓位")
    print(f"    总{scheme_a['count']}次买入, {scheme_a['trade_days']}个交易日")
    max_a = max(Counter(r['date'] for r in scheme_a_records).values())
    print(f"    单日最多{max_a}只, 日均{scheme_a['avg_per_day']:.1f}只")
    print(f"    同等仓位, 每个信号T+1平均{scheme_a['avg_oc']:+.2f}%")
    
    print(f"\n  方案B（评分系统）: 差异化仓位")
    for b in scheme_b_results:
        print(f"\n    {b['name']}:")
        # 计算实际仓位分布
        pos_dist = Counter()
        for r in b['records']:
            if r['score'] >= 80: pos_dist['S(30%)'] += 1
            elif r['score'] >= 65: pos_dist['A(20%)'] += 1
            elif r['score'] >= 50: pos_dist['B(10%)'] += 1
            else: pos_dist['C(5%)'] += 1
        for p, cnt in sorted(pos_dist.items()):
            print(f"      {p}: {cnt}次 ({cnt/b['count']*100:.0f}%)")
        print(f"      T+1平均{b['avg_oc']:+.2f}% 胜率{b['win_rate']:.1f}% 大亏率{b['big_loss_rate']:.1f}%")
    
    # ============================================================
    # 最终结论
    # ============================================================
    print(f"\n{'='*100}")
    print("🏆 最终对比结论")
    print(f"{'='*100}")
    
    # 选最佳B方案
    best_b = max(scheme_b_results, key=lambda x: x['avg_oc'] * x['win_rate']/100 * (1 - x['big_loss_rate']/100))
    
    print(f"\n  方案A（硬性过滤）:")
    print(f"    ✅ 优点: 规则简单明了, {scheme_a['count']}次信号/日均{scheme_a['avg_per_day']:.1f}只")
    print(f"    ❌ 缺点: 条件苛刻漏掉好票, 二板/三板完全不碰")
    print(f"    📊 结果: T+1 {scheme_a['avg_oc']:+.2f}%  胜率{scheme_a['win_rate']:.1f}%  大亏率{scheme_a['big_loss_rate']:.1f}%")
    
    print(f"\n  方案B（评分系统-最优: {best_b['name']}）:")
    print(f"    ✅ 优点: 灵活覆盖所有板数, 差异化仓位, {best_b['count']}次信号/日均{best_b['avg_per_day']:.1f}只")
    print(f"    ✅ 夏普比{best_b['sharpe']} vs A的{scheme_a['sharpe']}")
    print(f"    📊 结果: T+1 {best_b['avg_oc']:+.2f}%  胜率{best_b['win_rate']:.1f}%  大亏率{best_b['big_loss_rate']:.1f}%")
    
    # 对比差值
    diff_ret = best_b['avg_oc'] - scheme_a['avg_oc']
    diff_win = best_b['win_rate'] - scheme_a['win_rate']
    diff_loss = best_b['big_loss_rate'] - scheme_a['big_loss_rate']
    print(f"\n  对比（B-A）:")
    print(f"    收益: {diff_ret:+.2f}%")
    print(f"    胜率: {diff_win:+.1f}%")
    print(f"    大亏率: {diff_loss:+.1f}%")
    
    if best_b['count'] >= scheme_a['count']:
        print(f"    信号数: +{best_b['count'] - scheme_a['count']}次 (多{best_b['count']/scheme_a['count']*100-100:.0f}%)")
    else:
        print(f"    信号数: {best_b['count'] - scheme_a['count']}次 (少{100-best_b['count']/scheme_a['count']*100:.0f}%)")
    
    print(f"\n{'='*100}")
    print("✅ 对比完成!")
    print(f"{'='*100}")


if __name__ == '__main__':
    main()
