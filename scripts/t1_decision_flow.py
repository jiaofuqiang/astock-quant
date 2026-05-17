#!/usr/bin/env python3
"""
📐 A股T+1分层决策系统 v1.0

================================================================
⚠️ 核心数据边界 ⚠️
================================================================

每个数据源只能用于它出现的时间点之后：

  可用时间        数据源                     可用于
  ─────────────────────────────────────────────────
  T日盘中随时     腾讯实时行情                 T日买入决策 ✅
  T日盘中随时     封单数据                     T日买入决策 ✅
  T日盘中随时     散户情绪反指                 T日买入决策 ✅
  ─────────────────────────────────────────────────
  T日15:00后      当日涨跌幅/量比/振幅          T日买入决策 ✅（收盘前）
  T日15:30后      龙虎榜（游资/机构净买）       T+1卖出决策 ✅（已有持仓的人）
                                                次日的选股参考 ✅（没买的人）
  T日收盘后       晚间公告                      T+1卖出决策 ✅
  T日收盘后       美股夜盘/期货夜盘             T+1策略调整 ✅
  ─────────────────────────────────────────────────
  T+1日09:15      竞价数据                      T+1卖出决策 ✅
  T+1日09:30起    T+1实时行情                   T+1卖出决策 ✅

================================================================
三层决策节点
================================================================

Node1（T日14:50~15:00）—— 基于Layer1的买入决策
  能用的数据：腾讯行情/封单/量比/涨幅/散户情绪
  不能用的数据：龙虎榜（还没出）、公告（看运气）

Node2（T日15:30~T+1 09:15）—— 基于Layer2的持仓评估
  新增可用数据：龙虎榜、晚间公告、美股夜盘
  用途：已有持仓的人决定次日是持有、竞价卖、还是加仓

Node3（T+1 09:15~全程）—— 基于Layer3的卖出执行
  新增可用数据：竞价数据、T+1实时行情
  用途：执行卖出

================================================================
三层数据 × 三个决策节点 × 三种盈利模式
================================================================

数据时间线：
  Layer1: T日盘中  → 买入决策（封板质量/恐慌确认/趋势信号）
  Layer2: T日盘后  → 消息面评估（龙虎榜/公告/美股/期货）
  Layer3: T+1盘中  → 持有/卖出决策（竞价/量价/情绪）

三个决策节点：
  Node1: T日14:50~15:00 → 确认买入
  Node2: T+1日09:15~09:25 → 竞价检查（根据隔夜消息调整策略）
  Node3: T+1日09:30~全程 → 盘中卖出监控

================================================================
完整决策流程
================================================================

模式A：隔夜溢价
  ┌─────────────────────────────────────────────────────────────┐
  │ T日盘中: 封板质量评分 → 决定是否打板                      │
  │ T日盘后: 龙虎榜游资接力? 晚间公告利好/利空?               │
  │           → 决定T+1竞价策略（加仓/持有/竞价直接卖）       │
  │ T+1竞价: 高开>3%→竞价卖 / 高开1~3%→看量 / 平开→再看    │
  │ T+1盘中: 开盘5分钟方向 → 确认卖出时机                      │
  └─────────────────────────────────────────────────────────────┘

模式B：恐慌反转
  ┌─────────────────────────────────────────────────────────────┐
  │ T日盘中: 缩量恐慌确认 → 决定尾盘抄底                      │
  │ T日盘后: 晚间有无利空公告? 美股/期货对应板块怎么走?        │
  │           → 决定T+1是否要下调止损或提前卖出                │
  │ T+1竞价: 红开→反转确认 / 低开→反转失败风险                │
  │ T+1盘中: 反弹力度+3%止盈 / -3%止损                        │
  └─────────────────────────────────────────────────────────────┘

模式C：趋势延续
  ┌─────────────────────────────────────────────────────────────┐
  │ T日盘中: 趋势强度评分 → 决定是否买入                      │
  │ T日盘后: 机构龙虎榜锁仓? 产业有没有新催化?                 │
  │           → 决定T+1是持有还是加仓                          │
  │ T+1盘中: 量价关系 → 趋势持续还是衰竭                      │
  └─────────────────────────────────────────────────────────────┘
"""
import os, sys, json, re, subprocess
from datetime import datetime

BASE = os.path.expanduser("~/astock")
DATA_DIR = os.path.join(BASE, "data")

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr)


# ============================================================
# Layer1: T日盘中数据（买入决策用）
# ============================================================
def layer1_buy_signals():
    """T日盘中买入信号（已有profit_signals.py实现）"""
    pass

# ============================================================
# Layer2: T日盘后数据（评估隔夜影响，调整次日策略）
# ============================================================
def layer2_afternoon_assessment(trade_date):
    """
    T日15:00收盘后的消息面评估
    
    数据源：
      1. 龙虎榜（15:30出）— 游资/机构买卖方向
      2. 晚间公告 — 利好/利空
      3. 美股夜盘（A股对应板块映射）
      4. 期货夜盘（碳酸锂/铜/原油等）
      5. 同花顺涨停原因（题材持续性）
    
    输出：
      对每个持仓/关注标的的次日策略调整
    """
    log(f"📡 Layer2: T日盘后消息面评估 | {trade_date}")
    
    # 1. 龙虎榜
    lhb = None
    lf = os.path.join(DATA_DIR, 'lhb_signal.json')
    if os.path.exists(lf):
        with open(lf) as f:
            lhb = json.load(f)
    
    # 2. 同花顺涨停原因（题材归类）
    tags = {}
    tf = os.path.join(DATA_DIR, 'jiuyuan_reasons.json')
    if os.path.exists(tf):
        with open(tf) as f:
            tags = json.load(f)
    
    # 3. 弹性系数
    elastic = {}
    ef = os.path.join(DATA_DIR, 'elastic_scores.json')
    if os.path.exists(ef):
        with open(ef) as f:
            elastic = json.load(f)
    
    # 对每个核心标的生成盘后评估
    codes = ['603986','603019','600584','002472','002896','002929',
             '603636','002281','600498','603893']
    
    assessments = []
    for code in codes:
        # 龙虎榜数据
        lhb_info = None
        if lhb:
            stocks = lhb.get('stocks', []) if isinstance(lhb, dict) else []
            for s in stocks:
                sc = s.get('code','').replace('SH','').replace('SZ','').replace('sh','').replace('sz','')
                if sc == code:
                    lhb_info = {
                        'youzi_net': s.get('youzi_net', 0),
                        'jigou_net': s.get('jigou_net', 0),
                        'youzi_count': s.get('youzi_count', 0),
                    }
                    break
        
        # 涨停原因
        tag_info = tags.get(f'sh{code}', tags.get(f'sz{code}', {}))
        tag = tag_info.get('tags', '') if isinstance(tag_info, dict) else ''
        section = tag_info.get('section', '') if isinstance(tag_info, dict) else ''
        
        # 弹性
        ei = elastic.get(code, {}) if isinstance(elastic, dict) else {}
        t1 = ei.get('t1_avg_return', 0) if isinstance(ei, dict) else 0
        t5 = ei.get('t5_avg_return', 0) if isinstance(ei, dict) else 0
        
        # 综合评估
        strategy_adjust = 'normal'
        reason_parts = []
        
        if lhb_info:
            jg = lhb_info.get('jigou_net', 0) or 0
            yz = lhb_info.get('youzi_net', 0) or 0
            
            if jg > 1000:
                strategy_adjust = 'strengthen'
                reason_parts.append(f"机构净买{jg:.0f}万")
            elif jg < -1000:
                strategy_adjust = 'weaken'
                reason_parts.append(f"机构卖出{abs(jg):.0f}万")
            
            if yz > 2000:
                if strategy_adjust != 'weaken':
                    strategy_adjust = 'strengthen'
                reason_parts.append(f"游资接力{yz:.0f}万")
        
        if tag:
            reason_parts.append(f"题材:{tag[:25]}")
        
        assessments.append({
            'code': code,
            'tag': tag,
            'section': section,
            'lhb_info': lhb_info,
            't1_elastic': t1,
            't5_elastic': t5,
            'strategy_adjust': strategy_adjust,
            'reason': ' | '.join(reason_parts[:3]),
        })
    
    return assessments


# ============================================================
# Layer3: T+1盘中数据（持有/卖出决策）
# ============================================================
def layer3_intraday_sell(q, yesterday_close, mode='A'):
    """
    T+1盘中卖出信号
    
    输入：
      q: T+1实时行情
      mode: A隔夜溢价 / B恐慌反转 / C趋势延续
      yesterday_close: T日收盘价（买入成本基准）
    
    输出：
      sell_decision: HOLD / SELL / STOP_LOSS / TAKE_PROFIT
    """
    if not q:
        return None
    
    chg = q.get('change_pct', 0)
    vr = q.get('vol_ratio', 1.0)
    high = q.get('high', 0)
    current = q.get('current', 0)
    name = q.get('name', '')
    
    # 从昨收计算盈亏
    pnl = round((current - yesterday_close) / yesterday_close * 100, 2) if yesterday_close else 0
    
    result = {'name': name, 'pnl': pnl, 'chg': chg, 'vr': vr}
    
    if mode == 'A':
        # 隔夜溢价卖出逻辑
        open_change = round((q.get('open', 0) - yesterday_close) / yesterday_close * 100, 2) if yesterday_close else 0
        
        if pnl < -3:
            result['decision'] = 'STOP_LOSS'
            result['action'] = f'亏损{pnl:.1f}% > 止损线-3%，立即卖出'
        elif pnl > 5:
            result['decision'] = 'TAKE_PROFIT'
            result['action'] = f'盈利{pnl:.1f}% > 止盈线+5%，卖出锁定'
        elif open_change > 3:
            # 高开>3%，竞价就是最佳卖点
            result['decision'] = 'SELL'
            result['action'] = f'高开{open_change:.1f}%，溢价已兑现，卖出'
        elif chg < 0 and vr < 0.7:
            # 缩量下跌=没人接盘
            result['decision'] = 'WATCH'
            result['action'] = f'缩量回调{chg:.1f}%，等反抽再卖'
        elif chg > 0 and vr > 1.5:
            # 放量上涨=可能还有空间
            result['decision'] = 'HOLD'
            result['action'] = f'放量上涨+{chg:.1f}%(量比{vr:.1f}x)，持有等更高'
        else:
            result['decision'] = 'HOLD'
            result['action'] = f'小幅震荡{chg:.1f}%，持有观察'
    
    elif mode == 'B':
        # 恐慌反转卖出逻辑
        if pnl < -3:
            result['decision'] = 'STOP_LOSS'
            result['action'] = f'继续下跌{pnl:.1f}%，反转失败止损'
        elif pnl > 3:
            result['decision'] = 'TAKE_PROFIT'
            result['action'] = f'反弹+{pnl:.1f}%，到止盈目标卖出'
        elif pnl > 0:
            result['decision'] = 'HOLD'
            result['action'] = f'反弹中+{pnl:.1f}%，等+3%止盈'
        else:
            result['decision'] = 'WATCH'
            result['action'] = f'还在水下{pnl:.1f}%，等翻红'
    
    elif mode == 'C':
        # 趋势延续卖出逻辑
        if chg > 2 and 1.0 < vr < 2.5:
            result['decision'] = 'HOLD'
            result['action'] = f'放量上涨+{chg:.1f}%，趋势延续持有'
        elif chg > 0 and vr < 0.8:
            result['decision'] = 'SELL_HALF'
            result['action'] = f'缩量上涨+{chg:.1f}%，动能不足减半仓'
        elif chg < -2 and vr > 1.5:
            result['decision'] = 'SELL'
            result['action'] = f'放量下跌{chg:.1f}%，趋势反转卖出'
        elif pnl < -3:
            result['decision'] = 'STOP_LOSS'
            result['action'] = f'亏损{pnl:.1f}%止损'
        elif pnl > 8:
            result['decision'] = 'TAKE_PROFIT_HALF'
            result['action'] = f'盈利{pnl:.1f}%，减半仓锁利'
        else:
            result['decision'] = 'HOLD'
            result['action'] = f'小幅波动{chg:.1f}%，继续持有'
    
    return result


# ============================================================
# 三层联动的完整决策示例
# ============================================================
def full_decision_flow(code, trade_date):
    """
    对单只股票做三层完整决策
    
    示例用途：
      研究某只在持仓中的票：T日买入时怎么判断，
      T日收盘后怎么评估隔夜风险，T+1盘中怎么卖
    """
    # Layer1: T日盘中信号（略，用profit_signals.py的结果）
    # Layer2: T日盘后评估
    l2 = layer2_afternoon_assessment(trade_date)
    for a in l2:
        if a['code'] == code:
            print(f"  Layer2盘后: {a['reason']}")
            print(f"  策略调整: {a['strategy_adjust']}")
            if a['lhb_info']:
                li = a['lhb_info']
                print(f"  龙虎榜: 游资净{li.get('youzi_net',0):.0f}万 机构净{li.get('jigou_net',0):.0f}万")
            break
    
    # Layer3: T+1盘中决策
    prefix = 'sh' if code.startswith(('6','9')) else 'sz'
    try:
        r = subprocess.run(['curl','-s','--max-time','4',
            f'http://qt.gtimg.cn/q={prefix}{code}'], capture_output=True, timeout=6)
        text = r.stdout.decode('gbk', errors='replace')
        parts = text.split('~')
        if len(parts) >= 10:
            close = float(parts[2]) if parts[2] else 0
            current = float(parts[3]) if parts[3] else 0
            chg = float(parts[9]) if parts[9] else 0
            vr = float(parts[7]) if parts[7] else 1.0
            
            q_data = {
                'name': parts[1].strip('"'),
                'change_pct': chg, 'vol_ratio': vr,
                'close': close, 'current': current,
                'open': float(parts[6]) if parts[6] else 0,
                'high': float(parts[4]) if parts[4] else 0,
            }
            
            l3 = layer3_intraday_sell(q_data, close, 'A')
            if l3:
                print(f"  Layer3盘中: {l3['decision']} | {l3['action']}")
    except:
        pass


def main():
    trade_date = datetime.now().strftime('%Y-%m-%d')
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║     T+1分层决策系统 v1.0                                    ║
║                                                              ║
║  数据时间线：                                                ║
║     Layer1: T日盘中 → 买入决策                              ║
║     Layer2: T日盘后 → 隔夜消息评估(龙虎榜/公告/美股)        ║
║     Layer3: T+1盘中 → 持有/卖出决策                         ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # Layer2示例
    print(f"📡 Layer2: T日盘后消息面评估 | {trade_date}")
    assessments = layer2_afternoon_assessment(trade_date)
    for a in assessments[:5]:
        adj_icon = {'strengthen':'🟢','weaken':'🔴','normal':'⚪'}.get(a['strategy_adjust'],'⚪')
        print(f"  {adj_icon} {a['code']} {a['tag'][:20] or ''}: {a['reason'][:50]}")
    
    # Layer3示例
    print(f"\n📡 Layer3: T+1盘中卖出研究")
    print("  (交易日有实时数据时展示)")


if __name__ == '__main__':
    main()
