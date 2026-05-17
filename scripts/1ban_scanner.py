#!/usr/bin/env python3
"""
📈 1板（首板涨停）实时扫描+次日卖出决策 v1.0

买入决策（T日盘中）：
  - 三资金合力评分（已有）
  - 板块热度
  - 前日涨幅
  
卖出决策（T+1日参考）：
  - 封单额（封单大→持有，封单小→开盘卖）
  - 封单额/成交额比
  - 封单变化趋势

用法：
  python3 scripts/1ban_scanner.py                # 全市场1板扫描
  python3 scripts/1ban_scanner.py --codes 603986  # 指定个股
  python3 scripts/1ban_scanner.py --report        # 输出卖出建议报告
"""

import os, sys, json, sqlite3
from datetime import datetime
from collections import defaultdict

BASE = "/home/ubuntu/astock"
sys.path.insert(0, BASE)

from scripts.three_funds_scan import tencent_quote, SECTORS, score_stock
from scripts.ban_order_collector import collect_once, CORE_CODES, format_report

DB = os.path.join(BASE, "data", "ban_order.db")


def load_today_ban_orders():
    """从ban_order.db加载今天的封单数据"""
    date = datetime.now().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        SELECT code, name, MAX(timestamp), ban_amount_wan, ban_volume, is_limit_up, change_pct
        FROM ban_order WHERE date = ? 
        GROUP BY code ORDER BY ban_amount_wan DESC
    """, (date,))
    rows = c.fetchall()
    conn.close()
    return [{'code': r[0], 'name': r[1], 'time': r[2], 
             'ban_amt': r[3], 'ban_vol': r[4],
             'is_limit_up': r[5], 'chg': r[6]} for r in rows]


def scan_today_1ban():
    """今日1板扫描（综合三资金合力+封单额）"""
    # 1. 获取所有核心标的三资金合力
    all_codes = list(dict.fromkeys(
        c for s in SECTORS.values() for c in s['codes']
    ))
    rt_data = tencent_quote(all_codes)
    
    # 2. 加载今天的封单数据
    ban_data = {}
    ban_records = load_today_ban_orders()
    for r in ban_records:
        ban_data[r['code']] = r
    
    results = []
    for code in all_codes:
        rt = rt_data.get(code)
        if not rt:
            continue
        
        chg = rt.get('change_pct', 0)
        sc = score_stock(rt)
        ban = ban_data.get(code, {})
        
        # 涨停判断（用腾讯行情和东方财富双重确认）
        is_limit_up = rt.get('is_limit_up', False) or ban.get('is_limit_up', 0)
        
        results.append({
            'code': code,
            'name': rt['name'],
            'price': rt['price'],
            'change_pct': chg,
            'is_limit_up': is_limit_up,
            'score': sc['total'],
            'score_i': sc['institutional']['score'],
            'score_q': sc['quantitative']['score'],
            'score_h': sc['hot_money']['score'],
            'rti': sc.get('retail', {}).get('rti', 0),
            'rti_level': sc.get('retail', {}).get('level', ''),
            'ban_amount_wan': ban.get('ban_amt', 0),
            'ban_volume': ban.get('ban_vol', 0),
        })
    
    # 排序：涨停的按封单额排，未涨停的按涨幅排
    limit = [r for r in results if r['is_limit_up']]
    near = [r for r in results if 7 <= r['change_pct'] < 9.5]
    other = [r for r in results if r['change_pct'] < 7]
    
    limit.sort(key=lambda x: -x['ban_amount_wan'])
    near.sort(key=lambda x: -x['change_pct'])
    
    return limit, near, other


def sell_advice(ban_amt, score, rti, is_limit_up):
    """
    次日卖出建议（基于封单额+三资金合力+RTI）
    
    封单额分级：
      >5亿 → 强封，次日大概率高开/连板
      1-5亿 → 中封，次日有溢价
      0.1-1亿 → 弱封，次日可能低开
      <0.1亿 → 微量封，次日危险
    """
    advice = []
    
    if not is_limit_up:
        advice.append('未涨停，不适用')
        return advice
    
    # 封单额判断
    if ban_amt >= 50000:
        advice.append('🔒封单强(>5亿) → 可持有博连板')
    elif ban_amt >= 10000:
        advice.append('🔒封单中(1-5亿) → T+1冲高卖30%，T+2卖70%')
    elif ban_amt >= 1000:
        advice.append('🔓封单弱(0.1-1亿) → 竞价>3%持有，否则冲高卖')
    else:
        advice.append('🔓封单极少(<0.1亿) → 开盘直接卖')
    
    # 三资金合力判断
    if score >= 75:
        advice.append(f'合力{score}分 → 资金认可，加分')
    elif score >= 60:
        advice.append(f'合力{score}分 → 中等')
    else:
        advice.append(f'合力{score}分 → 资金分歧，减分')
    
    # RTI判断
    rti_advice = ''
    if rti >= 30:
        rti_advice = 'RTI={:+d} 散户接盘⚠️ → 卖出优先'.format(rti)
    elif rti <= -30:
        rti_advice = 'RTI={:+d} 散户恐慌📗 → 可持有'.format(rti)
    if rti_advice:
        advice.append(rti_advice)
    
    return advice


def format_1ban_report(limit, near, other):
    """格式化1板扫描报告"""
    now = datetime.now().strftime('%H:%M')
    lines = []
    
    lines.append(f"📈 **1板实时扫描 — 盘中监控**")
    lines.append(f"   ⏰ {now} | 腾讯行情+东方财富封单")
    lines.append("")
    
    if limit:
        lines.append(f"{'─'*55}")
        lines.append(f"🚀 **涨停板 ({len(limit)}只)**")
        lines.append("")
        for r in limit[:5]:
            amt = r['ban_amount_wan']
            amt_s = f"封单{amt:>8.0f}万" if amt else "封单N/A"
            s = f"合力{r['score']}分"
            rti_s = f" RTI={r['rti']:+d}" if abs(r['rti']) >= 15 else ""
            lines.append(f"   {r['name']}({r['code']}) {amt_s} | {s}{rti_s}")
            
            # 卖出建议
            adv = sell_advice(amt, r['score'], r['rti'], True)
            if adv:
                lines.append(f"   {'  ▶ ' + adv[0]}")
        lines.append("")
    
    if near:
        lines.append(f"{'─'*55}")
        lines.append(f"🟡 **即将涨停 (涨7-9.5%, {len(near)}只)**")
        lines.append("")
        for r in near[:3]:
            chg_s = f"+{r['change_pct']:.1f}%"
            s = f"合力{r['score']}分"
            lines.append(f"   {r['name']}({r['code']}) {chg_s} | {s}")
        lines.append("")
    
    # 封单额排行榜
    if limit:
        lines.append(f"{'─'*55}")
        lines.append("📊 **封单额排行榜（涨停标的）**")
        lines.append("")
        for i, r in enumerate(limit[:5]):
            amt = r['ban_amount_wan']
            if amt >= 50000:
                level = '🔒强'
            elif amt >= 10000:
                level = '🔒中'
            elif amt >= 1000:
                level = '🔓弱'
            else:
                level = '🔓微'
            lines.append(f"   {i+1}. {level} {r['name']}({r['code']}) {amt:>8.0f}万 | 合力{r['score']}")
        lines.append("")
    
    # 次日卖出参考
    lines.append(f"{'─'*55}")
    lines.append("📋 **次日卖出操作指南（基于封单额）**")
    lines.append("")
    lines.append("   封单>5亿  → 🟢 持有博连板")
    lines.append("   封单1-5亿 → 🟡 T+1冲高卖30%+T+2卖70%")
    lines.append("   封单<1亿  → 🔴 竞价决定：>3%持有，否则卖")
    lines.append("   封单<0.1亿 → 🚨 开盘直接卖")
    lines.append("")
    lines.append("💡 封单数据来自东方财富，每30秒更新一次")
    
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='📈 1板实时扫描')
    parser.add_argument('--codes', type=str, help='指定个股')
    parser.add_argument('--report', action='store_true', help='输出完整报告')
    args = parser.parse_args()
    
    limit, near, other = scan_today_1ban()
    
    if args.codes:
        codes = [c.strip() for c in args.codes.split(',')]
        for name, lst in [('涨停', limit), ('近涨停', near)]:
            for r in lst:
                if r['code'] in codes:
                    print(f"{r['name']}({r['code']}) {r['change_pct']:+.1f}%")
                    print(f"  三资金合力: {r['score']}分 ({r['score_i']}+{r['score_q']}+{r['score_h']})")
                    print(f"  封单额: {r['ban_amount_wan']:.0f}万")
                    print(f"  RTI: {r['rti_level']}")
                    for a in sell_advice(r['ban_amount_wan'], r['score'], r['rti'], r['is_limit_up']):
                        print(f"  {a}")
    else:
        print(format_1ban_report(limit, near, other))
    
    # 运行封单采集
    try:
        collect_once(once=True)
    except:
        pass


if __name__ == '__main__':
    main()
