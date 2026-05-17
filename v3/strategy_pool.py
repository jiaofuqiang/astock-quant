"""
🧬 V3.1 策略池在线进化引擎
============================
核心原则：策略应该是活的——每天自动验证、淘汰、进化。

1. 从 pattern_miner 获取当天的新模式候选
2. 对每个模式在历史数据上跨股验证
3. 胜率>55% → 加入活跃策略池
4. 胜率连续7天<50% → 自动降级/淘汰
5. 策略池状态持久化到SQLite

这样系统不会死守4个策略，而是主动发现+进化。
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DATA_DIR = "/home/ubuntu/astock/data"
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "strategy_pool.db")

INIT_SQL = """
CREATE TABLE IF NOT EXISTS active_strategies (
    name       TEXT PRIMARY KEY,
    type       TEXT,           -- expert / discovered / auto
    description TEXT,
    buy_score  INTEGER,
    created_at TEXT,
    last_validated TEXT,
    status     TEXT DEFAULT 'active',  -- active / probation / retired
    
    -- 统计
    total_signals  INTEGER DEFAULT 0,
    total_wins     INTEGER DEFAULT 0,
    current_win_rate REAL DEFAULT 0,
    
    -- 7日滚动胜率
    day1_win_rate REAL,
    day2_win_rate REAL,
    day3_win_rate REAL,
    day4_win_rate REAL,
    day5_win_rate REAL,
    day6_win_rate REAL,
    day7_win_rate REAL,
    day7_signals INTEGER DEFAULT 0,
    
    -- 特征条件（JSON）
    conditions TEXT
);

CREATE TABLE IF NOT EXISTS daily_validation (
    date       TEXT,
    strategy_name TEXT,
    signals    INTEGER,
    wins       INTEGER,
    win_rate   REAL,
    avg_return REAL,
    PRIMARY KEY (date, strategy_name)
);

CREATE TABLE IF NOT EXISTS strategy_pool_log (
    timestamp TEXT,
    action    TEXT,    -- added / retired / promoted / demoted / validated
    strategy  TEXT,
    detail    TEXT
);
"""


class StrategyPool:
    """策略池——管理所有策略的生命周期"""

    def __init__(self):
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.executescript(INIT_SQL)
        conn.commit()
        conn.close()

    def _conn(self):
        return sqlite3.connect(DB_PATH)

    def _log(self, action: str, strategy: str, detail: str = ""):
        conn = self._conn()
        conn.execute(
            "INSERT INTO strategy_pool_log (timestamp, action, strategy, detail) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), action, strategy, detail)
        )
        conn.commit()
        conn.close()

    def register_strategy(self, name: str, strategy_type: str,
                           description: str, buy_score: int = 2,
                           conditions: Dict = None, status: str = "probation"):
        """注册一个新策略（默认可疑状态，需验证后才能激活）"""
        conn = self._conn()
        conn.execute("""
            INSERT OR REPLACE INTO active_strategies
            (name, type, description, buy_score, created_at, last_validated,
             status, conditions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, strategy_type, description, buy_score,
              datetime.now().isoformat(), datetime.now().isoformat(),
              status, json.dumps(conditions, ensure_ascii=False) if conditions else "{}"))
        conn.commit()
        conn.close()
        self._log("registered", name, f"type={strategy_type} score={buy_score}")
        return name

    def record_validation(self, name: str, signals: int, wins: int,
                           avg_return: float):
        """记录一次每日验证结果"""
        today = datetime.now().strftime("%Y-%m-%d")
        win_rate = wins / signals * 100 if signals > 0 else 0

        conn = self._conn()

        # 写入每日验证
        conn.execute("""
            INSERT OR REPLACE INTO daily_validation
            (date, strategy_name, signals, wins, win_rate, avg_return)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (today, name, signals, wins, round(win_rate, 1), round(avg_return, 2)))

        # 更新策略统计
        conn.execute("""
            UPDATE active_strategies SET
                total_signals = total_signals + ?,
                total_wins = total_wins + ?,
                current_win_rate = CASE WHEN total_signals + ? > 0
                    THEN (total_wins + ?) * 100.0 / (total_signals + ?) ELSE 0 END,
                last_validated = ?
            WHERE name = ?
        """, (signals, wins, signals, wins, signals, datetime.now().isoformat(), name))

        # 滚动7日：把新的塞进day1，旧数据往后推
        conn.execute("""
            UPDATE active_strategies SET
                day7_win_rate = day6_win_rate,
                day7_signals = COALESCE(day6_signals, 0),
                day6_win_rate = day5_win_rate,
                day5_win_rate = day4_win_rate,
                day4_win_rate = day3_win_rate,
                day3_win_rate = day2_win_rate,
                day2_win_rate = day1_win_rate,
                day1_win_rate = ?
            WHERE name = ?
        """, (round(win_rate, 1) if signals > 0 else None, name))
        conn.execute("""
            UPDATE active_strategies SET day7_signals = ? WHERE name = ?
        """, (signals if signals > 0 else 0, name))

        conn.commit()
        conn.close()

        # 自动评估是否需要淘汰
        self._evaluate_strategy(name)
        return win_rate

    def _evaluate_strategy(self, name: str):
        """评估策略是否需要降级/淘汰"""
        conn = self._conn()
        c = conn.execute("""
            SELECT status, day1_win_rate, day2_win_rate, day3_win_rate,
                   day4_win_rate, day5_win_rate, day6_win_rate, day7_win_rate,
                   day7_signals, total_signals
            FROM active_strategies WHERE name=?
        """, (name,))
        row = c.fetchone()
        conn.close()
        if not row:
            return

        status = row[0]
        wr7 = row[1:8]  # 最近7天胜率
        recent_signals = row[8] or 0
        total_signals = row[9] or 0

        # 过滤 None
        valid_wr = [w for w in wr7 if w is not None]

        if len(valid_wr) < 3 or recent_signals < 5:
            return  # 数据不足，不做判断

        avg_7d_wr = sum(valid_wr) / len(valid_wr)

        if status == "probation":
            # 考察期：数据足够后决定激活还是淘汰
            if total_signals >= 20:
                if avg_7d_wr >= 52:
                    conn = self._conn()
                    conn.execute(
                        "UPDATE active_strategies SET status='active' WHERE name=?",
                        (name,))
                    conn.commit()
                    conn.close()
                    self._log("promoted", name, f"avg_7d_wr={avg_7d_wr:.1f}% >= 52%")
                else:
                    conn = self._conn()
                    conn.execute(
                        "UPDATE active_strategies SET status='retired' WHERE name=?",
                        (name,))
                    conn.commit()
                    conn.close()
                    self._log("retired", name, f"avg_7d_wr={avg_7d_wr:.1f}% < 52%")

        elif status == "active":
            # 活跃策略：连续7天胜率<50% → 降级考察
            if avg_7d_wr < 45 and total_signals >= 30:
                conn = self._conn()
                conn.execute(
                    "UPDATE active_strategies SET status='probation' WHERE name=?",
                    (name,))
                conn.commit()
                conn.close()
                self._log("demoted", name, f"avg_7d_wr={avg_7d_wr:.1f}% < 45%")

    def get_active_strategies(self) -> List[Dict]:
        """获取所有活跃策略"""
        conn = self._conn()
        c = conn.execute("""
            SELECT name, type, description, buy_score, status,
                   current_win_rate, total_signals, conditions,
                   day1_win_rate, day2_win_rate, day3_win_rate,
                   day4_win_rate, day5_win_rate, day6_win_rate, day7_win_rate
            FROM active_strategies
            WHERE status IN ('active', 'probation')
            ORDER BY
                CASE status WHEN 'active' THEN 0 ELSE 1 END,
                current_win_rate DESC
        """)
        results = []
        for row in c.fetchall():
            wr7 = [row[8], row[9], row[10], row[11], row[12], row[13], row[14]]
            valid_wr7 = [w for w in wr7 if w is not None]
            results.append({
                "name": row[0],
                "type": row[1],
                "description": row[2],
                "buy_score": row[3],
                "status": row[4],
                "win_rate": row[5],
                "total_signals": row[6],
                "conditions": json.loads(row[7]) if row[7] else {},
                "rolling_7d_avg": round(sum(valid_wr7) / len(valid_wr7), 1) if valid_wr7 else 0,
            })
        conn.close()
        return results

    def get_retired_strategies(self) -> List[Dict]:
        """获取已淘汰策略"""
        conn = self._conn()
        c = conn.execute("""
            SELECT name, type, description, current_win_rate, total_signals
            FROM active_strategies
            WHERE status='retired'
            ORDER BY total_signals DESC
        """)
        results = [{"name": r[0], "type": r[1], "description": r[2],
                     "win_rate": r[3], "total_signals": r[4]} for r in c.fetchall()]
        conn.close()
        return results

    def seed_expert_patterns(self):
        """把专家模式注册到策略池"""
        from v3.pattern_miner import EXPERT_PATTERNS

        for pattern in EXPERT_PATTERNS:
            conds = {c[0]: f"{c[1]} {c[2]}" for c in pattern.conditions}
            self.register_strategy(
                name=pattern.name,
                strategy_type="expert",
                description=pattern.description,
                buy_score=pattern.buy_score,
                conditions=conds,
                status="active",  # 专家策略直接激活
            )

    def daily_validate_all(self, klines_by_code: Dict[str, List[Dict]]):
        """对所有活跃策略做每日跨股验证"""
        from v3.pattern_miner import evaluate_pattern, EXPERT_PATTERNS

        strategies = self.get_active_strategies()
        # 也包括新发现的模式（需要构造PatternRule）

        # 先验证专家模式（已知条件）
        for pattern in EXPERT_PATTERNS:
            total_signals = 0
            total_wins = 0
            total_return = 0.0

            for code, kline in klines_by_code.items():
                if len(kline) < 60:
                    continue
                signals = evaluate_pattern(kline, pattern)
                # 统计近5天的信号
                recent_signals = signals[-5:]
                for i, sig in enumerate(recent_signals):
                    if sig == 1:
                        idx = len(signals) - 5 + i
                        if idx + 5 < len(kline):
                            future_ret = (kline[idx+5]["close"] - kline[idx]["close"]) / kline[idx]["close"] * 100
                            total_signals += 1
                            if future_ret >= 2.0:
                                total_wins += 1
                            total_return += future_ret

            if total_signals > 0:
                self.record_validation(pattern.name, total_signals, total_wins, total_return / total_signals)

    def report(self) -> str:
        """生成策略池状态报告"""
        lines = []
        lines.append("🧬 **策略池状态**")
        lines.append("")

        active = self.get_active_strategies()
        if not active:
            lines.append("策略池为空")
            return "\n".join(lines)

        lines.append(f"**活跃 ({sum(1 for s in active if s['status']=='active')}) / 考察 ({sum(1 for s in active if s['status']=='probation')})**")
        lines.append("")

        for s in active:
            mark = "✅" if s["status"] == "active" else "🔄"
            lines.append(f"  {mark} **{s['name']}**")
            lines.append(f"    类型: {s['type']} | 胜率: {s['win_rate']:.1f}% | "
                         f"信号: {s['total_signals']} | 7日均值: {s['rolling_7d_avg']:.1f}%")
            lines.append(f"    {s['description']}")

        retired = self.get_retired_strategies()
        if retired:
            lines.append("")
            lines.append(f"**🗑️ 已淘汰 ({len(retired)}个):**")
            for s in retired[:5]:
                lines.append(f"  {s['name']}: 最终胜率{s['win_rate']:.1f}% ({s['total_signals']}次信号)")

        return "\n".join(lines)


# ============= 策略池集成到V3 =============

def integrate_strategy_pool():
    """将策略池集成到V3每日运行中"""
    pool = StrategyPool()

    # 首次运行时播种专家模式
    conn = pool._conn()
    c = conn.execute("SELECT COUNT(*) FROM active_strategies")
    count = c.fetchone()[0]
    conn.close()

    if count == 0:
        pool.seed_expert_patterns()
        print(f"🌱 播种 {8} 个专家策略")

    return pool


if __name__ == "__main__":
    print("🧬 策略池在线进化引擎")
    print("=" * 60)

    pool = StrategyPool()
    integrate_strategy_pool()

    # 查看状态
    print("\n当前策略池:")
    print(pool.report())

    print("\n✅ 策略池引擎就绪")
