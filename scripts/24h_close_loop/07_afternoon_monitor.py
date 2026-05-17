#!/usr/bin/env python3
# 【13:00-15:00 下午监控】实时监控板块+个股买卖信号
# 监控逻辑已在 three_funds_scan.py + cloud_trading.py 中实现
# 本脚本只做聚合状态输出
import json, os, sys, re
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import V2BOARD as V2BOARD_DIR, SCAN_JSON, load_json_or_empty

TODAY = date.today().isoformat()

print(f"[13:00] 开始下午监控")

# 加载实时状态
scan_file = SCAN_JSON  # 优先 /dev/shm/three_funds_latest.json, fallback scan_data.txt
market_file = os.path.join(V2BOARD_DIR, 'market_env.json')

def check_buy_signal():
    """检查三资金合力扫描结果中有无买入信号(total_score>=75)"""
    buy_signals = []
    
    # 尝试加载JSON格式
    data = load_json_or_empty(scan_file)
    if data:
        if isinstance(data, dict):
            for key in ['stocks', 'signals', 'buy_list', 'results']:
                items = data.get(key, [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            score = item.get('total_score', item.get('score', 0))
                            if score >= 75:
                                buy_signals.append(item)
    
    if buy_signals:
        return buy_signals
    
    # 尝试加载文本格式(scan_data.txt)
    try:
        with open(scan_file) as f:
            text = f.read()
    except:
        return []
    
    for line in text.split('\n'):
        m = re.search(r'总分(\d+)', line)
        if m:
            score = int(m.group(1))
            if score >= 75:
                name_match = re.match(r'\s*([\u4e00-\u9fff\w]+)\s+', line)
                name = name_match.group(1) if name_match else '?'
                chg_match = re.search(r'([+-]\d+\.\d+)%', line)
                chg = float(chg_match.group(1)) if chg_match else 0.0
                subs = re.search(r'\((\d+)\+(\d+)\+(\d+)', line)
                subscores = [int(subs.group(1)), int(subs.group(2)), int(subs.group(3))] if subs else [0,0,0]
                
                buy_signals.append({
                    'name': name,
                    'code': '',
                    'chg_pct': chg,
                    'total_score': score,
                    'fund_scores': subscores,
                    'source': 'scan_data.txt'
                })
    
    return buy_signals

def parse_scan_for_display():
    """解析scan_data.txt为结构化摘要"""
    try:
        with open(scan_file) as f:
            text = f.read()
    except:
        return {'stocks': [], 'market_status': 'unknown', 'rti_warnings': []}
    
    result = {'stocks': [], 'market_status': 'unknown', 'rti_warnings': []}
    
    # 市场状态
    ms = re.search(r'市场状态:\s*(\S+)', text)
    if ms:
        result['market_status'] = ms.group(1)
    
    # RTI预警
    for line in text.split('\n'):
        if 'RTI=' in line and '⚠️' in line:
            result['rti_warnings'].append(line.strip())
    
    # 解析每只股票
    for line in text.split('\n'):
        # 跳过非数据行
        line = line.strip()
        if not line or line.startswith('─') or line.startswith('🎯') or line.startswith('🚨') or line.startswith('📋') or line.startswith('💡'):
            continue
        
        m = re.search(r'总分(\d+)', line)
        if m:
            score = int(m.group(1))
            name_match = re.match(r'\s*(?:\U0001f680)?([\u4e00-\u9fff\w]+)\s+', line)
            name = name_match.group(1) if name_match else '?'
            chg_match = re.search(r'([+-]\d+\.\d+)%', line)
            chg = float(chg_match.group(1)) if chg_match else 0.0
            subs = re.search(r'\((\d+)\+(\d+)\+(\d+)', line)
            subscores = [int(subs.group(1)), int(subs.group(2)), int(subs.group(3))] if subs else [0,0,0]
            force_type = '无合力'
            if '🟢' in line: force_type = '三力全开'
            elif '🟡' in line: force_type = '单力支撑'
            elif '🔴' in line: force_type = '分歧'
            
            result['stocks'].append({
                'name': name,
                'chg_pct': chg,
                'total_score': score,
                'fund_scores': subscores,
                'force_type': force_type
            })
    
    return result

# 检查买入信号
buy_signals = check_buy_signal()

# 解析全量展示数据
scan_display = parse_scan_for_display()

# 统计
total_stocks = len(scan_display['stocks'])
signals_ge60 = [s for s in scan_display['stocks'] if s['total_score'] >= 60]
signals_ge75 = [s for s in scan_display['stocks'] if s['total_score'] >= 75]
top_stock = None
if scan_display['stocks']:
    sorted_stocks = sorted(scan_display['stocks'], key=lambda x: x['total_score'], reverse=True)
    top_stock = sorted_stocks[0]

print(f"  市场状态: {scan_display['market_status']}")
print(f"  扫描标的: {total_stocks}只")
print(f"  总分≥60: {len(signals_ge60)}只")
print(f"  总分≥75: {len(signals_ge75)}只")

if buy_signals:
    print(f"  ⚡ 检测到买入信号: {len(buy_signals)}个")
    for s in buy_signals:
        print(f"    {s.get('name','?')} 总分={s.get('total_score',0)}")
else:
    print(f"  ✅ 无买入信号 (阈值75)")

if top_stock:
    print(f"  最强标的: {top_stock['name']} {top_stock['chg_pct']:+.1f}% 总分={top_stock['total_score']}")
    
    # 中大力德 + 芯源微 特别标注
    for s in sorted_stocks[:3]:
        print(f"    TOP{sorted_stocks.index(s)+1}: {s['name']} {s['chg_pct']:+.1f}% 总分={s['total_score']} {s['force_type']}")

# 写入最新信号到V2board
signal_out = {
    'time': datetime.now().strftime('%H:%M'),
    'timestamp': datetime.now().isoformat(),
    'market_status': scan_display['market_status'],
    'total_scanned': total_stocks,
    'buy_count': len(buy_signals),
    'buy_signals': buy_signals,
    'ge60_count': len(signals_ge60),
    'signals_ge60': [{'name': s['name'], 'chg_pct': s['chg_pct'], 'total_score': s['total_score'], 'force_type': s['force_type']} for s in signals_ge60],
    'top_stock': top_stock,
    'rti_warnings': scan_display['rti_warnings'],
}

os.makedirs(V2BOARD_DIR, exist_ok=True)
with open(os.path.join(V2BOARD_DIR, 'buy_signal.txt'), 'w') as f:
    json.dump(signal_out, f, ensure_ascii=False, indent=2)

print(f"  信号数据已写入 {os.path.join(V2BOARD_DIR, 'buy_signal.txt')}")
print(f"[13:00 下午监控完成]")