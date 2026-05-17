#!/usr/bin/env python3
"""东财龙虎榜 cache 桥接脚本 - 读取 em_lhb_cache.db 6张表，生成结构化摘要"""
import json, os, sqlite3, time
from datetime import datetime, date

DB_PATH = os.path.expanduser('~/astock/data/em_lhb_cache.db')
OUT_PATH = os.path.expanduser('~/V2board/data/em_lhb_summary.json')

def read_db():
    """读取并结构化 em_lhb_cache.db 全部 6 张表"""
    if not os.path.exists(DB_PATH):
        return {'error': f'DB not found: {DB_PATH}', 'generated_at': datetime.now().isoformat()}

    conn = sqlite3.connect(DB_PATH, timeout=3)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 0. 元信息
    tables = []
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for r in c.fetchall():
        cn = conn.execute(f'SELECT COUNT(*) FROM "{r[0]}"').fetchone()[0]
        tables.append({'table': r[0], 'rows': cn})
    meta = {'tables': tables, 'db_path': DB_PATH}

    # 1. stock_lhb_stats - 个股上榜次数排行
    stock_lhb = []
    c.execute("""SELECT * FROM stock_lhb_stats ORDER BY date DESC, "上榜次数" DESC""")
    for r in c.fetchall():
        stock_lhb.append(dict(r))

    # 2. dept_return_rank - 营业部 T+N 胜率排名 (TOP10)
    dept_return = []
    c.execute("""SELECT * FROM dept_return_rank ORDER BY date DESC, rank_num ASC LIMIT 10""")
    for r in c.fetchall():
        dept_return.append(dict(r))

    # 3. dept_lhb_rank - 营业部成交排名 (TOP10)
    dept_lhb = []
    c.execute("""SELECT * FROM dept_lhb_rank ORDER BY date DESC, rank_num ASC LIMIT 10""")
    for r in c.fetchall():
        dept_lhb.append(dict(r))

    # 4. inst_buy_sell - 机构买卖 (TOP5净买)
    inst_buy = []
    c.execute("""SELECT * FROM inst_buy_sell ORDER BY date DESC, "机构买入净额_万" DESC LIMIT 5""")
    for r in c.fetchall():
        inst_buy.append(dict(r))

    # 5. inst_seat_track - 机构席位跟踪 (TOP5)
    inst_seat = []
    c.execute("""SELECT * FROM inst_seat_track ORDER BY date DESC, "机构净买入额_万" DESC LIMIT 5""")
    for r in c.fetchall():
        inst_seat.append(dict(r))

    # 6. daily_active_dept - 当日活跃营业部 (TOP10)
    daily_dept = []
    c.execute("""SELECT * FROM daily_active_dept ORDER BY date DESC, rank_num ASC LIMIT 10""")
    for r in c.fetchall():
        daily_dept.append(dict(r))

    conn.close()

    # === 构建结构化摘要 ===
    summary = {
        'generated_at': datetime.now().isoformat(),
        'meta': meta,
        'tables': {
            'stock_lhb_stats': {
                'latest_date': stock_lhb[0]['date'] if stock_lhb else None,
                'period': stock_lhb[0]['period'] if stock_lhb else None,
                'rows': len(stock_lhb),
                'data': stock_lhb,
            },
            'dept_return_rank': {
                'latest_date': dept_return[0]['date'] if dept_return else None,
                'period': dept_return[0]['period'] if dept_return else None,
                'rows': len(dept_return),
                'top10': dept_return,
            },
            'dept_lhb_rank': {
                'latest_date': dept_lhb[0]['date'] if dept_lhb else None,
                'period': dept_lhb[0]['period'] if dept_lhb else None,
                'rows': len(dept_lhb),
                'top10': dept_lhb,
            },
            'inst_buy_sell': {
                'rows': len(inst_buy),
                'top5_net_buy': inst_buy,
            },
            'inst_seat_track': {
                'rows': len(inst_seat),
                'top5': inst_seat,
            },
            'daily_active_dept': {
                'rows': len(daily_dept),
                'top10': daily_dept,
            },
        },
    }

    # === 衍生摘要 (TOP 排行 + 趋势) ===
    digest = {}

    # TOP10 营业部 (按成交金额排序，从 dept_lhb_rank)
    if dept_lhb:
        digest['top10_dept_by_volume'] = [
            {
                'rank': d['rank_num'],
                'name': d['dept_name'],
                'volume_wan': d['成交金额_万'],
                'buy_wan': d['买入额_万'],
                'sell_wan': d['卖出额_万'],
                'appearances': d['上榜次数'],
            }
            for d in dept_lhb[:10]
        ]

    # TOP10 游资胜率 (从 dept_return_rank 的 D1 胜率排序)
    if dept_return:
        digest['top10_dept_by_winrate'] = [
            {
                'rank': d['rank_num'],
                'name': d['dept_name'],
                'd1_winrate': round(d['d1_winrate'] * 100, 1),
                'd1_avg_pct': round(d['d1_avg_pct'] * 100, 2),
                'd1_count': d['d1_count'],
                'd2_winrate': round(d['d2_winrate'] * 100, 1),
                'd5_winrate': round(d['d5_winrate'] * 100, 1),
                'd10_winrate': round(d['d10_winrate'] * 100, 1),
            }
            for d in dept_return[:10]
        ]

    # TOP5 机构净买 (从 inst_buy_sell)
    if inst_buy:
        digest['top5_inst_net_buy'] = [
            {
                'code': d['code'],
                'name': d['name'],
                'net_buy_wan': d['机构买入净额_万'],
                'buy_wan': d['机构买入总额_万'],
                'sell_wan': d['机构卖出总额_万'],
                'buy_count': d['买方机构数'],
                'sell_count': d['卖方机构数'],
                'reason': d['上榜原因'],
            }
            for d in inst_buy[:5]
        ]

    # 个股上榜次数排行 (从 stock_lhb_stats)
    if stock_lhb:
        digest['stock_appear_rank'] = [
            {
                'code': d['code'],
                'name': d['name'],
                'appearances': d['上榜次数'],
                'net_buy_wan': d['龙虎榜净买额_万'],
                'total_volume_wan': d['龙虎榜总成交额_万'],
            }
            for d in sorted(stock_lhb, key=lambda x: x.get('上榜次数', 0), reverse=True)[:10]
        ]

    # 近5日活跃度趋势 (如果有 daily_active_dept 则统计)
    if daily_dept:
        # 按日期分组统计活跃营业部数量
        from collections import defaultdict
        date_groups = defaultdict(list)
        for d in daily_dept:
            date_groups[d.get('date', '')].append(d)
        digest['daily_activity_trend'] = [
            {
                'date': dt,
                'active_dept_count': len(depts),
                'top3': [
                    {
                        'name': dd['dept_name'],
                        'net_wan': dd['买卖净额_万'],
                    }
                    for dd in sorted(depts, key=lambda x: abs(x.get('买卖净额_万', 0)), reverse=True)[:3]
                ],
            }
            for dt, depts in sorted(date_groups.items(), reverse=True)
        ]

    summary['digest'] = digest

    return summary


def main():
    summary = read_db()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({'_meta': {k: v for k, v in summary.get('meta', {}).items() if k in ('tables',)}},
                     ensure_ascii=False))
    return summary


if __name__ == '__main__':
    main()
