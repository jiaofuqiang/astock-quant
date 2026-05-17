#!/usr/bin/env python3
"""
🔥 板块延续性 × 三资金合力 实盘扫描器

基于回测结论的最佳赚钱模式：
  信号: 板块涨停≥2+跌停0 + 资金驱动>20 + 收敛<std<2.5
  买入: 盘尾收盘价（14:55后）
  持有: T+3~5等盘中最高
  卖出: 盘中冲高≥5%止盈，不等竞价

用法:
  python3 scripts/continuity_live_scan.py           # 全量扫描
  python3 scripts/continuity_live_scan.py --brief   # 简洁输出
  python3 scripts/continuity_live_scan.py --push    # 推送微信
"""
import os, sys, json, re, subprocess
from datetime import datetime, date
from collections import defaultdict

BASE = os.path.expanduser("~/astock")
DATA_DIR = os.path.join(BASE, "data")

# ====== 板块成分股（与three_funds_scan同步）======
SECTORS = {
    'chip': {'name': '存储芯片/AI芯片', 'codes': ['603986','603019','600584','603005','603160',
              '002049','600171','603893','002185','300655','300672','300661','688525','688110']},
    'gpu': {'name': 'AI算力/服务器', 'codes': ['601138','603019','000977','600498','000063',
            '002916','300308','688041']},
    'semicon': {'name': '半导体设备/材料', 'codes': ['688981','688012','688008','688126','688396',
               '002371','688072','688120','688037','300661','688019','688200']},
    'robot': {'name': '人形机器人', 'codes': ['002472','002896','300124','688160','300660',
             '688017','300580','601689','603662']},
    'ai_app': {'name': 'AI应用/AIGC', 'codes': ['300624','002230','300418','603533','002555',
              '300058','300315','300624','002517','688111']},
    'low_alt': {'name': '低空经济/飞行汽车', 'codes': ['002085','600580','300177','688070','688568',
               '002111','002023','603885','000099','600391']},
    'battery': {'name': '固态电池/新能源', 'codes': ['300750','002074','300014','002460','002709',
               '600884','300073','300568','002812','300769']},
}

ALL_CODES = list(dict.fromkeys(c for s in SECTORS.values() for c in s['codes']))
CODE_TO_SECTOR = {}
for sk, sv in SECTORS.items():
    for c in sv['codes']:
        CODE_TO_SECTOR[c] = {'key': sk, 'name': sv['name']}

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')

def mkt(code):
    return f'sh{code}' if code[0] in ('6','5','9') else f'sz{code}'

def fetch_quotes():
    """获取所有板块股票的实时行情"""
    quotes = {}
    for i in range(0, len(ALL_CODES), 30):
        batch = ALL_CODES[i:i+30]
        codes_str = ','.join(mkt(c) for c in batch)
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://qt.gtimg.cn/q={codes_str}'],
                capture_output=True, timeout=12
            )
            raw_text = r.stdout.decode('gbk', errors='replace')
            for line in raw_text.strip().split('\n'):
                line = line.strip()
                if not line or '=' not in line: continue
                parts = line.split('=', 1)
                raw = parts[1].strip().strip('"').strip(';').strip('"')
                fields = raw.split('~')
                if len(fields) < 50: continue
                
                code = fields[2].strip()
                if not code: continue
                
                try:
                    cur = float(fields[3]) if fields[3] else 0
                    prev = float(fields[4]) if fields[4] else 0
                    open_p = float(fields[5]) if fields[5] else 0
                    volume = int(fields[6]) if fields[6] else 0
                    buy_vol = int(fields[7]) if fields[7] else 0
                    sell_vol = int(fields[8]) if fields[8] else 0
                    high = float(fields[33]) if fields[33] else 0
                    low = float(fields[34]) if fields[34] else 0
                    chg = float(fields[32]) if fields[32] else 0
                    turn = float(fields[38]) if fields[38] else 0
                    amps = float(fields[43]) if fields[43] else 0
                    name = fields[1].strip()
                    vol_r = float(fields[49]) if len(fields) > 49 and fields[49] else 0
                    
                    # 主力资金
                    main_buy = float(fields[58]) if len(fields) > 58 and fields[58] else 0
                    main_sell = float(fields[59]) if len(fields) > 59 and fields[59] else 0
                    main_net = main_buy - main_sell
                    
                    is_limit = chg >= 9.5 and cur >= prev * 1.09
                    
                    quotes[code] = {
                        'name': name, 'price': cur, 'prev_close': prev,
                        'change_pct': chg, 'volume': volume, 'turnover': turn,
                        'amplitude': amps, 'vol_ratio': vol_r,
                        'buy_vol': buy_vol, 'sell_vol': sell_vol,
                        'main_net': main_net, 'is_limit_up': is_limit,
                        'high': high, 'low': low, 'open': open_p,
                    }
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            log(f"⚠️ 采集失败: {e}")
    return quotes


def calc_sector_stats(quotes):
    """计算板块级统计量"""
    sector_stats = {}
    
    for sk, sv in SECTORS.items():
        sec_quotes = {c: quotes.get(c) for c in sv['codes'] if c in quotes and quotes.get(c)}
        if len(sec_quotes) < 3:
            continue
        
        chgs = [q['change_pct'] for q in sec_quotes.values()]
        up_count = sum(1 for c in chgs if c > 0)
        total = len(chgs)
        median_chg = sorted(chgs)[len(chgs)//2]
        max_chg = max(chgs)
        min_chg = min(chgs)
        limit_up = sum(1 for c in chgs if c >= 9.5)
        limit_down = sum(1 for c in chgs if c <= -9.5)
        big_up = sum(1 for c in chgs if 7 <= c < 9.5)
        big_down = sum(1 for c in chgs if -9.5 < c <= -7)
        
        # 板块成交量
        vols = [q['volume'] for q in sec_quotes.values()]
        total_vol = sum(vols) if vols else 0
        
        # 板块主力净额
        total_main = sum(q.get('main_net', 0) for q in sec_quotes.values())
        
        # 收敛度（涨幅标准差）
        variance = sum((c - median_chg)**2 for c in chgs) / len(chgs) if chgs else 0
        convergence = round(variance**0.5, 2)
        
        # 资金驱动指数（同回测公式）
        fund_drive = 0
        if limit_up >= 2:
            fund_drive += 30
        if up_count / total >= 0.6:
            fund_drive += 20
        if limit_up >= 1 and big_up >= 2:
            fund_drive += 10
        if limit_down >= 1:
            fund_drive -= 20
        # 主力资金加成（新增！）
        if total_main > 0:
            fund_drive += 10
        if total_main > 1000000:
            fund_drive += 10
        
        # ====== 延续性评分 ======
        score = 50
        details = ['基础50']
        
        up_ratio = up_count / max(total, 1)
        if up_ratio >= 0.75:
            score += 15
            details.append(f'涨{up_ratio*100:.0f}%>75% +15')
        
        if median_chg >= 1.5:
            score += 10
            details.append(f'中位{median_chg:.1f}%>1.5 +10')
        elif median_chg >= 1.0:
            score += 5
            details.append(f'中位{median_chg:.1f}%>1% +5')
        
        if limit_up >= 2 and up_ratio >= 0.5:
            score += 10
            details.append(f'涨停{limit_up}+涨{up_ratio*100:.0f}% +10')
        elif limit_up >= 1 and up_ratio < 0.4:
            score -= 10
            details.append(f'龙头断层 +{limit_up}/涨{up_ratio*100:.0f}% -10')
        
        if limit_down >= 1:
            score -= 15
            details.append(f'有跌停{limit_down}只 -15')
        
        if convergence > 3.0:
            score -= 5
            details.append(f'分化std{convergence:.1f} -5')
        
        if total_main > 500000:
            score += 10
            details.append(f'主力+{total_main/10000:.0f}万 +10')
        
        # 收敛+涨停组合（最强）
        if convergence < 2.5 and limit_up >= 2 and up_ratio >= 0.5:
            score += 15
            details.append('收敛+涨停+普涨(强) +15')
        
        score = max(0, min(100, score))
        
        if score >= 85: level = '💎极强'
        elif score >= 70: level = '🔥强延续'
        elif score >= 50: level = '✅中延续'
        elif score >= 30: level = '⚠️弱延续'
        else: level = '❌低延续'
        
        sector_stats[sk] = {
            'name': sv['name'],
            'median_chg': median_chg,
            'up_ratio': round(up_ratio, 2),
            'limit_up': limit_up,
            'limit_down': limit_down,
            'big_up': big_up,
            'fund_drive': fund_drive,
            'convergence': convergence,
            'total_main': total_main,
            'cont_score': score,
            'cont_level': level,
            'cont_detail': ' | '.join(details),
            'stock_count': total,
            'top_stocks': sorted(sec_quotes.items(), key=lambda x: -x[1]['change_pct'])[:3],
        }
    
    return sector_stats


def evaluate_buy_signals(sector_stats, quotes):
    """
    基于回测结论，评估买入信号
    
    回测最佳条件：
      🥇 收敛+涨停>跌停 → 延续率82% +1.96%
      🥈 涨停≥2+跌停0 → 延续率73% +2.18%
      🥉 资金驱动>30 → 延续率72% +1.77%
    """
    signals = []
    
    for sk, ss in sector_stats.items():
        # === 买入信号 ===
        buy_reasons = []
        confidence = 0
        
        # 条件1: 涨停≥2只
        if ss['limit_up'] >= 2:
            buy_reasons.append(f'涨停{ss["limit_up"]}只')
            confidence += 1
        
        # 条件2: 无跌停
        if ss['limit_down'] == 0:
            buy_reasons.append('无跌停')
            confidence += 1
        
        # 条件3: 资金驱动强
        if ss['fund_drive'] >= 30:
            buy_reasons.append(f'资金驱动{ss["fund_drive"]}')
            confidence += 2
        elif ss['fund_drive'] >= 20:
            buy_reasons.append(f'资金驱动{ss["fund_drive"]}')
            confidence += 1
        
        # 条件4: 收敛+涨停（最强组合）
        if ss['convergence'] < 2.5 and ss['limit_up'] >= 2:
            buy_reasons.append('收敛+涨停(最强)')
            confidence += 3
        
        # 条件5: 主力净流入
        if ss['total_main'] > 500000:
            buy_reasons.append(f'主力+{ss["total_main"]/10000:.0f}万')
            confidence += 1
        
        # 条件6: 延续性评分
        if ss['cont_score'] >= 70:
            buy_reasons.append(f'延续{ss["cont_level"]}({ss["cont_score"]})')
            confidence += 2
        elif ss['cont_score'] >= 50:
            buy_reasons.append(f'延续{ss["cont_level"]}({ss["cont_score"]})')
        
        # === 综合判定 ===
        if confidence >= 5:
            level = '🟢🟢🟢 强烈买入'
        elif confidence >= 3:
            level = '🟢🟢 买入'
        elif confidence >= 2:
            level = '🟡 关注'
        else:
            continue
        
        # 找买入标的（板块内涨停的、大涨的）
        target_stocks = []
        for code, q in ss['top_stocks']:
            if q.get('is_limit_up'):
                target_stocks.append(f"{q['name']}(涨停)")
            elif q['change_pct'] >= 7:
                target_stocks.append(f"{q['name']}+{q['change_pct']:.1f}%")
            elif q['change_pct'] >= 5:
                target_stocks.append(f"{q['name']}+{q['change_pct']:.1f}%")
        
        signals.append({
            'sector_key': sk,
            'sector_name': ss['name'],
            'level': level,
            'confidence': confidence,
            'cont_level': ss['cont_level'],
            'cont_score': ss['cont_score'],
            'median_chg': ss['median_chg'],
            'limit_up': ss['limit_up'],
            'limit_down': ss['limit_down'],
            'fund_drive': ss['fund_drive'],
            'convergence': ss['convergence'],
            'total_main': ss['total_main'],
            'reasons': ' | '.join(buy_reasons),
            'targets': target_stocks,
            'detail': ss['cont_detail'],
        })
    
    signals.sort(key=lambda x: -x['confidence'])
    return signals


def format_output(signals, sector_stats):
    """格式化输出"""
    now = datetime.now()
    is_afternoon = now.hour >= 14 and now.minute >= 50
    
    lines = []
    lines.append(f"{'='*70}")
    lines.append(f"🔥 板块延续性 × 三资金 实盘扫描")
    lines.append(f"⏰ {now.strftime('%Y-%m-%d %H:%M')}  {'🟢 盘尾时间可买入' if is_afternoon else '⏳ 盘中观察中'}")
    lines.append(f"{'='*70}")
    
    if not signals:
        lines.append("\n❌ 当前无符合条件的买入信号")
        lines.append("💡 条件: 涨停≥2+无跌停+资金驱动>20+主力净流入")
        lines.append("\n📊 各板块速览:")
        lines.append(f"{'板块':20s} {'中位数':>8s} {'涨停':>4s} {'跌停':>4s} {'资金驱':>6s} {'主力净额':>10s} {'延续':>8s}")
        lines.append(f"{'─'*65}")
        for sk, ss in sorted(sector_stats.items(), key=lambda x: -x[1]['fund_drive']):
            lines.append(f"{ss['name']:20s} {ss['median_chg']:+7.2f}% {ss['limit_up']:3d} {ss['limit_down']:3d} {ss['fund_drive']:5d} {ss['total_main']/10000:+9.0f}万 {ss['cont_level']:>8s}")
        return '\n'.join(lines)
    
    for sig in signals:
        lines.append(f"\n{sig['level']} — {sig['sector_name']}")
        lines.append(f"  📊 板块: 中位数{sig['median_chg']:+.1f}% | 涨停{sig['limit_up']}只 | 跌停{sig['limit_down']}只 | 资金驱动{sig['fund_drive']}")
        lines.append(f"  🔗 收敛度: {sig['convergence']:.1f} | 主力净额: {sig['total_main']/10000:+.0f}万")
        lines.append(f"  📈 延续性: {sig['cont_level']}({sig['cont_score']}) | {sig['reasons']}")
        if sig['targets']:
            lines.append(f"  🎯 关注标的: {', '.join(sig['targets'][:5])}")
    
    # 底部说明
    lines.append(f"\n{'─'*70}")
    lines.append("🔥 基于全量回测(3,195只主板)的最佳赚钱模式:")
    lines.append("")
    lines.append("【隔夜溢价 — 最稳最暴利】")
    lines.append("  买入: 缩量<0.7 + 极硬板(上影<0.5%) → T+1开盘卖均+3.51%胜77.7%")
    lines.append("  卖出: T+1开盘卖(09:30)或冲高≥3%止盈")
    lines.append("  全部缩量板(<1倍): T+1开盘卖均+2.17%胜71.6%暴16.2% 样本6,706次")
    lines.append("")
    lines.append("【板块爆发+跟风买入 — 胜率最高】")
    lines.append("  条件: 板块涨停≥3只 + 资金驱动>30 + 无跌停")
    lines.append("  持有: T+5最高卖 — 均+9.96%胜94% (T+3最高+8.01%胜93%)")
    lines.append("  买任意板块内涨停股(排名不重要)")
    lines.append("")
    lines.append("【盘尾极硬板 — 每天可做】")
    lines.append("  条件: 极硬板≥80分(缩量+无上影+横盘)")
    lines.append("  持有: T+1开盘卖均+2.10%胜65.1%最高+6.07%暴20.8%")
    lines.append("")
    lines.append("⚠️ 避坑:")
    lines.append("  ❌ 量比>4(巨量涨停)T+1开盘卖均+0.94%胜51% → 不追")
    lines.append("  ❌ 上影>2%(烂板)T+1均亏-4.57% → 不买")
    lines.append("  ❌ F2信号仅+1.16% → 不单独做，融入隔夜溢价")
    lines.append("  ❌ 昆仑万维(25%)/晶方科技(0%)/华天科技(40%) → 避开")
    lines.append(f"{'─'*70}")
    
    return '\n'.join(lines)


def push_wechat(message):
    """推送微信"""
    log("📱 推送微信...")
    try:
        # 保存到文件让推送脚本读取
        push_file = os.path.join(BASE, 'data', 'continuity_push.json')
        with open(push_file, 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'message': message,
            }, f, ensure_ascii=False)
        log("✅ 推送数据已保存")
        return True
    except Exception as e:
        log(f"⚠️ 推送失败: {e}")
        return False


def main():
    args = sys.argv[1:]
    brief_mode = '--brief' in args
    push_mode = '--push' in args
    
    log("📡 获取实时行情...")
    quotes = fetch_quotes()
    if not quotes:
        log("❌ 无法获取行情数据")
        return
    
    log(f"✅ 获取 {len(quotes)}只股票行情")
    
    sector_stats = calc_sector_stats(quotes)
    signals = evaluate_buy_signals(sector_stats, quotes)
    
    output = format_output(signals, sector_stats)
    
    if brief_mode:
        if signals:
            print(json.dumps([{
                'sector': s['sector_name'],
                'level': s['level'],
                'confidence': s['confidence'],
                'limit_up': s['limit_up'],
                'limit_down': s['limit_down'],
                'cont_score': s['cont_score'],
                'cont_level': s['cont_level'],
                'targets': s['targets'][:3],
            } for s in signals], ensure_ascii=False))
        else:
            print(json.dumps({'signals': 0}))
    else:
        print(output)
    
    if push_mode and signals:
        push_msg = f"🔥 板块买入信号\n\n"
        for s in signals[:3]:
            push_msg += f"{s['level']} {s['sector_name']}\n"
            push_msg += f"  涨停{s['limit_up']}只 | 延续{s['cont_level']}({s['cont_score']})\n"
            push_msg += f"  关注: {', '.join(s['targets'][:3])}\n\n"
        push_wechat(push_msg)
    
    log(f"✅ 扫描完成: 买入信号{len(signals)}个")


if __name__ == '__main__':
    main()
