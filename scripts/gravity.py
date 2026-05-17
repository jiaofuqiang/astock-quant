#!/usr/bin/env python3
"""
🌀 引力面量化模型 v1.0

"龙头涨停时，买哪个跟风股能赚？买几只？什么时候买？"

核心逻辑：
  龙头打出涨停板 → 溢价能量向板块内其他标的传导
  本模型量化这个传导的力度、方向、时点

6维引力因子：
  ① 市值弹性 — 小市值弹性大
  ② 主力跟风强度 — 龙头涨停后资金是否向跟风股同步流入
  ③ 市值分层联动 — 同层市值联动更强
  ④ 概念纯度 — 主营业务越纯联动越强
  ⑤ 换手接力意愿 — 换手放大说明接力意愿强
  ⑥ 龙一龙二间距 — 间距小=板块合力强

决策输出：
  - 跟风标的TOP3（含多因子评分、建议仓位、买入时机）
  - 板块合力强度评级
  - 风险提示

用法：
  python3 scripts/gravity.py                          # 自动扫描所有配置板块
  python3 scripts/gravity.py --bk BK1127              # 指定板块代码
  python3 scripts/gravity.py --bk BK1127 --threshold 7  # 指定涨停阈值(默认9.5%)
  python3 scripts/gravity.py --watch                  # 持续监控模式（每60秒）
"""

import json, urllib.request, sys, os, time, re
from datetime import datetime
from collections import defaultdict

# ============================================================
# 配置
# ============================================================

# 关注的产业链板块（东方财富概念板块代码）
# 格式：{板块代码: {name: 中文名, level: 主线等级, industry: 产业链}}
TARGET_SECTORS = {
    'BK1127': {'name': 'AI芯片', 'level': 'S级主线', 'industry': 'AI算力'},
    'BK1137': {'name': '存储芯片', 'level': 'A级主线', 'industry': '存储芯片'},
    'BK1152': {'name': '高带宽内存(HBM)', 'level': 'A级主线', 'industry': '存储芯片'},
    'BK0917': {'name': '半导体概念', 'level': 'A级主线', 'industry': '半导体'},
    'BK0891': {'name': '国产芯片', 'level': 'A级主线', 'industry': '半导体'},
    'BK0969': {'name': '汽车芯片', 'level': 'B级支线', 'industry': '半导体'},
    'BK1184': {'name': '人形机器人', 'level': 'A级主线', 'industry': '机器人'},
    'BK1190': {'name': '虚拟机器人', 'level': 'B级支线', 'industry': '机器人'},
    'BK1145': {'name': '机器人执行器', 'level': 'B级支线', 'industry': '机器人'},
    'BK0952': {'name': '第三代半导体', 'level': 'B级支线', 'industry': '半导体'},
    'BK1121': {'name': '第四代半导体', 'level': 'B级支线', 'industry': '半导体'},
}

# 涨停判定阈值
DEFAULT_LIMIT_UP = 9.5        # 涨幅≥此值视为涨停
STRONG_THRESHOLD = 7.0        # 大涨阈值
WATCH_THRESHOLD = 3.0         # 关注阈值

# 市值分层（亿）
CAP_TIERS = [
    ('大盘', 500, float('inf')),
    ('中盘', 100, 500),
    ('小盘', 30, 100),
    ('微盘', 0, 30),
]

# 各因子权重
WEIGHTS = {
    'cap_elasticity': 0.25,      # 市值弹性
    'fund_follow': 0.25,         # 主力跟风
    'tier_cohesion': 0.15,       # 同层联动
    'concept_purity': 0.10,      # 概念纯度（暂用市值替代）
    'turnover_momentum': 0.15,   # 换手接力意愿
    'gap_to_leader': 0.10,       # 龙一龙二间距
}

# 建议买入时机时间窗（分钟）
BUY_WINDOW_MINUTES = 5

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


# ============================================================
# 数据获取
# ============================================================

def fetch_sector_stocks(bk_code: str, max_count: int = 80) -> list:
    """获取板块成分股及实时行情"""
    url = (f'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz={max_count}&po=1&np=1'
           f'&fltt=2&fid=f3&fs=b:{bk_code}'
           f'&fields=f2,f3,f4,f12,f14,f15,f20,f62,f8,f9,f38,f10,f168,f169')
    req = urllib.request.Request(url, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    if not data.get('data') or not data['data'].get('diff'):
        return []
    return data['data']['diff']


def fetch_realtime_stock(code: str) -> dict:
    """获取单只股票实时行情（含更多字段）"""
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    req = urllib.request.Request(url, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=8)
    txt = resp.read().decode('gbk', errors='replace')
    m = re.search(r'\"(.*)\"', txt)
    if not m:
        return {}
    parts = m.group(1).split('~')
    if len(parts) < 48:
        return {}
    return {
        'code': code,
        'name': parts[1],
        'price': float(parts[3]) if parts[3] and parts[3] != '-' else 0,
        'change_pct': float(parts[32]) if parts[32] and parts[32] != '-' else 0,
        'volume': float(parts[6]) if parts[6] and parts[6] != '-' else 0,
        'amount': float(parts[37]) if parts[37] and parts[37] != '-' else 0,
        'turnover': float(parts[38]) if parts[38] and parts[38] != '-' else 0,
        'pe': float(parts[39]) if parts[39] and parts[39] != '-' else 0,
        'amplitude': float(parts[43]) if parts[43] and parts[43] != '-' else 0,
        'market_cap': float(parts[44]) if parts[44] and parts[44] != '-' else 0,
        'circulating_cap': float(parts[45]) if parts[45] and parts[45] != '-' else 0,
        'high_52w': float(parts[46]) if parts[46] and parts[46] != '-' else 0,
        'low_52w': float(parts[47]) if parts[47] and parts[47] != '-' else 0,
    }


def fetch_concept_purity(code: str) -> float:
    """
    获取概念纯度（主营业务中该产业链收入占比）
    实现：通过东方财富F10主营构成数据估算
    暂用简单启发式：有该板块概念的默认0.6，无则0.2
    后续可替换为爬取F10主营构成的真实数据
    """
    return 0.5  # 默认值，后续用F10数据替换


def is_trading_time() -> bool:
    """判断当前是否在交易时段"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    if 9 <= h <= 11 and not (h == 11 and m > 30):
        return True
    if 13 <= h <= 14:
        return True
    return False


# ============================================================
# 引力面量化引擎
# ============================================================

class GravityEngine:
    """引力面量化模型"""

    def __init__(self, limit_up_threshold: float = DEFAULT_LIMIT_UP):
        self.limit_up = limit_up_threshold
        self.strong = STRONG_THRESHOLD

    def analyze(self, bk_code: str, stocks_data: list) -> dict:
        """
        主分析入口

        Args:
            bk_code: 板块代码
            stocks_data: 板块成分股实时数据列表

        Returns:
            dict: {
                'bk_code': str,
                'bk_name': str,
                'timestamp': str,
                'force_rating': str,      # 板块合力评级
                'force_score': float,     # 合力评分 0-100
                'leader': dict,           # 龙一
                'runner_up': dict,        # 龙二
                'gap_pct': float,         # 龙一龙二间距%
                'zforce: list[dict],      # 跟风候选TOP3
                'risk_notes': list[str],  # 风险提示
            }
        """
        if not stocks_data:
            return {'error': '无板块数据'}

        # 按涨幅从高到低排序
        sorted_stocks = sorted(
            stocks_data,
            key=lambda x: abs(x.get('f3', 0) or 0),
            reverse=True
        )

        # 识别龙头（涨幅最高且≥涨停阈值）
        leader = None
        runners = []
        for s in sorted_stocks:
            chg = s.get('f3', 0) or 0
            if leader is None:
                leader = s
                continue
            # 排除ST和暴跌股
            if chg < -5:
                continue
            runners.append(s)

        if not leader:
            return {'error': '无法识别龙头'}

        leader_chg = leader.get('f3', 0) or 0

        # 计算板块合力评分
        force_score, force_rating = self._calc_force_rating(leader, runners)

        # 龙一龙二间距
        runner_up = runners[0] if runners else None
        gap_pct = 0
        if runner_up:
            gap_pct = leader_chg - (runner_up.get('f3', 0) or 0)

        # 筛选跟风标的
        candidates = self._filter_candidates(leader, runners, force_score)

        # 风险提示
        risks = self._assess_risks(leader, candidates, gap_pct, force_score)

        return {
            'bk_code': bk_code,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'force_rating': force_rating,
            'force_score': force_score,
            'leader': {
                'name': leader.get('f14', ''),
                'code': leader.get('f12', ''),
                'change_pct': leader_chg,
                'market_cap': (leader.get('f20', 0) or 0) / 1e8,
                'fund_net': (leader.get('f62', 0) or 0) / 1e8,
                'amount': (leader.get('f10', 0) or 0) / 1e4 if leader.get('f10') else 0,
            },
            'gap_pct': round(gap_pct, 2),
            'follow_candidates': candidates,
            'risk_notes': risks,
            'stock_count': len(stocks_data),
        }

    def _calc_force_rating(self, leader: dict, runners: list) -> tuple:
        """
        计算板块合力评级

        因子：
        - 龙一涨幅（涨停强度）
        - 龙二涨幅（跟风力度）
        - 板块上涨占比
        - 主力资金集中度
        - 涨停/大涨数量
        """
        leader_chg = leader.get('f3', 0) or 0

        # 因子1：龙一强度 0-30分
        score1 = min(30, leader_chg * 3)

        # 因子2：龙二跌幅 0-25分
        if len(runners) > 0:
            runner2_chg = max(0, runners[0].get('f3', 0) or 0)
            score2 = min(25, runner2_chg * 2.5)
        else:
            score2 = 0

        # 因子3：板块上涨占比 0-25分
        total = len(runners)
        if total > 0:
            up_count = sum(1 for s in runners if (s.get('f3', 0) or 0) > 0)
            up_ratio = up_count / total
            score3 = min(25, up_ratio * 25)
        else:
            score3 = 0

        # 因子4：涨停/大涨标的数量 0-20分
        limit_count = sum(1 for s in runners if (s.get('f3', 0) or 0) >= self.limit_up)
        strong_count = sum(1 for s in runners if (s.get('f3', 0) or 0) >= self.strong)
        score4 = min(20, limit_count * 10 + strong_count * 3)

        total_score = score1 + score2 + score3 + score4

        if total_score >= 70:
            rating = '🔥🔥🔥 强合力'
        elif total_score >= 50:
            rating = '🔥 合力形成'
        elif total_score >= 30:
            rating = '➖ 合力一般'
        else:
            rating = '❄️ 孤龙独舞'

        return round(total_score, 1), rating

    def _filter_candidates(self, leader: dict, runners: list, force_score: float) -> list:
        """
        筛选跟风候选标的 TOP3

        6维评分：
        - 市值弹性：小市值高分
        - 主力跟风：资金同步流入
        - 同层联动：与龙头同市值层加分
        - 概念纯度：默认0.5
        - 换手接力：换手率适中放大
        - 间距合理：跟风涨幅在龙头50-80%之间
        """
        leader_cap = (leader.get('f20', 0) or 0) / 1e8
        leader_tier = self._get_cap_tier(leader_cap)
        leader_chg = leader.get('f3', 0) or 0

        scored = []
        for s in runners:
            chg = s.get('f3', 0) or 0
            if chg <= 0:
                continue  # 排除下跌股

            cap = (s.get('f20', 0) or 0) / 1e8
            tier = self._get_cap_tier(cap)
            fund = (s.get('f62', 0) or 0) / 1e8
            amount = (s.get('f10', 0) or 0) / 1e4 if s.get('f10') else 0

            # 因子①：市值弹性 0-100
            max_cap = max(1, max((s2.get('f20', 0) or 0) / 1e8 for s2 in runners[:20]))
            cap_elasticity = min(100, (1 - cap / max_cap) * 100) if max_cap > 0 else 50

            # 因子②：主力跟风强度 0-100
            if fund > 0:
                fund_score = min(100, fund * 10)
            elif fund > -0.5:
                fund_score = 30  # 小幅流出但不多
            else:
                fund_score = 0   # 大幅流出的不参与

            # 因子③：同层联动 0-100
            tier_score = 100 if tier == leader_tier else 50

            # 因子④：概念纯度 0-100
            purity = fetch_concept_purity(s.get('f12', ''))
            purity_score = purity * 100

            # 因子⑤：换手接力意愿 0-100（换手率3-15%为健康区间）
            turnover = s.get('f38', 0) or 0
            if 3 <= turnover <= 15:
                turnover_score = 80
            elif 1 <= turnover < 3:
                turnover_score = 50
            elif turnover < 1:
                turnover_score = 20  # 无人接力
            else:
                turnover_score = 40  # 换手过大，分歧大

            # 因子⑥：龙一龙二间距合理性 0-100
            gap_ratio = chg / leader_chg if leader_chg > 0 else 0
            if 0.3 <= gap_ratio <= 0.8:
                gap_score = 100  # 跟风在龙头30-80%之间=健康
            elif 0.8 < gap_ratio <= 0.95:
                gap_score = 60   # 跟得太紧
            elif gap_ratio > 0.95:
                gap_score = 20   # 几乎追上=要变龙头
            else:
                gap_score = 40   # 跟风太弱

            # 综合评分
            total = (
                cap_elasticity * WEIGHTS['cap_elasticity'] +
                fund_score * WEIGHTS['fund_follow'] +
                tier_score * WEIGHTS['tier_cohesion'] +
                purity_score * WEIGHTS['concept_purity'] +
                turnover_score * WEIGHTS['turnover_momentum'] +
                gap_score * WEIGHTS['gap_to_leader']
            )

            scored.append({
                'name': s.get('f14', ''),
                'code': s.get('f12', ''),
                'change_pct': round(chg, 2),
                'market_cap': round(cap, 1),
                'cap_tier': tier,
                'fund_net': round(fund, 2),
                'turnover': turnover,
                'amount_wan': round(amount, 0),
                'cap_score': round(cap_elasticity, 1),
                'fund_score': round(fund_score, 1),
                'tier_score': round(tier_score, 1),
                'purity_score': round(purity_score, 1),
                'turnover_score': round(turnover_score, 1),
                'gap_score': round(gap_score, 1),
                'total_score': round(total, 1),
                'gap_ratio': round(gap_ratio, 2),
            })

        # 按总分排序，取TOP3
        scored.sort(key=lambda x: x['total_score'], reverse=True)
        top3 = scored[:3]

        # 添加买入建议
        for i, c in enumerate(top3):
            c['rank'] = i + 1
            if c['total_score'] >= 70:
                c['action'] = '🔥 买入'
                c['position'] = f'建议{30 - i * 8}%'
                c['timing'] = f'龙头封板后{BUY_WINDOW_MINUTES}分钟内介入'
                c['stop_loss'] = f'-{5 + i * 1}%'
            elif c['total_score'] >= 50:
                c['action'] = '🟢 关注'
                c['position'] = f'建议{15 - i * 3}%'
                c['timing'] = '等跟风涨幅缩窄至龙头60%以内再介入'
                c['stop_loss'] = '-6%'
            else:
                c['action'] = '👀 观察'
                c['position'] = '建议0%'
                c['timing'] = '板块合力不足，暂不参与'
                c['stop_loss'] = '-'

        return top3

    def _get_cap_tier(self, cap: float) -> str:
        """获取市值分层"""
        for name, lo, hi in CAP_TIERS:
            if lo <= cap < hi:
                return name
        return '微盘'

    def _assess_risks(self, leader: dict, candidates: list, gap_pct: float, force_score: float) -> list:
        """评估风险，返回风险提示列表"""
        risks = []
        leader_chg = leader.get('f3', 0) or 0

        if leader_chg >= 9.5 and gap_pct > 8:
            risks.append('⚠️ 孤龙独舞：龙头涨停但龙二涨幅<2%，板块跟风意愿弱')
        if force_score < 30:
            risks.append('⚠️ 板块合力不足：跟风标的大面积下跌')
        if leader_chg < 7:
            risks.append('⚠️ 龙头未涨停：不算严格意义上的龙头涨停，情绪尚未确认')
        if len(candidates) == 0:
            risks.append('❌ 无合格跟风标的')
        if gap_pct < 1:
            risks.append('⚠️ 龙二紧跟龙头，可能发生龙头切换，注意追高风险')

        return risks


# ============================================================
# 报告生成
# ============================================================

def generate_report(results: dict, sector_info: dict) -> str:
    """生成微信推送格式报告"""
    if 'error' in results:
        return f"❌ {sector_info.get('name', '')}: {results['error']}"

    lines = []
    name = sector_info.get('name', results.get('bk_code', ''))
    level = sector_info.get('level', '')

    # 头部
    lines.append(f"🌀 **引力面扫描 — {name}** ({level})")
    lines.append(f"   ⏰ {results['timestamp']}")
    lines.append("")

    # 板块合力
    force = results['force_rating']
    force_score = results['force_score']
    lines.append(f"   📊 板块合力: {force} ({force_score}分)")
    lines.append(f"   📈 板块成分股: {results['stock_count']}只")
    lines.append("")

    # 龙一信息
    ld = results['leader']
    gap = results['gap_pct']
    lines.append(f"   👑 **龙一: {ld['name']} ({ld['code']})**")
    lines.append(f"      涨幅: {ld['change_pct']:+.2f}%  |  市值: {ld['market_cap']:.0f}亿")
    lines.append(f"      主力: {ld['fund_net']:+.2f}亿  |  龙一龙二间距: {gap:+.2f}%")
    lines.append("")

    # 跟风候选
    candidates = results['follow_candidates']
    if candidates:
        lines.append(f"   🎯 **跟风候选 TOP{len(candidates)}**")
        lines.append(f"   {'':─<45s}")
        for c in candidates:
            lines.append(f"   {c['rank']}. {c['name']} ({c['code']})")
            lines.append(f"      涨幅: {c['change_pct']:+.2f}%  |  市值: {c['market_cap']:.0f}亿({c['cap_tier']})")
            lines.append(f"      主力: {c['fund_net']:+.2f}亿  |  换手: {c['turnover']:.1f}%")
            lines.append(f"      评分: {c['total_score']} {'⭐' * int(c['total_score'] // 20)}")
            lines.append(f"      {c['action']} | 仓位: {c['position']} | 止损: {c['stop_loss']}")
            lines.append(f"      ⏱ {c['timing']}")
            lines.append("")
    else:
        lines.append("   ❌ 无合格跟风标的")
        lines.append("")

    # 风险提示
    risks = results['risk_notes']
    if risks:
        lines.append("   ⚠️ **风险提示**")
        for r in risks:
            lines.append(f"   {r}")
        lines.append("")

    lines.append(f"   {'─'*40}")
    lines.append("   💡 引力面核心原则：上行期跟风做多，末期孤龙不追")
    lines.append("")

    return '\n'.join(lines)


# ============================================================
# 主入口
# ============================================================

def scan_all_sectors(limit_up: float = DEFAULT_LIMIT_UP) -> dict:
    """扫描所有配置板块"""
    engine = GravityEngine(limit_up_threshold=limit_up)
    all_results = {}

    for bk_code, info in TARGET_SECTORS.items():
        try:
            stocks = fetch_sector_stocks(bk_code)
            result = engine.analyze(bk_code, stocks)
            all_results[bk_code] = result
        except Exception as e:
            all_results[bk_code] = {'error': str(e)}

    return all_results


def main():
    import argparse

    parser = argparse.ArgumentParser(description='🌀 引力面量化模型')
    parser.add_argument('--bk', type=str, help='指定板块代码（如BK1127），不指定则全量扫描')
    parser.add_argument('--threshold', type=float, default=DEFAULT_LIMIT_UP,
                        help=f'涨停阈值(默认{DEFAULT_LIMIT_UP}%)')
    parser.add_argument('--full', action='store_true', help='输出详细因子评分')
    parser.add_argument('--watch', action='store_true', help='持续监控模式（每60秒）')
    parser.add_argument('--interval', type=int, default=60, help='监控间隔秒数(默认60)')
    args = parser.parse_args()

    if args.watch and not is_trading_time():
        print("❌ 非交易时段，监控模式无法启动")
        return

    if args.watch:
        print(f"🌀 引力面监控模式启动 (间隔{args.interval}秒)")
        print(f"   板块: {'指定' + args.bk if args.bk else '全部配置板块'}")
        print(f"   涨停阈值: {args.threshold}%")
        print(f"   {'─'*40}")
        print()

        engine = GravityEngine(limit_up_threshold=args.threshold)

        while is_trading_time():
            print(f"\n{'='*50}")
            print(f"  扫描时间: {datetime.now().strftime('%H:%M:%S')}")
            print(f"{'='*50}")

            if args.bk:
                bk_codes = {args.bk: TARGET_SECTORS.get(args.bk, {'name': args.bk, 'level': ''})}
            else:
                bk_codes = TARGET_SECTORS

            for bk_code, info in bk_codes.items():
                try:
                    stocks = fetch_sector_stocks(bk_code)
                    result = engine.analyze(bk_code, stocks)
                    report = generate_report(result, info)
                    print(report)
                except Exception as e:
                    print(f"❌ {info.get('name', bk_code)}: {e}")

            time.sleep(args.interval)
    else:
        # 单次扫描
        engine = GravityEngine(limit_up_threshold=args.threshold)

        if args.bk:
            info = TARGET_SECTORS.get(args.bk, {'name': args.bk, 'level': ''})
            try:
                stocks = fetch_sector_stocks(args.bk)
                result = engine.analyze(args.bk, stocks)
                report = generate_report(result, info)
                print(report)

                if args.full:
                    print("📋 **详细因子评分**")
                    print(f"{'─'*60}")
                    candidates = result.get('follow_candidates', [])
                    if candidates:
                        headers = ['排名', '名称', '总评', '市值弹性', '主力跟风', '同层联动', '换手接力', '间距合理']
                        print(f"  {' | '.join(headers)}")
                        for c in candidates:
                            vals = [str(c['rank']), c['name'], str(c['total_score']),
                                    str(c['cap_score']), str(c['fund_score']),
                                    str(c['tier_score']), str(c['turnover_score']),
                                    str(c['gap_score'])]
                            print(f"  {' | '.join(vals)}")
                    print()
            except Exception as e:
                print(f"❌ {info.get('name', args.bk)}: {e}")
        else:
            # 全量扫描
            print(f"🌀 引力面全量扫描 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            print(f"   涨停阈值: {args.threshold}%")
            print(f"   {'='*50}")
            print()

            has_signal = False
            for bk_code, info in TARGET_SECTORS.items():
                try:
                    stocks = fetch_sector_stocks(bk_code)
                    result = engine.analyze(bk_code, stocks)
                    if result.get('force_score', 0) >= 30:
                        has_signal = True
                    report = generate_report(result, info)
                    print(report)
                except Exception as e:
                    print(f"❌ {info.get('name', bk_code)}: {e}")

            if not has_signal:
                print("   📭 当前无板块触发引力信号")


if __name__ == '__main__':
    main()
