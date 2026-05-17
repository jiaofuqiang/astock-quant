#!/usr/bin/env python3
"""快速拉取05-14和05-15日K线（搜狐API）"""
import subprocess, os, json, tempfile, time

KLINE_DB = os.path.expanduser("~/astock/data/kline_cache.db")

def sql(db, q):
    r = subprocess.run(['sqlite3', db, q], capture_output=True, timeout=30, text=True)
    return r

# 获取所有主板非ST代码
codes = sql(KLINE_DB, "SELECT DISTINCT code FROM kline WHERE code LIKE '60%' OR code LIKE '00%' ORDER BY code")
codes = [l.strip() for l in codes.stdout.split('\n') if l.strip()]
print(f"共 {len(codes)} 只主板代码")

# 分批拉取05-14和05-15
dates_to_fetch = ['20260514', '20260515']
total_inserted = 0
batch_size = 40

for batch_start in range(0, len(codes), batch_size):
    batch = codes[batch_start:batch_start+batch_size]
    cn_codes = [f"cn_{c}" for c in batch]
    
    url = f"https://q.stock.sohu.com/hisHq?code={','.join(cn_codes)}&start=20260513&end=20260515"
    
    try:
        r = subprocess.run(['curl', '-s', '--connect-timeout', '8', '--max-time', '15', url],
                          capture_output=True, text=True, timeout=20)
        if not r.stdout or r.stdout.strip() == '':
            continue
        
        data = json.loads(r.stdout)
        batch_values = []
        for item in data:
            if item.get('status') != 0:
                continue
            code_str = item.get('code', '')
            original_code = code_str.replace('cn_', '') if code_str.startswith('cn_') else code_str
            hq = item.get('hq', [])
            if not hq:
                continue
            for day in hq:
                date = day[0]
                if date not in ('2026-05-14', '2026-05-15'):
                    continue
                try:
                    open_p = float(day[1])
                    close = float(day[2])
                    high = float(day[6])
                    low = float(day[5])
                    volume = float(day[7])
                    if close <= 0 or open_p <= 0:
                        continue
                    batch_values.append(f"('{original_code}','{date}',{open_p},{close},{high},{low},{volume})")
                except:
                    continue
        
        if batch_values:
            sql_text = f"INSERT OR REPLACE INTO kline VALUES {','.join(batch_values)};"
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.sql') as tmp:
                tmp.write(sql_text)
                tmp_path = tmp.name
            sql(KLINE_DB, f".read {tmp_path}")
            os.unlink(tmp_path)
            total_inserted += len(batch_values)
        
    except Exception as e:
        pass
    
    if (batch_start // batch_size + 1) % 10 == 0:
        print(f"进度: {batch_start}/{len(codes)} 插入{total_inserted}条")

print(f"\n✅ 完成! 共插入 {total_inserted} 条K线")

# 验证
r = sql(KLINE_DB, "SELECT date, COUNT(DISTINCT code) FROM kline WHERE date IN ('2026-05-14','2026-05-15') GROUP BY date ORDER BY date")
print("验证:")
for row in r.stdout.strip().split('\n'):
    if row:
        print(f"  {row}")
