#!/usr/bin/env python3
"""
📊 市场日数据整合入库器 v1.0
==============================
每日收盘后(15:10)运行一次，整合所有已有数据源 → market_daily.db

采集流程：
  1. 全量扫描(涨停/跌停/涨跌/量比/高开/封单) — 从腾讯行情
  2. 竞价数据 — 从V2board/auction_trend.json读取
  3. 炸板/回封 — 从封单采集记录计算
  4. 板块数据 — 从sector_indexes.db读取
  5. 龙虎榜 — 从lhb_cache.db读取
  6. 最高板 — 从limit_strength读取
  7. 涨停前趋势 — 从kline_cache计算

输出：market_daily.db → day_full 表（每日一条完整记录）
"""
import os, sys, json, sqlite3, subprocess, time
from datetime import datetime, date, timedelta
from collections import defaultdict

BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
V2BOARD = os.path.expanduser('~/V2board')
DB_PATH = os.path.join(DATA_DIR, 'market_daily.db')
KLINE_DB = os.path.join(DATA_DIR, 'kline_cache.db')
LIMIT_DB = os.path.join(DATA_DIR, 'daily_limit_data.db')
SECTOR_DB = os.path.join(DATA_DIR, 'sector_indexes.db')
LHB_DB = os.path.join(DATA_DIR, 'lhb_cache.db')
ALL_CODES_FILE = os.path.join(DATA_DIR, 'all_main_board.txt')

def sf(v, default=0):
    try: return float(v) if v and v != '-' else default
    except: return default

def load_codes():
    try:
        with open(ALL_CODES_FILE) as f:
            return [l.strip() for l in f if l.strip() and not l.startswith('#')]
    except:
        return []

def mkt(code):
    return f'sh{code}' if code[0] in ('6','5','9') else f'sz{code}'

def sql_val(db, q):
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', db],
        capture_output=True, text=True, timeout=10, input=q)
    return r.stdout.strip()

def sql_json(db, q):
    r = subprocess.run(['sqlite3', '-json', db],
        capture_output=True, text=True, timeout=10, input=q)
    return json.loads(r.stdout) if r.stdout.strip() else []

# ========== Step 1: 全量扫描 ==========

def scan_full_market(codes):
    """全量扫描，返回全套市场指标
    用东财API替代腾讯行情获取涨停/跌停数（更准确）
    """
    # ===== 从东财API获取涨停/跌停/情绪数据 =====
    emoji = {'limit_up': 0, 'limit_down': 0, 'max_board': 0}
    try:
        r = subprocess.run(['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
            'https://datacenter-web.eastmoney.com/api/data/v1/get?'
            'reportName=RPT_INTSELECTION_EMOTION&columns=ALL&pageNumber=1&pageSize=1'
            '&sortColumns=TRADE_DATE&sortTypes=-1'],
            capture_output=True, timeout=10)
        raw = r.stdout.decode('utf-8', errors='replace')
        if raw:
            d = json.loads(raw)
            dat = d.get('result', {}).get('data', [{}])[0]
            emoji['limit_up'] = int(dat.get('UPLIMIT_NUM', 0))
            emoji['limit_down'] = int(dat.get('DOWNLIMIT_NUM', 0))
            emoji['max_board'] = int(dat.get('MAX_CONTINUS_UPLIMITS', 0))
            print(f'  东财API: 涨停{emoji["limit_up"]} 跌停{emoji["limit_down"]} 最高板{emoji["max_board"]}')
    except Exception as e:
        print(f'  东财API失败: {e}')

    limit_up = {}      # 涨停股明细
    limit_down = {}    # 跌停股明细
    up_count = down_count = 0
    vol_dist = {'<0.5': 0, '0.5~0.7': 0, '0.7~1': 0, '1~3': 0, '3~5': 0, '>5': 0}
    gap_dist = {'1~3%': 0, '3~5%': 0, '5~7%': 0, '>7%': 0}
    total_seal = 0      # 涨停总封单额
    yizi_count = 0
    suoliang_count = 0
    fangliang_count = 0
    boards = defaultdict(int)  # 连板分布 {板数: 数量}
    
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        cs = ','.join(mkt(c) for c in batch)
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://qt.gtimg.cn/q={cs}'],
                capture_output=True, timeout=12
            )
            raw = r.stdout.decode('gbk', errors='replace')
            for line in raw.strip().split('\n'):
                if not line or '=' not in line: continue
                parts = line.split('~')
                if len(parts) < 55: continue
                code = parts[2].strip()
                try:
                    cur = sf(parts[3]); prev = sf(parts[4])
                    open_p = sf(parts[5]); high = sf(parts[33])
                    chg = sf(parts[32]); vol_r = sf(parts[49])
                    name = parts[1].strip()
                    buy1 = sf(parts[9]); buy1_vol = sf(parts[10])
                    amount = sf(parts[37])
                    high_limit = sf(parts[47])
                    
                    if prev <= 0: continue
                    
                    is_up = chg > 0
                    is_down = chg < 0
                    if is_up: up_count += 1
                    if is_down: down_count += 1
                    
                    # 涨停
                    is_limit = chg >= 9.5 and cur >= prev * 1.09
                    if is_limit:
                        open_chg = (open_p - prev) / prev * 100
                        is_yizi = open_chg >= 9.5
                        seal = buy1 * buy1_vol / 10000  # 万元
                        body_high = max(cur, open_p)
                        upper = (high - body_high) / prev * 100 if prev > 0 else 0
                        
                        limit_up[code] = {
                            'name': name, 'chg': round(chg, 2),
                            'vol_ratio': round(vol_r, 2),
                            'open_chg': round(open_chg, 2),
                            'seal': round(seal, 1),
                            'is_yizi': is_yizi,
                            'upper_shadow': round(upper, 2),
                            'amount_wan': round(amount / 10000, 1),
                        }
                        total_seal += seal
                        if is_yizi: yizi_count += 1
                        elif vol_r > 0 and vol_r < 0.7: suoliang_count += 1
                        else: fangliang_count += 1
                    
                    # 跌停
                    if chg <= -9.5 and cur <= prev * 0.91:
                        limit_down[code] = {'name': name, 'chg': round(chg, 2)}
                    
                    # 量比分布
                    if vol_r > 0:
                        if vol_r < 0.5: vol_dist['<0.5'] += 1
                        elif vol_r < 0.7: vol_dist['0.5~0.7'] += 1
                        elif vol_r < 1: vol_dist['0.7~1'] += 1
                        elif vol_r < 3: vol_dist['1~3'] += 1
                        elif vol_r < 5: vol_dist['3~5'] += 1
                        else: vol_dist['>5'] += 1
                    
                    # 高开缺口
                    open_chg2 = (open_p - prev) / prev * 100
                    if open_chg2 > 0:
                        if open_chg2 < 3: gap_dist['1~3%'] += 1
                        elif open_chg2 < 5: gap_dist['3~5%'] += 1
                        elif open_chg2 < 7: gap_dist['5~7%'] += 1
                        else: gap_dist['>7%'] += 1
                        
                except:
                    continue
        except:
            continue
        time.sleep(0.05)
    
    return {
        'up_count': up_count, 'down_count': down_count,
        'limit_up': emoji['limit_up'] if emoji['limit_up'] > 0 else len(limit_up),
        'limit_down': emoji['limit_down'] if emoji['limit_down'] > 0 else len(limit_down),
        'yizi': yizi_count, 'suoliang': suoliang_count, 'fangliang': fangliang_count,
        'vol_lt_05': vol_dist['<0.5'], 'vol_05_07': vol_dist['0.5~0.7'],
        'vol_07_1': vol_dist['0.7~1'], 'vol_1_3': vol_dist['1~3'],
        'vol_3_5': vol_dist['3~5'], 'vol_gt_5': vol_dist['>5'],
        'gap_1_3': gap_dist['1~3%'], 'gap_3_5': gap_dist['3~5%'],
        'gap_5_7': gap_dist['5~7%'], 'gap_gt_7': gap_dist['>7%'],
            'limit_up_detail': limit_up,
        'limit_down_detail': limit_down,
        'total_seal_wan': round(total_seal, 0),
    }

# ========== Step 1b: 东财涨停明细 API ==========

def fetch_limit_detail_from_new_api(trade_date=None):
    """从东方财富涨跌停监控API获取涨停原因数据（新API，带AI原因解析）"""
    if trade_date is None:
        trade_date = str(date.today())
    result = {
        'limit_reasons': [],
        'limit_list_full': [],
        'stats': {'total': 0, 'yizi': 0, 'ziran': 0},
    }
    try:
        # API 1: 全量涨停数据
        url1 = (f'https://datacenter-web.eastmoney.com/api/data/v1/get?'
                f'reportName=RPT_INTSELECTION_LIMITSTOCKHIS&columns=ALL'
                f'&pageNumber=1&pageSize=5000'
                f'&filter=(TRADE_DATE%3D%27{trade_date}%27)'
                f'&sortColumns=CLOSE_LIMITUP_TIME&sortTypes=1')
        r1 = subprocess.run(['curl', '-s', '--connect-timeout', '5', '--max-time', '10', url1],
            capture_output=True, timeout=15)
        raw1 = r1.stdout.decode('utf-8', errors='replace')
        if raw1:
            d1 = json.loads(raw1)
            all_data = d1.get('result', {}).get('data', [])
            for r in all_data:
                nlimite = r.get('NDAYS_NLIMITE', '今日首板')
                limit_way = r.get('LIMIT_WAY', '自然涨停')
                board_count = 1
                if nlimite and '天' in nlimite and '板' in nlimite:
                    parts = nlimite.replace('板', '').split('天')
                    if len(parts) == 2: board_count = int(parts[1])
                
                result['limit_list_full'].append({
                    'code': r.get('SECURITY_CODE', ''),
                    'name': r.get('SECURITY_NAME_ABBR', ''),
                    'board_desc': nlimite,
                    'board_count': board_count,
                    'limit_way': limit_way,
                    'close_time': r.get('CLOSE_LIMITUP_TIME', ''),
                    'seal_wan': float(r.get('LAST_LIMITUP_NUM_NEW', 0) or 0),
                    'turnover_rate': float(r.get('TURNOVERRATE', 0) or 0),
                    'net_inflow': float(r.get('NET_INFLOW', 0) or 0),
                    'open_times': int(r.get('OPEN_TIMES', 0) or 0),
                    'fbl': float(r.get('FBL', 0) or 0),
                    'board_name': r.get('BOARD_NAME', ''),
                    'industry': r.get('INDUSTRY', ''),
                    'yield_pct': float(r.get('YIELD', 0) or 0),
                })
            result['stats']['total'] = len(all_data)
            yizi = sum(1 for r in all_data if r.get('LIMIT_WAY') == '一字涨停')
            result['stats']['yizi'] = yizi
            result['stats']['ziran'] = len(all_data) - yizi
        
        # API 2: 涨停原因数据（新API）
        import urllib.parse
        dt_filter = f"(TRADE_DATE='{trade_date} 00:00:00')"
        encoded = urllib.parse.quote(dt_filter, safe='()=')
        
        url2 = (f'https://datacenter.eastmoney.com/securities/api/data/v1/get?'
                f'source=SECURITIES&client=APP'
                f'&reportName=RPT_PCHOT_LIMITLIST_HSDETIAL'
                f'&columns=SECURITY_CODE,SECURITY_NAME_ABBR,LIMIT_REASON,LIMIT_CONTENT,BOARD_NAME,BOARD_CODE'
                f'&filter={encoded}'
                f'&pageNumber=1&pageSize=5000'
                f'&sortColumns=RANK_TIME&sortTypes=-1')
        r2 = subprocess.run(['curl', '-s', '--connect-timeout', '5', '--max-time', '10', url2],
            capture_output=True, timeout=15)
        raw2 = r2.stdout.decode('utf-8', errors='replace')
        if raw2:
            d2 = json.loads(raw2)
            reasons = d2.get('result', {}).get('data', [])
            for r in reasons:
                code = r.get('SECURITY_CODE', '')
                reason = r.get('LIMIT_REASON', '')
                content = r.get('LIMIT_CONTENT', '')
                if code and reason:
                    result['limit_reasons'].append({
                        'code': code,
                        'reason': reason,
                        'content': (content[:300] + '...') if len(content) > 300 else content,
                    })
    except Exception as e:
        print(f'  新API失败: {e}')
    
    return result

def fetch_limit_detail_from_eastmoney(trade_date=None):
    """从东方财富API获取当日涨停个股完整明细"""
    if trade_date is None:
        trade_date = str(date.today())
    result = {
        'limit_list': [],
        'board_dist': {},
        'limit_way_dist': {},
        'time_groups': {},
        'stats': {'total': 0, 'yizi': 0, 'ziran': 0, 'zhaban_count': 0, 'zhaban_rate': 0,
                   'avg_fbl': 0, 'seal_total_wan': 0, 'seal_count_gt_1yi': 0},
    }
    try:
        all_data = []
        page = 1
        while True:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://datacenter-web.eastmoney.com/api/data/v1/get?'
                 f'reportName=RPT_INTSELECTION_LIMITSTOCKHIS&columns=ALL'
                 f'&pageNumber={page}&pageSize=5000'
                 f'&sortColumns=CLOSE_LIMITUP_TIME&sortTypes=1'
                 f'&filter=(TRADE_DATE%3D%27{trade_date}%27)'],
                capture_output=True, timeout=15
            )
            raw = r.stdout.decode('utf-8', errors='replace')
            if not raw:
                break
            d = json.loads(raw)
            data = d.get('result', {}).get('data', [])
            if not data:
                break
            all_data.extend(data)
            total_pages = d.get('result', {}).get('pages', 0)
            if page >= total_pages:
                break
            page += 1
            time.sleep(0.1)
    except Exception as e:
        print(f'  东财涨停明细API失败: {e}')
        return result
    
    limit_list = []
    board_dist = defaultdict(int)
    limit_way_dist = defaultdict(int)
    
    for r in all_data:
        nlimite = r.get('NDAYS_NLIMITE', '今日首板')
        limit_way = r.get('LIMIT_WAY', '自然涨停')
        code = r.get('SECURITY_CODE', '')
        name = r.get('SECURITY_NAME_ABBR', '')
        
        # 解析板数
        board_count = 1  # 默认为首板
        if nlimite and '天' in nlimite and '板' in nlimite:
            parts = nlimite.replace('板', '').split('天')
            if len(parts) == 2:
                days = int(parts[0])
                boards = int(parts[1])
                board_count = boards
        board_dist[board_count] = board_dist.get(board_count, 0) + 1
        limit_way_dist[limit_way] = limit_way_dist.get(limit_way, 0) + 1
        
        # 封单额（万元）
        seal_wan = float(r.get('LAST_LIMITUP_NUM_NEW', 0) or 0)
        
        limit_list.append({
            'code': code,
            'name': name,
            'board_desc': nlimite,
            'board_count': board_count,
            'limit_way': limit_way,
            'close_time': r.get('CLOSE_LIMITUP_TIME', ''),
            'seal_wan': seal_wan,
            'turnover_rate': float(r.get('TURNOVERRATE', 0) or 0),
            'net_inflow': float(r.get('NET_INFLOW', 0) or 0),
            'open_times': int(r.get('OPEN_TIMES', 0) or 0),
            'fbl': float(r.get('FBL', 0) or 0),
            'board_name': r.get('BOARD_NAME', ''),
            'industry': r.get('INDUSTRY', ''),
            'yield_pct': float(r.get('YIELD', 0) or 0),
        })
    
    total = len(limit_list)
    yizi = limit_way_dist.get('一字涨停', 0)
    ziran = limit_way_dist.get('自然涨停', 0)
    
    # 炸板率：开板次数>0的比例
    zhaban_stocks = [s for s in limit_list if s['open_times'] > 0]
    zhaban_count = len(zhaban_stocks)
    zhaban_rate = round(zhaban_count / total * 100, 1) if total > 0 else 0
    
    # 平均封板率
    fbl_list = [s['fbl'] for s in limit_list if s['fbl'] > 0]
    avg_fbl = round(sum(fbl_list) / len(fbl_list), 1) if fbl_list else 0
    
    # 封单总额、大单统计
    seal_total_wan = sum(s['seal_wan'] for s in limit_list)
    seal_gt_1yi = sum(1 for s in limit_list if s['seal_wan'] >= 10000)
    
    # 按封板时间分组
    time_groups = defaultdict(int)
    for s in limit_list:
        t = s['close_time']
        if t:
            hour = t.split(':')[0]
            if int(hour) < 10:
                time_groups['09:25竞价'] += 1
            elif int(hour) < 11:
                time_groups['上午'] += 1
            elif int(hour) < 13:
                time_groups['午休'] += 1
            elif int(hour) < 14:
                time_groups['下午早段'] += 1
            else:
                time_groups['尾盘'] += 1
    
    result = {
        'limit_list': limit_list,
        'board_dist': dict(sorted(board_dist.items())),
        'limit_way_dist': dict(limit_way_dist),
        'time_groups': dict(time_groups),
        'stats': {
            'total': total,
            'yizi': yizi,
            'ziran': ziran,
            'zhaban_count': zhaban_count,
            'zhaban_rate': zhaban_rate,
            'avg_fbl': avg_fbl,
            'seal_total_wan': round(seal_total_wan, 1),
            'seal_count_gt_1yi': seal_gt_1yi,
            'max_board': max(board_dist.keys()) if board_dist else 0,
        },
    }
    mb = result["stats"]["max_board"]
    print(f'  东财涨停明细: {total}只涨停(一字{yizi}/自然{ziran}) 连板{mb}板 封单{seal_total_wan/10000:.0f}亿 炸板{zhaban_rate}%')
    return result


# ========== Step 1c: 昨涨停今日表现 API ==========

def fetch_pretoday_performance(trade_date=None):
    """从东方财富API获取昨涨停个股今日表现"""
    if trade_date is None:
        trade_date = str(date.today())
    result = {
        'total': 0,
        'avg_change': 0,
        'lianban_count': 0,
        'lianban_rate': 0,
        'die_gt_5_count': 0,
        'die_gt_5_rate': 0,
        'lianban_list': [],
        'die_list': [],
    }
    try:
        all_data = []
        page = 1
        while True:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://datacenter-web.eastmoney.com/api/data/v1/get?'
                 f'reportName=RPT_INTSELECTION_PRETODAY&columns=ALL'
                 f'&pageNumber={page}&pageSize=5000'
                 f'&sortColumns=CHANGE_RATE&sortTypes=-1'
                 f'&filter=(TRADE_DATE%3D%27{trade_date}%27)'],
                capture_output=True, timeout=15
            )
            raw = r.stdout.decode('utf-8', errors='replace')
            if not raw:
                break
            d = json.loads(raw)
            data = d.get('result', {}).get('data', [])
            if not data:
                break
            all_data.extend(data)
            total_pages = d.get('result', {}).get('pages', 0)
            if page >= total_pages:
                break
            page += 1
            time.sleep(0.1)
    except Exception as e:
        print(f'  东财昨涨停表现API失败: {e}')
        return result
    
    total = len(all_data)
    if total == 0:
        return result
    
    changes = []
    lianban = []
    die_gt_5 = []
    
    for r in all_data:
        chg = float(r.get('CHANGE_RATE', 0) or 0)
        changes.append(chg)
        
        # 连板：今日继续涨停（CONTINUS_UPLIMITS >= PRE_CONTINUS_UPLIMITS 即连板成功）
        cont = int(r.get('CONTINUS_UPLIMITS', 0) or 0)
        pre_cont = int(r.get('PRE_CONTINUS_UPLIMITS', 0) or 0)
        is_lianban = cont >= pre_cont and pre_cont > 0
        
        if is_lianban:
            lianban.append({
                'code': r.get('SECURITY_CODE', ''),
                'name': r.get('SECURITY_NAME_ABBR', ''),
                'chg': round(chg, 2),
                'pre_board': pre_cont,
                'now_board': cont,
            })
        
        if chg <= -5:
            die_gt_5.append({
                'code': r.get('SECURITY_CODE', ''),
                'name': r.get('SECURITY_NAME_ABBR', ''),
                'chg': round(chg, 2),
            })
    
    avg_change = round(sum(changes) / len(changes), 2)
    lianban_count = len(lianban)
    lianban_rate = round(lianban_count / total * 100, 1)
    die_gt_5_count = len(die_gt_5)
    die_gt_5_rate = round(die_gt_5_count / total * 100, 1)
    
    result = {
        'total': total,
        'avg_change': avg_change,
        'lianban_count': lianban_count,
        'lianban_rate': lianban_rate,
        'die_gt_5_count': die_gt_5_count,
        'die_gt_5_rate': die_gt_5_rate,
        'lianban_list': lianban[:10],
        'die_list': die_gt_5[:10],
    }
    print(f'  昨涨停表现: {total}只 均涨幅{avg_change:+.2f}% 连板{lianban_count}({lianban_rate}%) 大跌{die_gt_5_count}({die_gt_5_rate}%)')
    return result


# ========== Step 2: 竞价数据 ==========

def get_auction_data():
    """从V2board读取竞价趋势数据"""
    try:
        fp = os.path.join(V2BOARD, 'auction_trend.json')
        if os.path.exists(fp):
            with open(fp) as f:
                d = json.load(f)
            return {
                'bid_trend': d.get('trend', '未知'),
                'bid_gaokai_rate': d.get('gaokai_rate', 0),
                'bid_limit_count': d.get('limit_count', 0),
                'bid_amount': d.get('bid_amount', 0),
            }
    except:
        pass
    return {'bid_trend': '无', 'bid_gaokai_rate': 0, 'bid_limit_count': 0, 'bid_amount': 0}

# ========== Step 3: 炸板/回封 ==========

def get_zhaban_data():
    """从封单采集数据计算炸板率和回封率"""
    # 从limit_order_history.db读取今日数据
    try:
        lob_db = os.path.join(DATA_DIR, 'limit_order_history.db')
        # 获取今日所有快照时间点
        times_sql = sql_val(lob_db, 
            f"SELECT DISTINCT ts FROM limit_orders WHERE date='{date.today()}' ORDER BY ts")
        times = [t.strip() for t in times_sql.split('\n') if t.strip()]
        
        if len(times) < 2:
            return {'zhaban_count': 0, 'zhaban_rate': 0, 'huifeng_count': 0, 'huifeng_rate': 0}
        
        # 取最早和最晚的快照
        first_ts = times[0]
        last_ts = times[-1]
        
        # 最早的涨停列表
        first_limits_raw = sql_json(lob_db,
            f"SELECT code, name FROM limit_orders WHERE date='{date.today()}' AND ts='{first_ts}' AND is_limit=1")
        first_codes = set(r['code'] for r in first_limits_raw if isinstance(r, dict))
        
        # 最晚的涨停列表
        last_limits_raw = sql_json(lob_db,
            f"SELECT code, name FROM limit_orders WHERE date='{date.today()}' AND ts='{last_ts}' AND is_limit=1")
        last_codes = set(r['code'] for r in last_limits_raw if isinstance(r, dict))
        
        zhaban = len(first_codes - last_codes)
        persist = len(first_codes & last_codes)
        total = len(first_codes)
        zhaban_rate = round(zhaban / total * 100, 1) if total > 0 else 0
        
        # 回封：中间曾炸板但最后又封上
        all_times_codes = []
        for t in times:
            tc = sql_json(lob_db,
                f"SELECT code FROM limit_orders WHERE date='{date.today()}' AND ts='{t}' AND is_limit=1")
            all_times_codes.append(set(r['code'] for r in tc if isinstance(r, dict)))
        
        huifeng = 0
        for i in range(1, len(all_times_codes)):
            newly_back = all_times_codes[i] - all_times_codes[i-1]
            for c in newly_back:
                # 如果这个code在更早的某个时间点出现过（之前炸板了），现在重新封上
                for j in range(i-1):
                    if c in all_times_codes[j]:
                        huifeng += 1
                        break
        
        huifeng_rate = round(huifeng / max(zhaban, 1) * 100, 1) if zhaban > 0 else 0
        
        return {'zhaban_count': zhaban, 'zhaban_rate': zhaban_rate,
                'huifeng_count': huifeng, 'huifeng_rate': huifeng_rate}
    except Exception as e:
        return {'zhaban_count': 0, 'zhaban_rate': 0, 'huifeng_count': 0, 'huifeng_rate': 0}

# ========== Step 4: 板块数据 ==========

def get_sector_data():
    """从sector_indexes.db读取今日板块数据"""
    today = str(date.today())
    # 先查最晚的板块数据
    rows = sql_json(SECTOR_DB,
        f"SELECT sector_name, limit_up_count, avg_change FROM sector_daily_index "
        f"WHERE date='{today}' ORDER BY limit_up_count DESC LIMIT 20")
    
    if not rows:
        # 回溯最近
        last_date = sql_val(SECTOR_DB,
            "SELECT date FROM sector_daily_index ORDER BY date DESC LIMIT 1")
        if last_date:
            rows = sql_json(SECTOR_DB,
                f"SELECT sector_name, limit_up_count, avg_change FROM sector_daily_index "
                f"WHERE date='{last_date}' ORDER BY limit_up_count DESC LIMIT 20")
    
    boom_count = sum(1 for r in rows if isinstance(r, dict) and r.get('limit_up_count', 0) >= 3)
    top_sectors = [{'name': r['sector_name'], 'limit': r['limit_up_count']} 
                   for r in rows if isinstance(r, dict)][:5]
    
    return {'sector_boom_count': boom_count, 'sector_total': len(rows),
            'top_sectors': json.dumps(top_sectors, ensure_ascii=False)}

# ========== Step 5: 龙虎榜 ==========

def get_lhb_data():
    """读取今日龙虎榜数据（游资/机构/散户三分类）"""
    today = str(date.today())
    
    def get_cat(type_filter, label):
        """按类型筛选"""
        rows = sql_json(LHB_DB,
            f"SELECT ROUND(SUM(CASE WHEN d.direction='buy' THEN d.net ELSE 0 END)/10000, 0) as buy_wan, "
            f"ROUND(SUM(CASE WHEN d.direction='sell' THEN d.net ELSE 0 END)/10000, 0) as sell_wan, "
            f"COUNT(DISTINCT l.code) as stocks "
            f"FROM lhb_list l JOIN lhb_detail d ON l.date=d.date AND l.code=d.code "
            f"WHERE l.date='{today}' AND l.type IN ({type_filter})")
        if rows and isinstance(rows[0], dict) and rows[0].get('stocks', 0) > 0:
            d = rows[0]
            return {
                'buy': float(d.get('buy_wan', 0) or 0),
                'sell': float(d.get('sell_wan', 0) or 0),
                'net': float(d.get('buy_wan', 0) or 0) - float(d.get('sell_wan', 0) or 0),
                'stocks': d.get('stocks', 0),
            }
        return {'buy': 0, 'sell': 0, 'net': 0, 'stocks': 0}
    
    # 游资：知名游资席位 04,34,37,38,39,40
    youzi = get_cat("'04','34','37','38','39','40'", '游资')
    # 机构：03
    jigou = get_cat("'03'", '机构')
    # 散户：05
    sanhu = get_cat("'05'", '散户')
    
    return {
        'lhb_stocks': youzi['stocks'] + jigou['stocks'] + sanhu['stocks'],
        'youzi_buy_wan': youzi['buy'], 'youzi_sell_wan': youzi['sell'], 'youzi_net_wan': youzi['net'],
        'jigou_buy_wan': jigou['buy'], 'jigou_sell_wan': jigou['sell'], 'jigou_net_wan': jigou['net'],
        'sanhu_buy_wan': sanhu['buy'], 'sanhu_sell_wan': sanhu['sell'], 'sanhu_net_wan': sanhu['net'],
    }

# ========== Step 6: 板块资金流向（东方财富API） ==========

def get_sector_capital_flow():
    """从东方财富获取板块资金流向TOP5"""
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
             'https://push2.eastmoney.com/api/qt/clist/get?fid=f62&po=1&pz=5&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:2&fields=f12,f14,f3,f62,f66,f72,f78,f81,f184,f205'],
            capture_output=True, timeout=10
        )
        raw = r.stdout.decode('utf-8', errors='replace')
        if not raw:
            return {'data': [], 'main_total': 0}
        data = json.loads(raw)
        diffs = data.get('data', {}).get('diff', [])
        result = []
        main_total = 0
        for d in diffs[:5]:
            main = d.get('f62', 0) / 1e8
            main_total += main
            result.append({
                'name': d.get('f14', ''),
                'chg': d.get('f3', 0),
                'main_net_yi': round(main, 2),
                'super_large_yi': round(d.get('f66', 0) / 1e8, 2),
                'large_yi': round(d.get('f72', 0) / 1e8, 2),
                'medium_yi': round(d.get('f78', 0) / 1e8, 2),
                'small_yi': round(d.get('f81', 0) / 1e8, 2),
                'leader': d.get('f184', ''),
            })
        return {'data': result, 'main_total': round(main_total, 2)}
    except Exception as e:
        return {'data': [], 'main_total': 0}

# ========== Step 7: 概念板块异动 ==========

def get_concept_movers():
    """从东方财富获取概念板块涨幅TOP3"""
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
             'https://push2.eastmoney.com/api/qt/clist/get?fid=f3&po=1&pz=3&pn=1&np=1&fltt=2&invt=2&fs=m:90+t:3&fields=f12,f14,f3'],
            capture_output=True, timeout=10
        )
        raw = r.stdout.decode('utf-8', errors='replace')
        if not raw:
            return []
        data = json.loads(raw)
        return [{'name': d['f14'], 'chg': d['f3']} 
                for d in data.get('data', {}).get('diff', [])[:3]]
    except:
        return []

# ========== Step 8: 大盘全天资金流向 ==========

def get_market_capital_flow():
    """获取大盘全天主力资金净流向"""
    try:
        r = subprocess.run(
            ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
             'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid=1.000001&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55&klt=1&lmt=1'],
            capture_output=True, timeout=10
        )
        raw = r.stdout.decode('utf-8', errors='replace')
        if not raw:
            return {'main_net_yi': 0, 'super_large_yi': 0, 'large_yi': 0, 'medium_yi': 0, 'small_yi': 0}
        data = json.loads(raw)
        kline = data.get('data', {}).get('klines', [''])[0]
        parts = kline.split(',')
        if len(parts) >= 5:
            return {
                'main_net_yi': round(float(parts[1]) / 1e8, 2),
                'super_large_yi': round(float(parts[2]) / 1e8, 2),
                'large_yi': round(float(parts[3]) / 1e8, 2),
                'medium_yi': round(float(parts[4]) / 1e8, 2),
                'small_yi': round(float(parts[5]) / 1e8, 2) if len(parts) > 5 else 0,
            }
    except:
        pass
    return {'main_net_yi': 0, 'super_large_yi': 0, 'large_yi': 0, 'medium_yi': 0, 'small_yi': 0}

# ========== Step 6: 最高板 ==========

def get_board_data():
    """从limit_strength或tetegu_cache获取最高板数据"""
    today = str(date.today())
    # 先查limit_strength
    row = sql_json(LIMIT_DB,
        f"SELECT max_board, total_limit FROM limit_strength WHERE date='{today}'")
    if row and isinstance(row[0], dict):
        d = row[0]
        mb = d.get('max_board', 0) or 0
        if mb > 0:
            return {'max_board': mb}
    # fallback: tetegu_cache.market_emotion
    tetegu_db = os.path.join(DATA_DIR, 'tetegu_cache.db')
    if os.path.exists(tetegu_db):
        row2 = sql_json(tetegu_db,
            f"SELECT max_board, total_limit_count FROM market_emotion WHERE date='{today}'")
        if row2 and isinstance(row2[0], dict):
            mb = row2[0].get('max_board', 0) or 0
            if mb > 0:
                return {'max_board': mb}
    # 再fallback: 从sector_daily_index计算
    import subprocess as sp
    try:
        # 从tetegu_cache的limit_genes直接查
        r = sp.run(['sqlite3', '-json', tetegu_db,
            f"SELECT MAX(board_count) as max_board FROM limit_genes WHERE date='{today}'"],
            capture_output=True, text=True, timeout=10)
        if r.stdout.strip():
            d = json.loads(r.stdout)[0]
            mb = d.get('max_board', 0) or 0
            if mb > 0:
                return {'max_board': mb}
    except: pass
    return {'max_board': 0}

# ========== Step 7: 涨停前趋势(从K线) ==========

def get_limit_pretrend(codes_list):
    """计算今日涨停股的平均MA20乖离和60日回撤"""
    if not codes_list:
        return {'avg_ma20_dev': 0, 'avg_60d_retrace': 0, 'avg_5d_momentum': 0}
    
    total_ma20 = 0; total_retrace = 0; total_momentum = 0
    count = 0
    
    for code in codes_list[:20]:  # 只看前20只
        rows = sql_json(KLINE_DB,
            f"SELECT date, close, high, low FROM kline WHERE code='{code}' ORDER BY date DESC LIMIT 65")
        if len(rows) < 21:
            continue
        
        today_close = rows[0]['close']
        
        # MA20
        ma20 = sum(r['close'] for r in rows[:20]) / 20
        ma20_dev = (today_close - ma20) / ma20 * 100
        
        # 60日回撤
        highs = [r['high'] for r in rows[:60]]
        lows = [r['low'] for r in rows[:60]]
        max_high = max(highs) if highs else today_close
        min_low = min(lows) if lows else today_close
        retrace = 0
        if max_high > 0:
            retrace = (today_close - max_high) / max_high * 100
        
        # 5日动量
        close_5d = rows[5]['close'] if len(rows) > 5 else today_close
        momentum = (today_close - close_5d) / close_5d * 100 if close_5d > 0 else 0
        
        total_ma20 += ma20_dev
        total_retrace += retrace
        total_momentum += momentum
        count += 1
    
    if count == 0:
        return {'avg_ma20_dev': 0, 'avg_60d_retrace': 0, 'avg_5d_momentum': 0}
    
    return {
        'avg_ma20_dev': round(total_ma20 / count, 1),
        'avg_60d_retrace': round(total_retrace / count, 1),
        'avg_5d_momentum': round(total_momentum / count, 1),
    }

# ========== Step 9: 个股异动扫描（涨幅>7%未涨停 + 快速拉升/跳水） ==========

def scan_stock_anomaly(codes):
    """扫描盘中个股异动：涨幅>7%未涨停、涨幅>5%且量比>2、大跌>5%"""
    surge = []
    volume_spike = []
    crash = []
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        cs = ','.join(mkt(c) for c in batch)
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '3', '--max-time', '6',
                 f'https://qt.gtimg.cn/q={cs}'],
                capture_output=True, timeout=8
            )
            raw = r.stdout.decode('gbk', errors='replace')
            for line in raw.strip().split('\n'):
                if not line or '=' not in line: continue
                parts = line.split('~')
                if len(parts) < 55: continue
                try:
                    code = parts[2].strip()
                    name = parts[1].strip()
                    cur = sf(parts[3]); prev = sf(parts[4])
                    high = sf(parts[33])
                    vol_r = sf(parts[49])
                    amount = sf(parts[37])
                    if prev <= 0: continue
                    chg = (cur - prev) / prev * 100
                    amp = (high - prev) / prev * 100 if prev > 0 else 0
                    is_limit = chg >= 9.5 and cur >= prev * 1.09

                    if amp >= 7 and not is_limit:
                        surge.append({'code': code, 'name': name, 'chg': round(chg, 1),
                                       'amp': round(amp, 1), 'vol_ratio': round(vol_r, 1),
                                       'amount_wan': round(amount / 10000, 0)})

                    if chg > 5 and vol_r > 2.5:
                        volume_spike.append({'code': code, 'name': name, 'chg': round(chg, 1),
                                              'vol_ratio': round(vol_r, 1), 'amount_wan': round(amount / 10000, 0)})

                    if chg <= -5:
                        crash.append({'code': code, 'name': name, 'chg': round(chg, 1),
                                       'vol_ratio': round(vol_r, 1)})
                except:
                    continue
        except:
            continue
        time.sleep(0.02)

    surge = sorted(surge, key=lambda x: -abs(x['chg']))[:10]
    volume_spike = sorted(volume_spike, key=lambda x: -abs(x['chg']))[:10]
    crash = sorted(crash, key=lambda x: x['chg'])[:10]

    return {
        'surge_count': len(surge),
        'surge_top': json.dumps([{'n': s['name'], 'c': s['code'], 'chg': s['chg'], 'amp': s['amp']} for s in surge[:5]], ensure_ascii=False),
        'spike_count': len(volume_spike),
        'spike_top': json.dumps([{'n': s['name'], 'c': s['code'], 'chg': s['chg'], 'vr': s['vol_ratio']} for s in volume_spike[:5]], ensure_ascii=False),
        'crash_count': len(crash),
        'crash_top': json.dumps([{'n': s['name'], 'c': s['code'], 'chg': s['chg']} for s in crash[:5]], ensure_ascii=False),
    }

# ========== Step 10: 概念板块资金异动 ==========

def get_concept_anomaly(limit_up_detail):
    """从涨停数据中提取概念异动"""
    if not limit_up_detail:
        return {'concept_boom': 0, 'concept_list': '[]'}
    hot_stocks = len(limit_up_detail)
    return {'concept_boom': hot_stocks, 'concept_list': '[]'}

# ========== Step 11: 盘中恐慌/高潮信号 ==========

def get_market_extremes(scan_result):
    """从扫描结果衍生恐慌/高潮信号"""
    lu = scan_result.get('limit_up', 0)
    ld = scan_result.get('limit_down', 0)
    up = scan_result.get('up_count', 0)
    down = scan_result.get('down_count', 0)
    total = up + down
    zh = up / total * 100 if total > 0 else 50

    panic = 0
    if ld >= 10: panic += 2
    elif ld >= 5: panic += 1
    if zh < 30: panic += 2
    elif zh < 40: panic += 1
    if scan_result.get('vol_gt_5', 0) > scan_result.get('vol_lt_05', 0) * 3: panic += 1

    boom = 0
    if lu >= 50: boom += 2
    elif lu >= 30: boom += 1
    if zh > 70: boom += 2
    elif zh > 60: boom += 1
    if scan_result.get('yizi', 0) >= 10: boom += 1

    if panic >= 3: level = '恐慌'
    elif panic >= 2: level = '偏弱'
    elif boom >= 3: level = '高潮'
    elif boom >= 2: level = '活跃'
    else: level = '平淡'

    return {'market_mood': level, 'panic_score': panic, 'boom_score': boom}

def init_db():
    desired_cols = 74  # 目标列数（含东财涨停明细+昨涨停表现）
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 检查现有表结构
    try:
        c.execute("SELECT * FROM day_full LIMIT 0")
        actual = len(c.description)
        if actual == desired_cols:
            conn.close()
            return
        print(f"  ⚠ 表结构旧({actual}列→{desired_cols}列)，重建中...")
        c.execute("ALTER TABLE day_full RENAME TO day_full_old")
    except:
        # 表不存在，直接建
        pass
    
    c.execute('''CREATE TABLE IF NOT EXISTS day_full (
        date TEXT PRIMARY KEY, ts TEXT,
        up_count INTEGER, down_count INTEGER, zh_ratio REAL,
        limit_up INTEGER, limit_down INTEGER,
        yizi INTEGER, suoliang INTEGER, fangliang INTEGER,
        total_seal_wan REAL,
        vol_lt_05 INTEGER, vol_05_07 INTEGER, vol_07_1 INTEGER,
        vol_1_3 INTEGER, vol_3_5 INTEGER, vol_gt_5 INTEGER,
        gap_1_3 INTEGER, gap_3_5 INTEGER, gap_5_7 INTEGER, gap_gt_7 INTEGER,
        bid_trend TEXT, bid_gaokai_rate REAL, bid_limit_count INTEGER, bid_amount REAL,
        zhaban_count INTEGER, zhaban_rate REAL, huifeng_count INTEGER, huifeng_rate REAL,
        sector_boom_count INTEGER, sector_total INTEGER, top_sectors TEXT,
        youzi_buy_wan REAL, youzi_sell_wan REAL, youzi_net_wan REAL,
        jigou_buy_wan REAL, jigou_sell_wan REAL, jigou_net_wan REAL,
        sanhu_buy_wan REAL, sanhu_sell_wan REAL, sanhu_net_wan REAL,
        sector_main_top TEXT, sector_main_total REAL,
        concept_top TEXT,
        surge_count INTEGER, surge_top TEXT,
        spike_count INTEGER, spike_top TEXT,
        crash_count INTEGER, crash_top TEXT,
        market_mood TEXT, panic_score INTEGER, boom_score INTEGER,
        market_main_net REAL, market_super_large REAL,
        market_large REAL, market_medium REAL, market_small REAL,
        avg_ma20_dev REAL, avg_60d_retrace REAL, avg_5d_momentum REAL,
        max_board INTEGER,
        board_dist_json TEXT, limit_way_dist_json TEXT,
        limit_time_groups_json TEXT, limit_detail_json TEXT,
        pretoday_total INTEGER, pretoday_avg_change REAL,
        pretoday_lianban_count INTEGER, pretoday_lianban_rate REAL,
        pretoday_die_gt_5_count INTEGER, pretoday_die_gt_5_rate REAL,
        pretoday_lianban_json TEXT, pretoday_die_json TEXT
    )''')
    
    # 迁移旧数据
    try:
        c.execute('''INSERT INTO day_full (
            date, ts, up_count, down_count, zh_ratio,
            limit_up, limit_down, yizi, suoliang, fangliang, total_seal_wan,
            vol_lt_05, vol_05_07, vol_07_1, vol_1_3, vol_3_5, vol_gt_5,
            gap_1_3, gap_3_5, gap_5_7, gap_gt_7,
            bid_trend, bid_gaokai_rate, bid_limit_count, bid_amount,
            zhaban_count, zhaban_rate, huifeng_count, huifeng_rate,
            sector_boom_count, sector_total, top_sectors,
            youzi_buy_wan, youzi_sell_wan, youzi_net_wan,
            jigou_buy_wan, jigou_sell_wan, jigou_net_wan,
            sanhu_buy_wan, sanhu_sell_wan, sanhu_net_wan,
            sector_main_top, sector_main_total, concept_top,
            surge_count, surge_top, spike_count, spike_top,
            crash_count, crash_top,
            market_mood, panic_score, boom_score,
            market_main_net, market_super_large, market_large, market_medium, market_small,
            avg_ma20_dev, avg_60d_retrace, avg_5d_momentum, max_board
        ) SELECT * FROM day_full_old''')
        c.execute("DROP TABLE day_full_old")
        print(f"  ✅ {actual}→{desired_cols}列重建完成，旧数据已迁移")
    except:
        pass
    conn.commit()
    conn.close()

def run():
    now = datetime.now()
    today = str(date.today())
    ts = now.strftime('%H:%M:%S')
    
    print(f"\n{'='*55}")
    print(f"📊 市场日数据整合 — {today} {ts}")
    print(f"{'='*55}")
    
    init_db()
    
    codes = load_codes()
    print(f"\n[1/8] 全量扫描 ({len(codes)}只)...")
    market = scan_full_market(codes)
    print(f"  涨停{market['limit_up']}(一字{market['yizi']}/缩量{market['suoliang']}/放量{market['fangliang']}) "
          f"跌停{market['limit_down']} 涨{market['up_count']}跌{market['down_count']}")
    print(f"  封单总额: {market['total_seal_wan']:.0f}万")

    print(f"\n[1b/12] 东财涨停明细...")
    limit_detail = fetch_limit_detail_from_eastmoney()
    print(f"  连板分布: {dict(sorted(limit_detail['board_dist'].items())[:6])}")
    print(f"  封板时间: {dict(limit_detail['time_groups'])}")

    print(f"\n[1c/12] 昨涨停今日表现...")
    yesterday = str(date.today() - timedelta(days=1))
    pretoday = fetch_pretoday_performance(yesterday)
    
    print(f"\n[2/12] 竞价数据...")
    auction = get_auction_data()
    print(f"  趋势:{auction['bid_trend']} 高开率:{auction['bid_gaokai_rate']}%")
    
    print(f"\n[3/12] 炸板/回封...")
    zhaban = get_zhaban_data()
    print(f"  炸板{zhaban['zhaban_count']}({zhaban['zhaban_rate']}%) 回封{zhaban['huifeng_count']}({zhaban['huifeng_rate']}%)\n")

    print(f"\n[4/12] 板块数据...")
    sector = get_sector_data()
    print(f"  板块爆发{sector['sector_boom_count']}个 共{sector['sector_total']}个板块\n")

    print(f"\n[5/12] 龙虎榜(三资金分类)...")
    lhb = get_lhb_data()
    print(f"  游资: 净{lhb['youzi_net_wan']:+.0f}万 | 机构: 净{lhb['jigou_net_wan']:+.0f}万 | 散户: 净{lhb['sanhu_net_wan']:+.0f}万\n")

    print(f"\n[6/12] 板块资金流向...")
    sector_flow = get_sector_capital_flow()
    print(f"  主力流入TOP5总额: {sector_flow['main_total']:+.1f}亿")
    for s in sector_flow['data'][:3]:
        print(f"    {s['name']:12s} 主力{s['main_net_yi']:+.1f}亿 涨{s['chg']:+.1f}%")

    print(f"\n[7/12] 概念异动+大盘资金...")
    concepts = get_concept_movers()
    for c in concepts:
        print(f"  概念: {c['name']} +{c['chg']}%")

    capital = get_market_capital_flow()
    print(f"  大盘主力: {capital['main_net_yi']:+.1f}亿 | 超大单: {capital['super_large_yi']:+.1f}亿\n")

    print(f"\n[8/12] 最高板+涨停前趋势...")
    board = get_board_data()
    limit_codes = list(market['limit_up_detail'].keys())
    pretrend = get_limit_pretrend(limit_codes)
    print(f"  最高板:{board['max_board']}")

    print(f"\n[9/12] 个股异动扫描...")
    anomaly = scan_stock_anomaly(codes)
    mood = get_market_extremes(market)
    print(f"  异动拉升{anomaly['surge_count']}只 放量异动{anomaly['spike_count']}只 跳水{anomaly['crash_count']}只")
    print(f"  市场情绪: {mood['market_mood']}(恐慌{mood['panic_score']}/高潮{mood['boom_score']})")

    print(f"\n[10/12] 写入数据库...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    total = market['up_count'] + market['down_count']
    zh_ratio = round(market['up_count'] / total * 100, 1) if total > 0 else 0
    
    c.execute('''INSERT OR REPLACE INTO day_full VALUES (?,?,
        ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,
        ?,?,?, ?,?,
        ?,?,?, ?,?,?,
        ?,?,?, ?,?,
        ?,?,
        ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,
        ?,?,?, ?,?,?, ?,?,?, ?,?,?)''', (
        today, ts,
        market['up_count'], market['down_count'], zh_ratio,
        market['limit_up'], market['limit_down'],
        market['yizi'], market['suoliang'], market['fangliang'],
        market['total_seal_wan'],
        market['vol_lt_05'], market['vol_05_07'], market['vol_07_1'],
        market['vol_1_3'], market['vol_3_5'], market['vol_gt_5'],
        market['gap_1_3'], market['gap_3_5'], market['gap_5_7'], market['gap_gt_7'],
        auction['bid_trend'], auction['bid_gaokai_rate'], auction['bid_limit_count'], auction['bid_amount'],
        zhaban['zhaban_count'], zhaban['zhaban_rate'], zhaban['huifeng_count'], zhaban['huifeng_rate'],
        sector['sector_boom_count'], sector['sector_total'], sector['top_sectors'],
        lhb['youzi_buy_wan'], lhb['youzi_sell_wan'], lhb['youzi_net_wan'],
        lhb['jigou_buy_wan'], lhb['jigou_sell_wan'], lhb['jigou_net_wan'],
        lhb['sanhu_buy_wan'], lhb['sanhu_sell_wan'], lhb['sanhu_net_wan'],
        json.dumps([s['name'] for s in sector_flow['data']], ensure_ascii=False),
        sector_flow['main_total'],
        json.dumps([{'n': c['name'], 'c': c['chg']} for c in concepts], ensure_ascii=False),
        anomaly['surge_count'], anomaly['surge_top'],
        anomaly['spike_count'], anomaly['spike_top'],
        anomaly['crash_count'], anomaly['crash_top'],
        mood['market_mood'], mood['panic_score'], mood['boom_score'],
        capital['main_net_yi'], capital['super_large_yi'],
        capital['large_yi'], capital['medium_yi'], capital['small_yi'],
        pretrend['avg_ma20_dev'], pretrend['avg_60d_retrace'], pretrend['avg_5d_momentum'],
        board['max_board'],
        # 东财涨停明细
        json.dumps(limit_detail['board_dist'], ensure_ascii=False),
        json.dumps(limit_detail['limit_way_dist'], ensure_ascii=False),
        json.dumps(limit_detail['time_groups'], ensure_ascii=False),
        json.dumps([{
            'c': s['code'], 'n': s['name'],
            'b': s['board_desc'], 'bc': s['board_count'],
            'lw': s['limit_way'], 'ct': s['close_time'],
            'sw': s['seal_wan'],
        } for s in limit_detail['limit_list'][:50]], ensure_ascii=False),
        # 昨涨停今日表现
        pretoday['total'],
        pretoday['avg_change'],
        pretoday['lianban_count'],
        pretoday['lianban_rate'],
        pretoday['die_gt_5_count'],
        pretoday['die_gt_5_rate'],
        json.dumps(pretoday['lianban_list'], ensure_ascii=False),
        json.dumps(pretoday['die_list'], ensure_ascii=False),
    ))
    conn.commit()
    conn.close()
    
    print(f"  ✅ {today} 写入完成！共{62}个字段（含东财涨停明细+昨涨停表现）")
    print(f"\n{'='*55}")
    print(f"📊 今日画像摘要")
    print(f"{'='*55}")
    print(f"  涨跌: {market['up_count']}/{market['down_count']} ({zh_ratio}%)")
    print(f"  涨停: {market['limit_up']} | 炸板率: {zhaban['zhaban_rate']}% | 回封: {zhaban['huifeng_rate']}%")
    print(f"  竞价: {auction['bid_trend']} | 板块爆发: {sector['sector_boom_count']}个")
    print(f"  游资净额: {lhb['youzi_net_wan']:+.0f}万 | 最高板: {limit_detail['stats']['max_board']}板")
    print(f"  涨停质量: 一字{limit_detail['stats']['yizi']} 自然{limit_detail['stats']['ziran']} | 封单{limit_detail['stats']['seal_total_wan']/10000:.0f}亿")
    print(f"  昨涨停均涨幅: {pretoday['avg_change']:+.2f}% | 连板率: {pretoday['lianban_rate']}%")
    print(f"  涨停前趋势: MA20乖离{pretrend['avg_ma20_dev']:+.1f}%")

if __name__ == '__main__':
    run()
