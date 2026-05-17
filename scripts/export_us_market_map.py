#!/usr/bin/env python3
"""
美股→A股映射数据导出，供作战面板前端使用
输出: /home/ubuntu/V2board/us_market_map.json
"""
import json, os, sqlite3, subprocess
from datetime import datetime

DB = os.path.expanduser("~/astock/data/macro_cache.db")
OUT = os.path.expanduser("~/V2board/us_market_map.json")

# 美股→A股映射表
MAP = {
    '英伟达': {'code':'NVDA', 'name':'英伟达', 'category':'AI算力', 'a_stocks':[
        {'code':'603019','name':'中科曙光'}, {'code':'000977','name':'浪潮信息'},
        {'code':'601138','name':'工业富联'}, {'code':'603893','name':'瑞芯微'},
    ]},
    'AMD': {'code':'AMD', 'name':'AMD', 'category':'AI算力', 'a_stocks':[
        {'code':'603019','name':'中科曙光'}, {'code':'000977','name':'浪潮信息'},
    ]},
    '英特尔': {'code':'INTC', 'name':'英特尔', 'category':'芯片', 'a_stocks':[
        {'code':'603986','name':'兆易创新'}, {'code':'002049','name':'紫光国微'},
        {'code':'600667','name':'太极实业'},
    ]},
    '台积电': {'code':'TSM', 'name':'台积电', 'category':'芯片制造', 'a_stocks':[
        {'code':'002371','name':'北方华创'}, {'code':'688019','name':'安集科技(科创)'},
    ]},
    '博通': {'code':'AVGO', 'name':'博通', 'category':'网络芯片', 'a_stocks':[
        {'code':'002281','name':'光迅科技'}, {'code':'000063','name':'中兴通讯'},
    ]},
    '美满电子': {'code':'MRVL', 'name':'美满电子', 'category':'存储芯片', 'a_stocks':[
        {'code':'603986','name':'兆易创新'}, {'code':'600667','name':'太极实业'},
    ]},
    '超微电脑': {'code':'SMCI', 'name':'超微电脑', 'category':'AI服务器', 'a_stocks':[
        {'code':'000977','name':'浪潮信息'}, {'code':'603019','name':'中科曙光'},
    ]},
    '特斯拉': {'code':'TSLA', 'name':'特斯拉', 'category':'新能源车', 'a_stocks':[
        {'code':'002460','name':'赣锋锂业'}, {'code':'002074','name':'国轩高科'},
        {'code':'601689','name':'拓普集团'},
    ]},
    '苹果': {'code':'AAPL', 'name':'苹果', 'category':'消费电子', 'a_stocks':[
        {'code':'002475','name':'立讯精密'}, {'code':'600745','name':'闻泰科技'},
    ]},
    '微软': {'code':'MSFT', 'name':'微软', 'category':'AI应用', 'a_stocks':[
        {'code':'002517','name':'恺英网络'}, {'code':'603533','name':'掌阅科技'},
    ]},
    'Meta': {'code':'META', 'name':'Meta', 'category':'AI应用', 'a_stocks':[
        {'code':'002555','name':'三七互娱'}, {'code':'002517','name':'恺英网络'},
    ]},
    '英伟达/AMD': {'code':'NVDA+AMD', 'name':'AI算力双核', 'category':'AI算力', 'a_stocks':[
        {'code':'603019','name':'中科曙光'}, {'code':'000977','name':'浪潮信息'},
        {'code':'601138','name':'工业富联'},
    ]},
}

def export():
    if not os.path.exists(DB):
        print(f"❌ {DB} not found, no caching")
        return

    # 取最新日期
    r = subprocess.run(['sqlite3', DB, "SELECT MAX(date) FROM macro_key_stock"],
        capture_output=True, text=True, timeout=10)
    latest_date = r.stdout.strip()
    if not latest_date:
        print("❌ No data in macro_cache.db")
        return

    # 取美股指数
    r = subprocess.run(['sqlite3', '-separator', '|', DB,
        f"SELECT name_cn, price, change_pct FROM macro_index_data WHERE date='{latest_date}' ORDER BY ROWID ASC"],
        capture_output=True, text=True, timeout=10)
    indices = []
    for row in r.stdout.strip().split('\n'):
        if not row.strip(): continue
        parts = row.split('|')
        if len(parts) >= 3:
            indices.append({'name': parts[0], 'price': float(parts[1]), 'change_pct': float(parts[2])})

    # 取关键美股数据
    r = subprocess.run(['sqlite3', '-separator', '|', DB,
        f"SELECT symbol, name_cn, price, change_pct, volume FROM macro_key_stock WHERE date='{latest_date}' ORDER BY change_pct DESC"],
        capture_output=True, text=True, timeout=10)
    us_stocks_data = {}
    for row in r.stdout.strip().split('\n'):
        if not row.strip(): continue
        parts = row.split('|')
        if len(parts) >= 4:
            us_stocks_data[parts[1]] = {
                'symbol': parts[0], 'price': float(parts[1]) if parts[1].replace('.','').replace('-','').isdigit() else 0,
                'change_pct': float(parts[3]), 'volume': int(float(parts[4])) if len(parts) >= 5 and parts[4] else 0
            }

    # 构建映射结果
    mappings = []
    for cn_name, map_info in MAP.items():
        us = us_stocks_data.get(cn_name)
        if not us:
            # 尝试匹配
            for k, v in us_stocks_data.items():
                if cn_name in k or k in cn_name:
                    us = v
                    break
        if not us:
            continue

        entry = {
            'us_name': map_info['name'],
            'us_code': map_info['code'],
            'us_change': round(us['change_pct'], 2) if isinstance(us, dict) else 0,
            'category': map_info['category'],
            'a_stocks': []
        }
        # 查A股对应股票的实时行情（从scan_data或腾讯API）
        # 这里从已有的scan_data.txt反向匹配
        entry['a_stocks'] = [{'code': s['code'], 'name': s['name'], 'a_change': None, 'bias': None} for s in map_info['a_stocks']]
        entry['a_change'] = None
        mappings.append(entry)

    data = {
        'date': latest_date,
        'indices': indices,
        'mappings': mappings,
        'timestamp': datetime.now().isoformat(),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 美股映射已导出: {OUT}")
    print(f"   日期{latest_date} {len(indices)}个指数 {len(mappings)}个映射")

if __name__ == '__main__':
    export()
