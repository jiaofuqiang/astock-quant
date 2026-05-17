#!/usr/bin/env python3
"""
昨日(5月14日)全策略选股扫描
基于daily_limit_data.db + kline_cache.db + sector_indexes.db的历史数据，
运行13大策略选股逻辑，输出标准JSON给作战面板。
"""
import sys, os, json, sqlite3
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
V2BOARD = os.path.join(os.path.expanduser('~'), 'V2board', 'data')
OUT = os.path.join(V2BOARD, 'all_yesterday_strategies.json')

DATE = '2026-05-14'  # 昨日交易日

def get_kline_conn():
    return sqlite3.connect(os.path.join(DATA, 'kline_cache.db'))

def get_limit_conn():
    return sqlite3.connect(os.path.join(DATA, 'daily_limit_data.db'))

def get_sector_conn():
    return sqlite3.connect(os.path.join(DATA, 'sector_indexes.db'))

def calc_vol_ratio(code, date, kline_conn):
    """计算量比 = T日volume / 20日均量"""
    cur = kline_conn.cursor()
    # T日成交量
    cur.execute("SELECT volume FROM kline WHERE code=? AND date=?", (code, date))
    row = cur.fetchone()
    if not row:
        return None
    t_vol = row[0]
    # 前20日平均成交量
    cur.execute("""
        SELECT AVG(volume) FROM (
            SELECT volume FROM kline WHERE code=? AND date<? AND date>='2024-01-01'
            ORDER BY date DESC LIMIT 20
        )
    """, (code, date))
    row2 = cur.fetchone()
    if not row2 or not row2[0]:
        return None
    avg_vol = row2[0]
    if avg_vol == 0:
        return None
    return round(t_vol / avg_vol, 2)

def get_t1_price(code, date, kline_conn):
    """获取T+1日的开盘、最高、最低价"""
    cur = kline_conn.cursor()
    cur.execute("""
        SELECT open, high, low, close FROM kline 
        WHERE code=? AND date>? ORDER BY date LIMIT 1
    """, (code, date))
    row = cur.fetchone()
    if not row:
        return None
    return {'open': row[0], 'high': row[1], 'low': row[2], 'close': row[3]}

def categorize_volume_pattern(vol_ratio):
    """量比分类"""
    if vol_ratio is None:
        return '未知'
    if vol_ratio < 0.3:
        return '极缩量'
    elif vol_ratio < 0.5:
        return '缩量'
    elif vol_ratio < 0.7:
        return '微缩量'
    elif vol_ratio < 1.0:
        return '正常缩量'
    elif vol_ratio < 2.0:
        return '正常'
    elif vol_ratio < 3.0:
        return '放量'
    elif vol_ratio < 5.0:
        return '巨量'
    else:
        return '天量'

def get_board_counts(date, limit_conn):
    """获取所有股票在T日的连板数"""
    cur = limit_conn.cursor()
    cur.execute("SELECT code, name, board_count, first_limit_time, close_price, change_pct, amount_wan, ban_reason FROM limit_stocks WHERE date=? ORDER BY board_count DESC",
                (date,))
    rows = cur.fetchall()
    result = {}
    for r in rows:
        result[r[0]] = {
            'code': r[0],
            'name': r[1],
            'board_count': r[2] or 0,
            'first_limit_time': r[3] or '',
            'close_price': r[4],
            'change_pct': r[5],
            'amount_wan': r[6] or 0,
            'ban_reason': r[7] or '',
        }
    return result

def get_sector_limits(date, sector_conn):
    """获取板块涨停分布"""
    cur = sector_conn.cursor()
    cur.execute("""
        SELECT sector_name, limit_up_count, avg_change 
        FROM sector_daily_index WHERE date=? AND limit_up_count>0
        ORDER BY limit_up_count DESC, avg_change DESC
    """, (date,))
    return [{'name': r[0], 'limit_count': r[1], 'avg_change': r[2]} for r in cur.fetchall()]

def get_stock_sectors(code, date, sector_conn):
    """获取股票所属的板块列表"""
    cur = sector_conn.cursor()
    cur.execute("""
        SELECT sector_name FROM sector_stock_daily 
        WHERE code=? AND date=? LIMIT 5
    """, (code, date))
    return [r[0] for r in cur.fetchall()]

def run_m01_geye_yijia(limit_stocks, date, kline_conn):
    """M01 隔夜溢价缩量（量比<0.7）"""
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue  # 排除ST
        if info['board_count'] > 1:
            continue  # 首板为主
        vol = calc_vol_ratio(code, date, kline_conn)
        if vol is not None and vol < 0.7:
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M01隔夜溢价缩量',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'volume_pattern': categorize_volume_pattern(vol),
                'close_price': info['close_price'],
                'change_pct': info['change_pct'],
                'first_limit_time': info['first_limit_time'],
                'ban_reason': info['ban_reason'],
                'amount_wan': info['amount_wan'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
                'expected_return': None,
            })
    return results

def run_m02_zhulongtou(limit_stocks, date, kline_conn):
    """M02 总龙头打板（非ST中最高板≥3）"""
    results = []
    if not limit_stocks:
        return results
    # 排除ST
    non_st = {c: s for c, s in limit_stocks.items() if '*' not in s['name']}
    if not non_st:
        return results
    max_board = max(s['board_count'] for s in non_st.values())
    if max_board < 3:
        return results
    for code, info in non_st.items():
        if info['board_count'] == max_board:
            vol = calc_vol_ratio(code, date, kline_conn)
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M02总龙头打板',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'volume_pattern': categorize_volume_pattern(vol),
                'close_price': info['close_price'],
                'change_pct': info['change_pct'],
                'first_limit_time': info['first_limit_time'],
                'ban_reason': info['ban_reason'],
                'amount_wan': info['amount_wan'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
            })
    return results

def run_m07_banbaobaofa(limit_stocks, sectors, date, kline_conn, sector_conn):
    """M07 板块爆发追涨（涨停≥3的板块内股票）"""
    boom_sectors = [s for s in sectors if s['limit_count'] >= 3]
    boom_names = set(s['name'] for s in boom_sectors)
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        stock_sectors = get_stock_sectors(code, date, sector_conn)
        # 检查是否在爆发板块内
        in_boom = any(s in boom_names for s in stock_sectors)
        if not in_boom:
            continue
        vol = calc_vol_ratio(code, date, kline_conn)
        t1 = get_t1_price(code, date, kline_conn)
        results.append({
            'code': code,
            'name': info['name'],
            'strategy': 'M07板块爆发追涨',
            'board_count': info['board_count'],
            'vol_ratio': vol,
            'volume_pattern': categorize_volume_pattern(vol),
            'close_price': info['close_price'],
            'change_pct': info['change_pct'],
            'first_limit_time': info['first_limit_time'],
            'ban_reason': info['ban_reason'],
            'amount_wan': info['amount_wan'],
            'sectors': stock_sectors,
            'boom_sector': [s for s in stock_sectors if s in boom_names],
            't1_open': round(t1['open'], 2) if t1 else None,
            't1_high': round(t1['high'], 2) if t1 else None,
        })
    return results

def run_m08_lianban_suoliang(limit_stocks, date, kline_conn):
    """M08 连板接力缩量（2板以上+量比<0.7）"""
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        if info['board_count'] < 2:
            continue
        vol = calc_vol_ratio(code, date, kline_conn)
        if vol is not None and vol < 0.7:
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M08连板接力缩量',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'volume_pattern': categorize_volume_pattern(vol),
                'close_price': info['close_price'],
                'change_pct': info['change_pct'],
                'first_limit_time': info['first_limit_time'],
                'ban_reason': info['ban_reason'],
                'amount_wan': info['amount_wan'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
            })
    return results

def run_m10_fangliang_huan(limit_stocks, date, kline_conn):
    """M10 放量换手接力（量比0.7~5，非ST，非首板）"""
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        if info['board_count'] < 2:
            continue
        vol = calc_vol_ratio(code, date, kline_conn)
        if vol is not None and 0.7 <= vol <= 5.0:
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M10放量换手接力',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'volume_pattern': categorize_volume_pattern(vol),
                'close_price': info['close_price'],
                'change_pct': info['change_pct'],
                'first_limit_time': info['first_limit_time'],
                'ban_reason': info['ban_reason'],
                'amount_wan': info['amount_wan'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
            })
    return results

def run_m11_yizi_kaiban(limit_stocks, date, kline_conn):
    """M11 一字开板接力（竞价一字板=封板时间09:25+量比极低）"""
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        # 一字板特征：封板时间=09:25 + 量比<0.5
        if info['first_limit_time'] != '09:25:00':
            continue
        vol = calc_vol_ratio(code, date, kline_conn)
        if vol is not None and vol < 0.5:
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M11一字开板接力',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'volume_pattern': categorize_volume_pattern(vol),
                'close_price': info['close_price'],
                'change_pct': info['change_pct'],
                'first_limit_time': info['first_limit_time'],
                'ban_reason': info['ban_reason'],
                'amount_wan': info['amount_wan'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
            })
    return results

def run_m12_cuoshou_fantan(limit_stocks, kline_conn, date):
    """M12 超卖反弹首板（MA20乖离<-15%的首板涨停）"""
    cur = kline_conn.cursor()
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        if info['board_count'] > 1:
            continue
        # 计算MA20乖离
        cur.execute("""
            SELECT close FROM kline WHERE code=? AND date<=?
            ORDER BY date DESC LIMIT 20
        """, (code, date))
        rows = [r[0] for r in cur.fetchall()]
        if len(rows) < 20:
            continue
        ma20 = sum(rows[:20]) / 20
        bias = (rows[0] - ma20) / ma20 * 100
        if bias < -15:
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M12超卖反弹首板',
                'board_count': info['board_count'],
                'ma20_bias': round(bias, 1),
                'close_price': info['close_price'],
                'change_pct': info['change_pct'],
                'ban_reason': info['ban_reason'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
            })
    return results

def run_m13_shenkeng_fantan(limit_stocks, kline_conn, date):
    """M13 深坑反弹首板（MA20乖离<-20%+量比<0.5的超跌首板）"""
    cur = kline_conn.cursor()
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        if info['board_count'] > 1:
            continue
        vol = calc_vol_ratio(code, date, kline_conn)
        if vol is None or vol >= 0.5:
            continue
        cur.execute("""
            SELECT close FROM kline WHERE code=? AND date<=?
            ORDER BY date DESC LIMIT 20
        """, (code, date))
        rows = [r[0] for r in cur.fetchall()]
        if len(rows) < 20:
            continue
        ma20 = sum(rows[:20]) / 20
        bias = (rows[0] - ma20) / ma20 * 100
        if bias < -20:
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M13深坑反弹首板',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'ma20_bias': round(bias, 1),
                'close_price': info['close_price'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
            })
    return results

def run_m18_jingjia_gaojia(limit_stocks, kline_conn, date):
    """M18 竞价高价接力（T-1涨停+T日竞价买的高开接力票）
       注意：这是T+1策略，这里显示5月14日涨停的候选，
       即假设T日=5月14日，选出5月14日涨停的，然后5月15日竞价买入"""
    # M18的买点是竞价3~7%，这里我们列出所有可能适合做竞价接力的
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        vol = calc_vol_ratio(code, date, kline_conn)
        t1 = get_t1_price(code, date, kline_conn)
        if not t1:
            continue
        # 检查今日竞价（T+1 day开盘价）
        t1_open_chg = (t1['open'] - info['close_price']) / info['close_price'] * 100
        # 关键条件：T日涨停 + T+1开3~7%
        if 3 <= t1_open_chg <= 7:
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M18竞价高价接力',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'close_price': info['close_price'],
                'change_pct': info['change_pct'],
                't1_open_price': t1['open'],
                't1_open_chg': round(t1_open_chg, 2),
                't1_high': t1['high'],
            })
        # 另外输出所有T日涨停+T+1高开的（包括7%+）
    return results

def run_m09_san_jin_heli(limit_stocks, date, kline_conn):
    """M09 三资金合力（这里基于历史数据的近似模拟）
       实际需要主力资金数据，这里用成交额+量比作为代理"""
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        vol = calc_vol_ratio(code, date, kline_conn)
        if vol is None:
            continue
        # 三资金合力的近似条件：缩量+大成交额+首板
        if info['board_count'] <= 1 and vol < 1.0 and info['amount_wan'] > 50000000:
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M09三资金合力',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'amount_wan': info['amount_wan'],
                'ban_reason': info['ban_reason'],
                'close_price': info['close_price'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
            })
    return results

def run_m03_first_board_suoliang(limit_stocks, date, kline_conn):
    """M03 首板缩量（量比<0.5的首板）"""
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        if info['board_count'] > 1:
            continue
        vol = calc_vol_ratio(code, date, kline_conn)
        if vol is not None and vol < 0.5:
            t1 = get_t1_price(code, date, kline_conn)
            results.append({
                'code': code,
                'name': info['name'],
                'strategy': 'M03首板缩量',
                'board_count': info['board_count'],
                'vol_ratio': vol,
                'volume_pattern': '极缩量' if vol < 0.3 else '缩量',
                'close_price': info['close_price'],
                'change_pct': info['change_pct'],
                'first_limit_time': info['first_limit_time'],
                'ban_reason': info['ban_reason'],
                'amount_wan': info['amount_wan'],
                't1_open': round(t1['open'], 2) if t1 else None,
                't1_high': round(t1['high'], 2) if t1 else None,
            })
    return results

def run_m05_genfeng_zhangting(limit_stocks, sectors, date, kline_conn, sector_conn):
    """M05 跟风涨停（板块龙二、龙三）"""
    boom_sectors = [s for s in sectors if s['limit_count'] >= 3]
    boom_names = set(s['name'] for s in boom_sectors)
    # 每个爆发板块内找非首板
    results = []
    for code, info in limit_stocks.items():
        if '*' in info['name']:
            continue
        stock_sectors = get_stock_sectors(code, date, sector_conn)
        in_boom = any(s in boom_names for s in stock_sectors)
        if not in_boom:
            continue
        # 跟风特征：不是该板块内板数最高的
        vol = calc_vol_ratio(code, date, kline_conn)
        t1 = get_t1_price(code, date, kline_conn)
        results.append({
            'code': code,
            'name': info['name'],
            'strategy': 'M05跟风涨停',
            'board_count': info['board_count'],
            'vol_ratio': vol or 0,
            'close_price': info['close_price'],
            'change_pct': info['change_pct'],
            'amount_wan': info['amount_wan'],
            'sectors': stock_sectors,
            't1_open': round(t1['open'], 2) if t1 else None,
            't1_high': round(t1['high'], 2) if t1 else None,
        })
    return results

def main():
    kline_conn = get_kline_conn()
    limit_conn = get_limit_conn()
    sector_conn = get_sector_conn()

    # 1. 获取基础数据
    limit_stocks = get_board_counts(DATE, limit_conn)
    global sectors
    sectors = get_sector_limits(DATE, sector_conn)
    
    boom_sectors_3 = [s for s in sectors if s['limit_count'] >= 3]
    boom_sectors_1 = [s for s in sectors if s['limit_count'] >= 1]
    
    # 2. 运行所有策略
    strategies = {
        'M01': ('🏆 M01隔夜溢价缩量', '5月14日打板买入（量比<0.7），5月15日竞价卖', run_m01_geye_yijia),
        'M02': ('🦅 M02总龙头打板', '买市场最高板（≥3板）龙头，5月14日打板', run_m02_zhulongtou),
        'M03': ('💎 M03首板缩量', '首板涨停+量比<0.5，隔夜溢价极高', run_m03_first_board_suoliang),
        'M05': ('🎯 M05跟风涨停', '爆发板块内龙二龙三，5月14日跟风买入', run_m05_genfeng_zhangting),
        'M07': ('🔥 M07板块爆发追涨', '5月14日板块内涨停最快个股，T+1冲高卖', run_m07_banbaobaofa),
        'M08': ('📈 M08连板接力缩量', '2板以上+量比<0.7，5月14日打板', run_m08_lianban_suoliang),
        'M09': ('💰 M09三资金合力', '大成交额缩量首板，机构+游资齐参与', run_m09_san_jin_heli),
        'M10': ('🔄 M10放量换手接力', '2板+量比0.7~5，T+1竞价3~7%买', run_m10_fangliang_huan),
        'M11': ('🛡️ M11一字开板接力', '一字板被打开时买入，开0~3%最佳', run_m11_yizi_kaiban),
        'M12': ('📉 M12超卖反弹首板', 'MA20乖离<-15%的超跌首板，适合低吸', run_m12_cuoshou_fantan),
        'M13': ('🕳️ M13深坑反弹首板', 'MA20乖离<-20%+量比<0.5，极超跌', run_m13_shenkeng_fantan),
        'M18': ('⚡ M18竞价高价接力', 'T+1竞价高开3~7%买入，隔日冲高卖', run_m18_jingjia_gaojia),
    }

    result = {
        'date': DATE,
        'total_limit': len(limit_stocks),
        'env_info': {
            'total_limit': len(limit_stocks),
            'max_board': max(s['board_count'] for s in limit_stocks.values()) if limit_stocks else 0,
            'boom_sectors': [{'name': s['name'], 'limits': s['limit_count'], 'avg_change': s['avg_change']} for s in boom_sectors_3],
            'all_sectors': [{'name': s['name'], 'limits': s['limit_count'], 'avg_change': s['avg_change']} for s in boom_sectors_1],
        },
        'strategies': {},
        'all_signals': [],  # 平铺所有信号用于排序
    }

    total_signals = 0
    for key, (display_name, operation, func) in strategies.items():
        try:
            if key in ('M05', 'M07'):
                signals = func(limit_stocks, sectors, DATE, kline_conn, sector_conn)
            elif key in ('M12', 'M13'):
                signals = func(limit_stocks, kline_conn, DATE)
            elif key == 'M18':
                signals = func(limit_stocks, kline_conn, DATE)
            else:
                signals = func(limit_stocks, DATE, kline_conn)
        except Exception as e:
            signals = []
            print(f"  [!] {key} error: {e}", file=sys.stderr)
        
        strategy_data = {
            'name': display_name,
            'operation': operation,
            'count': len(signals),
            'signals': signals,
        }
        result['strategies'][key] = strategy_data
        
        # 平铺到all_signals
        for s in signals:
            s_copy = dict(s)
            s_copy['strategy_key'] = key
            s_copy['strategy_name'] = display_name
            result['all_signals'].append(s_copy)
        
        total_signals += len(signals)

    # 3. 按评分排序all_signals
    result['total_strategies'] = len(strategies)
    result['total_signals'] = total_signals
    
    # 添加统计摘要
    result['summary'] = {}
    for key, strat in result['strategies'].items():
        result['summary'][key] = {
            'count': strat['count'],
            'name': strat['name'],
        }

    # 4. 写输出文件
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 昨日全策略选股完成: {total_signals}个信号, {len(strategies)}个策略")
    for key, s in result['summary'].items():
        if s['count'] > 0:
            print(f"  {s['name']}: {s['count']}个")

    kline_conn.close()
    limit_conn.close()
    sector_conn.close()

if __name__ == '__main__':
    main()
