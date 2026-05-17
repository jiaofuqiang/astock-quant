#!/usr/bin/env python3
"""
作战面板数据更新器 — 每分钟运行
1. 运行三资金合力扫描
2. 保存扫描结果到 /home/ubuntu/astock/scan_data.txt
3. 检测买入信号（板块级 ≥3只合力≥70分）
4. 有信号时输出到 buy_signal.txt
"""
import os, sys, json, re, subprocess, shutil
from datetime import datetime
import json

BASE = "/home/ubuntu/astock"
DATA_FILE = os.path.join(BASE, "scan_data.txt")
SIGNAL_FILE = os.path.join(BASE, "buy_signal.txt")
WATCH_FILE = os.path.join(BASE, "watch_pool.txt")
RETAIL_FILE = os.path.join(BASE, "retail_sentiment.txt")
BAN_FILE = os.path.join(BASE, "ban_order_data.json")
F2_FILE = os.path.join(BASE, "f2_weipan_data.txt")
SECTOR_INDEX_FILE = os.path.join(BASE, "sector_index_data.json")

# 热门板块名单（用于面板高亮展示）
HOT_SECTORS = ['机器人', '低空经济', '半导体', '储能', '锂电池', '新能源汽车',
               '光模块与光通信', '数据中心', 'AI算力', '存储芯片', 'AI芯片',
               '智能驾驶', '消费电子', '液冷与散热', '软件与应用', '军工', '光伏']

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}')

def collect_ban_for_dashboard():
    """收集涨停封单数据供面板使用"""
    try:
        ban_db = os.path.join(BASE, 'data', 'ban_order.db')
        if not os.path.exists(ban_db):
            return []
        conn = sqlite3.connect(ban_db)
        c = conn.cursor()
        date = datetime.now().strftime('%Y-%m-%d')
        c.execute("""
            SELECT code, name, MAX(timestamp), ban_amount_wan, is_limit_up, change_pct
            FROM ban_order WHERE date = ? AND is_limit_up = 1
            GROUP BY code ORDER BY ban_amount_wan DESC
        """, (date,))
        rows = c.fetchall()
        conn.close()
        
        # 再获取三资金合力分
        result = []
        for row in rows:
            code, name, ts, ban_amt, is_limit, chg = row
            result.append({
                'code': code, 'name': name,
                'ban_amount_wan': ban_amt or 0,
                'change_pct': chg or 0,
                'score': 0,  # 后续可通过扫描补齐
            })
        return result
    except:
        return []

def export_sector_index_data():
    """从 sector_indexes.db 导出今日板块排名和联动数据
    如果K线库没有今日数据，尝试用三资金扫描的实时行情计算"""
    sector_db = os.path.join(BASE, "data", "sector_indexes.db")
    if not os.path.exists(sector_db):
        log(f"⚠ sector_indexes.db 不存在，跳过板块指数导出")
        return
    
    # 获取最新K线日期和今天
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db,
        "SELECT MAX(date) FROM sector_daily_index"],
        capture_output=True, text=True, timeout=10)
    latest_stored_date = r.stdout.strip()
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 如果最新存储日期就是今天，直接用
    if latest_stored_date == today:
        use_date = today
        log(f"  📊 板块数据来自K线库: {use_date}")
    else:
        # 尝试用实时行情计算今日数据
        log(f"  ⚡ K线库最新: {latest_stored_date}, 尝试用腾讯实时行情计算今日({today})板块指数...")
        realtime_data = calc_realtime_sector_index(today, latest_stored_date, sector_db)
        if realtime_data:
            # 直接写文件并返回
            with open(SECTOR_INDEX_FILE, 'w') as f:
                json.dump(realtime_data, f, ensure_ascii=False, indent=2)
            log(f"  ✅ 实时板块指数已导出 ({len(realtime_data['hot_sectors'])}热门+{len(realtime_data['other_sectors'])}其他)")
            return
        else:
            log(f"  ⚠ 实时数据不可用，回退到K线库最新日期: {latest_stored_date}")
            use_date = latest_stored_date
    
    # — 以下为K线库数据路径 —
    # 获取今日所有板块排名（热门板块在前）
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, f"""
        SELECT sector_name, round(avg_change,2), round(median_change,2), 
               round(max_change,2), round(std_change,2), stock_count,
               up_count, down_count, limit_up_count, limit_down_count, round(avg_volume_ratio,2)
        FROM sector_daily_index 
        WHERE date = '{use_date}' AND stock_count >= 5
        ORDER BY avg_change DESC
    """], capture_output=True, text=True, timeout=10)
    
    all_sectors = []
    for row in r.stdout.strip().split('\n'):
        if not row.strip():
            continue
        parts = row.split('|')
        if len(parts) >= 11:
            name = parts[0].strip()
            is_hot = 1 if name in HOT_SECTORS else 0
            all_sectors.append({
                'name': name,
                'avg_change': float(parts[1]),
                'median_change': float(parts[2]),
                'max_change': float(parts[3]),
                'std_change': float(parts[4]),
                'stock_count': int(parts[5]),
                'up_count': int(parts[6]),
                'down_count': int(parts[7]),
                'limit_up': int(parts[8]),
                'limit_down': int(parts[9]),
                'avg_volume_ratio': float(parts[10]),
                'is_hot': is_hot,
            })
    
    # 获取前一日数据（计算涨跌变化）
    r2 = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, f"""
        SELECT date FROM sector_daily_index 
        WHERE date < '{use_date}'
        ORDER BY date DESC LIMIT 1
    """], capture_output=True, text=True, timeout=10)
    prev_date = r2.stdout.strip()
    
    prev_sectors = {}
    if prev_date:
        r3 = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, f"""
            SELECT sector_name, round(avg_change,2)
            FROM sector_daily_index 
            WHERE date = '{prev_date}' AND stock_count >= 5
        """], capture_output=True, text=True, timeout=10)
        for row in r3.stdout.strip().split('\n'):
            if not row.strip():
                continue
            parts = row.split('|')
            if len(parts) >= 2:
                prev_sectors[parts[0].strip()] = float(parts[1])
    
    # 添加前一日数据（用于计算变化）
    for s in all_sectors:
        s['prev_change'] = prev_sectors.get(s['name'], None)
    
    # 计算板块联动强度：涨停数+涨幅+热度
    for s in all_sectors:
        s['strength'] = round(s['limit_up'] * 2 + s['avg_change'] * 0.5 + (2 if s['is_hot'] else 0), 1)
    
    # 计算前5日均值
    r4 = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, f"""
        WITH RECURSIVE dates_t AS (
            SELECT DISTINCT date FROM sector_daily_index 
            WHERE date <= '{use_date}' AND date >= date('{use_date}', '-10 days')
            ORDER BY date DESC
        )
        SELECT sector_name, round(avg(avg_change), 2)
        FROM sector_daily_index 
        WHERE date IN (SELECT date FROM dates_t LIMIT 5)
        GROUP BY sector_name
    """], capture_output=True, text=True, timeout=10)
    avg5 = {}
    for row in r4.stdout.strip().split('\n'):
        if not row.strip():
            continue
        parts = row.split('|')
        if len(parts) >= 2:
            avg5[parts[0].strip()] = float(parts[1])
    
    for s in all_sectors:
        s['ma5_change'] = avg5.get(s['name'], None)
    
    # 区分热板块和冷板块
    hot_sectors = [s for s in all_sectors if s['is_hot']]
    other_sectors = [s for s in all_sectors if not s['is_hot']]
    
    data = {
        'date': use_date,
        'prev_date': prev_date,
        'hot_sectors': hot_sectors,
        'other_sectors': other_sectors,
        'correlations': get_sector_correlations_for_display(),
        'follower_stats': get_follower_stats_for_display(),
        'rotation_stats': get_rotation_stats_for_display(),
        'volatility': get_volatility_for_display(use_date),
        'timestamp': datetime.now().isoformat(),
    }
    
    with open(SECTOR_INDEX_FILE, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_sector_correlations_for_display():
    """获取板块相关系数（热门板块前5对）"""
    sector_db = os.path.join(BASE, "data", "sector_indexes.db")
    if not os.path.exists(sector_db):
        return []
    
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, """
        SELECT sector_a, sector_b, ROUND(correlation,2)
        FROM sector_correlation
        ORDER BY correlation DESC LIMIT 10
    """], capture_output=True, text=True, timeout=10)
    
    corrs = []
    for row in r.stdout.strip().split('\n'):
        if not row.strip(): continue
        parts = row.split('|')
        if len(parts) >= 3:
            corrs.append({'a': parts[0], 'b': parts[1], 'r': float(parts[2])})
    return corrs

def get_follower_stats_for_display():
    """获取跟风T+1收益统计（热门板块）"""
    sector_db = os.path.join(BASE, "data", "sector_indexes.db")
    if not os.path.exists(sector_db):
        return []
    
    hot_names = "','".join(HOT_SECTORS)
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, f"""
        SELECT sector_name,
               COUNT(*) as samples,
               ROUND(AVG(rank2_t1_change), 2) as r2_t1,
               ROUND(AVG(rank3_t1_change), 2) as r3_t1,
               ROUND(AVG(rank4_t1_change), 2) as r4_t1,
               ROUND(AVG(leader_change_pct), 2) as ldr_chg
        FROM sector_follower_backtest
        WHERE sector_name IN ('{hot_names}')
        GROUP BY sector_name
        ORDER BY r2_t1 DESC
    """], capture_output=True, text=True, timeout=10)
    
    stats = []
    for row in r.stdout.strip().split('\n'):
        if not row.strip(): continue
        parts = row.split('|')
        if len(parts) >= 6:
            stats.append({
                'name': parts[0],
                'samples': int(parts[1]),
                'r2_t1': float(parts[2]),
                'r3_t1': float(parts[3]),
                'r4_t1': float(parts[4]),
                'ldr_chg': float(parts[5]),
            })
    return stats

def get_rotation_stats_for_display():
    """获取轮动统计（热门板块强弱变化）"""
    sector_db = os.path.join(BASE, "data", "sector_indexes.db")
    if not os.path.exists(sector_db):
        return []
    
    # 获取各板块平均轮动排名变化
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, """
        SELECT sector_name,
               ROUND(AVG(rank_change), 2) as avg_rchg,
               ROUND(AVG(ABS(rank_change)), 2) as avg_abs_chg,
               SUM(CASE WHEN rank_change > 0 THEN 1 ELSE 0 END) as up_days,
               SUM(CASE WHEN rank_change < 0 THEN 1 ELSE 0 END) as down_days
        FROM sector_rotation_log
        GROUP BY sector_name
        ORDER BY avg_abs_chg DESC
        LIMIT 15
    """], capture_output=True, text=True, timeout=10)
    
    stats = []
    for row in r.stdout.strip().split('\n'):
        if not row.strip(): continue
        parts = row.split('|')
        if len(parts) >= 5:
            stats.append({
                'name': parts[0],
                'avg_rchg': float(parts[1]),
                'avg_abs': float(parts[2]),
                'up_days': int(parts[3]),
                'down_days': int(parts[4]),
            })
    return stats

def get_volatility_for_display(date_str):
    """获取板块内个股波动异常统计数据"""
    sector_db = os.path.join(BASE, "data", "sector_indexes.db")
    if not os.path.exists(sector_db):
        return {}
    
    # 先检查当天是否有数据，避免无匹配数据时全表扫描超时
    check = subprocess.run(['sqlite3', '-noheader', 'data/sector_indexes.db',
        f"SELECT COUNT(*) FROM sector_stock_volatility WHERE date='{date_str}'"],
        capture_output=True, text=True, timeout=5)
    has_data = check.stdout.strip().isdigit() and int(check.stdout.strip()) > 0
    if not has_data:
        return {'outlier_sectors': [], 'top_outliers': []}
    
    # 当日异常波动最多的板块
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, f"""
        SELECT v.sector_name, 
               COUNT(*) as outliers,
               COUNT(DISTINCT v.code) as stocks,
               ROUND(AVG(v.z_score), 2) as avg_z
        FROM sector_stock_volatility v
        WHERE v.date='{date_str}' AND v.is_outlier=1
        GROUP BY v.sector_name
        ORDER BY outliers DESC
        LIMIT 8
    """], capture_output=True, text=True, timeout=10)
    
    outlier_sectors = []
    for row in r.stdout.strip().split('\n'):
        if not row.strip(): continue
        parts = row.split('|')
        if len(parts) >= 4:
            outlier_sectors.append({
                'name': parts[0],
                'outliers': int(parts[1]),
                'stocks': int(parts[2]),
                'avg_z': float(parts[3]),
            })
    
    # 当日板块内波动最大的个股（Z-score最高）
    r2 = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, f"""
        SELECT v.sector_name, v.name, v.code, 
               ROUND(v.z_score, 2) as z,
               ROUND(v.change_pct, 2) as chg,
               ROUND(v.std_5d_change, 2) as vol
        FROM sector_stock_volatility v
        WHERE v.date='{date_str}' AND v.is_outlier=1
        ORDER BY ABS(v.z_score) DESC
        LIMIT 10
    """], capture_output=True, text=True, timeout=10)
    
    top_outliers = []
    for row in r2.stdout.strip().split('\n'):
        if not row.strip(): continue
        parts = row.split('|')
        if len(parts) >= 6:
            top_outliers.append({
                'sector': parts[0],
                'name': parts[1],
                'code': parts[2],
                'z_score': float(parts[3]),
                'change_pct': float(parts[4]),
                'volatility': float(parts[5]),
            })
    
    return {
        'outlier_sectors': outlier_sectors,
        'top_outliers': top_outliers,
    }

def calc_realtime_sector_index(today, latest_stored_date, sector_db):
    """用腾讯实时行情计算今日板块指数（当K线库还未更新时）"""
    try:
        # 获取所有板块及成分股
        sector_rows = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, """
            SELECT sector_name, group_concat(code)
            FROM sector_stock_daily 
            WHERE date = (SELECT MAX(date) FROM sector_stock_daily)
            GROUP BY sector_name
        """], capture_output=True, text=True, timeout=10)
        
        if not sector_rows.stdout.strip():
            return None
        
        sectors = {}
        for row in sector_rows.stdout.strip().split('\n'):
            parts = row.split('|', 1)
            if len(parts) == 2:
                sectors[parts[0].strip()] = parts[1].split(',')
        
        # 获取股票名称（从stock_info或kline缓存）
        name_rows = subprocess.run(['sqlite3', '-noheader', '-separator', '|', 
            os.path.join(BASE, "data", "kline_cache.db"),
            "SELECT DISTINCT code, COALESCE((SELECT name FROM stock_info WHERE code=k.code), '') FROM kline k LIMIT 500"],
            capture_output=True, text=True, timeout=10)
        names_in_db = {}
        for row in name_rows.stdout.strip().split('\n'):
            parts = row.split('|')
            if len(parts) >= 2:
                names_in_db[parts[0].strip()] = parts[1].strip()
        
        # 从 chain_engine 获取名称（更可靠）
        chain_rows = subprocess.run(['sqlite3', '-noheader', '-separator', '|',
            os.path.join(BASE, "data", "chain_engine.db"),
            "SELECT code, code FROM stock_chain_v2 WHERE level2 IN ('" + "','".join(list(sectors.keys())[:5]) + "')"],
            capture_output=True, text=True, timeout=10)
        
        all_codes = set()
        for codes in sectors.values():
            all_codes.update(codes)
        
        all_codes_list = list(all_codes)
        
        # 添加 sh/sz 前缀
        def add_prefix(code):
            if code.startswith(('sh', 'sz', 'sh', 'sz')):
                return code
            if code[0] in ('6', '5', '9'):
                return f"sh{code}"
            else:
                return f"sz{code}"
        
        tcodes = [add_prefix(c) for c in all_codes_list]
        
        # 分批获取腾讯实时行情
        batch_size = 50
        today_changes = {}  # code -> change_pct
        
        for batch_start in range(0, len(all_codes_list), batch_size):
            batch = tcodes[batch_start:batch_start+batch_size]
            codes_str = ','.join(batch)
            
            # 腾讯批量行情API
            tencent_url = f"http://qt.gtimg.cn/q={','.join(batch)}"
            req = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10', tencent_url],
                capture_output=True, timeout=15
            )
            raw_stdout = req.stdout.decode('gbk', errors='replace')
            if not raw_stdout:
                continue
            
            for line in raw_stdout.split('\n'):
                if not line.strip() or '=' not in line:
                    continue
                try:
                    # 格式: v_sh603986="1...name...price...change_pct..."
                    eq_pos = line.index('=')
                    content = line[eq_pos+2:-2]  # 去掉引号
                    fields = content.split('~')
                    if len(fields) < 33:
                        continue
                    code = fields[2] if fields[2] else ''
                    change_pct = float(fields[32]) if fields[32] else 0
                    if code:
                        today_changes[code] = change_pct
                except (ValueError, IndexError):
                    continue
        
        if not today_changes or len(today_changes) < 10:
            log(f"  ⚠ 实时数据不足 ({len(today_changes)}只)，跳过")
            return None
        
        # 获取前一日（K线库最新日）的板块数据用于对比
        prev_rows = subprocess.run(['sqlite3', '-noheader', '-separator', '|', sector_db, f"""
            SELECT sector_name, round(avg_change,2)
            FROM sector_daily_index 
            WHERE date = '{latest_stored_date}' AND stock_count >= 5
        """], capture_output=True, text=True, timeout=10)
        prev_sectors = {}
        for row in prev_rows.stdout.strip().split('\n'):
            if not row.strip(): continue
            parts = row.split('|')
            if len(parts) >= 2:
                prev_sectors[parts[0].strip()] = float(parts[1])
        
        # 计算每个板块的实时数据
        all_sectors = []
        for sname, codes in sectors.items():
            valid_changes = []
            for code in codes:
                if code in today_changes:
                    valid_changes.append(today_changes[code])
            
            if len(valid_changes) < 5:
                continue
            
            pcts = valid_changes
            avg_c = sum(pcts) / len(pcts)
            sorted_p = sorted(pcts)
            median_c = sorted_p[len(sorted_p)//2]
            max_c = sorted_p[-1]
            min_c = sorted_p[0]
            variance = sum((p - avg_c)**2 for p in pcts) / len(pcts)
            std_c = variance ** 0.5
            up = sum(1 for p in pcts if p >= 0)
            down = sum(1 for p in pcts if p < 0)
            limit_up = sum(1 for p in pcts if p >= 9.5)
            limit_down = sum(1 for p in pcts if p <= -9.5)
            
            is_hot = 1 if sname in HOT_SECTORS else 0
            
            all_sectors.append({
                'name': sname,
                'avg_change': round(avg_c, 2),
                'median_change': round(median_c, 2),
                'max_change': round(max_c, 2),
                'std_change': round(std_c, 2),
                'stock_count': len(valid_changes),
                'up_count': up,
                'down_count': down,
                'limit_up': limit_up,
                'limit_down': limit_down,
                'avg_volume_ratio': 1.0,
                'is_hot': is_hot,
                'prev_change': prev_sectors.get(sname, None),
                'strength': round(limit_up * 2 + avg_c * 0.5 + (2 if is_hot else 0), 1),
                'is_realtime': True,
            })
        
        if len(all_sectors) < 3:
            return None
        
        hot_sectors = [s for s in all_sectors if s['is_hot']]
        other_sectors = [s for s in all_sectors if not s['is_hot']]
        
        return {
            'date': today,
            'prev_date': latest_stored_date,
            'hot_sectors': hot_sectors,
            'other_sectors': other_sectors,
            'correlations': get_sector_correlations_for_display(),
            'follower_stats': get_follower_stats_for_display(),
            'rotation_stats': get_rotation_stats_for_display(),
            'volatility': get_volatility_for_display(today),
            'timestamp': datetime.now().isoformat(),
            'is_realtime': True,
        }
    except Exception as e:
        log(f"  ⚠ 实时板块计算异常: {e}")
        return None

def run_scan():
    r = subprocess.run(
        ['python3', 'scripts/three_funds_scan.py'],
        capture_output=True, text=True, timeout=25,
        cwd=BASE
    )
    return r.stdout

def parse_scan(text):
    lines = text.split('\n')
    sectors = {}
    current = None
    for line in lines:
        if line.startswith('📈 '):
            current = line.replace('📈 ', '').replace('**', '').strip()
            if current not in sectors:
                sectors[current] = []
        elif current:
            m = re.search(r'(\S+)\s+([+-]?[\d.]+%)\s+\|\s+总分(\d+)\s*\((\d+)\+(\d+)\+(\d+)', line)
            if m:
                sectors[current].append({
                    'name': m.group(1), 'pct': m.group(2),
                    'total': int(m.group(3)),
                    'jg': int(m.group(4)), 'lh': int(m.group(5)), 'yz': int(m.group(6))
                })
    return sectors

def detect_buy_signal(sectors):
    signals = []
    for sector, stocks in sectors.items():
        high = [s for s in stocks if s['total'] >= 70]
        near = [s for s in stocks if 55 <= s['total'] < 70]
        
        if len(high) >= 3:
            signals.append({
                'level': 'BUY',
                'sector': sector,
                'stocks': sorted(high, key=lambda x: -x['total'])[:5]
            })
        elif len(near) >= 2:
            signals.append({
                'level': 'WATCH',
                'sector': sector,
                'stocks': sorted(near, key=lambda x: -x['total'])[:5]
            })
    
    return signals

def main():
    log("🔍 作战面板数据更新...")
    
    output = run_scan()
    sectors = parse_scan(output)
    
    # 保存原始扫描数据
    with open(DATA_FILE, 'w') as f:
        f.write(output)
    
    # 检测信号
    signals = detect_buy_signal(sectors)
    
    # 写入信号文件
    signal_data = {
        'timestamp': datetime.now().isoformat(),
        'signals': signals
    }
    with open(SIGNAL_FILE, 'w') as f:
        json.dump(signal_data, f, ensure_ascii=False, indent=2)
    
    # 写入前瞻池（55-69分的票）
    near_stocks = []
    for sector, stocks in sectors.items():
        for s in stocks:
            if 55 <= s['total'] < 70:
                near_stocks.append({**s, 'sector': sector})
    near_stocks.sort(key=lambda x: -x['total'])
    
    with open(WATCH_FILE, 'w') as f:
        json.dump({'stocks': near_stocks, 'timestamp': datetime.now().isoformat()}, 
                  f, ensure_ascii=False, indent=2)
    
    # 运行散户反指扫描v3.0，保存结果
    try:
        subprocess.run(
            ['python3', 'scripts/retail_signal_analyzer.py', '--brief'],
            capture_output=True, text=True, timeout=25,
            cwd=BASE
        )
        # 保存散户情绪分析结果
        retail_out = subprocess.run(
            ['python3', 'scripts/retail_signal_analyzer.py'],
            capture_output=True, text=True, timeout=25,
            cwd=BASE
        )
        with open(RETAIL_FILE, 'w') as f:
            f.write(retail_out.stdout)
    except Exception as e:
        log(f"散户扫描暂不可用: {e}")
    
    # 运行大盘多维环境量化
    try:
        r = subprocess.run(
            ['python3', 'scripts/market_env_quant.py', '--save'],
            capture_output=True, text=True, timeout=20,
            cwd=BASE
        )
        log(f"✅ 大盘环境数据已更新")
    except Exception as e:
        log(f"大盘环境扫描暂不可用: {e}")
    
    # 运行1板扫描+封单额采集
    try:
        subprocess.run(
            ['python3', 'scripts/1ban_scanner.py'],
            capture_output=True, text=True, timeout=25,
            cwd=BASE
        )
        # 同时采集封单额并保存
        ban_data = collect_ban_for_dashboard()
        with open(BAN_FILE, 'w') as f:
            json.dump(ban_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"1板扫描暂不可用: {e}")
    
    # 运行F2盘尾选股扫描
    try:
        subprocess.run(
            ['python3', 'scripts/three_funds_f2.py', '--to-file'],
            capture_output=True, text=True, timeout=30,
            cwd=BASE
        )
        log(f"✅ F2盘尾数据已更新")
        # 合并韭研涨停原因到F2盘尾数据（为每个盘尾选股标注题材）
        try:
            jiuyuan_r = subprocess.run(
                ['python3', 'scripts/fetch_jiuyuan_actions.py', '--merge-f2'],
                capture_output=True, text=True, timeout=15, cwd=BASE
            )
            if jiuyuan_r.returncode == 0:
                log(f"✅ 韭研涨停原因已合并到F2数据")
            else:
                log(f"⚠️ 韭研合并暂不可用: {jiuyuan_r.stderr[:100]}")
        except Exception as e:
            log(f"⚠️ 韭研合并异常: {e}")
        # 读取F2数据并注入到信号中
        if os.path.exists(os.path.join(BASE, 'f2_weipan_data.txt')):
            with open(os.path.join(BASE, 'f2_weipan_data.txt'), 'r') as f:
                f2_data = json.load(f)
            # 追加F2摘要到信号文件
            signal_data['f2_signal'] = {
                'f2_count': len(f2_data.get('f2', [])),
                'f2_plus_count': len(f2_data.get('f2_plus', [])),
                'weipan_count': len(f2_data.get('weipan', [])),
                'weipan_superb': len([p for p in f2_data.get('weipan', []) if p.get('score', 0) >= 80]),
                'timestamp': f2_data.get('timestamp', datetime.now().isoformat()),
            }
            with open(SIGNAL_FILE, 'w') as f:
                json.dump(signal_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"F2扫描暂不可用: {e}")
    
    # 运行跟风选股扫描
    try:
        r = subprocess.run(
            ['python3', 'scripts/sector_follower_picks.py', '--to-file'],
            capture_output=True, text=True, timeout=30,
            cwd=BASE
        )
        follower_file = os.path.join(BASE, 'follower_signals.txt')
        with open(follower_file, 'w') as f:
            f.write(r.stdout)
        log(f"✅ 跟风信号数据已更新")
    except Exception as e:
        log(f"跟风扫描暂不可用: {e}")
    
    # 运行龙虎榜扫描
    try:
        subprocess.run(
            ['python3', 'scripts/lhb_daily.py'],
            capture_output=True, text=True, timeout=30,
            cwd=BASE
        )
        log(f"✅ 龙虎榜数据已更新")
    except Exception as e:
        log(f"龙虎榜扫描暂不可用: {e}")
    
    # 运行游资v4扫描
    try:
        subprocess.run(
            ['python3', 'scripts/youzi_v4_scan.py', '--to-file', '--brief'],
            capture_output=True, text=True, timeout=30,
            cwd=BASE
        )
        log(f"✅ 游资v4数据已更新")
    except Exception as e:
        log(f"游资v4扫描暂不可用: {e}")

    # 如果有买入信号，打印出来
    buy_signals = [s for s in signals if s['level'] == 'BUY']
    if buy_signals:
        log(f"🔥🔥🔥 买入信号触发！")
        for s in buy_signals:
            log(f"  板块: {s['sector']} 龙头: {s['stocks'][0]['name']}(总分{s['stocks'][0]['total']})")
    else:
        log("✅ 无板块级买入信号")
    
    # 导出板块日指数数据（供面板板块联动模块使用）
    try:
        export_sector_index_data()
        log(f"✅ 板块指数已导出")
    except Exception as e:
        log(f"板块指数导出失败: {e}")

    # 同花顺数据采集（收盘后）
    if datetime.now().hour >= 15:  # 15:00后采集
        try:
            subprocess.run(
                ['python3', 'scripts/ths_collector.py', '--ban', '--news'],
                capture_output=True, text=True, timeout=25, cwd=BASE
            )
            log(f"✅ 同花顺数据已更新")
        except Exception as e:
            log(f"同花顺采集异常: {e}")

    # 涨停原因双源融合（收盘后运行）
    if datetime.now().hour >= 15 and datetime.now().hour < 18:
        try:
            subprocess.run(
                ['python3', 'scripts/ban_reason_fusion.py'],
                capture_output=True, text=True, timeout=30, cwd=BASE
            )
            log(f"✅ 涨停原因融合已更新")
        except Exception as e:
            log(f"涨停原因融合失败: {e}")
    
    log(f"✅ 数据已更新 ({len(sectors)}个板块)")

if __name__ == '__main__':
    main()
