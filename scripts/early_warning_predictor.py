#!/usr/bin/env python3
"""
🚀 提前买入信号预测器 v1.0
==========================
核心解决：板块级信号出现时股票已经涨停无法买入的问题
方案：用昨日尾盘数据预判今日会出板块级信号的板块

逻辑：
1. 检查各板块昨日尾盘的最佳先手股（合力55-69分）
2. 如果某板块≥2只接近达标 → 标记为"明日关注"
3. 结合板块生命周期阶段（爆发期加分）
4. 输出明日竞价关注清单

用法：
  python3 scripts/early_warning_predictor.py
"""
import os, sys, json, re, subprocess
from datetime import datetime

BASE = "/home/ubuntu/astock"
os.chdir(BASE)

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}')

def scan_three_funds():
    """运行三资金合力扫描"""
    r = subprocess.run(['python3', 'scripts/three_funds_scan.py'], 
                       capture_output=True, text=True, timeout=20)
    return r.stdout

def find_near_signals(output):
    """
    从扫描输出中找到合力55-69分（接近70的票）
    按板块分组
    """
    lines = output.split('\n')
    sector_map = {}  # 当前板块名
    near_signals = {}  # sector -> [(name, score, details)]
    
    current_sector = None
    for line in lines:
        if line.startswith('📈 '):
            current_sector = line.replace('📈 ', '').replace('**', '').strip()
            if current_sector not in near_signals:
                near_signals[current_sector] = []
        elif current_sector:
            m = re.search(r'(\S+)\s+([+-]?[\d.]+%)\s+\|\s+总分(\d+) \((\d+)\+(\d+)\+(\d+)', line)
            if m:
                name, pct, total, jg, lh, yz = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6))
                if 55 <= total <= 69:
                    near_signals[current_sector].append({
                        'name': name, 'pct': pct, 'total': total,
                        'jg': jg, 'lh': lh, 'yz': yz,
                        'gap_to_70': 70 - total
                    })
    
    return near_signals

def main():
    log("🔮 提前买入信号预测器启动")
    
    output = scan_three_funds()
    near = find_near_signals(output)
    
    print(f"\n{'='*60}")
    print(f"🚀 明日提前买入信号预测")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    found_any = False
    for sector in sorted(near.keys()):
        stocks = near[sector]
        if len(stocks) >= 2:
            found_any = True
            print(f"\n🔥 **{sector}** — {len(stocks)}只接近达标")
            for s in stocks:
                print(f"   {s['name']:10s} {s['pct']:>6s} 总分{s['total']:2d} 差{s['gap_to_70']}分")
                print(f"   分解: 机构{s['jg']}+量化{s['lh']}+游资{s['yz']}")
        elif len(stocks) == 1:
            print(f"\n📌 **{sector}** — 1只接近达标")
            s = stocks[0]
            print(f"   {s['name']:10s} {s['pct']:>6s} 总分{s['total']:2d} 差{s['gap_to_70']}分")
    
    if not found_any:
        print("\n❌ 当前无板块≥2只接近达标，明日暂无板块级信号预期")
    
    print(f"\n{'='*60}")
    print("【明日竞价操作参考】")
    print("""
方案A - 竞价预判法（推荐）🌅
  如果以重点关注板块的龙头竞价涨2%+
  → 09:25直接买入，不等开盘确认

方案B - 盘中追击法 ⚡
  开盘后实时监控，板块2只上60分即买入
  → 不等第3只

方案C - 尾盘等待法 🌇
  若全天无板块级信号 → 空仓，不勉强
""")
    print(f"{'='*60}")

if __name__ == '__main__':
    main()
