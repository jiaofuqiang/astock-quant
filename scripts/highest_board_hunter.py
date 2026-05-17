#!/usr/bin/env python3
"""
🏆 只买主线最高板 — 盘中扫描引擎 v1.0
========================================
基于历史回测：
  - 唯一最高板(总龙头) ≥3板: +4.03%/胜71.5% (410次)
  - 总龙头+涨停≥5只(主线活跃): +4.28%/胜73%+
  - 总龙头+早封≤09:35: +4.71%/胜87.5%

策略定位：和隔夜溢价互补，行情活跃时优先走龙头策略
数据源：tetegu_cache.db (涨停原因+板数) + 腾讯实时行情

⚠️ 已知坑：
  - sqlite3 -json可能返回dict或list格式，需兼容处理
  - 字段名是board_count不是board
  - 盘中tetegu_cache可能无今天数据，需手动跑tetegu_collector.py --date TODAY
"""

import os, sys, json, subprocess, re, time
from datetime import datetime, date
from collections import defaultdict

BASE = os.path.expanduser('~/astock')
DATA = os.path.join(BASE, 'data')
V2BOARD = os.path.expanduser('~/V2board')


# ============================================================
# 腾讯行情
# ============================================================

def mkt(code):
    return f"sh{code}" if code[0] in ('6', '5', '9') else f"sz{code}"


def fetch_quotes(codes, batch_size=100):
    """批量腾讯实时行情"""
    quotes = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        codes_str = ','.join(mkt(c) for c in batch)
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://qt.gtimg.cn/q={codes_str}'],
                capture_output=True, timeout=12
            )
            raw = r.stdout.decode('gbk', errors='replace')
            for line in raw.strip().split('\n'):
                line = line.strip()
                if not line or '=' not in line: continue
                raw_data = line.split('=', 1)[1].strip().strip('"').strip(';').strip('"')
                fields = raw_data.split('~')
                if len(fields) < 50: continue
                code = fields[2].strip()
                if not code: continue
                try:
                    cur = float(fields[3]) if fields[3] else 0
                    prev = float(fields[4]) if fields[4] else 0
                    open_p = float(fields[5]) if fields[5] else 0
                    high = float(fields[33]) if fields[33] else 0
                    low = float(fields[34]) if fields[34] else 0
                    chg = float(fields[32]) if fields[32] else 0
                    vol_r = float(fields[49]) if len(fields) > 49 and fields[49] else 0
                    name = fields[1].strip()
                    amplitude = (high - low) / (low or 1) * 100 if low > 0 else 0
                    
                    quotes[code] = {
                        'name': name, 'price': cur, 'prev_close': prev,
                        'open': open_p, 'high': high, 'low': low,
                        'change_pct': chg, 'vol_ratio': vol_r,
                        'amplitude': round(amplitude, 2),
                        'is_limit_up': chg >= 9.5 and cur >= prev * 1.09,
                        'is_yizi': open_p >= prev * 1.095 and amplitude < 1,
                    }
                except:
                    continue
        except:
            continue
        time.sleep(0.3)
    return quotes


# ============================================================
# 从tetegu_cache.db获取板块分类
# ============================================================

def load_stock_sectors():
    """从chain_engine.db加载股票→板块映射"""
    sector_map = {}
    try:
        r = subprocess.run(['sqlite3', '-json', os.path.join(DATA, 'chain_engine.db'),
            "SELECT code, level2 FROM stock_chain_v2"
        ], capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            rows = json.loads(r.stdout)
            for row in rows:
                if isinstance(row, dict):
                    if row.get('code') and row.get('level2'):
                        sector_map[row['code']] = row['level2']
                else:
                    if row[0] and row[1]:
                        sector_map[row[0]] = row[1]
    except:
        pass
    return sector_map


def get_today_limit_stocks():
    """从tetegu_cache.db获取今日完整涨停数据（含板数+涨停原因）
    
    注意：sqlite3 -json在不同版本可能返回dict格式（列名作key）或list格式（位置索引）
    """
    today = date.today().isoformat()
    limit_stocks = []
    
    # 从tetegu_cache取（有板数+原因）
    r = subprocess.run(['sqlite3', '-json', os.path.join(DATA, 'tetegu_cache.db'), 
        f"""
        SELECT l.date, l.code, l.name, l.board_count, r.reason
        FROM limit_genes l
        LEFT JOIN limit_reasons r ON l.date=r.date AND l.code=r.code
        WHERE l.date='{today}'
        ORDER BY l.board_count DESC
        """
    ], capture_output=True, text=True, timeout=5)
    
    if r.stdout.strip():
        try:
            rows = json.loads(r.stdout)
        except json.JSONDecodeError:
            rows = []
        for row in rows:
            if isinstance(row, dict):
                limit_stocks.append({
                    'date': row.get('date', ''), 'code': row.get('code', ''), 'name': row.get('name', ''),
                    'board_count': row.get('board_count', 1) or 1,
                    'reason': (row.get('reason', '') or '').replace('<div>','').replace('</div>','').replace('<font color="green">','').replace('</font>',''),
                })
            else:
                limit_stocks.append({
                    'date': row[0], 'code': row[1], 'name': row[2],
                    'board_count': row[3] if row[3] else 1,
                    'reason': (row[4] or '').replace('<div>','').replace('</div>','').replace('<font color="green">','').replace('</font>',''),
                })
    
    # 退回到daily_limit_data.db（tetegu可能没有当天数据）
    if not limit_stocks:
        r2 = subprocess.run(['sqlite3', '-json', os.path.join(DATA, 'daily_limit_data.db'),
            f"""
            SELECT date, code, name, board_count, ban_reason
            FROM limit_stocks
            WHERE date='{today}' AND board_count IS NOT NULL AND board_count > 0
            AND name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%'
            ORDER BY board_count DESC
            """
        ], capture_output=True, text=True, timeout=5)
        if r2.stdout.strip():
            try:
                rows = json.loads(r2.stdout)
            except json.JSONDecodeError:
                rows = []
            for row in rows:
                if isinstance(row, dict):
                    limit_stocks.append({
                        'date': row.get('date', ''), 'code': row.get('code', ''), 'name': row.get('name', ''),
                        'board_count': row.get('board_count', 1),
                        'reason': row.get('ban_reason', '') or '',
                    })
                else:
                    limit_stocks.append({
                        'date': row[0], 'code': row[1], 'name': row[2],
                        'board_count': row[3],
                        'reason': row[4] or '',
                    })
    
    # 过滤ST
    limit_stocks = [s for s in limit_stocks if 'ST' not in s['name'] and '*ST' not in s['name']]
    return limit_stocks


def classify_boom_sectors(stocks):
    """按涨停原因分类板块，统计各板块强度"""
    sectors = defaultdict(lambda: {'stocks': [], 'max_board': 0, 'count': 0})
    
    for s in stocks:
        reason = s.get('reason', '').lower()
        
        if any(k in reason for k in ['光纤', '通信', '光通信']):
            key = '光通信/光纤'
        elif any(k in reason for k in ['pcb', '铜箔', '封装基板']):
            key = 'PCB/封装'
        elif any(k in reason for k in ['存储', '芯片', '半导体', '封测']):
            key = '半导体/芯片'
        elif any(k in reason for k in ['算力', 'ai', '数据中心', '液冷', 'gpu']):
            key = 'AI算力'
        elif any(k in reason for k in ['电力', '绿电', '火电', '光伏', '风光']):
            key = '电力/新能源'
        elif any(k in reason for k in ['地产', '房地产', '物业', '建筑', '建材']):
            key = '房地产/基建'
        elif any(k in reason for k in ['机器人', '自动化', '智能制造']):
            key = '机器人'
        elif any(k in reason for k in ['汽车', '新能源车', '无人驾驶']):
            key = '汽车产业链'
        elif any(k in reason for k in ['军工', '航天', '商业航天']):
            key = '军工/航天'
        elif any(k in reason for k in ['黄金', '有色', '铜']):
            key = '有色金属'
        elif any(k in reason for k in ['低空', '飞行']):
            key = '低空经济'
        elif any(k in reason for k in ['医药', '医疗', '创新药', '宠物']):
            key = '医药/消费'
        elif any(k in reason for k in ['消费', '家居', '家电', '食品']):
            key = '消费'
        elif any(k in reason for k in ['涨价', '通胀', '化工']):
            key = '涨价/化工'
        elif any(k in reason for k in ['摘帽', '借壳']):
            key = '重组'
        else:
            key = '其他'
        
        sectors[key]['stocks'].append(s)
        sectors[key]['max_board'] = max(sectors[key]['max_board'], s['board_count'])
        sectors[key]['count'] += 1
    
    return sectors


def is_yizi_from_realtime(code, quotes):
    """用实时行情判断是否一字板"""
    q = quotes.get(code)
    if not q:
        return False, 0
    prev = q.get('prev_close', 1)
    open_p = q.get('open', prev)
    open_pct = (open_p - prev) / prev * 100 if prev > 0 else 0
    amp = q.get('amplitude', 100)
    return open_pct >= 9.5 and amp < 1.5, round(open_pct, 2)


def scan_highest_board(quotes=None):
    """
    核心扫描函数：只买主线最高板
    返回信号列表
    
    ⚠️ 使用前确保tetegu_cache有今天数据：
       python scripts/tetegu_collector.py --date $(date +%Y-%m-%d)
    """
    today = date.today().isoformat()
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    
    # 1. 获取今日涨停数据
    limit_stocks = get_today_limit_stocks()
    if not limit_stocks:
        print("  ❌ 今日无涨停数据（tetegu_cache可能未更新）")
        print("     ⚡ 修复: python3 scripts/tetegu_collector.py --date $(date +%Y-%m-%d)")
        return []
    
    # 2. 算最高板
    max_board = max(s['board_count'] for s in limit_stocks)
    tops = [s for s in limit_stocks if s['board_count'] == max_board]
    is_unique = len(tops) == 1
    
    # 3. 板块分类
    sectors = classify_boom_sectors(limit_stocks)
    
    # 4. 股票→板块映射
    stock_to_sector = {}
    for sector_name, info in sectors.items():
        for s in info['stocks']:
            stock_to_sector[s['code']] = sector_name
    
    # 5. 获取实时行情（如果没有传入）
    if quotes is None:
        all_codes = [s['code'] for s in limit_stocks]
        quotes = fetch_quotes(all_codes) if all_codes else {}
    
    # 6. 评估每个最高板标的是否值得买入
    signals = []
    
    for top in tops:
        code = top['code']
        name = top['name']
        board = top.get('board_count', 1)  # ⚠️ 字段名是board_count不是board
        reason = top['reason']
        
        q = quotes.get(code, {})
        chg = q.get('change_pct', 0)
        vol_ratio = q.get('vol_ratio', 0)
        amplitude = q.get('amplitude', 0)
        is_yizi_real, open_pct = is_yizi_from_realtime(code, quotes)
        
        # 所在板块信息
        sector_name = stock_to_sector.get(code, '其他')
        sector_boom = sectors.get(sector_name, {}).get('count', 0)
        total_zt = len(limit_stocks)
        
        # ===== 信号评估 =====
        score = 0
        confidence = 'LOW'
        expected_t1 = 0
        
        # 基础分：板数
        if board >= 10:
            score += 30
            expected_t1 = max(expected_t1, 4.5)
        elif board >= 5:
            score += 25
            expected_t1 = max(expected_t1, 3.72)
        elif board >= 3:
            score += 20
            expected_t1 = max(expected_t1, 3.50)
        elif board >= 2:
            score += 10
            expected_t1 = max(expected_t1, 2.50)
        
        # 唯一性加分
        if is_unique:
            score += 10
        
        # 主线加分（板块涨停≥3只）
        if sector_boom >= 3:
            score += 15
            expected_t1 = max(expected_t1, 1.5)
        
        # 市场活跃加分（总涨停≥5只）
        if total_zt >= 5:
            score += 5
        if total_zt >= 10:
            score += 5
        
        # 实时一字板加分
        if is_yizi_real:
            score += 10
            expected_t1 = max(expected_t1, 5.62)
        
        # 缩量加分（隔夜溢价在龙头上也有效）
        if 0 < vol_ratio < 0.7:
            score += 10
            expected_t1 = max(expected_t1, 5.62)
        
        # ⛔ 反向过滤
        if vol_ratio >= 10:
            score -= 30
        if is_yizi_real and amplitude >= 5:
            score -= 20
        if board >= 4 and vol_ratio >= 3:
            score -= 15
        if board >= 10 and vol_ratio >= 5:
            score -= 20  # 妖股天量见顶
        
        score = max(0, min(100, score))
        
        if score >= 60:
            confidence = '🔥HIGH'
        elif score >= 40:
            confidence = '✅MID'
        else:
            confidence = '⚪LOW'
        
        signals.append({
            'code': code, 'name': name,
            'board': board, 'is_unique': is_unique,
            'total_zt': total_zt,
            'sector': sector_name,
            'sector_boom': sector_boom,
            'reason': reason,
            'change_pct': chg,
            'vol_ratio': vol_ratio,
            'amplitude': amplitude,
            'is_yizi': is_yizi_real,
            'open_pct': open_pct,
            'score': score,
            'confidence': confidence,
            'expected_t1': round(expected_t1, 2),
        })
    
    # 排序
    signals.sort(key=lambda x: (-x['score'], -x['board'], -x['expected_t1']))
    
    return signals


def format_report(signals):
    """格式化微信推送报告"""
    lines = []
    today = date.today().isoformat()
    now = datetime.now()
    
    lines.append(f"🏆 只买主线最高板")
    lines.append(f"{'─'*45}")
    lines.append(f"📅 {today} {now.strftime('%H:%M')}")
    lines.append("")
    
    if not signals:
        lines.append("📭 今日无最高板信号")
        lines.append("")
        lines.append("建议：切换至隔夜溢价策略")
        return '\n'.join(lines)
    
    top = signals[0]
    max_board = max(s['board'] for s in signals)
    peaks = [s for s in signals if s['board'] == max_board]
    
    lines.append(f"🔥 市场最高板: {max_board}板 ({len(peaks)}只)")
    
    for i, s in enumerate(signals[:5], 1):
        flag = ''
        if s['is_unique'] and i == 1:
            flag = ' 👑总龙头'
        elif i == 1:
            flag = ' ⚔️龙头争夺'
        
        conf_icon = {'🔥HIGH': '🔥', '✅MID': '✅', '⚪LOW': '⚪'}.get(s['confidence'], '')
        
        lines.append(f"")
        lines.append(f"  {i}. {s['name']}({s['code']}) {s['board']}板{flag}")
        lines.append(f"     {conf_icon} 评分{s['score']} | 预期T+1: +{s['expected_t1']}%")
        lines.append(f"     板块: {s['sector']}({s['sector_boom']}涨停) | 涨停:{s['reason'][:30]}")
        lines.append(f"     实时: {s['change_pct']:+.1f}% | 量比{s['vol_ratio']:.2f}")
        if s['is_yizi']:
            lines.append(f"     一字板(开{s['open_pct']:+.1f}%) {'✅' if s['vol_ratio'] < 0.7 else '⚠️分歧'}")
    
    lines.append("")
    lines.append(f"{'─'*45}")
    lines.append(f"📋 操作建议:")
    
    high_signals = [s for s in signals if s['confidence'] == '🔥HIGH']
    mid_signals = [s for s in signals if s['confidence'] == '✅MID']
    
    if high_signals:
        lines.append(f"  🔥 立即关注: {', '.join(s['name'] for s in high_signals[:3])}")
    if mid_signals:
        lines.append(f"  ✅ 可关注: {', '.join(s['name'] for s in mid_signals[:3])}")
    
    lines.append("")
    lines.append("⚡ 龙头买卖铁律:")
    lines.append(f"  买入: T日排板 | 卖出: T+1竞价")
    lines.append(f"  ⛔ 低于3板不要追")
    lines.append(f"  ⛔ 非唯一最高板谨慎")
    lines.append(f"  ⛔ 涨停<5只时切换隔夜溢价")
    
    # 保存到文件
    try:
        os.makedirs(V2BOARD, exist_ok=True)
        with open(os.path.join(V2BOARD, 'highest_board_signal.json'), 'w') as f:
            json.dump({
                'timestamp': now.isoformat(),
                'signals': signals[:10],
                'max_board': max_board,
                'total_zt': len([s for s in signals if True]),
            }, f, ensure_ascii=False, indent=2)
    except:
        pass
    
    return '\n'.join(lines)


def main():
    print("🏆 最高板扫描引擎 v1.0")
    print(f"{'─'*45}")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    signals = scan_highest_board()
    
    if not signals:
        print("📭 无最高板信号")
        return
    
    for s in signals[:5]:
        print(f"  {s['board']}板 | {s['name']}({s['code']}) | 评分{s['score']} | {s['confidence']} | T+1预期+{s['expected_t1']}%")
        if s['reason']:
            print(f"     板块: {s['sector']}({s['sector_boom']}涨停) | {s['reason'][:50]}")
    
    # 输出报告
    report = format_report(signals)
    print()
    print(report)


if __name__ == '__main__':
    main()
