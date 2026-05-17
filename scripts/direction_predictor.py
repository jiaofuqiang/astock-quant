#!/usr/bin/env python3
"""
🔮 A股隔日方向实战引擎 v2.0

⚠️ 核心规则：T+1方向预测，只能用T日盘中及T日前的数据。
   ❌ 绝不使用T+1日的开盘价/行情/任何未来数据
   ✅ 只能用T日盘中（涨幅/量比/封单/资金流向等）
   ✅ T日之前的历史数据（弹性系数/历史龙虎榜/历史K线等）

三视角隔日预测框架：
  场景A: T日上涨 → T+1能否持续？（上涨持续判断）
  场景B: T日下跌 → T+1能否反转？（下跌反转判断）  
  场景C: T日震荡 → T+1方向选择？（震荡一致判断）

数据源（均为T日盘中或T日前的历史数据）：
  - 腾讯行情（T日涨幅/量比/振幅/换手）✅ T日盘中
  - 东方财富封单（T日封单额/封板质量）✅ T日盘中
  - 龙虎榜（游资/机构席位T日净买卖）✅ T日盘中（15:30后出）
  - 散户情绪（零售反指T日值）✅ T日盘中
  - 弹性系数（历史T+1/T+5收益统计）✅ T日前的历史数据
  - 前N日K线走势（涨跌连续性/回调幅度）✅ T日前的历史数据
  - 同花顺涨停原因（题材标签）✅ T日盘中

输出：
  - data/direction_predictions.json → 面板「明日推演」模块
  - 微信推送：三视角汇聚、最强信号TOP

用法：
  python3 scripts/direction_predictor.py              # 收盘后运行
  python3 scripts/direction_predictor.py --pull-data  # 先采集再预测
  python3 scripts/direction_predictor.py --push       # 推送微信
"""
import os, sys, json, re, subprocess
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.path.expanduser("~/astock")
DATA_DIR = os.path.join(BASE, "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "direction_predictions.json")

# 核心标的（按板块分组）
SECTOR_STOCKS = {
    '存储芯片': ['603986','603019','002049','603893','600584','603160'],
    'GPU/AI芯片': ['603005','600171','002185','000977','601138'],
    '光通信/光模块': ['002281','600498','000063','002916'],
    '人形机器人': ['002472','002896','601689','603662','603278','002031'],
    '算力/AI应用': ['002929','603636','601991','603533','002555'],
    '低空经济': ['002085','600580','300177','000099','600391'],
    '电池/新能源': ['002074','300014','002460','002709','600884','002812'],
}

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr)


# ============================================================
# T日数据加载（只能用T日盘中及之前的数据）
# ============================================================

def load_t_quotes(codes):
    """加载T日盘中实时行情 — 这是T日的实时数据，可用于预测T+1"""
    quotes = {}
    batch_size = 25
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        url = "http://qt.gtimg.cn/q=" + ",".join(
            f"sh{c}" if c.startswith(('6','9')) else f"sz{c}" for c in batch
        )
        try:
            r = subprocess.run(['curl','-s','--max-time','5',url], capture_output=True, timeout=8)
            text = r.stdout.decode('gbk', errors='replace')
            for line in text.split(';'):
                parts = line.split('~')
                if len(parts) < 10: continue
                code_match = re.search(r'(?:sh|sz|bj)(\d{6})', parts[0] if len(parts)>0 else '')
                if not code_match: continue
                code = code_match.group(1)
                try:
                    chg = float(parts[9]) if len(parts)>9 and parts[9] else 0
                    quotes[code] = {
                        'name': parts[1].strip('"') if len(parts)>1 else '',
                        'close': float(parts[2]) if parts[2] else 0,          # 昨收
                        'current': float(parts[3]) if parts[3] else 0,         # 现价
                        'high': float(parts[4]) if parts[4] else 0,            # 最高
                        'low': float(parts[5]) if parts[5] else 0,             # 最低
                        'open': float(parts[6]) if len(parts)>6 and parts[6] else 0,
                        'vol_ratio': float(parts[7]) if len(parts)>7 and parts[7] else 1.0,
                        'change_pct': chg,
                        'turnover_rate': parts[10] if len(parts)>10 else '',
                        'amplitude': float(parts[12]) if len(parts)>12 and parts[12] else 0,
                        # T日盘中状态
                        'is_limit_up': chg >= 9.5,      # 是否涨停
                        'is_limit_down': chg <= -9.5,   # 是否跌停
                        'is_up': chg > 0,
                        'is_down': chg < 0,
                    }
                except: continue
        except: continue
    return quotes


def load_t_fengdan():
    """加载T日封单数据 — T日盘中实时数据"""
    try:
        r = subprocess.run(
            ['curl','-s','--max-time','5',
             'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=50&po=1&np=1&fltt=2&invt=2'
             '&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048'
             '&fields=f12,f14,f3,f170,f171,f172,f173'],
            capture_output=True, timeout=8
        )
        data = json.loads(r.stdout)
        items = data.get('data',{}).get('diff',[])
        fengdan = {}
        for item in items:
            code = str(item.get('f12',''))
            chg = item.get('f3',0)
            if chg >= 9.5:
                fengdan[code] = {
                    'fengdan': item.get('f170', 0),
                    'fengdan_ratio': item.get('f171', 0),
                    'fengdan_amount': item.get('f172', 0),
                }
        return fengdan
    except:
        return {}


def load_t_lhb(trade_date):
    """加载T日龙虎榜数据 — T日收盘后15:30出，可用于预测T+1"""
    lhb_file = os.path.join(DATA_DIR, 'lhb_signal.json')
    if os.path.exists(lhb_file):
        try:
            with open(lhb_file) as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get('date') == trade_date:
                    return data
        except:
            pass
    return None


def load_t_retail():
    """加载T日散户情绪 — T日盘中实时反指"""
    try:
        r = subprocess.run(
            ['python3', f'{BASE}/scripts/retail_v3.py', '--json'],
            capture_output=True, text=True, timeout=30, cwd=BASE
        )
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except:
        pass
    return None


def load_pre_t_data():
    """
    加载T日前的历史数据（这些数据在T日预测T+1之前就已经存在）
    
    数据包括：
    1. elastic_scores.json — 历史弹性系数（涨停后T+1/T+5收益统计）
    2. kline_cache.db — 历史K线（前N日涨跌幅、均线位置等）
    """
    data = {}
    
    # 1. 弹性系数（历史统计数据，不是未来数据）
    ef = os.path.join(DATA_DIR, 'elastic_scores.json')
    if os.path.exists(ef):
        with open(ef) as f:
            data['elastic'] = json.load(f)
    
    # 2. 从kline_cache读前N日K线趋势
    try:
        import sqlite3
        db = os.path.join(DATA_DIR, 'kline_cache.db')
        if os.path.exists(db):
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            data['kline_cache'] = conn
    except:
        pass
    
    return data


def load_t_ban_reasons():
    """加载T日涨停原因（用于标注）"""
    rf = os.path.join(DATA_DIR, 'jiuyuan_reasons.json')
    if os.path.exists(rf):
        with open(rf) as f:
            return json.load(f)
    return {}


# ============================================================
# 三视角隔日预测核心逻辑
# ============================================================

def predict_t1(code, t_quote, t_fengdan, t_lhb, t_retail, pre_t_data, t_tags):
    """
    预测T+1方向 — 仅用T日盘中及T日前数据
    
    三视角评分逻辑：
    
    游资视角（35%）— 情绪+封板+接力
      上涨持续：封板质量好+龙虎榜游资接力+板块梯队完整
      下跌反转：尾盘翘板+游资跌停抄底+恐慌情绪释放
      震荡一致：游资无明显动作/炸板率低/方向不明
    
    量化视角（35%）— 因子+量价+反指
      上涨持续：量价齐升+动量强+散户未过热
      下跌反转：缩量下跌+散户恐慌+均值回归条件
      震荡一致：量价平稳+波动率缩窄
    
    机构视角（30%）— 趋势+基本面+龙虎榜
      上涨持续：机构龙虎榜净买+产业趋势向上
      下跌反转：机构左侧建仓+基本面未变
      震荡一致：机构无明显买卖等催化
    """
    chg = t_quote.get('change_pct', 0)
    vr = t_quote.get('vol_ratio', 1.0)
    amp = t_quote.get('amplitude', 0)
    name = t_quote.get('name', '')
    
    is_limit_up = chg >= 9.5
    is_limit_down = chg <= -9.5
    fd = t_fengdan.get(code, {})
    has_fengdan = fd.get('fengdan', 0) > 0
    
    # ====== 游资视角 ======
    y_up, y_down = 0.0, 0.0
    y_reason = []
    
    # T日场景A：涨停 → T+1能否持续
    if is_limit_up:
        if has_fengdan:
            y_up += 0.5 if fd.get('fengdan', 0) > 1000 else 0.3
            y_reason.append(f"封单{fd.get('fengdan',0):.0f}万")
        else:
            y_up -= 0.2  # 涨停但无封单=炸板隐患
            y_reason.append("无封单⚠️")
        
        # 封单/流通市值比 > 2% → 强封板
        fd_ratio = fd.get('fengdan_ratio', 0)
        if fd_ratio > 2:
            y_up += 0.2
            y_reason.append(f"封成比{fd_ratio:.1f}%")
    
    # T日场景B：大跌 → T+1能否反转
    elif chg <= -7:
        if vr < 0.7:
            y_down += 0.3  # 缩量大跌=恐慌出清
            y_reason.append(f"缩量大跌(量比{vr:.1f})")
        elif vr > 1.5:
            y_down -= 0.2  # 放量大跌=抛压未释放
            y_reason.append("放量大跌⚠️")
    
    # T日场景C：震荡
    elif abs(chg) < 3 and vr < 1.2:
        pass  # 游资一般不参与震荡
    
    # 连板接力判断（T日涨停+T-1日也涨停）
    if is_limit_up:
        y_up += 0.1  # 涨停本身加分
    
    # ====== 量化视角 ======
    q_up, q_down = 0.0, 0.0
    q_reason = []
    
    # 量价关系
    if chg > 3 and vr > 1.2:
        q_up += 0.35
        q_reason.append(f"量价齐升{vr:.1f}x")
    elif chg > 3 and vr <= 0.8:
        q_up -= 0.25  # 缩量上涨=动能不足
        q_reason.append("缩量上涨⚠️")
    elif chg < -3 and vr < 0.7:
        q_down += 0.3  # 缩量下跌=空头衰竭
        q_reason.append(f"缩量下跌{vr:.1f}x")
    elif chg < -3 and vr > 1.5:
        q_down -= 0.2  # 放量下跌=还有下杀
        q_reason.append("放量下跌⚠️")
    
    # 振幅
    if amp > 10 and chg > 0:
        q_up -= 0.1  # 振幅太大=分歧大
    elif amp > 10 and chg < 0:
        q_down -= 0.1
    
    # 弹性系数（历史统计数据，不是未来数据）
    elastic = pre_t_data.get('elastic', {})
    ei = elastic.get(code, {}) if isinstance(elastic, dict) else {}
    t1_avg = ei.get('t1_avg_return', 0) if isinstance(ei, dict) else 0
    win_rate = ei.get('win_rate', 0) if isinstance(ei, dict) else 0
    
    if t1_avg > 2:
        q_up += 0.15
        q_reason.append(f"弹性+{t1_avg:.1f}%")
    elif t1_avg < -1:
        q_down += 0.15
    
    # 散户情绪反指（T日实时）
    if t_retail:
        ri = t_retail.get(code, t_retail.get('market_avg', 50)) if isinstance(t_retail, dict) else 50
        if chg > 3 and ri > 65:
            q_up -= 0.3  # 大涨+散户过热=明天要跌
            q_reason.append("散户过热⚠️")
        elif chg < -3 and ri < 35:
            q_down += 0.2  # 大跌+散户恐慌=明天反弹
            q_reason.append("散户恐慌✅")
    
    # ====== 机构视角 ======
    j_up, j_down = 0.0, 0.0
    j_reason = []
    
    # 龙虎榜机构行为（T日数据）
    if t_lhb:
        stocks = t_lhb.get('stocks', []) if isinstance(t_lhb, dict) else []
        for s in stocks:
            sc = s.get('code','').replace('SH','').replace('SZ','').replace('sh','').replace('sz','')
            if sc == code:
                jg_net = s.get('jigou_net', 0) or 0
                yz_net = s.get('youzi_net', 0) or 0
                
                # 机构净买>1000万=看好中长线
                if jg_net > 1000:
                    j_up += 0.4
                    j_reason.append(f"机构净买{jg_net:.0f}万")
                elif jg_net < -1000:
                    j_up -= 0.3
                    j_reason.append(f"机构净卖{abs(jg_net):.0f}万⚠️")
                
                # 游资接力（游资净买+机构不卖）
                if yz_net > 1000 and jg_net > -500:
                    j_up += 0.15
                    j_reason.append("游资接力")
                    # 注意：这里游资和机构的分数会叠加
                    y_up += 0.15  # 游资视角再加分
                    y_reason.append("龙虎榜游资净买")
                break
    
    # 历史趋势（T日前的数据）
    if t1_avg > 5:
        j_up += 0.15
        j_reason.append(f"历史T+1强{t1_avg:.1f}%")
    
    # ====== 三视角加权汇总 ======
    w_y, w_q, w_j = 0.35, 0.35, 0.30
    total_up = y_up * w_y + q_up * w_q + j_up * w_j
    total_down = y_down * w_y + q_down * w_q + j_down * w_j
    net = total_up - total_down
    
    # 判断方向
    if net > 0.15 and total_up > 0.25:
        direction = 'UP_CONTINUE'
        confidence = min(net + 0.2, 0.95)
    elif net < -0.15 and total_down > 0.25:
        direction = 'DOWN_REVERSAL'
        confidence = min(abs(net) + 0.2, 0.95)
    elif abs(net) <= 0.15:
        direction = 'SIDEWAYS'
        confidence = 0.5
    else:
        direction = 'UNCLEAR'
        confidence = 0.3
    
    # 综合原因
    all_reasons = y_reason + q_reason + j_reason
    reason = ' | '.join(all_reasons[:3]) if all_reasons else ''
    
    # 涨停原因标签
    tags = ''
    br = t_tags.get(f'sh{code}', t_tags.get(f'sz{code}', {}))
    if br:
        tags = br.get('tags', '')
    
    return {
        'code': code,
        'name': name,
        'direction': direction,
        'confidence': round(confidence, 2),
        'change_pct': round(chg, 2),
        'vol_ratio': round(vr, 2),
        'youzi_up': round(y_up, 2),
        'youzi_down': round(y_down, 2),
        'quant_up': round(q_up, 2),
        'quant_down': round(q_down, 2),
        'jigou_up': round(j_up, 2),
        'jigou_down': round(j_down, 2),
        'net_score': round(net, 2),
        'reason': reason,
        'tags': tags,
    }


# ============================================================
# 板块聚合
# ============================================================

def aggregate_sectors(predictions):
    """按板块聚合方向"""
    sectors = {}
    for sname, codes in SECTOR_STOCKS.items():
        sp = [p for p in predictions if p['code'] in codes]
        if not sp:
            continue
        up = sum(1 for p in sp if p['direction'] == 'UP_CONTINUE')
        down = sum(1 for p in sp if p['direction'] == 'DOWN_REVERSAL')
        total = len(sp)
        up_ratio = up / total
        
        if up_ratio > 0.4:
            sd = 'UP_CONTINUE'
        elif down / total > 0.4:
            sd = 'DOWN_REVERSAL'
        else:
            sd = 'SIDEWAYS'
        
        top = sorted(sp, key=lambda x: -abs(x['net_score']))[:3]
        sectors[sname] = {
            'direction': sd,
            'total': total,
            'up_count': up,
            'down_count': down,
            'top_picks': [{'code': p['code'], 'name': p['name'],
                          'direction': p['direction'], 'confidence': p['confidence'],
                          'reason': p['reason'], 'tags': p['tags']} for p in top],
        }
    return sectors


# ============================================================
# 主流程
# ============================================================

def run_prediction(trade_date=None):
    """运行完整T+1预测"""
    if trade_date is None:
        trade_date = datetime.now().strftime('%Y-%m-%d')
    
    log(f"🔮 隔日方向预测 v2.0 | {trade_date}")
    log(f"  规则: 仅用T日盘中+T日前历史数据, 预测T+1方向")
    
    # 全部股票
    all_codes = list(set(c for codes in SECTOR_STOCKS.values() for c in codes))
    
    # 加载T日数据（允许用于预测T+1）
    t_quotes = load_t_quotes(all_codes)
    log(f"  ✅ T日行情: {len(t_quotes)}只")
    
    t_fengdan = load_t_fengdan()
    log(f"  ✅ T日封单: {len(t_fengdan)}只涨停")
    
    t_lhb = load_t_lhb(trade_date)
    log(f"  ✅ T日龙虎榜: {'有' if t_lhb else '无(收盘后才有)'}")
    
    t_retail = load_t_retail()
    log(f"  ✅ T日散户情绪: {'有' if t_retail else '无'}")
    
    # 加载T日前的历史数据
    pre_t = load_pre_t_data()
    log(f"  ✅ 历史数据: 弹性系数={'有' if pre_t.get('elastic') else '无'} | K线缓存={'有' if pre_t.get('kline_cache') else '无'}")
    
    t_tags = load_t_ban_reasons()
    
    # 逐只预测
    predictions = []
    for code in all_codes:
        tq = t_quotes.get(code)
        if not tq:
            continue
        pred = predict_t1(code, tq, t_fengdan, t_lhb, t_retail, pre_t, t_tags)
        predictions.append(pred)
    
    # 板块聚合
    sectors = aggregate_sectors(predictions)
    
    # 最强信号TOP10（按置信度排序）
    top_signals = sorted(predictions, key=lambda x: -abs(x['net_score']))[:10]
    
    # 保存
    output = {
        'date': trade_date,
        'timestamp': datetime.now().isoformat(),
        'version': 'v2.0',
        'disclaimer': '仅用T日盘中+T日前历史数据预测T+1方向',
        'total': len(predictions),
        'up_continue': sum(1 for p in predictions if p['direction'] == 'UP_CONTINUE'),
        'down_reversal': sum(1 for p in predictions if p['direction'] == 'DOWN_REVERSAL'),
        'sideways': sum(1 for p in predictions if p['direction'] == 'SIDEWAYS'),
        'unclear': sum(1 for p in predictions if p['direction'] == 'UNCLEAR'),
        'sectors': sectors,
        'top_signals': top_signals,
        'all_predictions': predictions,
    }
    
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    log(f"✅ 预测完成: 看涨持续{output['up_continue']}只 | 看跌反转{output['down_reversal']}只 | 震荡{output['sideways']}只")
    return output


def format_push(data):
    """生成微信推送"""
    lines = []
    lines.append(f"🔮 **明日推演 | {data['date']}**")
    lines.append(f"核心票{data['total']}只 | 🟢看涨{data['up_continue']} 🔴看跌{data['down_reversal']} ⚪震荡{data['sideways']}")
    lines.append("")
    
    # 板块方向
    lines.append("**📂 板块方向**")
    for sname, sd in data.get('sectors', {}).items():
        icon = {'UP_CONTINUE':'🟢','DOWN_REVERSAL':'🔴','SIDEWAYS':'⚪'}.get(sd.get('direction',''), '⚪')
        lines.append(f"  {icon} {sname}: {sd['up_count']}/{sd['total']}只看涨")
    lines.append("")
    
    # 最强信号
    sigs = data.get('top_signals', [])
    up_sigs = [s for s in sigs if s['direction'] == 'UP_CONTINUE'][:5]
    if up_sigs:
        lines.append("**🟢 看涨持续**")
        for s in up_sigs:
            tag = f" [{s['tags'][:20]}]" if s.get('tags') else ""
            r = f" → {s['reason'][:30]}" if s.get('reason') else ""
            lines.append(f"  {s['name']}({s['code'][-6:]}) ⭐{s['confidence']}{tag}{r}")
        lines.append("")
    
    dn_sigs = [s for s in sigs if s['direction'] == 'DOWN_REVERSAL'][:3]
    if dn_sigs:
        lines.append("**🔴 看跌反转**")
        for s in dn_sigs:
            r = f" → {s.get('reason','')[:30]}" if s.get('reason') else ""
            lines.append(f"  {s['name']}({s['code'][-6:]}) ⭐{s['confidence']}{r}")
        lines.append("")
    
    lines.append("⚪ 规则：仅用T日盘中+T日前历史数据预测T+1方向")
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='🔮 隔日方向预测 v2.0')
    parser.add_argument('--push', action='store_true', help='推送微信')
    parser.add_argument('--pull-data', action='store_true', help='先采集再预测')
    args = parser.parse_args()
    
    trade_date = datetime.now().strftime('%Y-%m-%d')
    
    if args.pull_data:
        log("📡 采集最新数据...")
        subprocess.run(['python3', f'{BASE}/scripts/dashboard_update.py'],
                       capture_output=True, timeout=120, cwd=BASE)
    
    data = run_prediction(trade_date)
    
    if data:
        print(f"\n🔮 明日推演 v2.0 | {data['date']}")
        print(f"规则: {data['disclaimer']}")
        print(f"核心票{data['total']}只 → 看涨{data['up_continue']} / 看跌{data['down_reversal']} / 震荡{data['sideways']}")
        
        for sname, sd in data.get('sectors', {}).items():
            icon = {'UP_CONTINUE':'🟢','DOWN_REVERSAL':'🔴','SIDEWAYS':'⚪'}.get(sd.get('direction',''), '⚪')
            print(f"  {icon} {sname}: {sd['up_count']}/{sd['total']}只看涨")
            for p in sd.get('top_picks', [])[:2]:
                print(f"    {p['name']}({p['code'][-6:]}) {p['direction']} ⭐{p['confidence']}")
        
        print(f"\n🏆 最强信号TOP5:")
        for s in data.get('top_signals', [])[:5]:
            print(f"  {s['name']}({s['code'][-6:]}) {s['direction']} ⭐{s['confidence']}")
            if s.get('reason'):
                print(f"    逻辑: {s['reason'][:60]}")
    
    if args.push:
        push_msg = format_push(data)
        print(f"\n{'='*40}\n{push_msg}")


if __name__ == '__main__':
    main()
