#!/usr/bin/env python3
"""One-shot dashboard bundle aggregator"""
import json, os, glob, time, shutil
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
DATA = os.path.join(ROOT, 'data')
V2BOARD = os.path.join(ROOT, 'V2board')
ARCHIVE = os.path.join(V2BOARD, 'archive')

def safe_read_text(path):
    try:
        if os.path.exists(path):
            with open(path, 'r') as f: return f.read(65536)
    except: pass
    return None

def safe_read_json(path):
    try:
        if os.path.exists(path):
            with open(path) as f: return json.load(f)
    except: pass
    return None

def safe_read_lines(path):
    try:
        if os.path.exists(path):
            with open(path) as f: return f.readlines()
    except: pass
    return None

now = datetime.now()
ts = int(time.time() * 1000)

bundle = {
    '_meta': {
        'timestamp': ts,
        'iso': now.isoformat(),
        'bundleVersion': f'b{ts}',
        'aggregatedAt': now.strftime('%H:%M:%S')
    }
}

# 核心数据源列表
sources = {
    'scan_data': safe_read_lines,
    'market_env': safe_read_json,
    'sector_index': safe_read_json,
    'ban_reasons': safe_read_text,
    'us_market_map': safe_read_json,
    'lhb': safe_read_json,
    'new_concepts': safe_read_json,
    'buy_signal': safe_read_text,
    'youzi_signal': safe_read_json,
    'retail_sentiment': safe_read_json,
    'f2_data': safe_read_json,
    'watch_pool': safe_read_text,
    'sector_decision': safe_read_json,
    'block_trade': safe_read_json,
}

# Try multiple paths for each source
for key, reader in sources.items():
    paths = [
        os.path.join(V2BOARD, f'{key}.json') if key != 'scan_data' else os.path.join(V2BOARD, 'scan_data.txt'),
        os.path.join(V2BOARD, f'{key}.txt'),
        os.path.join(V2BOARD, 'data', f'{key}.json'),
        os.path.join(ROOT, f'{key}.json'),
        os.path.join(ROOT, 'data', f'{key}.json'),
    ]
    if key == 'scan_data':
        paths = [
            os.path.join(ROOT, 'data', 'scan_data.txt'),
            os.path.join(V2BOARD, 'scan_data.txt'),
            os.path.join(ROOT, 'scan_data.txt'),
        ]
    if key == 'sector_index':
        # sector_index is a dict, not a JSON file per se
        paths = [
            os.path.join(ROOT, 'sector_index_data.json'),
            os.path.join(V2BOARD, 'sector_index_data.json'),
        ]
    if key == 'ban_reasons':
        paths = [
            os.path.join(ROOT, 'ban_reasons.json'),
            os.path.join(V2BOARD, 'ban_reasons.json'),
            os.path.join(ROOT, 'data', 'ban_reasons.json'),
        ]
    if key == 'lhb':
        paths = [
            os.path.join(ROOT, 'data', 'lhb_signal.json'),
            os.path.join(V2BOARD, 'lhb_signal.json'),
        ]
    if key == 'market_env':
        paths = [
            os.path.join(ROOT, 'data', 'market_env.json'),
            os.path.join(V2BOARD, 'market_env.json'),
        ]

    val = None
    for p in paths:
        val = reader(p)
        if val:
            break

    if val:
        bundle[key] = val

# Write main bundle to V2board
os.makedirs(V2BOARD, exist_ok=True)
out = os.path.join(V2BOARD, 'dashboard_bundle.json')
with open(out, 'w', encoding='utf-8') as f:
    json.dump(bundle, f, ensure_ascii=False, default=str)

# Write archive
os.makedirs(ARCHIVE, exist_ok=True)
date_str = now.strftime('%Y-%m-%d')
archive_path = os.path.join(ARCHIVE, f'dashboard_bundle_{date_str}.json')
bundle['_meta']['archiveDate'] = date_str
with open(archive_path, 'w', encoding='utf-8') as f:
    json.dump(bundle, f, ensure_ascii=False, default=str)

# Update archive index
archive_files = sorted(glob.glob(os.path.join(ARCHIVE, 'dashboard_bundle_*.json')))
dates = []
for af in archive_files:
    fn = os.path.basename(af)
    d = fn.replace('dashboard_bundle_', '').replace('.json', '')
    dates.append(d)
with open(os.path.join(ARCHIVE, 'archive_list.json'), 'w') as f:
    json.dump({'dates': dates, 'updated': now.isoformat()}, f, ensure_ascii=False)

# Copy archive_list to root
shutil.copy(os.path.join(ARCHIVE, 'archive_list.json'), os.path.join(ROOT, 'archive_list.json'))

# Timestamp file
with open(os.path.join(V2BOARD, 'bundle_ts.txt'), 'w') as f:
    f.write(str(ts))

keys = list(bundle.keys())
print(f"✅ Bundle aggregated: {len(keys)} sources, {len(json.dumps(bundle))//1024}KB")
for k in keys:
    print(f"   - {k}: {type(bundle[k]).__name__}")
