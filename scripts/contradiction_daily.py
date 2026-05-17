#!/usr/bin/env python3
"""
矛盾量化打板引擎 — 日度报告+集成输出
每日运行一次，输出完整决策报告给作战面板
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contradiction_engine import run, analyze_macro, analyze_sectors

ASTOCK = os.path.expanduser('~/astock')
V2BOARD = os.path.expanduser('~/V2board')
DATA = os.path.join(ASTOCK, 'data')

def daily_report():
    """生成日度打板决策报告"""
    result = run()
    
    # 计算各层级的详细摘要
    report = {
        'engine': 'contradiction-v1',
        'timestamp': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_score': result.get('total_score', 0),
        'decision': result.get('decision', 'N/A'),
        'position': result.get('position', 0),
        'reason': result.get('reason', ''),
        
        # 四层穿透摘要
        'macro': {
            'score': result.get('macro_score', 0),
            'state': result.get('macro', {}).get('state', '未知'),
            'max_position': result.get('macro', {}).get('max_position', 0),
            'details': result.get('macro', {}).get('details', []),
        },
        'sectors': {
            'score': result.get('sector_score', 0),
            'has_main_line': result.get('sectors', {}).get('has_main_line', False),
            'main_line': result.get('sectors', {}).get('main_line_name', ''),
            'top3': [(s['name'], s['score'], s['limit_count'], s['chg']) 
                     for s in result.get('sectors', {}).get('top_sectors', [])[:3]],
        },
        'stock': result.get('stock'),
        'resonance': result.get('resonance', 0),
    }
    
    return report


if __name__ == '__main__':
    report = daily_report()
    
    # 保存到V2board供作战面板读取
    path = os.path.join(V2BOARD, 'data', 'contradiction_report.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    
    if '--json' in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        r = report
        print("=" * 70)
        print("🧠 矛盾量化打板决策报告")
        print("=" * 70)
        print(f"\n综合矛盾得分: {r['total_score']:+d}")
        print(f"决策: {r['decision']}  |  仓位: {r['position']}%")
        print(f"理由: {r['reason']}")
        print(f"\n🌍 宏观层: {r['macro']['score']:+d} [{r['macro']['state']}]")
        for d in r['macro']['details'][:4]:
            print(f"   {d}")
        print(f"\n🏭 板块层: {r['sectors']['score']:+d}")
        print(f"   主线: {r['sectors']['main_line'] or '无'}")
        for t in r['sectors']['top3']:
            print(f"   {t[0]}: {t[1]:+d}分 (涨停{t[2]}家, {t[3]:+.1f}%)")
        print(f"\n✅ 报告已保存")
