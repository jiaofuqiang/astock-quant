#!/usr/bin/env python3
"""
竞价快照采集器 —— 在09:15和09:20运行
保存当前腾讯行情数据作为竞价快照，供09:25的trend分析器使用
"""
import os, json, sys, urllib.request, sqlite3

BASE = os.path.expanduser("~/astock")
DATA = os.path.join(BASE, "data")

def code_to_tencent(code):
    code = str(code).strip()
    if code.startswith('sh') or code.startswith('sz'):
        return code
    prefix = 'sh' if code[0] in '56' else 'sz'
    return f'{prefix}{code}'

SECTOR_TO_CONCEPT = {
    '光模块与光通信': ['光模块', '光通信'], 'AI算力': ['AI算力'],
    'AI芯片': ['AI芯片'], '数据中心': ['数据中心'],
    '存储芯片': ['存储芯片'], '机器人': ['机器人'],
    '半导体': ['半导体'], '新能源汽车': ['新能源汽车'],
    '低空经济': ['低空经济'], '消费电子': ['消费电子'],
    '智能驾驶': ['智能驾驶'], '军工': ['军工'],
    '券商': ['券商'], 'AI': ['AI', '人工智能'],
    '芯片': ['芯片', '国产芯片'],
}

def get_focus_stocks(limit=40):
    codes = []
    seen = set()
    try:
        conn = sqlite3.connect(os.path.join(DATA, 'stock_profiles.db'))
        cur = conn.cursor()
        for cn_list in SECTOR_TO_CONCEPT.values():
            for cn in cn_list:
                cur.execute('SELECT DISTINCT code FROM concepts WHERE concept_name=? LIMIT 5', (cn,))
                for r in cur.fetchall():
                    if r[0] not in seen:
                        seen.add(r[0])
                        codes.append(r[0])
        conn.close()
    except:
        pass
    return [{'code': c} for c in codes[:limit]]

def save(time_label):
    stocks = get_focus_stocks()
    if not stocks:
        print("❌ 无股票池")
        return
    
    tencent_codes = [code_to_tencent(s['code']) for s in stocks]
    url = f"http://qt.gtimg.cn/q={','.join(tencent_codes)}"
    
    try:
        req = urllib.request.urlopen(url, timeout=10)
        raw = req.read().decode('gbk')
    except Exception as e:
        print(f"❌ 腾讯行情: {e}")
        return
    
    results = []
    for line in raw.strip().split(';'):
        if not line or '=' not in line: continue
        parts = line.split('~')
        if len(parts) < 40: continue
        tdx_code = parts[2]
        price = float(parts[3]) if parts[3] else 0
        yesterday_close = float(parts[4]) if parts[4] else 0
        auction_pct = round((price - yesterday_close) / yesterday_close * 100, 2) if yesterday_close else 0
        
        results.append({
            'code': tdx_code.replace('sh','').replace('sz',''),
            'name': parts[1],
            'price': price,
            'yesterday_close': yesterday_close,
            'auction_pct': auction_pct,
            'volume': int(float(parts[6])) if parts[6] else 0,
            'amount_wan': round(float(parts[37]), 0) if len(parts)>37 and parts[37] else 0,
        })
    
    report_dir = os.path.join(BASE, 'scripts/24h_close_loop/reports/auction')
    os.makedirs(report_dir, exist_ok=True)
    today = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
    path = os.path.join(report_dir, f'auction_snapshot_{time_label}_{today}.json')
    
    with open(path, 'w') as f:
        json.dump({'time': time_label, 'date': today, 'stocks': results}, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 竞价快照 {time_label}: {len(results)}只股票 → {path}")

if __name__ == "__main__":
    time_label = sys.argv[1] if len(sys.argv) > 1 else '0915'
    save(time_label)
