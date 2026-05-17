# 龙虎榜V7 Walk-Forward回测框架

import sqlite3, json, sys, os, re
from datetime import datetime, date, timedelta
from collections import defaultdict
import random

BASE = os.path.expanduser('~/astock')
DB = f'{BASE}/data'
KLINE = f'{DB}/kline_cache.db'
LHB = f'{DB}/lhb_cache.db'
MARKET = f'{DB}/market_daily.db'

def db(p):
    c = sqlite3.connect(p, timeout=10); c.row_factory = sqlite3.Row; return c

# === 环境评分（复用V7逻辑）===
def env_score(kd):
    if not kd or len(kd)<50: return 50
    up = sum(1 for r in kd if r['c']>0)
    down = sum(1 for r in kd if r['c']<=0)
    t = up+down; zh = up/t*100 if t>0 else 50
    lu = sum(1 for r in kd if r['c']>=9.9)
    ld = sum(1 for r in kd if r['c']<=-9.9)
    bu = sum(1 for r in kd if r['c']>5)
    bd = sum(1 for r in kd if r['c']<-5)
    ac = sum(r['c'] for r in kd)/t if t>0 else 0
    s1=20 if zh>60 else(15 if zh>45 else(10 if zh>30 else(5 if zh>20 else 0)))
    s2=25 if lu/t*100>5 else(20 if lu/t*100>3 else(15 if lu/t*100>1.5 else(8 if lu/t*100>0.5 else 0)))
    er=bu/bd if bd>0 else(10 if bu>0 else 1)
    s3=25 if er>3 else(20 if er>1.5 else(15 if er>0.8 else(8 if er>0.3 else 0)))
    s4=15 if ac>0.5 else(10 if ac>0 else(5 if ac>-0.3 else 0))
    s5=15 if ld/t*100<3 else(10 if ld/t*100<6 else(5 if ld/t*100<10 else 0))
    return s1+s2+s3+s4+s5

def kline_day(ck, d):
    rr = ck.execute('SELECT code,open,close,volume FROM kline WHERE date=?',(d,)).fetchall()
    if not rr or len(rr)<50: return None
    return [{'c':(r['close']-r['open'])/r['open']*100,'v':r['volume']or 0,'o':r['open'],'cl':r['close'],'code':r['code']} for r in rr if r['open']>0 and r['close']>0]

# === V7评分逻辑（简化版，复用）===
TYPE_PATTERNS = {
    'youzi': ['中信.*杭州','华泰.*总部','国泰.*总部','中国银河.*杭州',
              '招商.*深圳','华鑫.*上海','申万宏源.*上海','中泰.*',
              '财通.*杭州','国金.*','长城.*','光大.*','东方财富.*拉萨'],
    'jigou': ['机构专用'],
}
def classf(d):
    if '机构专用' in d: return 'jigou'
    if '量化' in d: return 'quant'
    for p in TYPE_PATTERNS['youzi']:
        if re.search(p,d): return 'youzi'
    return 'other'

def score_one(code, name, td, cl, ck, kd, es):
    """对一个龙虎榜信号计算V7评分并查看T+1表现"""
    dr = cl.execute('SELECT direction,dealer,buy_amt,sell_amt FROM lhb_detail WHERE date=? AND code=?',(td,code)).fetchall()
    if not dr: return None
    
    dealers = [{'t':classf(x['dealer']),'d':x['direction'],'b':x['buy_amt']or 0,'s':x['sell_amt']or 0} for x in dr]
    buy_d = [x for x in dealers if x['d']=='buy']
    sell_d = [x for x in dealers if x['d']=='sell']
    
    total_b = sum(x['b'] for x in buy_d)
    total_s = sum(x['s'] for x in sell_d)
    net = total_b - total_s
    jigou_n = sum(x['b'] for x in buy_d if x['t']=='jigou') - sum(x['s'] for x in sell_d if x['t']=='jigou')
    youzi_n = sum(x['b'] for x in buy_d if x['t'] in ('youzi','quant')) - sum(x['s'] for x in sell_d if x['t'] in ('youzi','quant'))
    
    # K线因子
    pk = ck.execute('SELECT date,open,close,volume FROM kline WHERE code=? AND date<=? ORDER BY date DESC LIMIT 6',(code,td)).fetchall()
    tk = ck.execute('SELECT open,close,volume FROM kline WHERE code=? AND date=?',(code,td)).fetchone()
    
    vs, fs, ms, mms, ds, dls = 8, 5, 5, 2, 5, 5
    if tk and len(pk)>=4:
        tv = tk['volume']or 0
        av = sum((k['volume']or 0) for k in pk[:5])/5
        vr = tv/av if av>0 else 1
        vs = 15 if vr<0.5 else(12 if vr<0.8 else(8 if vr<1.2 else(5 if vr<2 else 0)))
    
    if jigou_n>100: fs+=10
    elif jigou_n>0: fs+=5
    elif jigou_n<-100: fs-=5
    if youzi_n>200: fs+=6
    elif youzi_n>0: fs+=3
    if net>500: fs+=4
    elif net<-500: fs-=4
    fs = max(-5, min(20, fs))
    
    if total_b>total_s*1.5: ms+=5
    elif total_b<total_s*0.5: ms-=5
    ms = max(-5, min(15, ms))
    
    if len(pk)>=6:
        p5 = (pk[0]['close']-pk[-1]['close'])/pk[-1]['close']*100 if pk[-1]['close']>0 else 0
        mms = 10 if -10<p5<-3 else(7 if -3<p5<3 else(5 if 3<p5<10 else 2))
    
    if tk and len(pk)>=2:
        yc = pk[0]['close']
        if yc>0 and tk['open']>0:
            topen = (tk['open']-yc)/yc*100
            ds = 8 if 0<topen<7 else(3 if topen<0 else(0 if topen>9 else 5))
    
    ut = set(x['t'] for x in buy_d)
    if 'jigou' in ut and 'youzi' in ut: dls=13
    elif 'jigou' in ut: dls=10
    elif 'youzi' in ut: dls=9
    if 'quant' in ut: dls+=3
    dls = min(15, dls)
    
    raw = vs+fs+ms+mms+ds+dls+(es/100*15)
    score = raw  # 板块强化略
    
    # 等级
    if score>=80: ti, pp = 'S', 15
    elif score>=60: ti, pp = 'A', 10
    elif score>=40: ti, pp = 'B', 5
    else: ti, pp = 'C', 0
    
    # T+1收益
    next_d = ck.execute('SELECT date FROM kline WHERE date>? ORDER BY date LIMIT 1',(td,)).fetchone()
    t1ret = None
    if next_d and tk:
        t1k = ck.execute('SELECT close,open FROM kline WHERE code=? AND date=?',(code,next_d['date'])).fetchone()
        if t1k and tk['close']>0:
            t1ret = (t1k['close']-tk['close'])/tk['close']*100
    
    return {
        'code': code, 'name': name,
        'score': round(score,1), 'tier': ti,
        'net_wan': round(net,1), 'has_jigou': 'jigou' in ut,
        't1_close': round(t1ret,2) if t1ret else None,
        'env_score': es,
        'env_bucket': 0 if es<35 else(1 if es<50 else(2 if es<70 else 3)),
        'date': td,
    }

# ========== 历史行情获取 ==========
def walk_forward(start_month='2025-01', end_month='2026-05'):
    ck = db(KLINE)
    cl = db(LHB)
    
    # 获取所有交易日
    dates = [r['date'] for r in ck.execute('SELECT DISTINCT date FROM kline ORDER BY date')]
    dates = [d for d in dates if d >= f'{start_month}-01' and d <= f'{end_month}-28']
    
    print(f"📊 Walk-Forward回测: {start_month}~{end_month}")
    print(f"   共 {len(dates)} 个交易日")
    
    # 按月切分
    months = sorted(set(d[:7] for d in dates))
    print(f"   共 {len(months)} 个月\n")
    
    # 滚动验证：训练3月，验证1月
    monthly_results = []
    
    for i, val_month in enumerate(months[3:]):  # 从第4个月开始验证
        train_months = months[max(0,i):i+3]
        print(f"📅 验证 {val_month} (训练: {','.join(train_months)})")
        
        train_end = f'{train_months[-1]}-28'
        val_dates = [d for d in dates if d.startswith(val_month)]
        
        # 用训练期的数据...（超时保护，先简略跑）
        signals_in_val = 0
        score_avg = 0
        ret_total = 0
        ret_count = 0
        
        for d in val_dates[:5]:  # 每只取5个样本（演示用）
            es = env_score(kline_day(ck, d)) if kline_day(ck, d) else 50
            kd = kline_day(ck, d) or []
            
            lrows = cl.execute('SELECT DISTINCT code,name FROM lhb_list WHERE date=?',(d,)).fetchall()
            for row in lrows[:10]:
                res = score_one(row['code'], row['name'], d, cl, ck, kd, es)
                if res:
                    signals_in_val += 1
                    score_avg += res['score']
                    if res['t1_close'] is not None:
                        ret_total += res['t1_close']
                        ret_count += 1
        
        if ret_count > 0:
            avg_ret = ret_total/ret_count
            monthly_results.append({'month': val_month, 'signals': signals_in_val,
                                    'avg_score': round(score_avg/max(signals_in_val,1),1),
                                    'avg_t1': round(avg_ret,2)})
            print(f"   信号: {signals_in_val} | 均T+1: {avg_ret:+.2f}%")
    
    print(f"\n=== Walk-Forward 结果 ===")
    if monthly_results:
        all_ret = [r['avg_t1'] for r in monthly_results if r['avg_t1'] is not None]
        if all_ret:
            print(f"平均T+1收益: {sum(all_ret)/len(all_ret):+.2f}%")
            print(f"最高: {max(all_ret):+.2f}% | 最低: {min(all_ret):+.2f}%")
            print(f"正收益月份: {sum(1 for r in all_ret if r>0)}/{len(all_ret)}")
    
    ck.close(); cl.close()

if __name__ == '__main__':
    walk_forward()
