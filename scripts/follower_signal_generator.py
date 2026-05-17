#!/usr/bin/env python3
"""跟风信号生成器（follower_signal_generator）
   基于板块轮动 + 龙头涨停信号，生成跟风买入候选
   从bundle读取:
     - sector_ranking (板块排名)
     - sector_micro (微观信号，龙头竞价/跟风买入)
     - buy_candidates (买入候选)
   输出到 /home/ubuntu/V2board/data/follower_signals.json
   格式: {stocks:[{code,name,reason,sector,confidence,expected_return}], summary}
"""

import json, os, sys
from datetime import datetime

DATA_DIR = os.path.expanduser('/home/ubuntu/V2board/data')
BUNDLE_PATH = os.path.expanduser('/home/ubuntu/V2board/dashboard_bundle.json')
OUTPUT_PATH = os.path.join(DATA_DIR, 'follower_signals.json')


def safe_read_json(path):
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except:
        return None
    return None


def generate_follower_signals():
    """基于板块轮动+龙头涨停信号生成跟风买入候选"""

    # 1. 尝试从bundle读取最新数据
    bundle = safe_read_json(BUNDLE_PATH)

    sector_ranking = []
    sector_micro = {}
    buy_candidates = []

    if bundle:
        sector_ranking = bundle.get('sector_ranking', [])
        sector_micro = bundle.get('sector_micro', {})
        buy_candidates = bundle.get('buy_candidates', [])

    # 2. 如果bundle没有，从独立文件读取
    if not sector_ranking:
        sc_path = os.path.join(DATA_DIR, 'sector_cycle_signals.json')
        sc = safe_read_json(sc_path)
        if sc:
            sectors = sc.get('sectors', [])
            if sectors and isinstance(sectors, list):
                for sec in sectors[:12]:
                    sector_ranking.append({
                        'name': sec.get('name', ''),
                        'change': sec.get('today_change', 0),
                        'limits': sec.get('today_limits', 0),
                        'streak': sec.get('streak', 0),
                        'status': sec.get('status', ''),
                    })

    if not sector_micro:
        micro_path = os.path.expanduser('/home/ubuntu/V2board/sector_micro_signals.json')
        sm = safe_read_json(micro_path)
        if sm:
            sector_micro = sm

    if not buy_candidates:
        # 从lhb_scoring cache读
        lhb_path = os.path.join(DATA_DIR, 'lhb_scoring_cache.json')
        lhb = safe_read_json(lhb_path)
        if lhb:
            ranked = lhb.get('ranked', [])
            for r in ranked[:10]:
                buy_candidates.append({
                    'code': r.get('code', ''),
                    'name': r.get('name', ''),
                    'score': r.get('score', 0),
                    'tier': r.get('tier', ''),
                })

    signals = []
    summary_parts = []
    now = datetime.now()

    # ============= 跟风逻辑信号生成 =============

    # 信号1: 板块轮动龙头跟风
    # 从sector_ranking找涨停数最多+涨跌幅最大的板块
    top_sectors = sorted(
        [s for s in sector_ranking if s.get('limits', 0) > 0],
        key=lambda x: (x.get('limits', 0), x.get('change', 0)),
        reverse=True
    )[:5]

    # 从sector_micro取跟风信号
    micro_sectors = {}
    if isinstance(sector_micro, dict):
        ms = sector_micro.get('sectors', sector_micro)
        if isinstance(ms, dict):
            micro_sectors = ms
        elif isinstance(ms, list):
            for item in ms:
                if isinstance(item, dict) and 'name' in item:
                    micro_sectors[item.get('name', '')] = item

    # 构建板块→候选股票映射
    sector_stocks_pool = {}
    for s in micro_sectors.values():
        if isinstance(s, dict):
            sname = s.get('name', s.get('meta_signal', ''))
            if not sname and hasattr(s, 'keys'):
                # 可能是直接用板块名作为key
                continue
            stocks = s.get('stocks', [])
            if stocks and isinstance(stocks, list):
                # 提取非龙头跟风标的（第2~3只）
                leader_code = ''
                leader_info = s.get('leader', {})
                if isinstance(leader_info, dict):
                    leader_code = leader_info.get('code', '')
                
                followers = [st for st in stocks if st.get('code', '') != leader_code]
                sector_stocks_pool[sname] = {
                    'leader_code': leader_code,
                    'leader_name': leader_info.get('name', '') if isinstance(leader_info, dict) else '',
                    'followers': followers,
                    'score': s.get('score', 0),
                    'meta_signal': s.get('meta_signal', ''),
                }

    # 处理top板块的跟风机会
    for sec in top_sectors:
        sname = sec.get('name', '')
        limits = sec.get('limits', 0)
        change = sec.get('change', 0)

        # 找到该板块的微信号
        sdata = sector_stocks_pool.get(sname, {})
        followers = sdata.get('followers', [])

        if limits >= 2 and len(followers) >= 2:
            # 板块有2个以上涨停=强势，跟风龙二龙三
            for i, stk in enumerate(followers[:3]):
                code = stk.get('code', '')
                stk_name = stk.get('name', '')
                change_pct = stk.get('change_pct', 0)
                amt_wan = stk.get('amount_wan', 0)

                if not code:
                    continue

                # 置信度：基于板块涨停数
                confidence = min(limits / 5.0, 1.0) * 0.7 + 0.15
                pos = i + 2  # 龙二、龙三、龙四
                expected_ret = limits * 0.8 + abs(change_pct) * 0.2

                signal = {
                    'code': code,
                    'name': stk_name,
                    'reason': f'板块"{sname}"涨停{limits}只，跟风第{pos}候选',
                    'sector': sname,
                    'rank_in_sector': pos,
                    'confidence': round(confidence, 2),
                    'expected_return': round(min(expected_ret, 15.0), 1),
                    'source': 'sector_limit_surge',
                    'current_change_pct': round(change_pct, 2),
                    'amount_wan': round(amt_wan, 0) if amt_wan else 0,
                }
                signals.append(signal)

    # 信号2: buy_candidates中的高评分标的作为跟风参考
    for bc in buy_candidates[:8]:
        code = bc.get('code', '')
        name = bc.get('name', '')
        score = bc.get('score', 0)
        tier = bc.get('tier', '')

        if not code:
            continue
        # 跳过已经在信号列表中的
        if any(s['code'] == code for s in signals):
            continue

        confidence = min(score / 100.0, 0.95)
        expected_ret = score * 0.08 + 2.0

        signal = {
            'code': code,
            'name': name,
            'reason': f'买入候选评分{score}分，层级{tier}，适合跟风',
            'sector': tier,
            'rank_in_sector': 0,
            'confidence': round(confidence, 2),
            'expected_return': round(min(expected_ret, 12.0), 1),
            'source': 'buy_candidates_scoring',
            'current_change_pct': 0,
            'amount_wan': 0,
        }
        signals.append(signal)

    # 信号3: 从sector_micro找有"跟风多头"信号的板块
    if isinstance(sector_micro, dict):
        sectors_dict = sector_micro.get('sectors', sector_micro)
        if isinstance(sectors_dict, dict):
            for sname, sdata in sectors_dict.items():
                if not isinstance(sdata, dict):
                    continue
                signals_list = sdata.get('micro_signals', [])
                if not isinstance(signals_list, list):
                    continue
                
                has_follower_signal = False
                for sig in signals_list:
                    if isinstance(sig, list) and len(sig) >= 2:
                        sig_name = str(sig[0])
                        sig_score = sig[1] if len(sig) > 1 else 0
                        if '跟风' in sig_name and sig_score >= 0:
                            has_follower_signal = True
                            break
                
                if has_follower_signal:
                    stocks = sdata.get('stocks', [])
                    if isinstance(stocks, list):
                        for i, stk in enumerate(stocks[:4]):
                            code = stk.get('code', '')
                            stk_name = stk.get('name', '')
                            if not code or any(s['code'] == code for s in signals):
                                continue
                            signal = {
                                'code': code,
                                'name': stk_name,
                                'reason': f'板块"{sname}"跟风信号积极',
                                'sector': sname,
                                'rank_in_sector': i + 1,
                                'confidence': 0.55,
                                'expected_return': 3.5,
                                'source': 'sector_micro_follower',
                                'current_change_pct': round(stk.get('change_pct', 0), 2),
                                'amount_wan': round(stk.get('amount_wan', 0), 0) if stk.get('amount_wan') else 0,
                            }
                            signals.append(signal)

    # 信号4: 如果完全没有找到任何信号，生成默认的高胜率策略跟风推荐
    if len(signals) == 0:
        # 基于历史回测结论的默认跟风信号
        default_signals = [
            {
                'code': 'N/A',
                'name': '总龙头打板策略',
                'reason': 'M02总龙头打板——唯一最高板≥3板，历史胜率66.7%，跟风标的开盘买入T+1平均+2.72%',
                'sector': '总龙头',
                'rank_in_sector': 1,
                'confidence': 0.67,
                'expected_return': 2.7,
                'source': 'strategy_memory_M02',
                'current_change_pct': 0,
                'amount_wan': 0,
            },
            {
                'code': 'N/A',
                'name': '连板缩量策略',
                'reason': 'M08连板缩量·2板——2板+缩量<0.7+10:00前封板，历史胜率89%，跟风T+1平均+7.3%',
                'sector': '连板接力',
                'rank_in_sector': 1,
                'confidence': 0.89,
                'expected_return': 7.3,
                'source': 'strategy_memory_M08',
                'current_change_pct': 0,
                'amount_wan': 0,
            },
            {
                'code': 'N/A',
                'name': '隔夜溢价策略',
                'reason': 'M01隔夜溢价·极品——缩量<0.3+竞价换手<0.5%，历史胜率96.6%，T+1平均+7.77%',
                'sector': '隔夜溢价',
                'rank_in_sector': 1,
                'confidence': 0.97,
                'expected_return': 7.8,
                'source': 'strategy_memory_M01',
                'current_change_pct': 0,
                'amount_wan': 0,
            },
        ]
        signals = default_signals
        summary = '⚠️ 无实时数据，使用历史回测的最佳跟风策略作为参考'
    else:
        summary = (f'基于{len(sector_ranking)}个板块排名和{len(buy_candidates)}个买入候选，'
                   f'生成{len(signals)}个跟风信号')

    # 按置信度排序
    signals.sort(key=lambda x: x.get('confidence', 0), reverse=True)

    # 限数量
    signals = signals[:12]

    result = {
        'timestamp': now.strftime('%Y-%m-%d %H:%M:%S'),
        'count': len(signals),
        'summary': summary,
        'stocks': signals,
    }

    return result


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    result = generate_follower_signals()

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    count = result['count']
    ts = result['timestamp']
    print(f"[follower_signals] {ts} | 生成{count}个跟风信号", flush=True)


if __name__ == '__main__':
    main()
