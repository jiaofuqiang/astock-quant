#!/usr/bin/env python3
"""
十大流通股东潜伏策略 — 采集+评分+回测+信号输出
=================================================
核心逻辑：
  1. 从东方财富API拉取最新十大流通股东新进数据
  2. 分类：个人牛散 / 证券投资基金 / QFII / 私募基金 / 保险 / 社保
  3. 回测：T日(新进公告后)买入 → T+1/T+5卖出 → 算收益
  4. 评分：按历史回测收益/胜率给每个股东类型打分
  5. 输出信号：当前可潜伏标的 + 建议买入价格

用法：
  python3 scripts/holder_ambush.py                    # 完整流程
  python3 scripts/holder_ambush.py --fetch-only       # 只采数据
  python3 scripts/holder_ambush.py --backtest         # 只跑回测
  python3 scripts/holder_ambush.py --signal           # 只输出信号
  python3 scripts/holder_ambush.py --json             # JSON输出(给dashboard)

数据源：
  - 股东数据: datacenter-web.eastmoney.com (RPT_F10_EH_FREEHOLDERS)
  - K线数据: kline_cache.db (已有)
  - 龙虎榜: lhb_cache.db (辅助验证股东是否是游资/量化)

作者: Hermes Agent
日期: 2026-05-15
"""

import sqlite3, os, sys, json, time, urllib.request
from urllib.parse import quote
from datetime import datetime, timedelta
from collections import defaultdict, Counter

# ========== 路径 ==========
BASE = os.path.expanduser('~/astock')
DATA_DIR = os.path.join(BASE, 'data')
KLINE_DB = os.path.join(DATA_DIR, 'kline_cache.db')
HOLDER_DB = os.path.join(DATA_DIR, 'holder_cache.db')
RESULT_FILE = os.path.join(BASE, 'temp', 'holder_signal.json')

# ========== 东方财富API ==========
API_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# ========== 股东类型标签映射 ==========
HOLDER_TYPE_LABELS = {
    '个人': '牛散',
    '证券投资基金': '基金',
    'QFII': '外资',
    '私募基金': '私募',
    '保险产品': '保险',
    '保险公司': '保险',
    '全国社保基金': '社保',
    '基本养老基金': '养老',
    '投资公司': '投资公司',
    '证券公司': '券商',
    '证券账户': '回购专户',
    '员工持股计划': '员工持股',
}

# ========== 知名牛散名单 ==========
FAMOUS_BULLS = {
    '周信钢', '赵建平', '孙惠刚', '葛卫东', '何雪萍', '徐开东',
    '夏重阳', '张素芬', '王孝安', '章建平', '陈发树', '刘益谦',
    '吕小奇', '邹瀚枢', '沈国军', '蒋仕波', '高雅萍', '杨燕灵',
    '周爽', '应淑英', '赵吉', '叶玉莲',
}

# ========== 知名量化/游资机构关键词 ==========
QUANT_KEYWORDS = ['量化', '对冲', '九坤', '幻方', '明汯', '衍复', '天演', '启林',
                  '灵均', '稳博', '宽德', '因诺', '黑翼', '世纪前沿', '聚宽',
                  '星阔', '龙旗', '茂源', '千象', '念空', '平方和', '赫富',
                  '凡硕', '安诚数盈', '锐天', '宁波幻方', '幻方量化']

YOUZI_KEYWORDS = ['鹤禧', '汐泰', '南土', '喜世润', '秉昊', '金泰', '尚伟']

# ========== DB初始化 ==========
def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(RESULT_FILE), exist_ok=True)
    db = sqlite3.connect(HOLDER_DB)
    db.execute('''
        CREATE TABLE IF NOT EXISTS holder_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            security_code TEXT,
            security_name TEXT,
            holder_name TEXT,
            holder_type TEXT,
            hold_num REAL,
            hold_ratio REAL,
            holder_market_cap REAL,
            holder_rank INTEGER,
            report_date_name TEXT,
            end_date TEXT,
            fetch_date TEXT,
            UNIQUE(security_code, holder_name, report_date_name)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS holder_backtest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            security_code TEXT,
            security_name TEXT,
            holder_name TEXT,
            holder_type TEXT,
            report_date_name TEXT,
            t0_date TEXT,
            t1_open REAL,
            t1_close REAL,
            t1_high REAL,
            t1_ret REAL,
            t5_ret REAL,
            win INTEGER,
            UNIQUE(security_code, holder_name, report_date_name)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS holder_type_stats (
            holder_type TEXT PRIMARY KEY,
            total_count INTEGER,
            win_count INTEGER,
            win_rate REAL,
            avg_t1_ret REAL,
            avg_t5_ret REAL,
            max_t1_ret REAL,
            min_t1_ret REAL,
            last_update TEXT
        )
    ''')
    db.commit()
    return db

# ========== Step 1: 拉取最新股东数据 ==========
def fetch_holder_data():
    """从东方财富API拉取所有新进股东数据"""
    all_items = []
    for page in range(1, 6):
        url = f'{API_URL}?reportName=RPT_F10_EH_FREEHOLDERS&columns=ALL&pageNumber={page}&pageSize=5000&sortColumns=END_DATE&sortTypes=-1'
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
            items = data.get('result', {}).get('data', [])
            for i in items:
                if i.get('HOLD_CHANGE') == '新进':
                    code = i.get('SECURITY_CODE','')
                    # 只保留主板(非300/688/920)
                    if not code.startswith(('300','688','920')):
                        all_items.append(i)
            if len(items) < 5000:
                break
        except Exception as e:
            print(f'⚠️ 第{page}页采集失败: {e}')
            break
        time.sleep(0.3)
    
    print(f'✅ 共采集新进数据 {len(all_items)} 条')
    
    # 存入数据库
    db = init_db()
    inserted = 0
    for i in all_items:
        try:
            db.execute('''
                INSERT OR IGNORE INTO holder_new
                (security_code, security_name, holder_name, holder_type,
                 hold_num, hold_ratio, holder_market_cap, holder_rank,
                 report_date_name, end_date, fetch_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                i.get('SECURITY_CODE',''),
                i.get('SECURITY_NAME_ABBR',''),
                i.get('HOLDER_NAME',''),
                i.get('HOLDER_TYPE',''),
                i.get('HOLD_NUM',0) or 0,
                i.get('FREE_HOLDNUM_RATIO',0) or 0,
                i.get('HOLDER_MARKET_CAP',0) or 0,
                i.get('HOLDER_RANK',0) or 0,
                i.get('REPORT_DATE_NAME',''),
                i.get('END_DATE',''),
                datetime.now().strftime('%Y-%m-%d')
            ))
            inserted += 1
        except Exception as e:
            pass
    db.commit()
    print(f'✅ 存入 {inserted} 条到数据库')
    
    return all_items

def klassify_holder_type(holder_name, holder_type):
    """对股东进行分类：牛散/基金/外资/私募/量化/游资等"""
    label = HOLDER_TYPE_LABELS.get(holder_type, holder_type)
    
    # 检查是否是知名牛散
    if holder_type == '个人':
        if holder_name in FAMOUS_BULLS:
            return '知名牛散'
        return '牛散'
    
    # 检查量化
    for kw in QUANT_KEYWORDS:
        if kw in holder_name:
            return '量化'
    
    # 检查游资类私募
    for kw in YOUZI_KEYWORDS:
        if kw in holder_name:
            return '游资私募'
    
    # 著名外资机构
    if any(x in holder_name for x in ['UBS', 'MORGAN', 'J.P.Morgan', 'GOLDMAN', '高盛', 'BARCLAYS', '华泰金融']):
        return '知名外资'
    
    if holder_type == 'QFII':
        return '外资(QFII)'
    
    return label

# ========== Step 2: 回测 ==========
def backtest_holders():
    """对每个新进股东标的做T+1/T+5回测"""
    db = init_db()
    kdb = sqlite3.connect(KLINE_DB)
    
    # 读取所有未回测的记录
    rows = db.execute('''
        SELECT h.id, h.security_code, h.security_name, h.holder_name,
               h.holder_type, h.report_date_name, h.end_date
        FROM holder_new h
        LEFT JOIN holder_backtest b ON h.security_code = b.security_code 
            AND h.holder_name = b.holder_name 
            AND h.report_date_name = b.report_date_name
        WHERE b.id IS NULL
        ORDER BY h.report_date_name DESC
    ''').fetchall()
    
    print(f'待回测: {len(rows)} 条')
    if not rows:
        print('全部已回测')
        return
    
    # 获取所有K线日期（用于找公告后的第一个交易日）
    kline_dates = set()
    for r in kdb.execute('SELECT DISTINCT date FROM kline ORDER BY date').fetchall():
        kline_dates.add(r[0])
    sorted_dates = sorted(kline_dates)
    
    win = 0
    total = 0
    
    for row in rows:
        rid, code, name, holder, htype, rpn, end_date = row
        total += 1
        
        # T日：公告日期之后的第一个交易日
        # 注意：一季报(2026一季报)的end_date可能是2026-03-31
        # 日常更新(2026-05-12)的end_date就是当天
        
        # 找公告后的第一个交易日
        # 如果是日常更新(report_date_name以2026-开头)，直接用这个日期
        if rpn and rpn.startswith('2026-'):
            announce_date = rpn
        else:
            # 季报：用end_date或年报公布日期
            announce_date = end_date[:10] if end_date else '2026-04-01'
        
        # 找之后的第一个交易日
        t0 = None
        for d in sorted_dates:
            if d >= announce_date:
                t0 = d
                break
        
        if not t0:
            continue
        
        # 找T+1和T+5的K线
        t1_idx = sorted_dates.index(t0) + 1
        t5_idx = sorted_dates.index(t0) + 5
        
        if t1_idx >= len(sorted_dates):
            continue
        
        t1_date = sorted_dates[t1_idx]
        t5_date = sorted_dates[t5_idx] if t5_idx < len(sorted_dates) else t1_date
        
        # 读T日数据（买入：T日开盘价买入）
        t0_k = kdb.execute('SELECT open, close, high FROM kline WHERE code=? AND date=?', (code, t0)).fetchone()
        t1_k = kdb.execute('SELECT open, close, high FROM kline WHERE code=? AND date=?', (code, t1_date)).fetchone()
        t5_k = kdb.execute('SELECT open, close, high FROM kline WHERE code=? AND date=?', (code, t5_date)).fetchone()
        
        if not t0_k or not t1_k:
            continue
        
        buy_price = t0_k[0]  # T日开盘买入
        t1_open = t1_k[0]
        t1_close = t1_k[1]
        t1_high = t1_k[2]
        t5_close = t5_k[1] if t5_k else t1_close
        
        # 收益
        t1_ret = (t1_close - buy_price) / buy_price * 100
        t5_ret = (t5_close - buy_price) / buy_price * 100
        is_win = 1 if t1_ret > 0 else 0
        
        if is_win: win += 1
        
        # 存入
        try:
            db.execute('''
                INSERT OR REPLACE INTO holder_backtest
                (security_code, security_name, holder_name, holder_type,
                 report_date_name, t0_date, t1_open, t1_close, t1_high,
                 t1_ret, t5_ret, win)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (code, name, holder, htype, rpn, t0,
                  t1_open, t1_close, t1_high, t1_ret, t5_ret, is_win))
        except:
            pass
        
        if total % 50 == 0:
            print(f'  进度: {total}/{len(rows)}, 当前胜率: {win/total*100:.1f}%')
    
    db.commit()
    
    print(f'\n✅ 回测完成: {total}条')
    print(f'   整体胜率: {win}/{total} = {win/total*100:.1f}%' if total else '')
    
    # 按股东类型统计
    update_type_stats(db)
    
    return total

def update_type_stats(db):
    """更新股东类型的回测统计数据"""
    rows = db.execute('''
        SELECT b.holder_type,
               COUNT(*) as cnt,
               SUM(b.win) as wins,
               AVG(b.t1_ret) as avg_ret,
               AVG(b.t5_ret) as avg_t5,
               MAX(b.t1_ret) as max_ret,
               MIN(b.t1_ret) as min_ret
        FROM holder_backtest b
        GROUP BY b.holder_type
    ''').fetchall()
    
    updated = datetime.now().strftime('%Y-%m-%d %H:%M')
    for row in rows:
        htype, cnt, wins, avg_ret, avg_t5, max_ret, min_ret = row
        cnt = cnt or 0
        wins = wins or 0
        win_rate = (wins / cnt * 100) if cnt > 0 else 0
        avg_ret = avg_ret or 0
        avg_t5 = avg_t5 or 0
        max_ret = max_ret or 0
        min_ret = min_ret or 0
        
        db.execute('''
            INSERT OR REPLACE INTO holder_type_stats
            (holder_type, total_count, win_count, win_rate,
             avg_t1_ret, avg_t5_ret, max_t1_ret, min_t1_ret, last_update)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', (htype, cnt, wins, win_rate, avg_ret, avg_t5, max_ret, min_ret, updated))
    
    db.commit()
    print(f'✅ 类型统计更新完成')

# ========== Step 3: 评分+信号输出 ==========
def compute_signals():
    """计算当前可潜伏信号"""
    db = init_db()
    
    # 读取类型统计
    type_stats = {}
    for r in db.execute('SELECT * FROM holder_type_stats').fetchall():
        type_stats[r[0]] = {
            'count': r[1], 'win': r[2], 'win_rate': r[3],
            'avg_ret': r[4], 'avg_t5': r[5], 'max_ret': r[6]
        }
    
    # 获取最新的新进记录（最近30天 + 2026一季报）
    # 对于一季报，假设公告日=2026-04-30(季报截止日)
    latest_rows = db.execute('''
        SELECT h.* FROM holder_new h
        WHERE h.report_date_name IN (
            '2026一季报', '2026-05-12', '2026-05-11', '2026-05-08',
            '2026-05-06', '2026-04-29', '2026-04-28', '2026-04-27',
            '2026-04-24', '2026-04-23', '2026-04-22', '2026-04-21',
            '2026-04-20', '2026-04-17', '2026-04-16', '2026-04-14',
            '2026-04-13'
        )
        ORDER BY h.holder_market_cap DESC
    ''').fetchall()
    
    # 去重：同一只股票多个股东新进 → 合并评分
    stock_signals = defaultdict(list)
    for row in latest_rows:
        code = row[1]
        name = row[2]
        holder = row[3]
        htype = row[4]
        mcap = row[7] or 0
        ratio = row[6] or 0
        rank = row[8] or 0
        
        klass = klassify_holder_type(holder, htype)
        
        # 查看该类型的回测统计
        stat = type_stats.get(htype, {})
        type_win_rate = stat.get('win_rate', 0)
        type_avg_ret = stat.get('avg_ret', 0)
        type_count = stat.get('count', 0)
        
        stock_signals[code].append({
            'name': name,
            'holder': holder,
            'holder_type_raw': htype,
            'holder_klass': klass,
            'mcap': mcap,
            'ratio': ratio,
            'rank': rank,
            'type_win_rate': type_win_rate,
            'type_avg_ret': type_avg_ret,
            'type_count': type_count,
        })
    
    # 评分
    scored = []
    for code, holders in stock_signals.items():
        name = holders[0]['name']
        
        # 基础分
        score = 50
        
        # 因子1: 股东质量
        klass_bonus = {
            '知名牛散': 15, '牛散': 5, '知名外资': 12,
            '量化': 10, '游资私募': 8,
            '基金': 5, '社保': 8, '养老': 6,
            '外资(QFII)': 7, '保险': 4, '员工持股': 3,
        }
        max_bonus = max(klass_bonus.get(h['holder_klass'], 0) for h in holders)
        score += max_bonus
        
        # 因子2: 多个股东同时新进 （合力加分）
        if len(holders) >= 2:
            score += min(len(holders) * 3, 12)
        
        # 因子3: 持股市值大（说明机构看好）
        avg_mcap = sum(h['mcap'] for h in holders) / len(holders)
        if avg_mcap > 5e8:
            score += 10
        elif avg_mcap > 1e8:
            score += 5
        elif avg_mcap > 0.5e8:
            score += 2
        
        # 因子4: 持股占比大
        avg_ratio = sum(h['ratio'] for h in holders) / len(holders)
        if avg_ratio > 5:
            score += 10
        elif avg_ratio > 2:
            score += 5
        
        # 因子5: 回测类型数据置信度
        type_count = max(h['type_count'] for h in holders)
        type_avg_ret = max(h['type_avg_ret'] for h in holders)
        if type_count >= 20:
            score += 5
        if type_avg_ret > 1:
            score += 5
        
        # 股东分类标签
        klass_set = set(h['holder_klass'] for h in holders)
        
        scored.append({
            'code': code,
            'name': name,
            'score': min(score, 100),
            'holder_count': len(holders),
            'holders': [{'name': h['holder'], 'type': h['holder_klass'], 
                        'mcap': h['mcap'], 'ratio': h['ratio']} for h in holders],
            'holder_types': list(klass_set),
            'avg_mcap': avg_mcap,
            'avg_ratio': avg_ratio,
        })
    
    # 按评分排序
    scored.sort(key=lambda x: x['score'], reverse=True)
    
    # 输出
    result = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'engine': 'holder-ambush-v1',
        'total': len(scored),
        'ranked': scored[:20],  # 前20名
        'type_stats': {k: v for k, v in type_stats.items()},
    }
    
    # 写入文件
    os.makedirs(os.path.dirname(RESULT_FILE), exist_ok=True)
    with open(RESULT_FILE, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    
    print(f'\n✅ 信号输出: {len(scored)} 只标的')
    print(f'   信号文件: {RESULT_FILE}')
    
    return result

# ========== 格式化输出 ==========
def print_signals(result):
    """美化打印"""
    ranked = result.get('ranked', [])
    type_stats = result.get('type_stats', {})
    
    dt = result.get('date', '--')
    print('')
    print('='*70)
    print('🐅 十大流通股东潜伏策略信号')
    print(f'   生成时间: {dt}')
    print('='*70)
    
    print('')
    print('【股东类型历史回测统计】')
    h1 = '类型'
    h2 = '样本数'
    h3 = '胜率'
    h4 = 'T+1收益'
    h5 = 'T+5收益'
    print(f'{h1:12s} {h2:>6s} {h3:>6s} {h4:>10s} {h5:>10s}')
    print('-'*50)
    for htype, stat in sorted(type_stats.items(), key=lambda x: x[1]['count'], reverse=True):
        if stat['count'] < 5:
            continue
        ret = stat.get('avg_ret', 0) or 0
        t5 = stat.get('avg_t5', 0) or 0
        wr = stat.get('win_rate', 0) or 0
        cnt = stat['count']
        print(f'{htype:12s} {cnt:>6d} {wr:>5.1f}% {ret:>+7.2f}% {t5:>+7.2f}%')
    
    print('')
    print('【当前可潜伏标的 TOP20】')
    h1 = '#'
    h2 = '代码'
    h3 = '名称'
    h4 = '评分'
    h5 = '股东数'
    h6 = '类型'
    h7 = '持股市值'
    print(f'{h1:>2s} {h2:8s} {h3:8s} {h4:>4s} {h5:>4s} {h6:20s} {h7:>10s}')
    print('-'*70)
    for i, s in enumerate(ranked[:20], 1):
        types_str = ','.join(s.get('holder_types', []))[:18]
        if s['avg_mcap'] > 1e8:
            mcap_str = f'{s["avg_mcap"]/1e8:.2f}亿'
        else:
            mcap_str = f'{s["avg_mcap"]/1e4:.0f}万'
        code = s['code']
        name = s['name']
        score = s['score']
        hcnt = s['holder_count']
        print(f'{i:>2d} {code:8s} {name:8s} {score:>4d} {hcnt:>4d} {types_str:20s} {mcap_str:>10s}')
        hlist = [f"{h['name']}({h['type']})" for h in s['holders'][:3]]
        for h in hlist:
            print(f'    └ {h}')
        print()

# ========== Main ==========
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--fetch-only', action='store_true')
    parser.add_argument('--backtest', action='store_true')
    parser.add_argument('--signal', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    
    if args.fetch_only:
        fetch_holder_data()
        sys.exit(0)
    
    if args.backtest:
        init_db()
        backtest_holders()
        sys.exit(0)
    
    if args.signal:
        init_db()
        result = compute_signals()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, default=str))
        else:
            print_signals(result)
        sys.exit(0)
    
    # 默认：完整流程
    print('📡 Step 1: 采集最新股东数据...')
    fetch_holder_data()
    print()
    
    print('📊 Step 2: 回测...')
    cnt = backtest_holders()
    print()
    
    print('🎯 Step 3: 评分+信号输出...')
    result = compute_signals()
    print()
    
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        print_signals(result)
    
    print('\n✅ 完整流程完成')
