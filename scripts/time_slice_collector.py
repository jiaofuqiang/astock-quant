#!/usr/bin/env python3
"""
📸 时间片数据采集器 v1.0
========================
A股交易时段每5分钟全量扫描 → 时间片快照

自给自足：仅依赖腾讯行情API和tetegu_cache，不依赖其他外部表。

时间片结构:
  1. 全量扫描 → 涨停/涨跌/量比/高开分布
  2. 九维穿透 → 实时市场分类标签
  3. 板块结构 → 从tetegu_cache涨停原因提取板块分布
  4. 特征向量 → 12维归一化特征(用于历史匹配)
  5. 保存 → time_slice_history.db

用法:
  python3 scripts/time_slice_collector.py           # 采集当前时刻
  python3 scripts/time_slice_collector.py --watch   # 持续监控(每5分钟)
  python3 scripts/time_slice_collector.py --list    # 查看基因库
"""
import os, sys, json, sqlite3, subprocess, time
from datetime import datetime, date
from collections import defaultdict, Counter

BASE = os.path.expanduser('~/astock')
DATA = os.path.join(BASE, 'data')
HISTORY_DB = os.path.join(DATA, 'time_slice_history.db')
TETEGU_DB = os.path.join(DATA, 'tetegu_cache.db')
ALL_CODES_FILE = os.path.join(DATA, 'all_main_board.txt')

# ============ 工具函数 ============

def load_codes():
    try:
        with open(ALL_CODES_FILE) as f:
            return [l.strip() for l in f if l.strip() and not l.startswith('#')]
    except: return []

def mkt(code):
    return f'sh{code}' if code[0] in ('6','5','9') else f'sz{code}'

def sf(v, d=0):
    try: return float(v) if v and v not in ('-', '') else d
    except: return d

def round_ts(now=None):
    if now is None: now = datetime.now()
    m = (now.minute // 5) * 5
    return f"{now.hour:02d}:{m:02d}"

def pct(v, total):
    return round(v / max(total, 1) * 100, 1)

def init_db():
    schema = os.path.join(DATA, 'schemas', 'time_slice_schema.sql')
    if not os.path.exists(HISTORY_DB) and os.path.exists(schema):
        subprocess.run(['sqlite3', HISTORY_DB], stdin=open(schema), timeout=10)
        print(f"  ✅ 数据库已创建: {HISTORY_DB}")

# ============ 1. 腾讯行情采集 ============

def fetch_index():
    try:
        r = subprocess.run(['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
            'https://qt.gtimg.cn/q=sh000001,sz399001,sz399006'], capture_output=True, timeout=10)
        raw = r.stdout.decode('gbk', errors='replace')
        indices = {}
        for line in raw.strip().split('\n'):
            parts = line.split('~')
            if len(parts) < 40: continue
            code = parts[2]
            indices[code] = {'chg': sf(parts[32]), 'amount': sf(parts[37])}
        return indices
    except: return {}

def scan_market():
    """全量扫描 → 不依赖任何外部表"""
    codes = load_codes()
    if not codes: return {}
    limit_up = {}; limit_down = {}
    up_c = dn_c = 0; vol_r_list = []
    gaps = {'1~3%':0,'3~5%':0,'5~7%':0,'>7%':0}
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        try:
            r = subprocess.run(['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                f'https://qt.gtimg.cn/q={",".join(mkt(c) for c in batch)}'],
                capture_output=True, timeout=12)
            raw = r.stdout.decode('gbk', errors='replace')
            for line in raw.strip().split('\n'):
                if not line or '=' not in line: continue
                parts = line.split('~')
                if len(parts) < 50: continue
                code = parts[2].strip()
                try:
                    cur = sf(parts[3]); prev = sf(parts[4]); op = sf(parts[5])
                    chg = sf(parts[32]); vr = sf(parts[49], 0); hi = sf(parts[33])
                    if prev <= 0: continue
                    if chg > 0: up_c += 1
                    elif chg < 0: dn_c += 1
                    if vr > 0: vol_r_list.append(vr)
                    oc = (op-prev)/prev*100
                    if oc > 0:
                        if oc < 3: gaps['1~3%']+=1
                        elif oc < 5: gaps['3~5%']+=1
                        elif oc < 7: gaps['5~7%']+=1
                        else: gaps['>7%']+=1
                    if chg >= 9.5 and cur >= prev*1.09:
                        b1p = sf(parts[9]); b1v = sf(parts[10])
                        limit_up[code] = {'vol_ratio':round(vr,2), 'open_chg':round(oc,2),
                            'seal':round(b1p*b1v/10000,1), 'is_yizi':oc>=9.5}
                    if chg <= -9.5 and cur <= prev*0.91:
                        limit_down[code] = {}
                except: continue
        except: continue
        time.sleep(0.05)
    vd = {'<0.5':0,'0.5~0.7':0,'0.7~1':0,'1~3':0,'3~5':0,'>5':0}
    for vr in vol_r_list:
        if vr < 0.5: vd['<0.5']+=1
        elif vr < 0.7: vd['0.5~0.7']+=1
        elif vr < 1: vd['0.7~1']+=1
        elif vr < 3: vd['1~3']+=1
        elif vr < 5: vd['3~5']+=1
        else: vd['>5']+=1
    yi = sum(1 for s in limit_up.values() if s['is_yizi'])
    sl = sum(1 for s in limit_up.values() if not s['is_yizi'] and s['vol_ratio']<0.7)
    fl = sum(1 for s in limit_up.values() if not s['is_yizi'] and s['vol_ratio']>=0.7)
    return {'up_count':up_c, 'down_count':dn_c, 'limit_up':len(limit_up),
        'limit_down':len(limit_down), 'limit_yizi':yi, 'limit_suoliang':sl,
        'limit_fangliang':fl, 'limit_detail':limit_up, 'vol_dist':vd, 'gap_dist':gaps}

# ============ 2. 辅助数据(tetegu_cache) ============

def get_tetegu_data():
    today = str(date.today())
    r = {'max_board':0, 'total_limit':0, 'zhaban':0, 'sectors':[]}
    if not os.path.exists(TETEGU_DB): return r
    try:
        conn = sqlite3.connect(TETEGU_DB)
        c = conn.execute("SELECT MAX(board_count) FROM limit_genes WHERE date=?", (today,))
        row = c.fetchone()
        if row and row[0]: r['max_board'] = row[0]
        c = conn.execute("SELECT COUNT(*) FROM limit_genes WHERE date=?", (today,))
        row = c.fetchone()
        if row: r['total_limit'] = row[0]
        c = conn.execute("SELECT COUNT(*) FROM limit_reasons WHERE date=? AND reason LIKE '%炸板%'", (today,))
        row = c.fetchone()
        if row: r['zhaban'] = row[0]
        c = conn.execute(
            "SELECT r.reason FROM limit_reasons r WHERE r.date=? AND r.reason NOT LIKE '%炸板%' AND r.reason != '<div></div>'",
            (today,))
        sm = defaultdict(int)
        for row in c.fetchall():
            rea = row[0]
            if 'st' in rea.lower() and 'st股' in rea: sm['ST股']+=1
            elif '半导体' in rea or '芯片' in rea: sm['半导体']+=1
            elif '算力' in rea or 'ai' in rea.lower() or '光纤' in rea or '通信' in rea or '数据中心' in rea: sm['AI算力']+=1
            elif '房地产' in rea or '地产' in rea: sm['地产']+=1
            elif '医药' in rea: sm['医药']+=1
            elif '电力' in rea or '绿电' in rea or '能源' in rea: sm['电力']+=1
            elif '新能源' in rea or '锂' in rea: sm['新能源']+=1
            elif '军工' in rea: sm['军工']+=1
            elif '机器人' in rea: sm['机器人']+=1
            elif '消费' in rea or '家电' in rea: sm['消费']+=1
            elif '化工' in rea or '水泥' in rea: sm['化工']+=1
            else: sm['其他']+=1
        r['sectors'] = sorted([{'name':k,'lu':v} for k,v in sm.items()], key=lambda x:-x['lu'])
        conn.close()
    except: pass
    return r

# ============ 3. 九维穿透 ============

def l1_heat(m):
    lu=m.get('limit_up',0); zh=m.get('zh_ratio',0); ld=m.get('limit_down',0)
    if ld>=10 and lu<20: return '❄️恐慌冰点',0
    if lu>=80 and zh>=55: return '☀️狂热',4
    if lu>=60 and zh>=60: return '☀️狂热',4
    if lu>=40 or (lu>=30 and m.get('max_board',0)>=5): return '🌤活跃',3
    if lu>=30 and zh>=50: return '🌤活跃',3
    if lu>=15 or m.get('max_board',0)>=3: return '☁️平淡',2
    if lu>=10 and zh>=35: return '☁️平淡',2
    return '❄️冰点',1

def l2_style(m):
    fl=m.get('fangliang_pct',0); sl=m.get('suoliang_pct',0); x=m
    sc={}
    sc['放量游资']=(2 if fl>=65 else 0)+(1 if sl<=8 else 0)
    sc['缩量惜售']=(2 if sl>=12 else 0)+(1 if x.get('yizi_pct',0)>=10 else 0)
    sc['机构趋势']=(1 if x.get('zh_ratio',50)>=55 else 0)+(1 if fl>=60 else 0)
    sc['散户博弈']=(2 if fl>=85 else 0)
    return max(sc,key=sc.get),sc[max(sc,key=sc.get)]

def l3_profit(m):
    mb=m.get('max_board',0); sb=m.get('sector_boom_count',0); lu=m.get('limit_up',0)
    sc={}
    sc['龙头接力']=(3 if mb>=7 else 2 if mb>=5 else 0)+(1 if sb>=8 else 0)
    sc['首板套利']=(1 if mb<=4 else 0)+(1 if lu>=30 else 0)
    sc['板块集群']=(3 if sb>=10 else 2 if sb>=6 else 0)+(1 if lu>=50 else 0)
    sc['轮动打地鼠']=(2 if mb<=3 else 0)+(1 if sb<=3 else 0)+(1 if 20<=lu<=50 else 0)
    return max(sc,key=sc.get),sc[max(sc,key=sc.get)]

def l4_sector(m):
    c=m.get('top1_concentration',0); sb=m.get('sector_boom_count',0)
    if c>=50 and sb>=5: return '🎯集中主线',4
    if c>=30 or sb>=3: return '🔀双线并行',3
    if sb>=1: return '📊散乱多线',2
    return '🌫️无主线',1

def l5_health(m):
    vlt=m.get('vol_lt_07_pct',50)
    if vlt>=40 and m.get('suoliang_pct',5)>=15: return '💎缩量惜售',4
    if m.get('fangliang_pct',80)<=70: return '✅量价健康',3
    if m.get('fangliang_pct',80)<=85: return '⚖️量价温和',2
    return '⚠️量价虚胖',1

def l6_trend(m):
    ma=m.get('avg_ma20_dev',0); lu=m.get('limit_up',0); mb=m.get('max_board',0)
    if ma>=15 and mb>=5 and lu>=40: return '🚀加速冲顶',4
    if ma>=5 or (mb>=4 and lu>=30): return '📈强势延续',3
    if ma>=-5 or lu>=20: return '↔️震荡筑底',2
    return '📉超跌反弹',1

def l7_cycle(m):
    lu=m.get('limit_up',0); zh=m.get('zh_ratio',50); ld=m.get('limit_down',0)
    if lu<=10 and ld>=5: return '😱绝望',0
    if lu<=20 or (zh<=35 and lu<=30): return '😔悲观',2
    if lu>=30 and zh>=50: return '😊乐观',4
    if lu>=60 and zh>=60: return '🤩狂热',5
    return '😐平衡',3

def l8_rotation(m):
    sb=m.get('sector_boom_count',0)
    if sb>=4: return '🐢单主线',4
    if sb>=2: return '🐇双线轮动',3
    if sb>=1: return '⚡高速轮动',2
    return '🌪️混沌',1

def l9_game(m):
    fl=m.get('fangliang_pct',80)
    if fl>=70: return '🃏游资主导(推断)',2
    return '❓未知',1

def classify_full(m):
    l1n,l1s=l1_heat(m); l2n,l2s=l2_style(m); l3n,l3s=l3_profit(m)
    l4n,l4s=l4_sector(m); l5n,l5s=l5_health(m); l6n,l6s=l6_trend(m)
    l7n,l7s=l7_cycle(m); l8n,l8s=l8_rotation(m); l9n,l9s=l9_game(m)
    tag=f"{l1n}·{l2n}·{l3n}·{l4n}·{l5n}·{l6n}·{l7n}·{l8n}·{l9n}"
    return tag,{'l1_热度':l1n,'l2_风格':l2n,'l3_效应':l3n,
        'l4_板块':l4n,'l5_量价':l5n,'l6_趋势':l6n,
        'l7_情绪':l7n,'l8_轮动':l8n,'l9_博弈':l9n}

# ============ 4. 特征向量 ============

def compute_feature_vector(m, sectors):
    lu=m.get('limit_up',0) or 1
    hm={'❄️恐慌冰点':0,'❄️冰点':0.15,'☁️平淡':0.35,'🌤活跃':0.6,'☀️狂热':0.85}
    f0=hm.get(m.get('_l1_热度','☁️平淡'),0.35)
    f1=min(lu/100,1.0); f2=m.get('zh_ratio',50)/100.0
    t1=sectors[0].get('lu',0) if sectors else 0
    f3=t1/max(lu,1); f4=min(m.get('max_board',0)/7.0,1.0)
    f5=m.get('limit_suoliang',0)/max(lu,1)
    f6=(m.get('zhaban_rate',0) or 0)/100.0; f7=0.5
    vt=m.get('vol_lt_07_pct',0); vg=m.get('vol_gt_3_pct',0)
    f8=vg/(vt+vg) if (vt+vg)>0 else 0.5
    f9=0.5; f10=len(sectors)/40.0
    f11=(m.get('gap_gt_3_pct',0) or 0)/100.0
    return {f'f{i:02d}':round(v,4) for i,v in enumerate([f0,f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11])}

# ============ 5. 构建全市场字典 ============

def build_market_dict(raw, indices, tetegu):
    vol=raw.get('vol_dist',{}); gap=raw.get('gap_dist',{})
    total=raw.get('up_count',0)+raw.get('down_count',0)
    lu=raw.get('limit_up',0); sectors=tetegu.get('sectors',[])
    sb=sum(1 for s in sectors if s.get('lu',0)>=3)
    t1=sectors[0].get('lu',0) if sectors else 0
    conc=round(t1/max(lu,1)*100,1) if lu>0 else 0
    vt=sum(vol.values()) or 1; gt=sum(gap.values()) or 1
    return {'total_count':total,'up_count':raw.get('up_count',0),
        'down_count':raw.get('down_count',0),
        'zh_ratio':round(raw.get('up_count',0)/max(total,1)*100,1),
        'limit_up':lu,'limit_down':raw.get('limit_down',0),
        'limit_yizi':raw.get('limit_yizi',0),
        'limit_suoliang':raw.get('limit_suoliang',0),
        'limit_fangliang':raw.get('limit_fangliang',0),
        'yizi_pct':pct(raw.get('limit_yizi',0),lu),
        'suoliang_pct':pct(raw.get('limit_suoliang',0),lu),
        'fangliang_pct':pct(raw.get('limit_fangliang',0),lu),
        'max_board':tetegu.get('max_board',0),
        'zhaban_count':tetegu.get('zhaban',0),
        'zhaban_rate':round(tetegu.get('zhaban',0)/max(lu,1)*100,1),
        'sector_boom_count':sb,'sector_total':len(sectors),
        'top1_concentration':conc,
        'vol_lt_07_pct':(vol.get('<0.5',0)+vol.get('0.5~0.7',0))/vt*100,
        'vol_gt_3_pct':(vol.get('3~5',0)+vol.get('>5',0))/vt*100,
        'gap_gt_3_pct':(gap.get('3~5%',0)+gap.get('5~7%',0)+gap.get('>7%',0))/gt*100,
        'sh_amount':indices.get('000001',{}).get('amount',0),
        'avg_ma20_dev':0,'youzi_net_wan':0,'jigou_net_wan':0,'sanhu_net_wan':0,
        '_sectors':sectors}

# ============ 6. 存储 ============

def save_slice(date_str, ts_str, raw, indices, tetegu):
    conn=sqlite3.connect(HISTORY_DB); c=conn.cursor()
    c.execute("INSERT OR IGNORE INTO time_slices(date,ts,source) VALUES(?,?,?)",
              (date_str,ts_str,'live'))
    c.execute("SELECT id FROM time_slices WHERE date=? AND ts=? AND source=?",
              (date_str,ts_str,'live'))
    row=c.fetchone()
    if not row: conn.close(); return None
    sid=row[0]
    m=build_market_dict(raw,indices,tetegu)
    tag,detail=classify_full(m)
    fv=compute_feature_vector(m,tetegu.get('sectors',[]))
    sectors=tetegu.get('sectors',[]); top3=sectors[:3]
    vol=raw.get('vol_dist',{}); gap=raw.get('gap_dist',{})

    c.execute("INSERT OR REPLACE INTO dim_market VALUES(?,?,?,?,?,?,?)",
              (sid,indices.get('000001',{}).get('chg',0),
               indices.get('399001',{}).get('chg',0),
               indices.get('399006',{}).get('chg',0),
               m.get('sh_amount',0),0,m.get('avg_ma20_dev',0)))
    c.execute("INSERT OR REPLACE INTO dim_limit VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (sid,m['up_count'],m['down_count'],m['zh_ratio'],
               m['limit_up'],m['limit_down'],m['max_board'],
               m['limit_yizi'],m['limit_suoliang'],m['limit_fangliang'],
               0,m['zhaban_count'],m['zhaban_rate'],0))
    c.execute("INSERT OR REPLACE INTO dim_volume VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (sid,vol.get('<0.5',0),vol.get('0.5~0.7',0),vol.get('0.7~1',0),
               vol.get('1~3',0),vol.get('3~5',0),vol.get('>5',0),
               gap.get('1~3%',0),gap.get('3~5%',0),gap.get('5~7%',0),gap.get('>7%',0)))
    c.execute("INSERT OR REPLACE INTO dim_sector VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (sid,m['sector_boom_count'],m['sector_total'],
               top3[0].get('lu',0) if len(top3)>0 else 0,top3[0].get('name','') if len(top3)>0 else '',
               top3[1].get('lu',0) if len(top3)>1 else 0,top3[1].get('name','') if len(top3)>1 else '',
               top3[2].get('lu',0) if len(top3)>2 else 0,top3[2].get('name','') if len(top3)>2 else '',
               m['top1_concentration'],json.dumps([s['name'] for s in sectors[:5]])))
    c.execute("INSERT OR REPLACE INTO dim_fund VALUES(?,?,?,?,?,?,?,?,?,?)",
              (sid,0,0,0,0,0,0,0,0,0))
    c.execute("INSERT OR REPLACE INTO dim_sentiment VALUES(?,?,?,?,?,?,?,?,?)",
              (sid,'😐平衡',0,0,0,0,0,0,0))
    c.execute("INSERT OR REPLACE INTO dim_cluster VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (sid,
               detail.get('l1_热度',''),l1_heat(m)[1],
               detail.get('l2_风格',''),l2_style(m)[1],
               detail.get('l3_效应',''),l3_profit(m)[1],
               detail.get('l4_板块',''),l4_sector(m)[1],
               detail.get('l5_量价',''),l5_health(m)[1],
               detail.get('l6_趋势',''),l6_trend(m)[1],
               detail.get('l7_情绪',''),l7_cycle(m)[1],
               detail.get('l8_轮动',''),l8_rotation(m)[1],
               detail.get('l9_博弈',''),l9_game(m)[1],
               tag))
    c.execute("INSERT OR REPLACE INTO feature_vector VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (sid,)+tuple(fv[f'f{i:02d}'] for i in range(12)))
    c.execute("INSERT OR IGNORE INTO snapshot_log(date,ts,slice_id,status,created_at) VALUES(?,?,?,?,datetime('now','localtime'))",
              (date_str,ts_str,sid,'ok'))
    conn.commit(); conn.close()
    return {'slice_id':sid,'tag':tag,'detail':detail}

# ============ 7. 主流程 ============

def take_slice():
    t0=time.time(); now=datetime.now(); today=str(date.today()); ts=round_ts(now)
    print(f"\n📸 时间片 — {today} {ts}"); print('─'*50)
    init_db()
    indices=fetch_index(); print("  ✅ 指数")
    raw=scan_market()
    if not raw: print("  ❌ 全量扫描失败"); return None
    print(f"  涨停{raw['limit_up']} 涨{raw['up_count']}/跌{raw['down_count']}")
    tetegu=get_tetegu_data()
    print(f"  最高板:{tetegu['max_board']} 炸板:{tetegu['zhaban']} 板块:{len(tetegu['sectors'])}")
    result=save_slice(today,ts,raw,indices,tetegu)
    et=time.time()-t0
    if result:
        print(f"  ✅ #{result['slice_id']} | {result['tag'][:60]} | ⏱️{et:.1f}s")
        return result
    return None

def list_slices(limit=20):
    init_db()
    try:
        conn=sqlite3.connect(HISTORY_DB)
        c=conn.execute(f"""SELECT t.id,t.date,t.ts,l.limit_up,l.zh_ratio,l.max_board,cl.full_tag
            FROM time_slices t JOIN dim_limit l ON t.id=l.slice_id
            JOIN dim_cluster cl ON t.id=cl.slice_id
            ORDER BY t.date DESC,t.ts DESC LIMIT {limit}""")
        rows=c.fetchall(); conn.close()
        print(f"\n📊 基因库 ({len(rows)}个)")
        print(f"{'ID':<5} {'日期':<12} {'时间':<6} {'涨停':<6} {'涨跌%':<8} {'最高板':<6} 标签")
        print('─'*5+' '+'-'*12+' '+'-'*6+' '+'-'*6+' '+'-'*8+' '+'-'*6+' '+'-'*40)
        for r in rows:
            print(f"{r[0]:<5} {r[1]:<12} {r[2]:<6} {r[3]:<6} {r[4]:>6}% {r[5]:<6} {str(r[6])[:50]}")
    except Exception as e: print(f"查询失败: {e}")

def run_watch():
    print("\n🔄 持续监控 (09:15~15:00 工作日)"); print('='*55)
    last=""
    while True:
        now=datetime.now()
        if now.weekday()>=5 or now.hour*60+now.minute<555 or now.hour*60+now.minute>=900:
            time.sleep(60); continue
        ts=round_ts(now)
        if ts!=last:
            print(f"\n⏰ {now.strftime('%H:%M:%S')} — {ts}")
            take_slice()
            last=ts
        nm=((now.minute//5)+1)*5
        slp=max(1,(nm-now.minute)*60-now.second)
        time.sleep(slp)

if __name__=='__main__':
    a=sys.argv[1:]
    if '--watch' in a: run_watch()
    elif '--list' in a: list_slices(50)
    else: take_slice()
