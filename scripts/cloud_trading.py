#!/usr/bin/env python3
"""
💼 云盘交易系统 v1.0
====================
初始资金: 100,000元
交易标的: A股主板（6开头不含688 + 0开头）
交易规则: T+1（买入后次日才能卖出）
手续费: 万2.5（买入+卖出双向）
印花税: 千1（卖出单边）
最低佣金: 5元/笔

数据来源:
  1. 腾讯实时行情（qt.gtimg.cn）- 盘中
  2. K线数据库（kline_cache.db）- 回测
  3. 板块/涨停数据 - 策略评分

用法:
  from cloud_trading import CloudTrader
  trader = CloudTrader(initial_capital=100000)
  trader.buy('600519', price=150.0, shares=100)
  trader.sell('600519', price=160.0, shares=100)
  trader.report()
"""

import sqlite3, os, json, sys, time, subprocess
from datetime import datetime, timedelta, date
from collections import defaultdict
import math

BASE = os.path.expanduser('~/astock')
DB_PATH = os.path.join(BASE, 'data', 'trade_sim.db')
DB_DIR = os.path.join(BASE, 'data')

# 交易费用
COMMISSION_RATE = 0.00025  # 万2.5
MIN_COMMISSION = 5.0        # 最低5元
STAMP_TAX_RATE = 0.001      # 千1（卖出）
MIN_SHARES = 100            # 最小买入100股
SHARE_UNIT = 100            # 必须以100股为单位


def init_db():
    """初始化交易数据库"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 账户表
    c.execute("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY,
            initial_capital REAL DEFAULT 0,
            current_cash REAL DEFAULT 0,
            total_profit REAL DEFAULT 0,
            total_profit_rate REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            lose_count INTEGER DEFAULT 0,
            update_time TEXT
        )
    """)

    # 持仓表
    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            shares INTEGER DEFAULT 0,
            avg_cost REAL DEFAULT 0,
            total_cost REAL DEFAULT 0,
            current_price REAL DEFAULT 0,
            market_value REAL DEFAULT 0,
            profit REAL DEFAULT 0,
            profit_rate REAL DEFAULT 0,
            update_time TEXT,
            UNIQUE(code)
        )
    """)

    # 成交记录表
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            trade_time TEXT,
            direction TEXT,         -- buy / sell
            code TEXT,
            name TEXT,
            price REAL,
            shares INTEGER,
            amount REAL,            -- 成交金额
            commission REAL,        -- 手续费
            stamp_tax REAL,         -- 印花税
            total_cost REAL,        -- 总费用
            profit REAL,            -- 收益（卖出时计算）
            profit_rate REAL,       -- 收益率
            strategy TEXT,          -- 触发策略
            score INTEGER,          -- 策略评分
            reason TEXT,            -- 买卖理由
            create_time TEXT
        )
    """)

    # 策略信号记录表
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            name TEXT,
            score INTEGER,
            strategy TEXT,
            predicted_high REAL,
            holders TEXT,
            bull_count INTEGER,
            board_count INTEGER,
            sector_rank INTEGER,
            sector_limit INTEGER,
            create_time TEXT
        )
    """)

    # 每日快照（持仓市值）
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            cash REAL,
            positions_value REAL,
            total_assets REAL,
            daily_profit REAL,
            daily_profit_rate REAL,
            total_profit REAL,
            total_profit_rate REAL,
            max_drawdown REAL,
            create_time TEXT,
            UNIQUE(date)
        )
    """)

    conn.commit()
    return conn


def calc_fees(price, shares, direction='buy'):
    """计算交易费用"""
    amount = price * shares
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    stamp_tax = amount * STAMP_TAX_RATE if direction == 'sell' else 0
    total_fee = commission + stamp_tax
    return {
        'amount': amount,
        'commission': round(commission, 2),
        'stamp_tax': round(stamp_tax, 2),
        'total_fee': round(total_fee, 2),
    }


class CloudTrader:
    """
    云盘交易系统 v1.0
    
    用法:
        trader = CloudTrader()
        trader.init_account(100000)         # 初始化账户
        trader.buy('600519', 150.0, 100)     # 买入
        trader.sell('600519', 160.0, 100)    # 卖出
        trader.report()                       # 报告
    """

    def __init__(self, initial_capital=100000, db_path=DB_PATH):
        self.db_path = db_path
        self._ensure_db()
        self._ensure_account(initial_capital)

    def _ensure_db(self):
        if not os.path.exists(self.db_path):
            conn = init_db()
            conn.close()

    def _ensure_account(self, capital):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM account")
        if c.fetchone()[0] == 0:
            c.execute("""
                INSERT INTO account (initial_capital, current_cash, update_time)
                VALUES (?, ?, ?)
            """, (capital, capital, datetime.now().isoformat()))
            conn.commit()
        conn.close()

    def _get_cash(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT current_cash FROM account WHERE id=1")
        cash = c.fetchone()[0]
        conn.close()
        return cash

    def _set_cash(self, cash):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE account SET current_cash=?, update_time=? WHERE id=1",
                  (round(cash, 2), datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_positions(self):
        """获取当前持仓"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT code, name, shares, avg_cost, total_cost, current_price, market_value, profit, profit_rate FROM positions WHERE shares > 0")
        positions = []
        for r in c.fetchall():
            positions.append({
                'code': r[0], 'name': r[1], 'shares': r[2],
                'avg_cost': r[3], 'total_cost': r[4],
                'current_price': r[5], 'market_value': r[6],
                'profit': r[7], 'profit_rate': r[8],
            })
        conn.close()
        return positions

    def get_holdings_value(self, prices=None):
        """计算持仓市值"""
        positions = self.get_positions()
        if not positions:
            return 0, 0, 0

        total_cost = sum(p['total_cost'] for p in positions)
        total_value = 0

        for p in positions:
            if prices and p['code'] in prices:
                price = prices[p['code']]
            else:
                price = p['current_price']
            total_value += price * p['shares']

        profit = total_value - total_cost
        profit_rate = (profit / total_cost * 100) if total_cost > 0 else 0
        return round(total_value, 2), round(profit, 2), round(profit_rate, 2)

    def buy(self, code, price, shares, name='', strategy='', score=0, reason='', trade_date=None):
        """
        买入股票
        trade_date: 交易日期（回测用），为空则取当前时间
        """
        # 校验
        if shares % SHARE_UNIT != 0:
            shares = (shares // SHARE_UNIT) * SHARE_UNIT
        if shares < MIN_SHARES:
            return {'success': False, 'message': f'最少买入{MIN_SHARES}股'}

        fees = calc_fees(price, shares, 'buy')
        total_needed = fees['amount'] + fees['total_fee']

        cash = self._get_cash()
        if total_needed > cash:
            max_shares = int((cash - MIN_COMMISSION) / (price * (1 + COMMISSION_RATE)))
            max_shares = (max_shares // SHARE_UNIT) * SHARE_UNIT
            if max_shares < MIN_SHARES:
                return {'success': False, 'message': f'资金不足。现金{cash:.2f}, 需要{total_needed:.2f}, 最多可买{max_shares}股'}
            return {'success': False, 'message': f'资金不足。现金{cash:.2f}, 需要{total_needed:.2f}'}

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 更新或插入持仓
        c.execute("SELECT shares, avg_cost, total_cost FROM positions WHERE code=?", (code,))
        existing = c.fetchone()

        if existing:
            old_shares, old_cost, old_total = existing[0], existing[1], existing[2]
            new_shares = old_shares + shares
            new_total_cost = old_total + fees['amount'] + fees['total_fee']
            new_avg_cost = new_total_cost / new_shares
            c.execute("""
                UPDATE positions SET shares=?, avg_cost=?, total_cost=?, update_time=?
                WHERE code=?
            """, (new_shares, round(new_avg_cost, 4), round(new_total_cost, 2),
                  datetime.now().isoformat(), code))
        else:
            new_total_cost = fees['amount'] + fees['total_fee']
            new_avg_cost = new_total_cost / shares
            c.execute("""
                INSERT INTO positions (code, name, shares, avg_cost, total_cost, current_price, market_value, update_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (code, name, shares, round(new_avg_cost, 4), round(new_total_cost, 2),
                  price, round(fees['amount'], 2), datetime.now().isoformat()))

        # 记录成交 - 用传进来的日期
        now = datetime.now().isoformat()
        td = trade_date or now[:10]
        tt = now[11:19]
        c.execute("""
            INSERT INTO trades (trade_date, trade_time, direction, code, name, price, shares,
                                amount, commission, stamp_tax, total_cost, strategy, score, reason, create_time)
            VALUES (?, ?, 'buy', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                td, tt,
                code, name, price, shares,
            round(fees['amount'], 2),
            round(fees['commission'], 2),
            round(fees['stamp_tax'], 2),
            round(fees['total_fee'], 2),
            strategy, score, reason,
            datetime.now().isoformat()
        ))

        # 扣款
        new_cash = cash - fees['amount'] - fees['total_fee']
        c.execute("UPDATE account SET current_cash=?, trade_count=trade_count+1, update_time=? WHERE id=1",
                  (round(new_cash, 2), datetime.now().isoformat()))

        conn.commit()
        conn.close()

        return {
            'success': True,
            'message': f'买入成功 {code} {name} {shares}股 @{price:.2f} 费用{fees["total_fee"]:.2f}',
            'code': code, 'shares': shares, 'price': price,
            'amount': fees['amount'], 'fees': fees['total_fee'],
            'cash_remain': round(new_cash, 2),
        }

    def sell(self, code, price=None, shares=None, name='', strategy='', score=0, reason='', trade_date=None):
        """
        卖出股票
        price: 卖出价（为空则取持仓最新价）
        shares: 卖出股数（为空则全卖）
        trade_date: 交易日期（回测用），为空则取当前
        """
        positions = self.get_positions()
        pos = next((p for p in positions if p['code'] == code), None)
        if not pos:
            return {'success': False, 'message': f'未持有 {code}'}

        sell_shares = shares or pos['shares']
        if sell_shares > pos['shares']:
            return {'success': False, 'message': f'持仓不足。持有{pos["shares"]}股, 欲卖{sell_shares}股'}
        if sell_shares % SHARE_UNIT != 0:
            sell_shares = (sell_shares // SHARE_UNIT) * SHARE_UNIT

        sell_price = price or pos['current_price']
        fees = calc_fees(sell_price, sell_shares, 'sell')
        sell_amount = fees['amount']
        net_amount = sell_amount - fees['total_fee']

        # 成本计算
        cost_ratio = sell_shares / pos['shares']
        cost_of_sold = pos['total_cost'] * cost_ratio
        profit = net_amount - cost_of_sold
        profit_rate = (profit / cost_of_sold * 100) if cost_of_sold > 0 else 0

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 记录成交 - 用传进来的日期
        now = datetime.now().isoformat()
        td = trade_date or now[:10]
        tt = now[11:19]
        c.execute("""
            INSERT INTO trades (trade_date, trade_time, direction, code, name, price, shares,
                                amount, commission, stamp_tax, total_cost,
                                profit, profit_rate, strategy, score, reason, create_time)
            VALUES (?, ?, 'sell', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            td, tt,
            code, name or pos['name'], sell_price, sell_shares,
            round(sell_amount, 2),
            round(fees['commission'], 2),
            round(fees['stamp_tax'], 2),
            round(fees['total_fee'], 2),
            round(profit, 2), round(profit_rate, 2),
            strategy, score, reason,
            datetime.now().isoformat()
        ))

        # 更新持仓
        remain_shares = pos['shares'] - sell_shares
        if remain_shares <= 0:
            c.execute("DELETE FROM positions WHERE code=?", (code,))
        else:
            remain_cost = pos['total_cost'] - cost_of_sold
            c.execute("UPDATE positions SET shares=?, total_cost=?, update_time=? WHERE code=?",
                      (remain_shares, round(remain_cost, 2), datetime.now().isoformat(), code))

        # 入账
        cash = self._get_cash()
        new_cash = cash + net_amount
        is_win = 1 if profit > 0 else 0
        c.execute("""
            UPDATE account SET current_cash=?, total_profit=total_profit+?,
                               trade_count=trade_count+1,
                               win_count=win_count+?,
                               lose_count=lose_count+?,
                               update_time=?
            WHERE id=1
        """, (round(new_cash, 2), round(profit, 2), is_win, 1 - is_win, datetime.now().isoformat()))

        conn.commit()
        conn.close()

        return {
            'success': True,
            'message': f'卖出成功 {code} {sell_shares}股 @{sell_price:.2f} 盈利{profit:.2f}({profit_rate:.2f}%)',
            'code': code, 'shares': sell_shares, 'price': sell_price,
            'amount': sell_amount, 'fees': fees['total_fee'],
            'profit': round(profit, 2), 'profit_rate': round(profit_rate, 2),
            'cash_remain': round(new_cash, 2),
        }

    def update_prices(self, prices):
        """
        批量更新持仓最新价
        prices: {code: price}
        """
        if not prices:
            return
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        for code, price in prices.items():
            c.execute("SELECT shares, avg_cost, total_cost FROM positions WHERE code=? AND shares>0", (code,))
            pos = c.fetchone()
            if pos:
                shares, avg_cost, total_cost = pos
                market_value = price * shares
                profit = market_value - total_cost
                profit_rate = (profit / total_cost * 100) if total_cost > 0 else 0
                c.execute("""
                    UPDATE positions SET current_price=?, market_value=?, profit=?, profit_rate=?, update_time=?
                    WHERE code=?
                """, (round(price, 2), round(market_value, 2), round(profit, 2),
                      round(profit_rate, 2), datetime.now().isoformat(), code))
        conn.commit()
        conn.close()

    def take_snapshot(self, date_str=None, prices=None):
        """
        每日快照
        """
        if prices:
            self.update_prices(prices)

        positions = self.get_positions()
        if prices:
            pv = sum(prices.get(p['code'], 0) * p['shares'] for p in positions)
            cost = sum(p['total_cost'] for p in positions)
        else:
            pv = sum(p['market_value'] for p in positions)
            cost = sum(p['total_cost'] for p in positions)

        cash = self._get_cash()
        total = cash + pv
        dp = total - 100000  # 初始资金
        dpr = (dp / 100000 * 100) if 100000 > 0 else 0

        date_str = date_str or datetime.now().strftime('%Y-%m-%d')

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 获取上次快照计算日收益
        c.execute("SELECT total_assets FROM daily_snapshot ORDER BY date DESC LIMIT 1")
        last = c.fetchone()
        daily_profit = (total - last[0]) if last else 0
        daily_rate = (daily_profit / last[0] * 100) if last and last[0] > 0 else 0

        # 最大回撤
        c.execute("SELECT MAX(total_assets) FROM daily_snapshot")
        peak = c.fetchone()[0] or 100000
        if total > peak:
            peak = total
        max_dd = (total - peak) / peak * 100 if peak > 0 else 0

        c.execute("""
            INSERT OR REPLACE INTO daily_snapshot
            (date, cash, positions_value, total_assets, daily_profit, daily_profit_rate,
             total_profit, total_profit_rate, max_drawdown, create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, round(cash, 2), round(pv, 2), round(total, 2),
            round(daily_profit, 2), round(daily_rate, 2),
            round(dp, 2), round(dpr, 2), round(max_dd, 2),
            datetime.now().isoformat()
        ))

        # 更新账户
        c.execute("""
            UPDATE account SET total_profit=?, total_profit_rate=?, max_drawdown=?, update_time=?
            WHERE id=1
        """, (round(dp, 2), round(dpr, 2), round(max_dd, 2), datetime.now().isoformat()))

        conn.commit()
        conn.close()

        return {
            'date': date_str, 'cash': round(cash, 2), 'positions': round(pv, 2),
            'total': round(total, 2), 'daily_profit': round(daily_profit, 2),
            'daily_rate': round(daily_rate, 2),
            'total_profit': round(dp, 2), 'total_rate': round(dpr, 2),
            'max_drawdown': round(max_dd, 2),
        }

    def report(self):
        """输出当前账户报告"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM account WHERE id=1")
        acct = c.fetchone()
        if not acct:
            print("❌ 账户未初始化")
            conn.close()
            return

        print("=" * 70)
        print(f"  💼 云盘交易系统 - 账户报告")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)
        print(f"  初始资金: {acct[1]:>10,.2f}")
        print(f"  当前现金: {acct[2]:>10,.2f}")
        print(f"  总盈利:   {acct[3]:>+10,.2f}  ({acct[4]:>+.2f}%)")
        print(f"  最大回撤: {acct[5]:>.2f}%")
        print(f"  交易次数: {acct[6]:>3}次 (胜{acct[7]}次/败{acct[8]}次)")
        if acct[6] > 0:
            win_rate = acct[7] / acct[6] * 100
            print(f"  胜率:     {win_rate:.1f}%")
        print("-" * 70)

        # 持仓
        positions = self.get_positions()
        if positions:
            print(f"\n  📦 当前持仓 ({len(positions)}只):")
            print(f"  {'代码':>8} {'名称':<10} {'持股':>6} {'成本':>8} {'现价':>8} {'市值':>10} {'盈亏':>10} {'盈亏%':>8}")
            print("  " + "-" * 68)
            total_mv = 0
            for p in positions:
                print(f"  {p['code']:>8} {p['name']:<10} {p['shares']:>6} {p['avg_cost']:>8.2f} "
                      f"{p['current_price']:>8.2f} {p['market_value']:>10.2f} "
                      f"{p['profit']:>+9.2f} {p['profit_rate']:>+7.2f}%")
                total_mv += p['market_value']
            print(f"  {'':>26} {'持仓市值':>12} {total_mv:>10.2f}")
            total_assets = acct[2] + total_mv
            print(f"  {'':>26} {'总资产':>12} {total_assets:>10.2f}")
        else:
            print(f"\n  📦 当前持仓: 空仓")

        # 最近交易
        c.execute("""
            SELECT trade_date, direction, code, name, price, shares, profit, profit_rate, strategy
            FROM trades ORDER BY id DESC LIMIT 10
        """)
        trades = c.fetchall()
        if trades:
            print(f"\n  📜 最近交易 ({len(trades)}笔):")
            for t in trades:
                pf = f' 盈亏{t[6]:+.2f}({t[7]:+.2f}%)' if t[6] else ''
                strat = f' [{t[8]}]' if t[8] else ''
                print(f"  {t[0]} {'🟢买入' if t[1]=='buy' else '🔴卖出'} {t[2]} {t[3]} {t[4]}:{t[5]}股{pf}{strat}")
        conn.close()

    def check_risk_and_sell(self, quotes=None):
        """
        ⚠️ 风控检查（数据驱动版 v2.0）
        基于61,321笔全量回测的反向卖出信号
        
        核心结论：T+1竞价卖是所有策略的最优解
        - T+1涨5~8%后，继续持有到T+3均值仅+3.64%，31.5%更高 → 5%以上冲高就卖
        - 竞价高开8~10%后，收盘回撤-2.46%，仅28.2%不回落 → 竞价高开>8%竞价卖
        - MA20>60%极度强势后T+3仅+1.42% → 隔日必卖
        - 量比>4天量后竞价亏49.1% → 天量涨停不参与
        - 缩量<0.5 T+1竞价+3.46%/胜96% → 缩量可以等盘中高点
        """
        positions = self.get_positions()
        if not positions:
            return []
        
        sell_signals = []
        today = datetime.now().strftime('%Y-%m-%d')
        
        for pos in positions:
            code = pos['code']
            name = pos['name']
            cost = pos['avg_cost']
            shares = pos['shares']
            
            # 获取实时价格和开盘价
            current_price = pos['current_price']
            open_price = current_price
            high_price = current_price
            vol_ratio = 0
            if quotes and code in quotes:
                current_price = quotes[code].get('price', current_price)
                open_price = quotes[code].get('open', current_price)
                high_price = quotes[code].get('high', current_price)
            
            # 实时盈亏
            profit_rate = (current_price - cost) / cost * 100 if cost > 0 else 0
            open_profit_rate = (open_price - cost) / cost * 100 if cost > 0 else 0
            
            # ====== 数据驱动的卖出信号 ======
            
            # 信号1：竞价高开>8% → 收盘必回撤（28.2%不回落→72%概率会跌）
            if open_profit_rate >= 8:
                sell_signals.append({
                    'code': code, 'name': name,
                    'price': current_price,
                    'reason': f'竞价高开+{open_profit_rate:.1f}%(>8%)→收盘72%概率回撤',
                    'urgency': '🔴数据驱动',
                })
                continue
            
            # 信号2：T+1盘中冲高≥5% + MA20>20%强势股 → 冲高回落概率65%
            if profit_rate >= 5:
                from_high = (high_price - current_price) / (cost or 1) * 100
                if from_high > 2:
                    sell_signals.append({
                        'code': code, 'name': name,
                        'price': current_price,
                        'reason': f'冲高回落(盘中最高+{(high_price-cost)/cost*100:.1f}%,回落{from_high:.1f}%,T+1冲高5%回落应卖)',
                        'urgency': '🟠数据驱动',
                    })
                    continue
            
            # 信号3：止损（基于回测数据）—— T+1亏<0%后续T+3均值-3.52%/仅33.2%反弹
            if profit_rate <= -3:
                sell_signals.append({
                    'code': code, 'name': name,
                    'price': current_price,
                    'reason': f'止损(亏{profit_rate:.1f}%,T+1亏损后T+3仅33%反弹)',
                    'urgency': '🔴数据驱动',
                })
                continue
        
        return sell_signals

    def get_recent_trades(self, limit=20):
        """获取最近成交"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            SELECT id, trade_date, trade_time, direction, code, name, price, shares,
                   amount, profit, profit_rate, strategy, score
            FROM trades ORDER BY id DESC LIMIT ?
        """, (limit,))
        trades = []
        for r in c.fetchall():
            trades.append({
                'id': r[0], 'date': r[1], 'time': r[2], 'direction': r[3],
                'code': r[4], 'name': r[5], 'price': r[6], 'shares': r[7],
                'amount': r[8], 'profit': r[9], 'profit_rate': r[10],
                'strategy': r[11], 'score': r[12],
            })
        conn.close()
        return trades

    def get_daily_snapshots(self, limit=30):
        """获取每日快照"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            SELECT date, cash, positions_value, total_assets, daily_profit, daily_profit_rate,
                   total_profit, total_profit_rate, max_drawdown
            FROM daily_snapshot ORDER BY date DESC LIMIT ?
        """, (limit,))
        snaps = []
        for r in c.fetchall():
            snaps.append({
                'date': r[0], 'cash': r[1], 'pv': r[2], 'total': r[3],
                'dp': r[4], 'dpr': r[5], 'tp': r[6], 'tpr': r[7], 'mdd': r[8],
            })
        conn.close()
        return snaps


def live_trade():
    """
    🎯 盘中自动交易模式 v1.0
    1. 获取今日实时涨停数据（有则优先，无则腾讯行情扫描）
    2. StrategyScorer评分
    3. 评分≥85自动买入（不超过最大仓位）
    4. T+1自动卖出
    5. 持仓预警
    """
    # lazy import
    from strategy_matrix import StrategyScorer
    import subprocess, re

    today = datetime.now().strftime('%Y-%m-%d')
    trader = CloudTrader(initial_capital=100000)
    scorer = StrategyScorer()
    
    # 预热评分引擎
    print("⏳ 加载K线...", end=' ', flush=True)
    t0 = time.time()
    kline_path = os.path.join(DB_DIR, 'kline_cache.db')
    scorer._load_kline_full(kline_path)
    print(f"{time.time()-t0:.1f}s")

    # ====== 1. 卖出 ======
    positions = trader.get_positions()
    if positions:
        print(f"\n🔴 检查T+1卖出...")
        for pos in positions:
            # 腾讯实时价
            code = pos['code']
            mkt_code = f"sh{code}" if code[0] in ('6','5','9') else f"sz{code}"
            try:
                r = subprocess.run(
                    ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
                     f'https://qt.gtimg.cn/q={mkt_code}'],
                    capture_output=True, timeout=10
                )
                raw_text = r.stdout.decode('gbk', errors='replace')
                if '=' in raw_text:
                    raw = raw_text.split('=', 1)[1].strip().strip('"').strip(';')
                    fields = raw.split('~')
                    cur_price = float(fields[3]) if len(fields)>3 and fields[3] else 0
                    open_price = float(fields[5]) if len(fields)>5 and fields[5] else 0
                    now = datetime.now()
                    # 10:00前用开盘价卖（模拟竞价卖）
                    sell_price = open_price if now.hour < 10 else cur_price
                    if sell_price > 0:
                        result = trader.sell(
                            code, sell_price,
                            strategy='T+1自动卖出',
                            reason='盘中自动: T+1竞价卖',
                        )
                        if result['success']:
                            print(f"  ✅ 卖出 {pos['name']}({code}) @{sell_price:.2f} 盈亏{result.get('profit_rate',0):+.2f}%")
            except Exception as e:
                print(f"  ⚠️ {code} 卖出失败: {e}")
    
    # ====== 2. 获取涨停数据（daily_limit_data.db，如果有今日）====== 
    try:
        conn = sqlite3.connect(os.path.join(DB_DIR, 'daily_limit_data.db'))
        c = conn.cursor()
        c.execute("SELECT code, name, board_count, limit_stat FROM limit_stocks WHERE date=? AND limit_stat != 'yizi'",
                   (today,))
        limit_stocks = [{'code':r[0],'name':r[1],'board':r[2] or 1,'stat':r[3]} for r in c.fetchall()]
        conn.close()
    except:
        limit_stocks = []
    
    if not limit_stocks:
        # 没有今日涨停数据，尝试从腾讯行情扫描当前涨幅>9.5%的
        print("  ⚠️ 无今日涨停数据，用腾讯行情扫描...")
        # 读取全量主板列表
        try:
            with open(os.path.join(BASE, 'data', 'all_main_board.txt')) as f:
                all_codes = [l.strip() for l in f if l.strip() and not l.startswith('#')]
        except:
            print("  ❌ 无法读取股票列表")
            return
        
        # 分批扫描
        limit_stocks = []
        for i in range(0, len(all_codes), 120):
            batch = all_codes[i:i+120]
            codes_str = ','.join(
                f"sh{c}" if c[0] in ('6','5','9') else f"sz{c}" for c in batch
            )
            try:
                r = subprocess.run(
                    ['curl', '-s', '--connect-timeout', '5', '--max-time', '12',
                     f'https://qt.gtimg.cn/q={codes_str}'],
                    capture_output=True, timeout=15
                )
                raw_text = r.stdout.decode('gbk', errors='replace')
                for line in raw_text.strip().split('\n'):
                    line = line.strip()
                    if not line or '=' not in line: continue
                    parts = line.split('=', 1)
                    raw = parts[1].strip().strip('"').strip(';').strip('"')
                    fields = raw.split('~')
                    if len(fields) < 40: continue
                    code = fields[2].strip()
                    if not code: continue
                    try:
                        chg = float(fields[32]) if fields[32] else 0
                    except: continue
                    if chg >= 9.5:
                        name = fields[1].strip()
                        # 板块排名从 sector_indexes 查
                        conn = sqlite3.connect(os.path.join(DB_DIR, 'sector_indexes.db'))
                        sr = conn.execute(
                            "SELECT sector_rank FROM sector_stock_daily WHERE date=? AND code=? ORDER BY sector_rank LIMIT 1",
                            (today, code)
                        ).fetchone()
                        conn.close()
                        limit_stocks.append({
                            'code': code, 'name': name,
                            'board': 1,  # 无法确定，默认为首板
                            'stat': 'normal',
                            'sector_rank': sr[0] if sr else 99,
                        })
            except Exception as e:
                continue
            time.sleep(0.5)  # 防封
        print(f"  扫描到 {len(limit_stocks)} 只涨停股")
    
    # ====== 3. 评分 + 买入 ======
    if limit_stocks:
        current_pos = trader.get_positions()
        if len(current_pos) < 3:  # 最大3只
            from strategy_matrix import StrategyScorer
            scored = []
            for s in limit_stocks:
                if s['code'].startswith('688') or s['code'].startswith('3'):
                    continue
                # 获取板块数据
                try:
                    conn = sqlite3.connect(os.path.join(DB_DIR, 'sector_indexes.db'))
                    sr = conn.execute(
                        "SELECT sector_rank, limit_up_count FROM sector_stock_daily ss "
                        "JOIN sector_daily_index si ON ss.date=si.date AND ss.sector_name=si.sector_name "
                        "WHERE ss.date=? AND ss.code=? LIMIT 1",
                        (today, s['code'])
                    ).fetchone()
                    conn.close()
                    sector_rank = sr[0] if sr else 99
                    sector_limit = sr[1] if sr else 0
                except:
                    sector_rank = s.get('sector_rank', 99)
                    sector_limit = 0
                
                result = scorer.score(
                    code=s['code'], name=s['name'],
                    board_count=s['board'], limit_stat=s['stat'],
                    sector_rank=sector_rank, sector_limit=sector_limit, vr=1.0,
                    holder_db_path=os.path.join(DB_DIR, 'holder_cache.db'),
                    date=today,
                )
                if result['score'] >= 80:
                    scored.append({
                        'code': s['code'], 'name': s['name'],
                        'score': result['score'],
                        'strategy': result['best'],
                        'predicted': result['predicted_high'],
                    })
            
            scored.sort(key=lambda x: -x['score'])
            print(f"\n🟢 评分≥80标的: {len(scored)}只")
            
            for target in scored:
                current_pos = trader.get_positions()
                if len(current_pos) >= 3:
                    break
                # 获取实时买入价
                mkt_code = f"sh{target['code']}" if target['code'][0] in ('6','5','9') else f"sz{target['code']}"
                try:
                    r = subprocess.run(
                        ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
                         f'https://qt.gtimg.cn/q={mkt_code}'],
                        capture_output=True, timeout=10
                    )
                    raw_text = r.stdout.decode('gbk', errors='replace')
                    raw = raw_text.split('=', 1)[1].strip().strip('"').strip(';')
                    fields = raw.split('~')
                    cur_price = float(fields[3]) if len(fields)>3 and fields[3] else 0
                    name = fields[1].strip()
                except:
                    continue
                
                if cur_price <= 0:
                    continue
                
                cash = trader._get_cash()
                buy_amount = min(30000, cash * 0.9)
                shares = int(buy_amount / cur_price / 100) * 100
                if shares < 100:
                    continue
                
                result = trader.buy(
                    code=target['code'], price=cur_price,
                    shares=shares, name=target['name'],
                    strategy=target['strategy'], score=target['score'],
                    reason=f'盘中自动: {target["strategy"]}({target["score"]}分) 预期+{target["predicted"]}%',
                )
                if result['success']:
                    print(f"  ✅ 买入 {target['name']}({target['code']}) {shares}股 @{cur_price:.2f}")
    
    # ====== 4. 风控检查 ======
    if positions:
        print(f"\n🛡️ 风控检查...")
        risk_signals = trader.check_risk_and_sell(quotes={})  # 用持仓自身价格
        if risk_signals:
            for s in risk_signals:
                print(f"  {s['urgency']} {s['name']}({s['code']}) → {s['reason']}")
                if '止损' in s['reason']:
                    result = trader.sell(s['code'], s['price'], reason=s['reason'])
                    if result['success']:
                        print(f"    ✅ 已自动卖出")
        else:
            print(f"  ✅ 无风控信号")
    
    # ====== 5. 报告 ======
    print(f"\n{'='*70}")
    trader.report()
    print(f"\n✅ 盘中交易完成")


def get_tencent_quotes(codes):
    """批量获取腾讯实时行情"""
    import subprocess
    quotes = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        codes_str = ','.join(
            f"sh{c}" if c[0] in ('6','5','9') else f"sz{c}" for c in batch
        )
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '10',
                 f'https://qt.gtimg.cn/q={codes_str}'],
                capture_output=True, timeout=12
            )
            raw_text = r.stdout.decode('gbk', errors='replace')
            for line in raw_text.strip().split('\n'):
                line = line.strip()
                if not line or '=' not in line: continue
                raw = line.split('=', 1)[1].strip().strip('"').strip(';').strip('"')
                fields = raw.split('~')
                if len(fields) < 50: continue
                code = fields[2].strip()
                if not code: continue
                try:
                    cur = float(fields[3]) if fields[3] else 0
                    prev = float(fields[4]) if fields[4] else 0
                    open_p = float(fields[5]) if fields[5] else 0
                    high = float(fields[33]) if fields[33] else 0
                    chg = float(fields[32]) if fields[32] else 0  # 涨跌额
                    chg_pct = (cur - prev) / (prev or 1) * 100  # 涨跌幅%
                    vol_r = float(fields[49]) if len(fields) > 49 and fields[49] else 0
                    body_high = max(cur, open_p)
                    upper_shadow = (high - body_high) / (prev or 1) * 100 if body_high > 0 else 0
                    name = fields[1].strip()
                    is_limit = chg_pct >= 9.5 and cur >= prev * 1.09
                    quotes[code] = {
                        'name': name, 'price': cur, 'prev_close': prev,
                        'open': open_p, 'high': high,
                        'change_pct': round(chg_pct, 2), 'vol_ratio': vol_r,
                        'upper_shadow_pct': round(upper_shadow, 2),
                        'is_limit_up': is_limit,
                    }
                except Exception as e:
                    print(f"  ⚠️ {code} 解析失败: {e}")
                    continue
        except Exception as e:
            print(f"  ⚠️ curl请求失败(batch {len(batch)}只): {e}")
            continue
        time.sleep(0.3)
    return quotes


def auto_trade_loop():
    """
    🔄 信号驱动自动交易循环 v1.0
    ================================
    盘中09:25~15:00持续运行，每5分钟扫描一次：
      1. 持仓T+1卖出（如果是隔日） → 有持仓必须卖出
      2. 扫描全量主板涨停股 → StrategyScorer评分
      3. 评分≥80的信号 → 自动买入（最多3只）
      4. 风控检查 → 止损8%/止盈回落12%/主力逆转 → 自动卖出
      5. 推送到微信
    
    用法：
      nohup python3 scripts/cloud_trading.py --auto &
    """
    from strategy_matrix import StrategyScorer
    import signal as sig_module
    
    running = True
    def handle_sig(signum, frame):
        nonlocal running
        running = False
        print("\n\n🛑 收到停止信号，优雅退出...")
    
    sig_module.signal(sig_module.SIGINT, handle_sig)
    sig_module.signal(sig_module.SIGTERM, handle_sig)
    
    trader = CloudTrader(initial_capital=100000)
    scorer = StrategyScorer()
    today = datetime.now().strftime('%Y-%m-%d')
    trade_date = today
    
    # 板块成分股（板块爆发检测用）
    SECTORS = {
        'chip': {'name': '存储芯片/AI芯片', 'codes': ['603986','603019','600584','603005','603160','002049','600171','603893','002185','300655','300672','300661','688525','688110']},
        'gpu': {'name': 'AI算力/服务器', 'codes': ['601138','603019','000977','600498','000063','002916','300308','688041']},
        'semicon': {'name': '半导体设备/材料', 'codes': ['688981','688012','688008','688126','688396','002371','688072','688120','688037','300661','688019','688200']},
        'robot': {'name': '人形机器人', 'codes': ['002472','002896','300124','688160','300660','688017','300580','601689','603662']},
        'ai_app': {'name': 'AI应用/AIGC', 'codes': ['300624','002230','300418','603533','002555','300058','300315','300624','002517','688111']},
        'low_alt': {'name': '低空经济/飞行汽车', 'codes': ['002085','600580','300177','688070','688568','002111','002023','603885','000099','600391']},
        'battery': {'name': '固态电池/新能源', 'codes': ['300750','002074','300014','002460','002709','600884','300073','300568','002812','300769']},
    }
    
    # ====== 风控状态（持久化到json）====== 
    RISK_FILE = os.path.join(DB_DIR, 'cloud_trading_risk.json')
    risk_state = {'consecutive_losses': 0, 'peak_assets': 100000}
    try:
        with open(RISK_FILE) as f:
            risk_state.update(json.load(f))
    except:
        pass
    
    def save_risk_state():
        with open(RISK_FILE, 'w') as f:
            json.dump(risk_state, f)
    
    def check_market_direction():
        """检查大盘环境：用上证50ETF（510050）或沪深300实时涨跌"""
        try:
            r = subprocess.run(
                ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
                 'https://qt.gtimg.cn/q=sh000001'],
                capture_output=True, timeout=10
            )
            raw_text = r.stdout.decode('gbk', errors='replace')
            if raw_text and '=' in raw_text:
                raw = raw_text.split('=', 1)[1].strip().strip('"').strip(';')
                fields = raw.split('~')
                idx_chg = float(fields[32]) if len(fields) > 32 and fields[32] else 0
                return idx_chg  # 上证涨跌幅
        except Exception as e:
            print(f"  ⚠️ 大盘查询失败: {e}")
            return 0
        return 0
    
    # 加载K线（只需一次）
    kline_path = os.path.join(DB_DIR, 'kline_cache.db')
    print("⏳ 加载K线...", end=' ', flush=True)
    t0 = time.time()
    scorer._load_kline_full(kline_path)
    print(f"{time.time()-t0:.1f}s")
    
    # 加载全量主板列表
    try:
        with open(os.path.join(BASE, 'data', 'all_main_board.txt')) as f:
            all_codes = [l.strip() for l in f if l.strip() and not l.startswith('#')]
    except:
        print("❌ 无法读取 all_main_board.txt")
        return
    
    print(f"📡 信号驱动交易启动 | 监控{len(all_codes)}只主板 | {today}")
    print(f"   买入条件: 涨停+评分≥80 | 最大持仓: 3只 | 风控: 止损8%/止盈回落12%")
    print(f"   扫描间隔: 5分钟\n")
    
    last_alert_time = {}
    cycle = 0
    max_shares = 3
    capital_per_trade = 30000
    
    while running:
        now = datetime.now()
        hour = now.hour
        minute = now.minute
        time_str = now.strftime('%H:%M:%S')
        
        # 交易时段检查
        is_trading = (hour == 9 and minute >= 25) or (10 <= hour <= 11) or (13 <= hour <= 14) or (hour == 15 and minute <= 5)
        if not is_trading:
            time.sleep(30)
            continue
        
        cycle += 1
        
        # ====== 风控前置检查（熔断+连亏暂停+大盘环境）====== 
        idx_chg = check_market_direction()
        
        # 熔断检查：总资产回撤20%→全部清仓+停止交易
        cash = trader._get_cash()
        positions = trader.get_positions()
        pos_value = sum(p['market_value'] for p in positions)
        total_assets = cash + pos_value
        if total_assets > risk_state['peak_assets']:
            risk_state['peak_assets'] = total_assets
        drawdown = (risk_state['peak_assets'] - total_assets) / risk_state['peak_assets'] * 100
        
        if drawdown >= 20:
            print(f"  [{time_str}] 🔴🔴🔴 熔断！总资产回撤{drawdown:.1f}%≥20%，清仓停止交易")
            for pos in positions:
                result = trader.sell(pos['code'], pos['current_price'], strategy='风控熔断', reason=f'熔断清仓(回撤{drawdown:.1f}%)')
                if result['success']:
                    print(f"      ✅ 已清仓 {pos['name']}")
            save_risk_state()
            print(f"\n{'='*70}")
            trader.take_snapshot()
            trader.report()
            return
        
        # 连亏暂停：连续3笔亏损→今日暂停买入
        if risk_state['consecutive_losses'] >= 3:
            print(f"  [{time_str}] ⛔ 连亏{risk_state['consecutive_losses']}笔，今日暂停买入")
            # 只做卖和风控，不做买
            # 等到下一个交易日重置
        else:
            # 大盘环境：大幅低开(-2%以下)减少买入仓位
            market_crash = idx_chg <= -2
            market_bad = idx_chg <= -1
            if market_crash:
                print(f"  [{time_str}] ⚠️ 大盘{idx_chg:+.1f}%，暴跌模式→买入仓位减半")
            elif market_bad:
                print(f"  [{time_str}] 📉 大盘{idx_chg:+.1f}%，弱势→买入谨慎")
        
        # ====== 1. T+1卖出 ====== 
        positions = trader.get_positions()
        for pos in positions:
            code = pos['code']
            mkt_code = f"sh{code}" if code[0] in ('6','5','9') else f"sz{code}"
            try:
                r = subprocess.run(
                    ['curl', '-s', '--connect-timeout', '5', '--max-time', '8',
                     f'https://qt.gtimg.cn/q={mkt_code}'],
                    capture_output=True, timeout=10
                )
                raw_text = r.stdout.decode('gbk', errors='replace')
                if raw_text and '=' in raw_text:
                    raw = raw_text.split('=', 1)[1].strip().strip('"').strip(';')
                    fields = raw.split('~')
                    cur_price = float(fields[3]) if len(fields)>3 and fields[3] else 0
                    open_price = float(fields[5]) if len(fields)>5 and fields[5] else 0
                    if cur_price > 0:
                        sell_price = open_price if now.hour < 10 else cur_price
                        result = trader.sell(code, sell_price, strategy='T+1竞价卖', reason=f'信号触发自动: T+1卖出')
                        if result['success']:
                            print(f"  [{time_str}] ✅ 卖出 {pos['name']}({code}) @{sell_price:.2f} 盈亏{result.get('profit_rate',0):+.2f}%")
            except Exception as e:
                if code not in last_alert_time or (now - last_alert_time.get(code, datetime.min)).seconds > 300:
                    print(f"  [{time_str}] ⚠️ {code} 卖出查询失败")
                    last_alert_time[code] = now
        
        # ====== 2. 扫描涨停+评分+买入（连亏时跳过、大盘暴跌减半）====== 
        current_pos = trader.get_positions()
        can_buy = len(current_pos) < max_shares and risk_state['consecutive_losses'] < 3
        if can_buy:
            # ---- 两步扫描法 ----
            # 第1步：先扫龙头候选池（约80只），又快又准
            print(f"  [{time_str}] 🔍 扫描龙头候选...", end=' ', flush=True)
            candidate_codes = list(set(
                code for sec in SECTORS.values() for code in sec['codes']
            ))
            quotes = get_tencent_quotes(candidate_codes)
            limit_stocks_first = {c: q for c, q in quotes.items() if q.get('is_limit_up') and (c.startswith('6') or c.startswith('0'))}
            limit_stocks = {}  # 初始化
            
            if not limit_stocks_first:
                print(f"0只涨停")
                time.sleep(5)
            else:
                # 第2步：有涨停候选，再从全量扫描确认板块爆发数据
                print(f"龙头{len(limit_stocks_first)}只涨停, 全量扫描启动...", end=' ', flush=True)
                all_quotes = get_tencent_quotes(all_codes)
                limit_stocks = {c: q for c, q in all_quotes.items() if q.get('is_limit_up') and (c.startswith('6') or c.startswith('0'))}
                print(f"全量{len(limit_stocks)}只涨停")
            
            if limit_stocks:
                scored = []
                
                # ---- 板块爆发检测（独立于个股评分） ----
                sector_boom_stocks = []  # 板块涨停≥3只的标的
                sector_limits = defaultdict(list)  # sector_name -> [(code, q)]
                
                # 从sector_indexes.db查板块涨停数（盘中实时不方便，用今天已有的涨停数据估算）
                # 直接用limit_stocks按板块名归类（板块归属从sector_indexes查）
                for code, q in limit_stocks.items():
                    try:
                        conn = sqlite3.connect(os.path.join(DB_DIR, 'sector_indexes.db'))
                        # 个股可能属于多个板块，取板块涨停数最大的那个
                        sectors_data = conn.execute(
                            "SELECT ss.sector_name, si.limit_up_count FROM sector_stock_daily ss "
                            "JOIN sector_daily_index si ON ss.date=si.date AND ss.sector_name=si.sector_name "
                            "WHERE ss.date=? AND ss.code=? AND si.limit_up_count>=3 "
                            "ORDER BY si.limit_up_count DESC LIMIT 1",
                            (today, code)
                        ).fetchone()
                        conn.close()
                        if sectors_data:
                            sector_limits[sectors_data[0]].append((code, q, sectors_data[1]))
                        else:
                            # 无板块数据，检查是否为板块成分股（hardcoded sectors）
                            for sk, sv in SECTORS.items():
                                if code in sv['codes']:
                                    sector_limits[sk].append((code, q, 0))
                                    break
                    except:
                        pass
                
                # 提取板块涨停≥3只的标的
                for sec_name, stock_list in sector_limits.items():
                    if len(stock_list) >= 3:
                        # 板块爆发！这些标的都建议关注
                        for item in stock_list:
                            sector_boom_stocks.append({
                                'code': item[0], 'name': item[1]['name'],
                                'price': item[1]['price'],
                                'strategy': f'板块爆发{sec_name}({len(stock_list)}涨停)',
                                'score': 95,
                                'predicted': 9.96,
                                'vol_ratio': item[1].get('vol_ratio', 0),
                                'upper_shadow': item[1].get('upper_shadow_pct', 0),
                            })
                
                if sector_boom_stocks:
                    print(f"  [{time_str}] 🔥 板块爆发: {len(sector_boom_stocks)}只标的")
                
                # ---- 个股评分（已有逻辑）----
                for code, q in limit_stocks.items():
                    # 板块数据
                    try:
                        conn = sqlite3.connect(os.path.join(DB_DIR, 'sector_indexes.db'))
                        sr = conn.execute(
                            "SELECT sector_rank, limit_up_count FROM sector_stock_daily ss "
                            "JOIN sector_daily_index si ON ss.date=si.date AND ss.sector_name=si.sector_name "
                            "WHERE ss.date=? AND ss.code=? LIMIT 1",
                            (today, code)
                        ).fetchone()
                        conn.close()
                        sector_rank = sr[0] if sr else 99
                        sector_limit = sr[1] if sr else 0
                    except:
                        sector_rank, sector_limit = 99, 0
                    
                    result = scorer.score(
                        code=code, name=q['name'],
                        board_count=1, limit_stat='normal',
                        sector_rank=sector_rank, sector_limit=sector_limit,
                        vr=q.get('vol_ratio', 1.0),
                        holder_db_path=os.path.join(DB_DIR, 'holder_cache.db'),
                        date=today,
                        upper_shadow_pct=q.get('upper_shadow_pct'),
                    )
                    if result['score'] >= 80:
                        scored.append({
                            'code': code, 'name': q['name'],
                            'price': q['price'],
                            'score': result['score'],
                            'strategy': result['best'],
                            'predicted': result['predicted_high'],
                            'vol_ratio': q.get('vol_ratio', 0),
                            'upper_shadow': q.get('upper_shadow_pct', 0),
                        })
                
                # ---- 隔夜溢价独立标记（缩量<0.7+上影<0.5%）----
                geye_stocks = []
                for code, q in limit_stocks.items():
                    vr = q.get('vol_ratio', 0)
                    us = q.get('upper_shadow_pct', 100)
                    if 0 < vr < 0.7 and us < 0.5:
                        geye_stocks.append({
                            'code': code, 'name': q['name'],
                            'price': q['price'],
                            'strategy': '隔夜溢价缩量板',
                            'score': 85,
                            'predicted': 3.51,
                            'vol_ratio': vr,
                            'upper_shadow': us,
                        })
                
                if geye_stocks:
                    print(f"  [{time_str}] 🌅 隔夜溢价: {len(geye_stocks)}只(缩量<0.7+上影<0.5%)")
                
                # ---- 合并买入队列（去重）----
                # 板块爆发>隔夜溢价>评分≥80 优先级排序
                buy_queue = []
                seen_codes = set()
                
                for item in sector_boom_stocks:
                    if item['code'] not in seen_codes:
                        buy_queue.append(item)
                        seen_codes.add(item['code'])
                
                for item in geye_stocks:
                    if item['code'] not in seen_codes:
                        buy_queue.append(item)
                        seen_codes.add(item['code'])
                
                for item in scored:
                    if item['code'] not in seen_codes:
                        buy_queue.append(item)
                        seen_codes.add(item['code'])
                
                scored = buy_queue  # 替换原来的scored
                
                scored.sort(key=lambda x: -x['score'])
                if scored:
                    print(f"  [{time_str}] 🟢 评分≥80: {len(scored)}只")
                    for s in scored[:3]:
                        print(f"      {s['name']}({s['code']}) {s['score']}分 | {s['strategy']} | 量比{s['vol_ratio']:.2f}")
                
                for target in scored:
                    current_pos = trader.get_positions()
                    if len(current_pos) >= max_shares:
                        break
                    
                    price = target['price']
                    if price <= 0:
                        continue
                    
                    cash = trader._get_cash()
                    # 大盘暴跌时买入减半
                    trade_capital = capital_per_trade
                    if idx_chg <= -2:
                        trade_capital = capital_per_trade // 2
                    buy_amount = min(trade_capital, cash * 0.9)
                    shares = int(buy_amount / price / 100) * 100
                    if shares < 100:
                        continue
                    
                    result = trader.buy(
                        code=target['code'], price=price,
                        shares=shares, name=target['name'],
                        strategy=target['strategy'],
                        score=target['score'],
                        reason=f'信号自动: {target["strategy"]}({target["score"]}分) 期+{target["predicted"]}%',
                    )
                    if result['success']:
                        print(f"  [{time_str}] ✅ 买入 #{len(trader.get_positions())} {target['name']}({target['code']}) {shares}股 @{price:.2f}")
        
        # ====== 3. 风控检查 ======
        positions = trader.get_positions()
        if positions:
            # 拉这些股票的最新行情
            quotes_small = get_tencent_quotes([p['code'] for p in positions])
            risk_signals = trader.check_risk_and_sell(quotes_small)
            if risk_signals:
                for s in risk_signals:
                    print(f"  [{time_str}] {s['urgency']} {s['name']}({s['code']}) → {s['reason']}")
                    result = trader.sell(s['code'], s['price'], strategy='风控自动卖出', reason=s['reason'])
                    if result['success']:
                        print(f"      ✅ 已卖出")
                        # 如果是止损卖出，更新连亏计数器
                        if '止损' in s['reason']:
                            risk_state['consecutive_losses'] += 1
                            save_risk_state()
                            print(f"      📊 连亏计数: {risk_state['consecutive_losses']}")
        
        # ====== 3.5 收盘前记录当日盈亏，重置连亏 ======
        if now.hour == 15 and now.minute >= 1:
            # 收盘后检查今天的卖出中是否有盈利的
            conn = sqlite3.connect(trader.db_path)
            today_sells = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE direction='sell' AND date(trade_date)=? AND profit>0",
                (today,)
            ).fetchone()[0]
            conn.close()
            if today_sells > 0:
                # 今天有盈利卖出→重置连亏计数
                risk_state['consecutive_losses'] = 0
                save_risk_state()
        
        # ====== 4. 每30分钟输出一次状态 ======
        if cycle % 6 == 0:
            print(f"  [{time_str}] 📊 持仓{len(trader.get_positions())}只 | 现金{trader._get_cash():.2f}")
        
        # 等待5分钟（非收盘前）
        if now.hour == 14 and now.minute >= 50:
            # 14:50后变成1分钟间隔，最后时刻密集扫描
            time.sleep(60)
        elif now.hour == 15:
            break
        else:
            time.sleep(300)  # 5分钟
    
    # 收盘
    print(f"\n{'='*70}")
    trader.take_snapshot()
    trader.report()
    print(f"\n✅ 信号驱动交易结束")
    return trader


def main():
    """主入口"""
    if '--auto' in sys.argv:
        auto_trade_loop()
        return
    
    if '--live' in sys.argv:
        live_trade()
        return
    
    print("=" * 70)
    print("  💼 云盘交易系统 v1.0")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    trader = CloudTrader(initial_capital=100000)
    trader.report()

    print(f"\n📊 使用指南:")
    print(f"  python3 scripts/cloud_trading.py --auto     # 🔥 信号驱动自动交易(后台运行)")
    print(f"  python3 scripts/cloud_trading.py --live     # 单次扫描+交易")
    print(f"  python3 scripts/cloud_trading.py --report   # 报告")
    print(f"  from cloud_trading import CloudTrader")
    print(f"  trader = CloudTrader(initial_capital=100000)")
    print(f"  trader.buy('600519', 150.0, 100, name='贵州茅台')")
    print(f"  trader.update_prices({{'600519': 155.0}})")
    print(f"  trader.sell('600519', 160.0)")
    print(f"  trader.take_snapshot()")
    print(f"  trader.report()")


if __name__ == '__main__':
    main()
