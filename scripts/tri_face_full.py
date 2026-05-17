#!/usr/bin/env python3
"""
⏰ 三资金合力股 — 每日全链路交易流程 v2.0

精确到分钟的执行清单，覆盖7:30-21:00全部时间窗口。
集成三面量化 + 三资金合力 + 情绪周期 + 风控规则。

时间线：
  7:30-8:20 隔夜信息扫描
  8:20-8:40 市场情绪定位
  8:40-9:15 制定交易计划
  9:15-9:30 集合竞价交易
  9:30-10:00 早盘黄金30分钟
  10:00-11:30 处理持仓，不新开仓
  13:00-14:30 低吸做T
  14:30-15:00 尾盘清仓
  15:00-21:00 盘后复盘

用法：
  python3 scripts/tri_face_full.py               # 三面量化（默认）
  python3 scripts/tri_face_full.py --3funds       # 三资金合力
  python3 scripts/tri_face_full.py --code 603986  # 个股综合分析
  python3 scripts/tri_face_full.py --full         # 完整流程输出
  python3 scripts/tri_face_full.py --now          # 根据当前时间自动匹配流程段
"""

import os, sys, json
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# ============================================================
# 时间判断
# ============================================================

def get_current_slot() -> str:
    """返回当前时间所属的交易流程段"""
    now = datetime.now()
    h, m = now.hour, now.minute
    if now.weekday() >= 5:
        return 'weekend'
    if h < 7 or h >= 21:
        return 'off'
    if h == 7 or (h == 8 and m < 20):
        return 'prep_scan'        # 7:30-8:20 隔夜扫描
    if h == 8 and m < 40:
        return 'prep_emotion'     # 8:20-8:40 情绪定位
    if h == 8 or (h == 9 and m < 15):
        return 'prep_plan'        # 8:40-9:15 制定计划
    if h == 9 and m < 30:
        return 'auction'          # 9:15-9:30 集合竞价
    if h == 9 or (h == 10 and m < 30):
        return 'morning_golden'   # 9:30-10:00 黄金30分
    if h == 10 or h == 11:
        return 'morning_hold'     # 10:00-11:30 处理持仓
    if h == 13 or (h == 14 and m < 30):
        return 'afternoon_t'      # 13:00-14:30 低吸做T
    if h == 14 and m >= 30:
        return 'afternoon_close'  # 14:30-15:00 尾盘清仓
    if h == 15 or h == 16 or h == 17 or h == 18 or h == 19 or h == 20:
        return 'review'           # 15:00-21:00 复盘
    if h == 12:
        return 'lunch'
    return 'off'


# ============================================================
# 全流程执行
# ============================================================

def run_prep_scan() -> str:
    """7:30-8:20 隔夜信息全扫描"""
    from scripts.three_funds_data import (
        get_index_snapshot, get_market_stats, get_limit_up_list,
        get_sector_rank, get_concept_board_rank, get_dragon_tiger
    )
    lines = []
    lines.append("🌙 **【7:30-8:20】隔夜信息全扫描**")
    lines.append("")

    # 外围市场
    idx = get_index_snapshot()
    if idx:
        for k, v in idx.items():
            lines.append(f"   📊 {v['name']}: {v['price']} ({v['change_pct']:+.2f}%)")
    lines.append("")

    # 全市场统计
    stats = get_market_stats()
    if stats:
        lines.append(f"   📈 全市场: 涨{stats['up']}跌{stats['down']} 涨停{stats['zt']}跌停{stats['dt']}")
        lines.append(f"   上涨占比: {stats['up_ratio']*100:.0f}%")
    lines.append("")

    # 涨停股
    limit_data = get_limit_up_list()
    if limit_data.get('zt'):
        zt_names = [f"{s['name']}({s['change_pct']:+.1f}%)" for s in limit_data['zt'][:5]]
        lines.append(f"   🚀 涨停TOP5: {' '.join(zt_names)}")
    lines.append("")

    # 板块排行
    sectors = get_sector_rank(5)
    if sectors:
        lines.append(f"   🏭 强势板块:")
        for s in sectors:
            arrow = '↑' if s['change_pct'] > 0 else '↓'
            lines.append(f"     {s['name']} {arrow} {s['change_pct']:+.2f}% 主力{s['fund_net']:+.1f}亿")
    lines.append("")

    # 概念排行
    concepts = get_concept_board_rank(5)
    if concepts:
        lines.append(f"   💡 强势概念:")
        for c in concepts:
            lines.append(f"     {c['name']} ↑{c['change_pct']:+.2f}%")
    lines.append("")

    # 龙虎榜
    dt = get_dragon_tiger()
    if dt:
        lines.append(f"   🐯 龙虎榜:")
        for d in dt[:3]:
            lines.append(f"     {d['name']} {d['change_pct']:+.2f}% 净额{d['fund_net']:+.1f}亿")
    lines.append("")

    lines.append(f"   {'─'*40}")
    return '\n'.join(lines)


def run_prep_emotion() -> str:
    """8:20-8:40 市场情绪定位"""
    from scripts.tri_face_db import SectorData
    from scripts.three_funds_data import get_market_stats, get_limit_up_list
    from scripts.three_funds_engine import judge_emotion_cycle

    sd = SectorData()
    stats = get_market_stats()
    limit_data = get_limit_up_list()
    emotion = judge_emotion_cycle(stats, limit_data,
                                   limit_data.get('zt_count',0),
                                   limit_data.get('dt_count',0))

    lines = []
    lines.append("🎯 **【8:20-8:40】市场情绪定位**")
    lines.append("")
    lines.append(f"   情绪周期: {emotion['phase']}")
    lines.append(f"   仓位上限: {emotion['position_limit']}%")
    lines.append(f"   策略建议: {emotion['strategy']}")
    lines.append("")
    lines.append(f"   涨停: {emotion['zt_count']}只 | 跌停: {emotion['dt_count']}只")
    lines.append(f"   上涨占比: {emotion['up_ratio']*100:.0f}%")
    lines.append("")

    # 今日策略
    pos = emotion['position_limit']
    if pos >= 80:
        lines.append("   🔥 高潮期策略：重仓主线龙头+补涨龙")
    elif pos >= 50:
        lines.append("   🌱 复苏期策略：主线2进3/3进4接力")
    elif pos >= 30:
        lines.append("   ➖ 震荡期策略：轻仓试错，快进快出")
    elif pos > 0:
        lines.append("   ❄️ 冰点期策略：轻仓试错新题材首板")
    else:
        lines.append("   🍂 退潮期策略：强制空仓，不抢反弹")
    lines.append("")
    lines.append(f"   {'─'*40}")
    return '\n'.join(lines)


def run_prep_plan() -> str:
    """8:40-9:15 制定交易计划——跑三资金合力"""
    from scripts.three_funds_engine import analyze_stock_batch, generate_batch_report
    from scripts.three_funds_data import get_market_stats, get_limit_up_list
    from scripts.three_funds_engine import judge_emotion_cycle

    CORE_CODES = ['603986', '601138', '603019', '603893', '002281',
                  '600584', '603005', '603160', '600745', '600519']
    results = analyze_stock_batch(CORE_CODES)
    ms = get_market_stats()
    ld = get_limit_up_list()
    emo = judge_emotion_cycle(ms, ld, ld.get('zt_count',0), ld.get('dt_count',0))

    lines = []
    lines.append("📋 **【8:40-9:15】标的池筛选 —— 三资金合力评分**")
    lines.append("")
    batch_report = generate_batch_report(results, emo)
    lines.append(batch_report)
    lines.append("")

    # 高分标的详细报告
    high_score = [r for r in results if r['combined']['total_score'] >= 50]
    if high_score:
        lines.append(f"🎯 **候选标的 ({len(high_score)}只 ≥50分):**")
        for r in sorted(high_score, key=lambda x: x['combined']['total_score'], reverse=True):
            cb = r['combined']
            name = r.get('name', r['code'])
            lines.append(f"   {name} 总分{cb['total_score']}  {cb['action']}")
            lines.append(f"     买: 高开3-7%竞价量比>5 | 仓位≤{emo['position_limit']}%")
            lines.append(f"     止损: 低开>3%或5分钟不拉升 | 止盈: 缩量加速次日")
        lines.append("")

    lines.append(f"   {'─'*40}")
    return '\n'.join(lines)


def run_auction() -> str:
    """9:15-9:30 集合竞价"""
    from scripts.three_funds_data import get_market_stats, get_limit_up_list

    stats = get_market_stats()
    limit_data = get_limit_up_list()

    lines = []
    lines.append("⏰ **【9:15-9:30】集合竞价**")
    lines.append("")
    lines.append("   9:15-9:20 只观察不操作 —— 识别虚假挂单")
    lines.append("   9:20-9:25 盯紧真实博弈：")
    lines.append("     竞价量≥昨日5%、高开3-7%最佳")
    lines.append("     封单/流通市值>1%为强势")
    lines.append("   9:25-9:30 最终决策挂单")
    lines.append("")
    lines.append(f"   实时涨停: {limit_data.get('zt_count',0)}只")
    lines.append(f"   实时跌停: {limit_data.get('dt_count',0)}只")
    lines.append(f"   上涨占比: {stats.get('up_ratio',0.5)*100:.0f}%")
    lines.append("")
    lines.append(f"   {'─'*40}")
    return '\n'.join(lines)


def run_morning_golden() -> str:
    """9:30-10:00 早盘黄金30分钟"""
    from scripts.three_funds_data import get_market_stats, get_limit_up_list
    from scripts.three_funds_engine import analyze_stock_batch

    results = analyze_stock_batch(['603986', '601138', '603019', '603893', '002281'])
    stats = get_market_stats()
    limit_data = get_limit_up_list()

    lines = []
    lines.append("🚀 **【9:30-10:00】早盘黄金30分钟**")
    lines.append("")
    lines.append("   核心任务：执行盘前计划，符合条件立即买入")
    lines.append("   监控：大盘成交额(30分≥2000亿正常)、主线板块涨停数")
    lines.append("   禁止：追高杂毛、临时开仓、抄底下跌股")
    lines.append("")
    lines.append(f"   实时涨停: {limit_data.get('zt_count',0)}只")
    lines.append(f"   实时跌停: {limit_data.get('dt_count',0)}只")
    lines.append("")

    for r in results:
        cb = r['combined']
        name = r.get('name', r['code'])
        if cb['total_score'] >= 50:
            lines.append(f"   ✅ {name}: 总分{int(cb['total_score'])} → 执行买入计划")
        elif cb['total_score'] >= 30:
            lines.append(f"   👀 {name}: 总分{int(cb['total_score'])} → 观察")
        else:
            lines.append(f"   ❌ {name}: 总分{int(cb['total_score'])} → 放弃")

    lines.append("")
    lines.append(f"   {'─'*40}")
    return '\n'.join(lines)


def run_afternoon_close() -> str:
    """14:30-15:00 尾盘清仓"""
    from scripts.three_funds_data import get_market_stats, get_limit_up_list

    stats = get_market_stats()
    limit_data = get_limit_up_list()

    lines = []
    lines.append("🧹 **【14:30-15:00】尾盘清仓期**")
    lines.append("")
    lines.append("   14:50前清掉所有非计划内持仓")
    lines.append("   只保留：主线龙头(确认封板)+高分合力股(≥80分)")
    lines.append("   若龙头断板 → 清仓所有相关标的")
    lines.append("")
    lines.append(f"   今日涨停: {limit_data.get('zt_count',0)}只")
    lines.append(f"   今日跌停: {limit_data.get('dt_count',0)}只")
    lines.append("")
    lines.append(f"   {'─'*40}")
    return '\n'.join(lines)


def run_review() -> str:
    """15:00-21:00 盘后复盘"""
    from scripts.three_funds_data import get_market_stats, get_limit_up_list, get_dragon_tiger

    stats = get_market_stats()
    limit_data = get_limit_up_list()
    dt = get_dragon_tiger()

    lines = []
    lines.append("📝 **【15:00-21:00】盘后复盘**")
    lines.append("")
    lines.append(f"   今日数据:")
    lines.append(f"     涨停: {limit_data.get('zt_count',0)}只")
    lines.append(f"     跌停: {limit_data.get('dt_count',0)}只")
    lines.append(f"     上涨: {stats.get('up',0)}只")
    lines.append(f"     下跌: {stats.get('down',0)}只")
    lines.append("")
    lines.append("   大盘复盘：")
    lines.append("     情绪周期是否变化？仓位上限是否调整？")
    lines.append("   板块复盘：")
    lines.append("     最强板块/概念是谁？持续性如何？")
    lines.append("   个股复盘：")
    lines.append("     各标的操作是否符合计划？")
    lines.append("   龙虎榜复盘：")
    for d in dt[:3]:
        lines.append(f"     {d['name']} {d['change_pct']:+.2f}% 净额{d['fund_net']:+.1f}亿")
    lines.append("")
    lines.append("   ✅ 今日交易总结 (填写):")
    lines.append("     正确: ______  错误: ______  改进: ______")
    lines.append("")
    lines.append(f"   {'─'*40}")
    return '\n'.join(lines)


# ============================================================
# 完整流程输出
# ============================================================

def run_full_daily() -> str:
    """输出全链路报告"""
    lines = []
    lines.append(f"⏰ **三资金合力股 — 每日全链路流程**")
    lines.append(f"   日期: {datetime.now().strftime('%Y-%m-%d %A')}")
    lines.append(f"   时间: {datetime.now().strftime('%H:%M:%S')}")
    lines.append("")

    slot = get_current_slot()
    lines.append(f"   当前时段: [{slot}]")
    lines.append("")

    sections = [
        ('🌙 隔夜扫描', run_prep_scan()),
        ('🎯 情绪定位', run_prep_emotion()),
        ('📋 交易计划', run_prep_plan()),
        ('⏰ 集合竞价', run_auction()),
        ('🚀 早盘黄金', run_morning_golden()),
        ('🧹 尾盘清仓', run_afternoon_close()),
        ('📝 盘后复盘', run_review()),
    ]

    for title, content in sections:
        lines.append(content)
        lines.append("")

    return '\n'.join(lines)


# ============================================================
# 主入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description='⏰ 三资金合力股每日全链路交易流程')
    parser.add_argument('--full', action='store_true', help='输出全链路流程')
    parser.add_argument('--now', action='store_true', help='根据当前时间匹配流程段')
    parser.add_argument('--3funds', dest='threefunds', action='store_true', help='只跑三资金合力扫描')
    parser.add_argument('--code', type=str, help='个股分析')
    args = parser.parse_args()

    if args.code:
        from scripts.three_funds_engine import analyze_stock, generate_report
        result = analyze_stock(args.code)
        print(generate_report(result))

    elif args.full:
        print(run_full_daily())

    elif args.now:
        slot = get_current_slot()
        runners = {
            'prep_scan': run_prep_scan,
            'prep_emotion': run_prep_emotion,
            'prep_plan': run_prep_plan,
            'auction': run_auction,
            'morning_golden': run_morning_golden,
            'afternoon_close': run_afternoon_close,
            'review': run_review,
        }
        fn = runners.get(slot)
        if fn:
            print(fn())
        else:
            print(f"⏰ 当前时段({slot})无定时流程")

    elif args.threefunds:
        from scripts.three_funds_engine import analyze_stock_batch, generate_batch_report
        from scripts.three_funds_data import get_market_stats, get_limit_up_list
        from scripts.three_funds_engine import judge_emotion_cycle

        codes = ['603986', '601138', '603019', '603893', '002281',
                 '600584', '603005', '603160', '600745', '600519']
        results = analyze_stock_batch(codes)
        ms = get_market_stats()
        ld = get_limit_up_list()
        emo = judge_emotion_cycle(ms, ld, ld.get('zt_count',0), ld.get('dt_count',0))
        print(generate_batch_report(results, emo))

    else:
        # 默认输出三面量化
        from scripts.tri_face import main as tri_face_main
        sys.argv = [sys.argv[0]]
        tri_face_main()


if __name__ == '__main__':
    main()
