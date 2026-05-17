#!/usr/bin/env python3
"""
P0: market_daily.db 历史数据回填
============================
从env_daily_history.json(570天)回填到day_full表
只填环境相关的核心字段，其余留NULL

执行: cd ~/astock && python3 scripts/backfill_market_daily.py
"""

import os, sys, json, sqlite3
from datetime import datetime

BASE = os.path.expanduser("~/astock")
DATA = os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA, "market_daily.db")
ENV_PATH = os.path.join(DATA, "env_daily_history.json")

# ============================================================
# 字段映射：env_daily_history → day_full
# ============================================================
FIELD_MAP = {
    'up_count': 'up',           # 上涨数
    'down_count': 'down',       # 下跌数
    'zh_ratio': ('up_ratio', lambda v: round(v * 100, 1) if v else 50),  # 涨跌比%
    'limit_up': 'limit_up',     # 涨停数
    'limit_down': 'limit_down', # 跌停数
    'max_board': 'env_zt',      # 最高板(估计值)
}

# ============================================================
def main():
    print(f"\n{'='*60}")
    print(f"📊 market_daily.db 历史回填 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    # 1. 加载环境数据
    if not os.path.exists(ENV_PATH):
        print(f"  ❌ 未找到环境数据: {ENV_PATH}")
        return 1
    
    with open(ENV_PATH) as f:
        env_raw = json.load(f)
    daily = env_raw.get('daily', {})
    print(f"  📡 env_daily_history: {len(daily)}个交易日")
    
    # 2. 连接数据库
    conn = sqlite3.connect(DB_PATH)
    
    # 3. 获取已有日期
    existing = set(r[0] for r in conn.execute('SELECT date FROM day_full').fetchall())
    print(f"  📋 day_full已有: {len(existing)}条")
    
    # 4. 排序回填
    dates = sorted(daily.keys())
    total = len(dates)
    inserted = 0
    skipped = 0
    
    for i, d in enumerate(dates):
        if d in existing:
            skipped += 1
            continue
        
        ev = daily[d]
        
        # 构建INSERT语句
        fields = ['date']
        values = [f"'{d}'"]
        
        for day_col, env_key in FIELD_MAP.items():
            if isinstance(env_key, tuple):
                key, transform = env_key
                val = ev.get(key, None)
                if val is not None:
                    try:
                        val = transform(val)
                        fields.append(day_col)
                        values.append(str(val))
                    except:
                        pass
            else:
                val = ev.get(env_key, None)
                if val is not None:
                    fields.append(day_col)
                    values.append(str(val))
        
        # 补充计算字段
        up_cnt = ev.get('up', 0)
        down_cnt = ev.get('down', 0)
        total_stocks = ev.get('total_stocks', up_cnt + down_cnt)
        
        if up_cnt and down_cnt and total_stocks:
            zh = round(up_cnt / down_cnt, 2) if down_cnt > 0 else 99
            fields.append('zh_ratio')
            values.append(str(zh))
        
        sql = f"INSERT INTO day_full ({','.join(fields)}) VALUES ({','.join(values)})"
        
        try:
            conn.execute(sql)
            inserted += 1
            if inserted % 50 == 0:
                print(f"  ✅ 已回填 {inserted}/{total} 天...", end='\r')
        except Exception as e:
            print(f"\n  ⚠️ 第{i}天({d})写入失败: {e}")
        
        if inserted % 100 == 0:
            conn.commit()
    
    conn.commit()
    
    # 5. 验证
    final_count = conn.execute('SELECT COUNT(*) FROM day_full').fetchone()[0]
    dates_range = conn.execute('SELECT MIN(date), MAX(date) FROM day_full').fetchone()
    conn.close()
    
    print(f"\n{'='*60}")
    print(f"✅ 回填完成!")
    print(f"  新增: {inserted}天")
    print(f"  跳过(已存在): {skipped}天")
    print(f"  day_full总计: {final_count}条")
    print(f"  时间范围: {dates_range[0]} ~ {dates_range[1]}")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
