#!/usr/bin/env python3
"""
实盘席位追踪筛选器 v1
======================
盘中快速扫描龙虎榜cache中的个股
识别赚钱席位 vs 亏钱席位
输出买入/观望/回避信号

基于22维穿透回测数据
"""

import os, json, sqlite3
from datetime import datetime, timedelta

BASE = os.path.expanduser('~/astock')
DATA = os.path.join(BASE, 'data')
LHB_DB = os.path.join(DATA, 'lhb_cache.db')

# ============================================================
# 赚钱/亏钱席位数据库（基于22维回测）
# ============================================================

# 【大样本≥20笔】赚钱席位TOP15 — 按T+1收益排序
WIN_SEATS = {
    '国盛证券宁波天童南路': 4.90,
    '国泰海通上海静安区新闸路': 4.22,
    '国泰君安南京太平南路': 3.15,
    '中国银河大连黄河路': 2.85,
    '华泰证券总部': 2.45,
    '东吴证券上海西藏南路': 2.36,
    '国泰海通成都北一环路': 2.36,
    '国泰海通上海长宁区江苏路': 1.92,
    '国新证券北京中关村大街': 1.80,
    '国盛证券宁波桑田路': 1.77,
}

# 赚钱券商集团
WIN_BROKERS = {
    '国盛证券': 2.75,  # 宁波系
    '华泰证券': 2.06,
    '东莞证券': 1.52,
    '国新证券': 1.30,
    '国泰海通': 1.11,
}

# 【大样本≥20笔】亏钱席位TOP10
LOSE_SEATS = {
    '拉萨团结路第二': -1.76,
    '拉萨东环路第一': -1.45,
    '拉萨团结路第一': -1.01,
    '拉萨金融城南环路': -1.01,
    '山南香曲东路': -1.05,
    '广发证券郑州农业路': -1.49,
    '平安证券浙江分公司': -1.58,
}

# 亏钱券商集团
LOSE_BROKERS = {
    '东方财富(拉萨系)': -1.14,
    '广发证券': -1.49,
}

# 波动率聚类
VOLATILITY = {
    '低波动(最稳)': [],
    '激进(正常)': ['华泰总部', '开源西安', '国泰海通成都', '国泰海通武汉'],
    '赌博(高波动)': ['平安杭州曙光路', '国泰海通成都北一环路'],
}


# ============================================================
# 席位匹配器
# ============================================================

def score_dealer(dealer_name):
    """
    给营业部打分：
    正分=赚钱，负分=亏钱，0=中性
    """
    score = 0
    reasons = []
    
    # 检查赚钱席位
    for name, ret in WIN_SEATS.items():
        if name in dealer_name:
            score += ret * 10  # +49~18分
            reasons.append(f"赚钱席位+{ret}%")
            break
    
    # 检查亏钱席位
    for name, ret in LOSE_SEATS.items():
        if name in dealer_name:
            score += ret * 10  # -17.6~-10分
            reasons.append(f"亏钱席位{ret}%")
            break
    
    # 检查赚钱券商
    for broker, ret in WIN_BROKERS.items():
        if broker in dealer_name:
            score += ret * 5  # +13.75~+5.55分
            if not reasons:
                reasons.append(f"{broker}系+{ret}%")
            break
    
    # 检查亏钱券商
    for broker, ret in LOSE_BROKERS.items():
        if broker in dealer_name:
            score += ret * 5  # -5.7分
            if not reasons:
                reasons.append(f"{broker}系{ret}%")
            break
    
    # 波动率修正
    if '国泰海通成都北一环路' in dealer_name or '平安杭州曙光路' in dealer_name:
        score -= 10  # 赌博席位降级
        reasons.append("高波动赌博席位-10")
    
    # 特殊标记：拉萨
    if '拉萨' in dealer_name:
        score -= 10
        reasons.append("拉萨系(-10)")
    
    # 宁波帮加分
    if '宁波' in dealer_name and score <= 0:
        score += 15
        reasons.append("宁波帮+15")
    
    return score, reasons


def scan_lhb_cache():
    """
    扫描当前lhb_scoring_cache.json
    对每个候选股，分析其席位的赚钱/亏钱特征
    """
    cache_path = os.path.join(DATA, 'lhb_scoring_cache.json')
    if not os.path.exists(cache_path):
        print("⚠️ lhb_scoring_cache.json 不存在")
        return []
    
    with open(cache_path) as f:
        lhb = json.load(f)
    
    actionable = lhb.get('actionable', [])
    results = []
    
    for item in actionable:
        code = item.get('code', '')
        name = item.get('name', '')
        score = item.get('score', 50)
        tier = item.get('tier', 'C')
        details = item.get('details', '')
        
        # 提取席位数
        dealer_score = 0
        dealer_reasons = []
        
        if details:
            # details 格式: "游资40 | 板数20 | ..."
            details_str = details
        
        # 从龙虎榜数据库查真实席位
        conn = None
        try:
            conn = sqlite3.connect(f'file:{LHB_DB}?mode=ro', uri=True, timeout=5)
            date = lhb.get('date', datetime.now().strftime('%Y-%m-%d'))
            
            dealers = conn.execute("""
                SELECT DISTINCT d.dealer
                FROM lhb_detail d
                JOIN lhb_list l ON d.date=l.date AND d.code=l.code
                WHERE l.code=? AND l.date=?
            """, (code, date)).fetchall()
            
            for (dealer,) in dealers:
                dscore, dreasons = score_dealer(dealer)
                if dscore != 0:
                    dealer_score += dscore
                    dealer_reasons.extend(dreasons)
            
            if conn:
                conn.close()
        except:
            pass
        
        # 无席位数据时从item的字段推断
        if not dealer_reasons:
            jg = item.get('jg', 0)
            yz = item.get('yz', 0)
            ql = item.get('ql', 0)
            sh = item.get('sh', 0)
            
            # 机构+量化组合
            if jg > 0 and ql > 0:
                dealer_score += 15
                dealer_reasons.append('机构+量化+15')
            # 机构单打
            elif jg > 0:
                dealer_score += 5
                dealer_reasons.append('机构+5')
            # 拉萨散户
            if sh > 0:
                dealer_score -= 10
                dealer_reasons.append(f'散户(sh={sh})-10')
            # 游资+量化合力(无机构)=要回避
            if yz > 0 and ql > 0 and jg == 0:
                dealer_score -= 15
                dealer_reasons.append('游资+量化合力-15')
        
        # 综合判断
        adjusted_score = score + dealer_score
        
        if dealer_score > 15:
            action = '✅积极买入'
        elif dealer_score > 0:
            action = '✅可买'
        elif dealer_score > -10:
            action = '👀观望'
        else:
            action = '🚫回避'
        
        results.append({
            'code': code,
            'name': name,
            'orig_score': score,
            'dealer_score': dealer_score,
            'adjusted_score': adjusted_score,
            'tier': tier,
            'action': action,
            'reasons': dealer_reasons[:3],
            'seat_count': len(dealer_reasons),
        })
    
    # 排序
    results.sort(key=lambda x: x['adjusted_score'], reverse=True)
    return results


def print_report(results):
    """打印可读报告"""
    if not results:
        print("⚠️ 无候选股")
        return
    
    print(f"\n{'='*70}")
    print(f"📊 实盘席位追踪筛选手报")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")
    
    print(f"\n{'信号':<10} {'代码':<8} {'名称':<10} {'综合分':>6} {'席位分':>6} {'理由':<30}")
    print(f"{'─'*70}")
    
    buy_count = 0
    watch_count = 0
    avoid_count = 0
    
    for r in results:
        sig = r['action']
        if '买入' in sig or '可买' in sig:
            buy_count += 1
        elif '回避' in sig:
            avoid_count += 1
        else:
            watch_count += 1
        
        reasons = ' '.join(r['reasons'][:2])
        print(f"  {sig:<8} {r['code']:<6} {r['name']:<8} {r['adjusted_score']:>5}  {r['dealer_score']:>+5}  {reasons:<28}")
    
    print(f"\n{'─'*70}")
    print(f"  ✅可买: {buy_count} | 👀观望: {watch_count} | 🚫回避: {avoid_count}")


if __name__ == '__main__':
    results = scan_lhb_cache()
    print_report(results)
