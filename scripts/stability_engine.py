#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规律稳定性引擎 + 黑天鹅检测器 v1
"""

import os, json, sqlite3
from datetime import datetime, timedelta
from collections import deque

HOME = os.path.expanduser('~')
DATA = os.path.join(HOME, 'astock/data')
KLINE_DB = os.path.join(DATA, 'kline_cache.db')
LHB_DB = os.path.join(DATA, 'lhb_cache.db')


class RuleTracker:
    def __init__(self, name, baseline_wr=50, baseline_ret=0, n_baseline=100):
        self.name = name
        self.baseline_wr = baseline_wr
        self.baseline_ret = baseline_ret
        self.recent_returns = deque(maxlen=20)
        self.recent_wins = deque(maxlen=20)
        self.confidence = 80
        self.anomaly_score = 0

    def add_observation(self, ret, is_win=None):
        if is_win is None:
            is_win = 1 if ret > 0 else 0
        self.recent_returns.append(ret)
        self.recent_wins.append(is_win)
        if len(self.recent_returns) >= 10:
            recent_wr = sum(self.recent_wins) / len(self.recent_wins) * 100
            wr_diff = recent_wr - self.baseline_wr
            self.anomaly_score = max(0, -wr_diff / 10)
            if recent_wr < self.baseline_wr - 15:
                self.confidence = max(10, self.confidence - 15)
            elif recent_wr < self.baseline_wr - 8:
                self.confidence = max(30, self.confidence - 5)
            elif recent_wr > self.baseline_wr + 10:
                self.confidence = min(100, self.confidence + 5)
            self.confidence = max(10, min(100, self.confidence))

    def health_status(self):
        if self.confidence < 30: return '失效'
        if self.confidence < 50: return '偏弱'
        if self.confidence > 85: return '强'
        return '正常'


def run_check():
    rules = {
        '宁波帮买入': RuleTracker('宁波帮买入', baseline_wr=80.8, baseline_ret=4.90),
        '上海静安新闸路': RuleTracker('上海静安新闸路', baseline_wr=74.1, baseline_ret=4.22),
        '冰点+北向独买': RuleTracker('冰点+北向独买', baseline_wr=68.5, baseline_ret=2.77),
        '华泰总部买入': RuleTracker('华泰总部买入', baseline_wr=60.0, baseline_ret=2.45),
        '量化高换手_150%': RuleTracker('量化高换手_150%', baseline_wr=63.9, baseline_ret=2.54),
        '龙虎榜后接力': RuleTracker('龙虎榜后接力', baseline_wr=56.9, baseline_ret=1.43),
        '拉萨买入回避': RuleTracker('拉萨买入回避', baseline_wr=41.5, baseline_ret=-1.14),
        '沪股通买入': RuleTracker('沪股通买入', baseline_wr=48.1, baseline_ret=0.24),
        '深股通买入': RuleTracker('深股通买入', baseline_wr=44.9, baseline_ret=-0.38),
    }

    conn = None
    try:
        conn = sqlite3.connect(f'file:{KLINE_DB}?mode=ro', uri=True, timeout=5)
        lconn = sqlite3.connect(f'file:{LHB_DB}?mode=ro', uri=True, timeout=5)

        cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        deals = lconn.execute("""
            SELECT l.date, l.code, d.dealer, d.buy_amt
            FROM lhb_list l JOIN lhb_detail d ON l.date=d.date AND l.code=d.code
            WHERE l.name NOT LIKE '%%ST%%' AND l.date >= ?
              AND l.code NOT LIKE '300%%' AND l.code NOT LIKE '920%%'
            ORDER BY l.date DESC LIMIT 100
        """, (cutoff,)).fetchall()
        lconn.close()

        for date, code, dealer, buy_amt in deals:
            if buy_amt <= 0: continue
            next_date = (datetime.strptime(date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            t1 = conn.execute("SELECT close FROM kline WHERE code=? AND date=?", (code, next_date)).fetchone()
            if not t1: continue
            lc = conn.execute("SELECT close FROM kline WHERE code=? AND date=?", (code, date)).fetchone()
            if not lc: continue
            ret = (t1[0] - lc[0]) / lc[0] * 100
            if '沪股通' in dealer: rules['沪股通买入'].add_observation(ret)
            elif '深股通' in dealer: rules['深股通买入'].add_observation(ret)
            elif '拉萨' in dealer: rules['拉萨买入回避'].add_observation(ret)
            elif '宁波' in dealer: rules['宁波帮买入'].add_observation(ret)
    except Exception as e:
        print(f"DB error: {e}")
    finally:
        if conn: conn.close()

    avg_conf = sum(r.confidence for r in rules.values()) / len(rules)
    anomalies = sum(1 for r in rules.values() if r.anomaly_score > 2)

    print("=" * 60)
    print(f"规律稳定性检测 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if avg_conf < 40:
        print(f"\n黑天鹅警告! 平均置信度{avg_conf:.0f}%, {anomalies}个异常")
        print(f"建议: 切换到生存模式, 减仓至10%以下, 停用买入策略")
    elif avg_conf < 60:
        print(f"\n市场偏弱! 平均置信度{avg_conf:.0f}%, {anomalies}个异常")
        print(f"建议: 减仓至30%, 只做最强信号")
    else:
        print(f"\n市场正常 (平均置信度{avg_conf:.0f}%)")

    print(f"\n{'规律':<20} {'状态':<8} {'置信度':<8} {'异常分':<8}")
    print("-" * 48)
    for name, rule in sorted(rules.items(), key=lambda x: x[1].confidence):
        s = rule.health_status()
        icon = {'强': 'G', '正常': 'Y', '偏弱': 'O', '失效': 'R'}.get(s, '?')
        print(f"  {name:<18} {icon}{s:<5} {rule.confidence:>3.0f}%   {rule.anomaly_score:.2f}")

    return rules


if __name__ == '__main__':
    run_check()
