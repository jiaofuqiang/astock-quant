#!/usr/bin/env python3
"""
【15:00 盘后报告】全天复盘 + 对比昨日各报告 → 《经验报告》→ 《升级报告》

数据：大盘(涨跌幅/量比/成交量/换手率) + 游资/量化/机构表现 + 板块TOP5 + 个股TOP10
对比：昨日盘后/预测/消息面/交易计划/获利报告
输出：经验报告 → 升级报告（用户阅读确认后执行）
"""
import sys, os, json, sqlite3, urllib.request
from datetime import date, datetime
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import *

TODAY = today()
print(f"[24h] 15:00 盘后报告 {TODAY}")
print("="*60)

# ============================================================
# 1. 大盘数据
# ============================================================
def get_market_full():
    """获取上证/深证/创业板的涨跌幅、量比、成交量、换手率"""
    res = {}
    codes = {'sh000001':'上证','sz399001':'深证','sz399006':'创业板'}
    try:
        url = 'http://qt.gtimg.cn/q=' + ','.join(codes.keys())
        req = urllib.request.urlopen(url, timeout=10)
        raw = req.read().decode('gbk')
        for line in raw.strip().split(';'):
            if not line or '=' not in line: continue
            parts = line.split('~')
            if len(parts) < 40: continue
            name = parts[1]
            res[name] = {
                'change_pct': float(parts[32]) if parts[32] else 0,       # 涨跌幅%
                'change': float(parts[31]) if parts[31] else 0,            # 涨跌额
                'price': float(parts[3]) if parts[3] else 0,               # 现价
                'volume_shou': float(parts[6]) if parts[6] else 0,         # 成交量(手)
                'amount_wan': float(parts[37]) if len(parts)>37 and parts[37] else 0,  # 成交额(万)
                'turnover_pct': float(parts[38]) if len(parts)>38 and parts[38] else 0, # 换手率%
            }
    except Exception as e:
        print(f"  [WARN] 大盘数据获取失败: {e}")
    return res

market = get_market_full()
if not market:
    market = {'上证':{'change_pct':0,'change':0,'price':0,'volume_shou':0,'amount_wan':0,'turnover_pct':0}}

print(f"  大盘")
for k,v in market.items():
    print(f"    {k}: {v.get('change_pct',0):+.2f}% | 量{v.get('volume_shou',0)/10000:.0f}万手 | 额{v.get('amount_wan',0)/10000:.0f}亿 | 换手{v.get('turnover_pct',0):.2f}%")

# ============================================================
# 2. 游资/量化/机构表现（从bundle获取）
# ============================================================
def get_funds_performance():
    """从bundle提取三资金数据"""
    bundle = load_json_or_empty(BUNDLE_JSON)
    funds = {'youzi':0, 'lianghua':0, 'jigou':0, 'total':0}
    if not bundle:
        return funds, ''
    scan_text = bundle.get('scan_data', '')
    if isinstance(scan_text, str):
        # 解析scan_text中的三资金信息
        lines = scan_text.split('\n')
        youzi_ct = sum(1 for l in lines if '游资' in l and ('分' in l or '+' in l))
        lianghua_ct = sum(1 for l in lines if '量化' in l and ('分' in l or '+' in l))
        jigou_ct = sum(1 for l in lines if '机构' in l and ('分' in l or '+' in l))
        funds = {'youzi':youzi_ct, 'lianghua':lianghua_ct, 'jigou':jigou_ct,
                 'total':youzi_ct+lianghua_ct+jigou_ct}
    # 游资信号
    youzi_signal = bundle.get('youzi_signal', {})
    lhb = bundle.get('lhb', {})
    lhb_summary = lhb.get('summary', '') if isinstance(lhb, dict) else ''
    return funds, lhb_summary

funds, lhb_summary = get_funds_performance()
print(f"  三资金: 游资{funds.get('youzi',0)} 量化{funds.get('lianghua',0)} 机构{funds.get('jigou',0)}")

# ============================================================
# 3. 板块表现 + 最强题材
# ============================================================
def get_sectors_from_bundle():
    bundle = load_json_or_empty(BUNDLE_JSON)
    sector_index = bundle.get('sector_index', {})
    if not sector_index:
        return [], []
    hot = sector_index.get('hot_sectors', [])
    other = sector_index.get('other_sectors', [])
    # 按涨停数排序
    all_sorted = sorted(hot + other, key=lambda x: (x.get('limit_up',0), x.get('avg_change',0)), reverse=True)
    top5 = [{
        'name': s.get('name',''),
        'avg_change': round(s.get('avg_change',0),2),
        'limit_up': s.get('limit_up',0),
        'stock_count': s.get('stock_count',0),
        'up_count': s.get('up_count',0),
        'strength': round(s.get('strength',0),2),
        'avg_volume_ratio': round(s.get('avg_volume_ratio',0),2),
    } for s in all_sorted[:5]]
    # 最强题材=涨停最多
    max_limit = max([s.get('limit_up',0) for s in all_sorted]) if all_sorted else 0
    strongest = [s.get('name','') for s in all_sorted if s.get('limit_up',0) == max_limit]
    return top5, strongest

sectors_top5, strongest = get_sectors_from_bundle()
print(f"  最强题材: {', '.join(strongest) if strongest else '无'}")
print(f"  板块TOP5:")
for s in sectors_top5:
    print(f"    {s['name']} | {s['avg_change']:+.2f}% | 涨停{s['limit_up']}只 | 强度{s['strength']}")

# ============================================================
# 4. 个股表现TOP10（从涨停数据）
# ============================================================
def get_stocks_top10():
    try:
        db = sqlite3.connect(LIMIT_DB)
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT code, name, board_count, limit_stat, first_limit_time,
                   turnover, amount_wan, change_pct
            FROM limit_stocks WHERE date=? AND board_count>=1
            ORDER BY board_count DESC, change_pct DESC LIMIT 10
        """, (TODAY,)).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"  [WARN] 个股数据获取失败: {e}")
        return []

stocks_top10 = get_stocks_top10()
print(f"  个股TOP10: {[s['name']+'/'+str(s['board_count'])+'板' for s in stocks_top10]}")

# ============================================================
# 5. 对比昨日全部报告 → 经验总结
# ============================================================
def compare_all_reports():
    """拉取昨日盘后/预测/消息面/交易计划/获利报告，对比今日实际"""
    result = {}
    # 昨日各报告
    for name, dname, prefix in [
        ('yest_afternoon', AFTERNOON_DIR, 'afternoon'),
        ('yest_prediction', PREDICTION_DIR, 'prediction'),
        ('yest_news', NEWS_DIR, 'news'),
        ('yest_plan', PLAN_DIR, 'plan'),
        ('yest_profit', PROFIT_DIR, 'profit'),
    ]:
        path = yest_report_filename(dname, prefix)
        result[name] = load_json_or_empty(path)

    # 大盘预测对比
    pred = result.get('yest_prediction', {})
    market_actual = market.get('上证', {}).get('change_pct', 0)
    market_scenarios = pred.get('market_scenarios', [])
    pred_main = market_scenarios[0].get('scenario','') if market_scenarios else ''

    def market_correct():
        if market_actual > 0.5 and pred_main == '强更强': return True
        if market_actual < -0.5 and pred_main == '强转弱': return True
        if -0.5 <= market_actual <= 0.5 and pred_main == '震荡': return True
        return False

    # 题材预测对比
    sector_scenarios = pred.get('sector_scenarios', [])
    pred_sectors = sector_scenarios[0].get('sectors',[]) if sector_scenarios else []
    actual_sectors = [s['name'] for s in sectors_top5]
    hit = sum(1 for s in pred_sectors if s in actual_sectors)

    # 交易计划对比
    plan = result.get('yest_plan', {})
    plan_hit = {}
    for level in ['plan_1', 'plan_2', 'plan_3']:
        p = plan.get(level, {})
        p_sectors = p.get('target_sectors', [])
        p_stocks = p.get('target_stocks', [])
        sector_hit = [s for s in p_sectors if s in actual_sectors]
        stock_hit = [s for s in p_stocks if any(t['name']==s for t in stocks_top10)]
        if sector_hit or stock_hit:
            plan_hit[level] = {'sector_hit': sector_hit, 'stock_hit': stock_hit}

    # ========== 经验总结 ==========
    experience = {
        'date': TODAY,
        'market': {
            'actual_pct': market_actual,
            'predicted': pred_main,
            'correct': market_correct(),
        },
        'sectors': {
            'predicted': pred_sectors,
            'actual_top5': actual_sectors,
            'hit_count': hit,
            'accuracy': round(hit/len(pred_sectors),2) if pred_sectors else 0,
        },
        'plan_hits': plan_hit,
        'success_items': [],
        'fail_items': [],
        'improvements': [],
        'consecutive_fails': 0,
    }

    # 成功/失败评估
    if market_correct():
        experience['success_items'].append(f"大盘方向正确(预测{pred_main}/实际{market_actual:+.2f}%)")
    else:
        experience['fail_items'].append(f"大盘方向偏差(预测{pred_main}/实际{market_actual:+.2f}%)")

    if hit >= 2:
        experience['success_items'].append(f"题材预测命中{hit}/{len(pred_sectors)}")
    elif hit > 0:
        experience['fail_items'].append(f"题材预测仅命中{hit}/{len(pred_sectors)}")
    else:
        experience['fail_items'].append("题材预测完全偏差")

    if plan_hit:
        for k, v in plan_hit.items():
            experience['success_items'].append(f"交易计划{k}命中板块{len(v.get('sector_hit',[]))}个/个股{len(v.get('stock_hit',[]))}只")

    # 连续失败统计
    yest_exp = result.get('yest_experience', {})
    if isinstance(yest_exp, dict):
        prev_fails = yest_exp.get('consecutive_fails', 0)
    else:
        prev_fails = 0
    if market_correct() and hit >= 1:
        experience['consecutive_fails'] = 0
    else:
        experience['consecutive_fails'] = prev_fails + 1
    if experience['consecutive_fails'] >= 3:
        experience['improvements'].append(f"⚠️ 连续{experience['consecutive_fails']}次预测失败，建议调整预测参数")

    # 涨停数分析
    total_limit = sum(s.get('limit_up',0) for s in sectors_top5)
    if total_limit >= 5:
        experience['success_items'].append(f"今日热点明确(涨停≥5板块{len([s for s in sectors_top5 if s.get('limit_up',0)>=5])}个)")
    elif total_limit < 2:
        experience['improvements'].append("无明确热点，应降低仓位预期")

    return result, experience

old_reports, experience = compare_all_reports()
print(f"  经验总结: 成功{len(experience['success_items'])}项 失败{len(experience['fail_items'])}项 改进{len(experience['improvements'])}项")
for s in experience['success_items']: print(f"    ✅ {s}")
for f in experience['fail_items']: print(f"    ❌ {f}")
for i in experience['improvements']: print(f"    💡 {i}")

# ============================================================
# 6. 升级报告
# ============================================================
def generate_upgrade_report(exp):
    """基于经验总结生成升级建议，auto=False的需要用户确认"""
    upgrade = {
        'date': TODAY,
        'consecutive_fails': exp.get('consecutive_fails', 0),
        'items': [],
    }
    # 自动升级项
    if exp.get('consecutive_fails', 0) >= 3:
        upgrade['items'].append({
            'type': 'parameter_adjust',
            'auto': False,
            'desc': '连续预测失败≥3次，建议：①降低仓位至50% ②减少买入条件门槛',
        })
    # 检查是否需要调参
    if exp.get('sectors',{}).get('accuracy', 1) < 0.3:
        upgrade['items'].append({
            'type': 'sector_weight',
            'auto': True,
            'desc': '板块预测偏差大，自动降低右侧板块权重，提高消息面板块权重',
        })
    # 涨停太少建议
    total_limit = sum(s.get('limit_up',0) for s in sectors_top5)
    if total_limit < 2:
        upgrade['items'].append({
            'type': 'position_control',
            'auto': False,
            'desc': '无明确热点，建议明日观望/仓位≤30%，需你确认',
        })
    # 赚钱模式建议
    upgrade['items'].append({
        'type': 'trade_mode',
        'auto': False,
        'desc': '最赚钱模式：龙≥5板+竞价开≥3%跟风(+7.6%/暴利70%)，建议优先执行',
    })
    return upgrade

upgrade = generate_upgrade_report(experience)
print(f"  升级报告: {len(upgrade['items'])}项建议")
for item in upgrade['items']:
    tag = '✅自动' if item['auto'] else '⚠️需确认'
    print(f"    [{tag}] {item['desc']}")

# ============================================================
# 7. 保存全部报告
# ============================================================
report = {
    'type': 'afternoon_report',
    'date': TODAY,
    'generate_time': datetime.now().strftime('%H:%M'),
    'market': {k:{
        'change_pct': v.get('change_pct',0),
        'change': v.get('change',0),
        'price': v.get('price',0),
        'volume_shou': v.get('volume_shou',0),
        'amount_wan': v.get('amount_wan',0),
        'turnover_pct': v.get('turnover_pct',0),
    } for k,v in market.items()},
    'funds_performance': funds,
    'lhb_summary': lhb_summary[:200] if isinstance(lhb_summary, str) else str(lhb_summary)[:200],
    'sectors_top5': sectors_top5,
    'strongest_themes': strongest,
    'stocks_top10': stocks_top10,
    'experience': experience,
    'upgrade': upgrade,
}

report_file = report_filename(AFTERNOON_DIR, 'afternoon')
os.makedirs(os.path.dirname(report_file), exist_ok=True)
with open(report_file, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(f"  盘后报告已保存: {report_file}")

exp_file = report_filename(EXPERIENCE_DIR, 'experience')
os.makedirs(os.path.dirname(exp_file), exist_ok=True)
with open(exp_file, 'w', encoding='utf-8') as f:
    json.dump(experience, f, ensure_ascii=False, indent=2)
print(f"  经验报告已保存: {exp_file}")

upgrade_file = report_filename(UPGRADE_DIR, 'upgrade')
os.makedirs(os.path.dirname(upgrade_file), exist_ok=True)
with open(upgrade_file, 'w', encoding='utf-8') as f:
    json.dump(upgrade, f, ensure_ascii=False, indent=2)
print(f"  升级报告已保存: {upgrade_file}")
print(f"\n[24h] 15:00 盘后报告完成 ✅")
