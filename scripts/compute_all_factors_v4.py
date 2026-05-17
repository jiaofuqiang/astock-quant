#!/usr/bin/env python3
"""
全量因子计算 V4 — 批量化写入，解决逐行INSERT太慢的问题
"""
import os, sys, subprocess, time, tempfile
from datetime import datetime

PROJECT_DIR = "/home/ubuntu/astock"
DATA_DIR = f"{PROJECT_DIR}/data"
KLINE_DB = f"{DATA_DIR}/kline_cache.db"
FACTOR_DB = f"{DATA_DIR}/factor_v6.db"
os.chdir(PROJECT_DIR)

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)

def sql(query, db=KLINE_DB):
    """exec sql query"""
    r = subprocess.run(['sqlite3', '-noheader', '-separator', '|', db, query],
                       capture_output=True, timeout=30, text=True)
    if r.returncode != 0: return []
    return [l for l in r.stdout.strip().split('\n') if l.strip()]

def bulk_insert(code, factors, db=FACTOR_DB):
    """批量INSERT一只股票的全部因子数据"""
    if not factors: return 0
    
    # 生成INSERT语句
    vals_list = []
    for f in factors:
        ds = str(f[0]).strip()[:10]
        cv = f[1]
        vol = f[2]
        cols = ','.join(str(v) for v in f[3:])
        vals_list.append(f"('{code}','{ds}',{cv},{vol},{cols})")
    
    # 每500行一批写入
    batch_size = 500
    total = 0
    for i in range(0, len(vals_list), batch_size):
        batch = vals_list[i:i+batch_size]
        sql_text = f"INSERT OR REPLACE INTO factors VALUES {','.join(batch)}"
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.sql') as tmp:
            tmp.write(sql_text + ';\n')
            tmp_path = tmp.name
        try:
            r = subprocess.run(['sqlite3', db, f'.read {tmp_path}'],
                             capture_output=True, timeout=60, text=True)
            if r.returncode != 0:
                log(f"    批量写入失败, 单行降级...")
                cnt2 = 0
                for vl in batch[:100]:  # 只试100行
                    s = f"INSERT OR REPLACE INTO factors VALUES {vl}"
                    if subprocess.run(['sqlite3', db, s], capture_output=True, timeout=10).returncode == 0:
                        cnt2 += 1
                total += cnt2
            else:
                total += len(batch)
        except:
            pass
        finally:
            os.unlink(tmp_path)
    
    return total

def compute_factors(code, rows):
    """计算40因子"""
    n = len(rows)
    if n < 60: return []
    
    dates = [r[0] for r in rows]
    opens = [float(r[1]) for r in rows]
    closes = [float(r[2]) for r in rows]
    highs = [float(r[3]) for r in rows]
    lows = [float(r[4]) for r in rows]
    volumes = [float(r[5]) for r in rows]
    
    def sma(arr, p):
        if len(arr) < p: return [0.0]*len(arr)
        res = [0.0]*(p-1)
        for i in range(p-1, len(arr)): res.append(sum(arr[i-p+1:i+1])/p)
        return res
    
    vs5 = sma(volumes, 5)
    vs20 = sma(volumes, 20)
    
    def cv(p, idx):
        if idx < p: return 0.0
        rets = [(closes[j]-closes[j-1])/closes[j-1]*100 for j in range(idx-p+1, idx+1)]
        m = sum(rets)/len(rets)
        return (sum((r-m)**2 for r in rets)/len(rets))**0.5 * 100
    
    def rsi(idx):
        if idx < 14: return 50.0
        g, l = 0.0, 0.0
        for j in range(idx-13, idx+1):
            ch = closes[j]-closes[j-1]
            if ch>0: g+=ch
            else: l+=abs(ch)
        ag, al = g/14, l/14
        if al==0: return 100.0
        return 100-100/(1+ag/al)
    
    def vpc(idx):
        if idx < 20: return 0.0
        pr = [(closes[j]-closes[j-1])/closes[j-1] for j in range(idx-19, idx+1)]
        vc = [(volumes[j]-volumes[j-1])/volumes[j-1] if volumes[j-1]>0 else 0 for j in range(idx-19, idx+1)]
        np_ = len(pr)
        mp, mv = sum(pr)/np_, sum(vc)/np_
        cov = sum((pr[j]-mp)*(vc[j]-mv) for j in range(np_))
        sp = (sum((r-mp)**2 for r in pr)/np_)**0.5
        sv = (sum((v-mv)**2 for v in vc)/np_)**0.5
        return cov/(sp*sv*np_) if sp>0 and sv>0 else 0.0
    
    results = []
    for idx in range(n):
        if idx < 60: continue
        c, h, l, v, o = closes[idx], highs[idx], lows[idx], volumes[idx], opens[idx]
        
        ma5 = sum(closes[idx-4:idx+1])/5
        ma20 = sum(closes[idx-19:idx+1])/20
        ma60 = sum(closes[idx-59:idx+1])/60
        ma120 = sum(closes[idx-119:idx+1])/120 if idx>=119 else c
        
        f = []
        f.append(round(ma5/ma20-1,4))
        f.append(round(c/ma20-1,4))
        f.append(round(c/ma60-1,4))
        f.append(round(c/ma120-1,4))
        
        if idx>=20:
            m5p=sum(closes[idx-5:idx])/5; m20p=sum(closes[idx-20:idx])/20
            f.append(1.0 if (ma5>=ma20 and m5p<m20p) else (-1.0 if (ma5<=ma20 and m5p>m20p) else 0.0))
        else: f.append(0.0)
        
        cr=h-l
        f.append(round((c-l)/cr,4) if cr>0 else 0.5)
        f.append(round(cr/c*100,4) if c>0 else 0)
        f.append(round((h-max(c,o))/cr,4) if cr>0 else 0)
        f.append(round((min(c,o)-l)/cr,4) if cr>0 else 0)
        
        def cg(p):
            return round((c-closes[idx-p])/closes[idx-p]*100,4) if idx>=p and closes[idx-p]>0 else 0.0
        f.extend([cg(1),cg(3),cg(5),cg(10),cg(20),cg(30),cg(60)])
        f.extend([round(cv(5,idx),4), round(cv(10,idx),4), round(cv(20,idx),4), round(cv(60,idx),4)])
        f.append(round(rsi(idx),4))
        f.append(round(vs5[idx]/vs20[idx],4) if vs20[idx]>0 else 1.0)
        mn60, mx60 = min(closes[idx-59:idx+1]), max(closes[idx-59:idx+1])
        f.append(round((c-mn60)/(mx60-mn60),4) if (mx60-mn60)>0 else 0.5)
        f.append(round(vpc(idx),4))
        
        # risk_reward
        if idx>=24:
            fwd=[(closes[j+5]-closes[j])/closes[j]*100 for j in range(idx-19, idx+1) if j+5<n]
            if fwd: mf=sum(fwd)/len(fwd); sf=(sum((r-mf)**2 for r in fwd)/len(fwd))**0.5; f.append(round(mf/sf,4) if sf>0 else 0)
            else: f.append(0.0)
        else: f.append(0.0)
        
        f.append(round((c-closes[idx-20])/closes[idx-20]*100/f[18],4) if f[18]>0 else 0)
        f.append(round(f[12]-f[11],4))
        f.append(round(f[16]/f[17],4) if f[17]>0 else 1.0)
        f.append(round(f[16]/f[19],4) if f[19]>0 else 1.0)
        # vp_divergence (三分位)
        c5=f[11]; vr=f[21]
        if (c5>2 and vr<0.7) or (c5<-2 and vr>1.5):
            f.append(-1.0)  # 强背离
        elif (c5>1 and vr<0.85) or (c5<-1 and vr>1.2):
            f.append(-0.5)  # 弱背离
        elif (c5>0 and vr>1.1 and vr<1.5) or (c5<0 and vr<0.9 and vr>0.6):
            f.append(0.5)   # 弱配合
        elif (c5>1.5 and vr>1.5) or (c5<-1.5 and vr<0.6):
            f.append(1.0)   # 强配合
        else:
            f.append(0.0)   # 中性
        
        u,d=0,0
        for j in range(idx,idx-10,-1):
            if j<=0: break
            if closes[j]>closes[j-1]: u+=1; break
        for j in range(idx,idx-10,-1):
            if j<=0: break
            if closes[j]<closes[j-1]: d+=1; break
        f.extend([u,d])
        f.append(round((o/closes[idx-1]-1)*100,4) if idx>=1 and closes[idx-1]>0 else 0)
        f.append(round(abs(h-l)/c*100/(abs(f[9])+0.01),4) if c>0 else 1.0)
        f.append(round((c-l)/(h-l),4) if (h-l)>0 else 0.5)
        f.append(1.0 if vs5[idx]>0 and v/vs5[idx]>2.0 else 0.0)
        
        bbm=sum(closes[idx-19:idx+1])/20; bbs=(sum((x-bbm)**2 for x in closes[idx-19:idx+1])/20)**0.5
        f.append(round((c-bbm)/(bbs*2+0.01),4) if bbs>0 else 0)
        
        if idx>=27:
            dus,dds,ts=0,0,0
            for j in range(idx-13,idx+1):
                if j<=0: continue
                um=highs[j]-highs[j-1]; dm=lows[j-1]-lows[j]
                du=max(um,0) if um>dm and um>0 else 0; dd=max(dm,0) if dm>um and dm>0 else 0
                tv=max(highs[j]-lows[j],abs(highs[j]-closes[j-1]),abs(lows[j]-closes[j-1]))
                dus+=du; dds+=dd; ts+=tv
            if ts>0:
                f.append(round(abs(dus/ts*100-dds/ts*100)/(dus/ts*100+dds/ts*100+0.01)*100,4))
            else: f.append(0.0)
        else: f.append(0.0)
        
        pr20=max(closes[idx-19:idx+1])-min(closes[idx-19:idx+1])
        f.append(round(1-min(pr20/c,1),4) if c>0 else 0.5)
        f.append(1.0 if f[12]>0 and f[23]>0 else (-1.0 if f[12]<0 and f[23]>0 else 0.0))
        f.append(round(f[17]/f[18],4) if f[18]>0 else 1.0)
        
        if idx>=13:
            trs=[max(highs[j]-lows[j],abs(highs[j]-closes[j-1]),abs(lows[j]-closes[j-1])) for j in range(idx-13,idx+1) if j>0]
            f.append(round(sum(trs)/len(trs)/c*100,4) if c>0 else 0)
        else: f.append(0.0)
        
        f.append(round(c/ma60-1,4))
        
        ds = dates[idx][:10] if len(dates[idx])>=10 else str(dates[idx])[:10]
        results.append((ds, round(c,4), round(v,4)) + tuple(f))
    
    return results

def main():
    log("🚀 全量因子计算 V4 — 批量写入")
    
    # 获取主板股票
    raw = sql("SELECT DISTINCT code FROM kline WHERE (code LIKE '6%' AND code NOT LIKE '688%') OR code LIKE '0%' ORDER BY code")
    all_codes = [r.strip() for r in raw if r.strip()]
    log(f"📊 主板: {len(all_codes)} 只")
    
    existing_raw = sql("SELECT DISTINCT code FROM factors", FACTOR_DB)
    existing = set(r.strip() for r in existing_raw if r.strip())
    log(f"📊 已有: {len(existing)} 只")
    
    to_compute = [c for c in all_codes if c not in existing]
    log(f"📊 需算: {len(to_compute)} 只")
    
    if not to_compute:
        log("✅ 全部完成!")
        return
    
    # 创建表
    sql("""
        CREATE TABLE IF NOT EXISTS factors (
            code TEXT, date TEXT, close REAL, volume REAL,
            ma5_ma20 REAL, close_ma20 REAL, close_ma60 REAL, close_ma120 REAL,
            golden_cross REAL, kbar_mid REAL, kbar_len REAL,
            upper_shadow REAL, lower_shadow REAL,
            chg_1d REAL, chg_3d REAL, chg_5d REAL, chg_10d REAL,
            chg_20d REAL, chg_30d REAL, chg_60d REAL,
            vola_5d REAL, vola_10d REAL, vola_20d REAL, vola_60d REAL,
            rsi_14 REAL, vol_ratio REAL, price_pos_60d REAL,
            vp_corr_20d REAL,
            risk_reward REAL, rs_vola_std REAL, chg_accel_10d REAL,
            vola_ratio_5_10 REAL, vola_ratio_5_60 REAL,
            vp_divergence REAL, up_days REAL, dn_days REAL,
            gap_open REAL, amp_chg_ratio REAL, intra_pos REAL,
            vol_spike REAL, boll_pos REAL, trend_strength REAL,
            price_concentration REAL, vp_trend_align REAL,
            vola_shrink REAL, atr_pct REAL, rel_ma60 REAL,
            PRIMARY KEY (code, date)
        )
    """, FACTOR_DB)
    log("✅ 表就绪")
    
    total_rows = 0
    start = time.time()
    
    for i, code in enumerate(to_compute):
        if i % 50 == 0:
            elapsed = time.time() - start
            pct = i/len(to_compute)*100
            speed = total_rows/elapsed if elapsed>0 else 0
            log(f"[{pct:.1f}%] #{i}/{len(to_compute)} {total_rows}行 {elapsed:.0f}s ({speed:.0f}行/s)")
        
        try:
            raw2 = sql(f"SELECT date,open,close,high,low,volume FROM kline WHERE code='{code}' ORDER BY date")
            if len(raw2) < 60:
                continue
            
            rows = [r.split('|') for r in raw2 if r.strip() and len(r.split('|')) >= 6]
            factors = compute_factors(code, rows)
            if not factors: continue
            
            inserted = bulk_insert(code, factors, FACTOR_DB)
            total_rows += inserted
            
        except Exception as e:
            log(f"  ❌ {code}: {e}")
    
    elapsed = time.time() - start
    log(f"🏁 完成! {elapsed:.0f}s, {total_rows}行")
    
    nc = sql("SELECT COUNT(DISTINCT code) FROM factors", FACTOR_DB)
    nr = sql("SELECT COUNT(*) FROM factors", FACTOR_DB)
    log(f"📊 factor_v6.db: {nc[0] if nc else '?'}只, {nr[0] if nr else '?'}行")

if __name__ == "__main__":
    main()
