#!/usr/bin/env python3
"""
🔥 A股概念热度指数 v1.1 - 修复版
关键修复：腾讯行情接口返回的fields[0]是交易所前缀码(1/51)，fields[2]才是正确的6位股票代码
"""
import os, sys, json, time, subprocess
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, 'data', 'stock_profiles.db')

HEADERS = 'User-Agent: Mozilla/5.0'

def curl_get(url, timeout=8):
    try:
        r = subprocess.run(
            ['curl', '-s', url, '-H', HEADERS, '--connect-timeout', str(timeout), '--max-time', str(timeout+5)],
            capture_output=True, timeout=timeout+8
        )
        return r.stdout.decode('utf-8', errors='replace')
    except:
        return None

def run_sql(sql):
    """执行SQL并返回结果，适用于简单查询"""
    r = subprocess.run(['sqlite3', DB_PATH], input=sql.encode(), capture_output=True, timeout=120)
    return r.stdout.decode().strip()

def run_sql_script(sql_script):
    """用临时文件执行SQL脚本（支持大量语句和事务）"""
    tmp_file = os.path.join(BASE, 'data', '_tmp_sql_' + str(os.getpid()) + '.sql')
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            f.write(sql_script)
        r = subprocess.run(['sqlite3', DB_PATH], 
            stdin=open(tmp_file, 'r'), capture_output=True, timeout=120)
        return r.stdout.decode().strip(), r.stderr.decode().strip(), r.returncode
    finally:
        try:
            os.remove(tmp_file)
        except:
            pass

def fetch_realtime_batch(codes):
    """
    腾讯行情批量查询，返回 {6位code: {price, change_pct, turnover}}
    注意：腾讯接口fields[0]是交易所前缀码(1=沪/51=深/...)，fields[2]才是6位股票代码
    """
    if not codes:
        return {}
    code_str = ','.join(codes)
    url = f"https://qt.gtimg.cn/q={code_str}"
    raw = curl_get(url)
    if not raw:
        return {}
    result = {}
    for line in raw.split('\n'):
        if not line.strip():
            continue
        try:
            parts = line.split('"')
            if len(parts) < 2:
                continue
            fields = parts[1].split('~')
            if len(fields) < 40:
                continue
            code = fields[2]
            price = float(fields[3]) if fields[3] else 0
            change_pct = float(fields[32]) if fields[32] else 0
            turnover = float(fields[37]) if fields[37] else 0
            result[code] = {
                'price': price, 'change_pct': change_pct,
                'turnover': turnover,
            }
        except (IndexError, ValueError):
            continue
    return result

def get_concept_stocks():
    out = run_sql("""
        SELECT concept_name, GROUP_CONCAT(code) as codes
        FROM concepts
        WHERE concept_name NOT IN ('新材料','信用','新材料')
        GROUP BY concept_name
        HAVING COUNT(DISTINCT code) >= 5 AND COUNT(DISTINCT code) <= 1000
        ORDER BY COUNT(DISTINCT code) DESC
    """)
    concepts = {}
    for line in out.split('\n'):
        if '|' in line:
            p = line.split('|')
            name = p[0].strip()
            codes = [c.strip() for c in p[1].split(',') if c.strip()]
            if len(codes) >= 5:
                concepts[name] = codes
    return concepts

def get_today_ann_counts():
    out = run_sql("""
        SELECT concept_name, COUNT(*) as cnt
        FROM concepts
        WHERE source = 'ann'
        GROUP BY concept_name
        ORDER BY cnt DESC
    """)
    ann_counts = {}
    for line in out.split('\n'):
        if '|' in line:
            p = line.split('|')
            ann_counts[p[0].strip()] = int(p[1].strip())
    return ann_counts

def get_stock_names(codes):
    if not codes:
        return {}
    quoted_codes = ','.join(f"'{c}'" for c in codes)
    out = run_sql(f"SELECT code, name FROM stock_basic WHERE code IN ({quoted_codes})")
    names = {}
    for line in out.split('\n'):
        if '|' in line:
            p = line.split('|')
            names[p[0].strip()] = p[1].strip()
    return names

def escape_sql(val):
    """转义SQL字符串中的单引号"""
    return str(val).replace("'", "''")

def main():
    now = datetime.now()
    print(f"🔥 A股概念热度指数 v1.1 (修复版)")
    print(f"⏰ {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    # 1. 加载概念
    print("📋 加载概念成分股...")
    concept_stocks = get_concept_stocks()
    total_stocks = sum(len(v) for v in concept_stocks.values())
    print(f"   {len(concept_stocks)} 个概念, 共 {total_stocks} 条映射")
    
    # 2. 获取行情
    all_codes = set()
    for codes in concept_stocks.values():
        all_codes.update(codes)
    
    tencent_codes = []
    for code in all_codes:
        prefix = 'sh' if code.startswith('6') else 'sz'
        tencent_codes.append(f"{prefix}{code}")
    
    print(f"📈 获取 {len(tencent_codes)} 只股票的行情...")
    BATCH_SIZE = 80
    realtime_data = {}
    for i in range(0, len(tencent_codes), BATCH_SIZE):
        batch = tencent_codes[i:i+BATCH_SIZE]
        data = fetch_realtime_batch(batch)
        realtime_data.update(data)
        time.sleep(0.1)
    
    print(f"   ✅ {len(realtime_data)} 只有有效数据")
    
    # 3. 公告频次
    ann_counts = get_today_ann_counts()
    print(f"   📰 公告概念: {len(ann_counts)} 个")
    
    # 4. 计算热度
    print("\n🔥 计算热度指数...")
    heat_data = {}
    
    for concept, codes in concept_stocks.items():
        stock_count = len(codes)
        if stock_count < 5:
            continue
        
        changes = []
        turnovers = []
        up_count = 0
        limit_up = 0
        
        for code in codes:
            if code in realtime_data:
                d = realtime_data[code]
                cp = d['change_pct']
                changes.append(cp)
                turnovers.append(d['turnover'])
                if cp > 0:
                    up_count += 1
                if cp >= 9.5:
                    limit_up += 1
        
        if not changes:
            ann_score = ann_counts.get(concept, 0)
            if ann_score > 0:
                heat_data[concept] = {
                    'heat': round(min(50, ann_score * 5), 1),
                    'avg_change': 0,
                    'up_ratio': 0,
                    'limit_up': 0,
                    'stock_count': stock_count,
                    'avg_turnover': 0,
                    'ann_count': ann_score,
                    'codes': codes,
                    'no_price': True,
                }
            continue
        
        avg_change = sum(changes) / len(changes)
        avg_turnover = sum(turnovers) / len(turnovers) if turnovers else 0
        up_ratio = up_count / len(changes) if changes else 0
        ann_score = ann_counts.get(concept, 0)
        
        change_score = max(0, min(100, (avg_change + 10) * 5))
        up_ratio_score = up_ratio * 100
        limit_score = min(30, limit_up * 10)
        turnover_score = min(100, avg_turnover / 1_000_000) if avg_turnover > 0 else 0
        ann_score_norm = min(50, ann_score * 5)
        
        heat = (
            0.35 * change_score +
            0.25 * up_ratio_score +
            0.15 * limit_score +
            0.15 * turnover_score +
            0.10 * ann_score_norm
        )
        
        heat_data[concept] = {
            'heat': round(heat, 1),
            'avg_change': round(avg_change, 2),
            'up_ratio': round(up_ratio * 100, 1),
            'limit_up': limit_up,
            'stock_count': stock_count,
            'avg_turnover': round(avg_turnover, 0),
            'ann_count': ann_score,
            'codes': codes,
        }
    
    print(f"   📊 有效热度数: {len(heat_data)} 个概念")
    
    # 5. 获取TOP3成分股名称
    print("   🏆 获取热门概念成分股...")
    all_top_codes = set()
    for concept, data in heat_data.items():
        codes = data['codes']
        ranked = [(code, realtime_data.get(code, {}).get('change_pct', 0), 
                   realtime_data.get(code, {}).get('price', 0))
                  for code in codes if code in realtime_data]
        ranked.sort(key=lambda x: -x[1])
        for code, _, _ in ranked[:3]:
            all_top_codes.add(code)
    
    stock_names = get_stock_names(list(all_top_codes))
    
    # 6. 排行榜
    ranked = sorted(heat_data.items(), key=lambda x: -x[1]['heat'])
    
    print(f"\n{'='*60}")
    print(f"🔥 概念热度排行榜 TOP20")
    print(f"{'='*60}")
    print(f"{'#':>3} {'概念名':<14} {'热度':>6} {'涨幅%':>7} {'上涨比':>7} {'涨停':>4} {'公告':>4}")
    print(f"{'-'*50}")
    
    for i, (name, data) in enumerate(ranked[:20], 1):
        print(f"{i:3d} {name:<14} {data['heat']:>5.1f} ", end='')
        if data.get('no_price'):
            print(f"{'N/A':>7} {'N/A':>7} {'N/A':>4} {data['ann_count']:>3d}")
        else:
            print(f"{data['avg_change']:>+6.2f}% {data['up_ratio']:>5.1f}% {data['limit_up']:>3d} {data['ann_count']:>3d}")
        
        codes = data['codes']
        stock_ranked = [(code, realtime_data.get(code, {}).get('change_pct', 0),
                        realtime_data.get(code, {}).get('price', 0))
                       for code in codes if code in realtime_data]
        stock_ranked.sort(key=lambda x: -x[1])
        for j, (code, cp, pr) in enumerate(stock_ranked[:3], 1):
            name_s = stock_names.get(code, code)
            arrow = '📈' if cp > 0 else '📉'
            print(f"       {j}.{name_s}({code}) {arrow}{cp:+.2f}%")
    
    # 7. 写入数据库（使用临时文件+事务，确保大量INSERT正确提交）
    print("\n💾 写入数据库...")
    today = now.strftime('%Y-%m-%d')
    
    run_sql("""
        CREATE TABLE IF NOT EXISTS concept_heat (
            date TEXT,
            concept_name TEXT,
            heat_score REAL,
            avg_change REAL,
            up_ratio REAL,
            limit_up INTEGER,
            stock_count INTEGER,
            ann_count INTEGER,
            top3_stocks TEXT DEFAULT '',
            PRIMARY KEY (date, concept_name)
        )
    """)
    
    # 先生成CSV格式数据，然后用sqlite3的.import
    csv_lines = []
    for name, data in ranked:
        codes = data['codes']
        stock_ranked = [(code, realtime_data.get(code, {}).get('change_pct', 0),
                        realtime_data.get(code, {}).get('price', 0))
                       for code in codes if code in realtime_data]
        stock_ranked.sort(key=lambda x: -x[1])
        top3_str = '|'.join(
            f"{code}|{stock_names.get(code, code)}|{cp}"
            for code, cp, pr in stock_ranked[:3]
        )
        avg_c = data.get('avg_change', 0)
        up_r = data.get('up_ratio', 0)
        lu = data.get('limit_up', 0)
        # CSV: 日期,概念名,热度,涨幅,上涨比,涨停,股票数,公告数,TOP3
        # 用特殊分隔符避免和内容冲突
        csv_lines.append(
            f"{today}|{escape_sql(name)}|{data['heat']}|{avg_c}|{up_r}|{lu}|{data['stock_count']}|{data['ann_count']}|{escape_sql(top3_str)}"
        )
    
    # 写入临时CSV
    csv_path = os.path.join(BASE, 'data', '_concept_heat_' + str(os.getpid()) + '.csv')
    with open(csv_path, 'w', encoding='utf-8') as f:
        for line in csv_lines:
            f.write(line + '\n')
    
    # 用sqlite3导入 - 先创建临时表再INSERT OR REPLACE
    # sqlite3的点命令必须独占一行，不能和其他SQL混在同一行
    import_script = (
        f"CREATE TEMP TABLE _tmp_import (\n"
        f"  date TEXT, concept_name TEXT, heat_score REAL,\n"
        f"  avg_change REAL, up_ratio REAL, limit_up INTEGER,\n"
        f"  stock_count INTEGER, ann_count INTEGER, top3_stocks TEXT\n"
        f");\n"
        f".separator |\n"
        f".import '{csv_path}' _tmp_import\n"
        f"INSERT OR REPLACE INTO concept_heat\n"
        f"SELECT * FROM _tmp_import;\n"
        f"DROP TABLE _tmp_import;\n"
    )
    
    out, err, rc = run_sql_script(import_script)
    os.remove(csv_path)
    
    if rc != 0:
        print(f"   ⚠️ 导入错误: {err[:300]}")
    
    # 验证
    verify = run_sql(f"SELECT COUNT(*) FROM concept_heat WHERE date='{today}'")
    print(f"   ✅ 写入 {len(csv_lines)} 条热度记录 (数据库确认: {verify}条)")
    
    # 8. TOP5摘要
    print(f"\n{'🔥'*10}")
    print(f"今日热门概念TOP5:")
    for i, (name, data) in enumerate(ranked[:5], 1):
        codes = data['codes']
        stock_ranked = [(code, realtime_data.get(code, {}).get('change_pct', 0),
                        realtime_data.get(code, {}).get('price', 0))
                       for code in codes if code in realtime_data]
        stock_ranked.sort(key=lambda x: -x[1])
        stocks_str = ' '.join(
            f"{stock_names.get(code, code)}({'📈' if cp>0 else '📉'}{cp:+.1f}%)"
            for code, cp, pr in stock_ranked[:3]
        )
        if data.get('no_price'):
            print(f"  {i}. {name} (热度:{data['heat']}, 公告频次:{data['ann_count']})")
        else:
            print(f"  {i}. {name} (热度:{data['heat']}, 涨幅:{data['avg_change']:+.2f}%)")
        if stocks_str:
            print(f"     TOP3: {stocks_str}")
    
    print(f"\n✅ 完成!")

if __name__ == '__main__':
    main()
