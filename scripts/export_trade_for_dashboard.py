#!/usr/bin/env python3
"""
导出妙想模拟交易数据供作战面板前端使用
替换旧trade_sim.db → 妙想API
输出: /home/ubuntu/V2board/data/trade_export.json
"""
import json, os, sys, subprocess, re
from datetime import datetime

OUT = os.path.expanduser("~/V2board/data/trade_export.json")
MX_MONI = os.path.expanduser("~/.hermes/skills/mx-moni/mx_moni.py")
OUTPUT_DIR = os.path.expanduser("~/.hermes/mx_data/output")

def run_mx(cmd):
    """调用妙想脚本，返回解析后的数据"""
    r = subprocess.run([sys.executable, MX_MONI, cmd],
                       capture_output=True, text=True, timeout=20)
    # 找最近输出的JSON文件
    files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith(f'mx_moni_{cmd}_') and f.endswith('.json')]
    if not files:
        print(f"  ⚠️  妙想输出未找到: {cmd}")
        return None
    latest = sorted(files)[-1]
    with open(os.path.join(OUTPUT_DIR, latest)) as f:
        return json.load(f)

def parse_positions(api_data):
    """解析妙想持仓API返回 → 统一格式"""
    if not api_data or not api_data.get('data'):
        return []
    pos_list = api_data['data'].get('posList') or []
    positions = []
    for p in pos_list:
        cost_p = p.get('costPrice', 0) / (10 ** p.get('costPriceDec', 2))
        cur_p = p.get('price', 0) / (10 ** p.get('priceDec', 2))
        positions.append({
            'code': p.get('secCode', ''),
            'name': p.get('secName', ''),
            'price': round(cur_p, 3),
            'cost': round(cost_p, 3),
            'shares': p.get('count', 0),
            'profit_rate': round(p.get('profitPct', 0), 2),
            'profit': round(p.get('profit', 0), 2),
            'day_profit': round(p.get('dayProfit', 0), 2),
            'day_profit_pct': round(p.get('dayProfitPct', 0), 2),
            'market_value': round(p.get('value', 0), 2),
        })
    return positions

def parse_balance(api_data):
    """解析妙想资金查询结果"""
    if not api_data or not api_data.get('data'):
        return {}
    d = api_data['data']
    return {
        'total_assets': d.get('totalAssets', 0),
        'avail_balance': d.get('availBalance', 0),
        'frozen': d.get('frozenMoney', 0),
        'total_pos_value': d.get('totalPosValue', 0),
        'pos_ratio': d.get('totalPosPct', 0),
        'init_money': d.get('initMoney', 0),
        'nav': d.get('nav', 1.0),
    }

def parse_orders(api_data):
    """解析委托/成交查询"""
    if not api_data or not api_data.get('data'):
        return [], []
    orders_data = api_data['data'].get('orders') or []
    trades = []
    pending = []
    for o in orders_data:
        price = o.get('price', 0) / (10 ** o.get('priceDec', 2))
        status = o.get('status', 0)
        ts = o.get('time', 0)
        item = {
            'id': o.get('id', ''),
            'code': o.get('secCode', ''),
            'name': o.get('secName', ''),
            'direction': 'buy' if o.get('drt') == 1 else 'sell',
            'price': round(price, 3),
            'shares': o.get('count', 0),
            'traded_shares': o.get('tradeCount', 0),
            'status': status,
            'status_text': {1:'未报',2:'已报',3:'部成',4:'已成',5:'部待撤',6:'报待撤',7:'部撤',8:'已撤',9:'废单',10:'撤失败'}.get(status, '未知'),
            'time': datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else '',
            'ts': ts,
        }
        if status == 4:  # 已成
            trades.append(item)
        elif status in (2, 3):  # 已报/部成
            pending.append(item)
    trades.sort(key=lambda x: x['ts'], reverse=True)
    pending.sort(key=lambda x: x['ts'], reverse=True)
    return trades, pending

def export():
    print(f"\n📊 导出妙想模拟交易数据...")

    # 获取资金
    bal_raw = run_mx("我的资金")
    bal = parse_balance(bal_raw) if bal_raw else {}

    # 获取持仓
    pos_raw = run_mx("我的持仓")
    positions = parse_positions(pos_raw) if pos_raw else []

    # 获取历史成交
    his_raw = run_mx("历史成交")
    trades_today, pending = parse_orders(his_raw) if his_raw else ([], [])

    # 计算今日盈亏
    today_pnl = sum(p.get('day_profit', 0) for p in positions)
    today_pnl_sell = sum(t.get('traded_shares', 0) * t.get('price', 0) for t in trades_today if t['direction'] == 'sell')

    asset = bal.get('total_assets', 0)
    cash = bal.get('avail_balance', 0)
    init_money = bal.get('init_money', 100000)
    total_profit = asset - init_money
    total_profit_rate = round((total_profit / init_money) * 100, 2) if init_money > 0 else 0

    data = {
        'asset': round(asset, 2),
        'cash': round(cash, 2),
        'total_profit': round(total_profit, 2),
        'total_profit_rate': total_profit_rate,
        'today_pnl': round(today_pnl, 2),
        'trade_count': len(trades_today),
        'positions': positions,
        'trades': trades_today[:20],
        'pending_orders': pending[:20],
        'balance': bal,
        'update_time': datetime.now().isoformat(),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 交易数据已导出: {OUT}")
    print(f"   资产: ¥{data['asset']:,.0f} 持仓: {len(positions)}只 今日盈亏: {data['today_pnl']:+,.0f}")
    if positions:
        for p in positions[:5]:
            print(f"   {p['name']}({p['code']}) {p['shares']}股 盈亏{p['profit']:+,.0f}")
    return data

if __name__ == '__main__':
    export()
