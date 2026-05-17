#!/usr/bin/env python3
"""
📊 上证牵引面量化模型 v1.0

"大盘涨1%，这个股应该涨多少？"

核心逻辑：
  每只股票与大盘之间有一个"牵引系数"——β。
  但β不是恒定的：
    - 牛市中β放大（个股更激进）
    - 熊市中β缩小（个股更抗跌/更脆弱）
    - 横盘中β≈0（个股走独立行情）

  本模型量化3个维度12个因子：
  ┌─ 维度A：β强度（大盘涨1%，个股跟多少）
  │  ① 全量β — 所有交易日的回归β
  │  ② 上涨β — 大盘上涨日的β
  │  ③ 下跌β — 大盘下跌日的β
  │  ④ 不对称性 — 上涨β/下跌β（>1=跟涨不跟跌，<1=跟跌不跟涨）
  │
  ├─ 维度B：β稳定性（这个牵引关系可靠吗）
  │  ⑤ R²决定系数 — β的解释力
  │  ⑥ β滚动变异系数 — β的稳定性
  │  ⑦ 近60日β vs 全量β — 近期变化
  │  ⑧ 残差标准差 — 不可解释的波动
  │
  └─ 维度C：α超额收益（个股跑赢大盘的部分）
      ⑨ α截距 — 超额收益（>0=持续跑赢）
     ⑩ α稳定性 — α的滚动变异系数
     ⑪ 近60日α — 近期超额收益
     ⑫ 夏普α比 — α/残差标准差

输出：
  - β值（动态+上涨+下跌+不对称）
  - 牵引等级（强牵引/弱牵引/独立行情/反向指标）
  - α超额收益评级
  - 预测功能：若大盘涨X%，个股应涨Y±Z%

用法：
  python3 scripts/beta.py --code 603986              # 个股β分析
  python3 scripts/beta.py --code 603986 --period 120   # 指定回归窗口
  python3 scripts/beta.py --code 603986 --predict 2.0  # 预测大盘+2%时个股
  python3 scripts/beta.py --sector AI芯片              # 板块内TOP分析
  python3 scripts/beta.py --scan                       # 全量扫描引力股
"""

import os, sys, json, sqlite3, math
from datetime import datetime, timedelta
from collections import deque

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, 'data', 'kline_cache.db')
INDEX_CODE = '000001'  # 上证指数

# ============================================================
# 数据加载
# ============================================================

def load_aligned_klines(code: str, max_days: int = 250) -> tuple:
    """
    加载个股和上证指数对齐的K线数据
    
    Returns:
        (stock_returns, index_returns, stock_prices, index_prices, dates)
        所有序列从旧到新（最早的在前）
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 加载个股K线
    cur.execute(
        "SELECT date, close FROM kline WHERE code=? ORDER BY date ASC",
        (code,)
    )
    stock_data = {r[0]: r[1] for r in cur.fetchall()}
    
    # 加载上证指数K线
    cur.execute(
        "SELECT date, close FROM kline WHERE code=? ORDER BY date ASC",
        (INDEX_CODE,)
    )
    index_data = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    
    # 取交集日期
    common_dates = sorted(set(stock_data.keys()) & set(index_data.keys()))
    if len(common_dates) < 30:
        return [], [], [], [], []
    
    # 截取最近max_days
    if len(common_dates) > max_days:
        common_dates = common_dates[-max_days:]
    
    # 计算日收益率
    stock_prices = [stock_data[d] for d in common_dates]
    index_prices = [index_data[d] for d in common_dates]
    
    stock_returns = []
    index_returns = []
    for i in range(1, len(common_dates)):
        sr = (stock_prices[i] - stock_prices[i-1]) / stock_prices[i-1] * 100
        ir = (index_prices[i] - index_prices[i-1]) / index_prices[i-1] * 100
        stock_returns.append(sr)
        index_returns.append(ir)
    
    return stock_returns, index_returns, stock_prices, index_prices, common_dates


# ============================================================
# 统计工具
# ============================================================

def linreg(x: list, y: list):
    """一元线性回归，返回(slope, intercept, r2, resid_std)"""
    n = len(x)
    if n < 5:
        return 0, 0, 0, 0
    
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    
    ss_xy = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    ss_xx = sum((x[i] - mean_x) ** 2 for i in range(n))
    ss_yy = sum((y[i] - mean_y) ** 2 for i in range(n))
    
    if ss_xx == 0:
        return 0, mean_y, 0, 0
    
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    
    if ss_yy == 0:
        r2 = 0
    else:
        r2 = (ss_xy ** 2) / (ss_xx * ss_yy)
    
    # 残差标准差
    residuals = [y[i] - (slope * x[i] + intercept) for i in range(n)]
    resid_var = sum(r ** 2 for r in residuals) / max(1, n - 2)
    resid_std = math.sqrt(resid_var) if resid_var > 0 else 0
    
    return slope, intercept, r2, resid_std


def mean(vals):
    return sum(vals) / len(vals) if vals else 0

def std(vals):
    if len(vals) < 2:
        return 0
    m = mean(vals)
    v = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
    return math.sqrt(v) if v > 0 else 0


# ============================================================
# 牵引面量化引擎
# ============================================================

class BetaEngine:
    """
    上证牵引面量化引擎
    """

    def __init__(self, window: int = 120):
        self.window = window  # 滚动窗口

    def analyze(self, code: str, stock_returns: list = None, index_returns: list = None,
                stock_prices: list = None, index_prices: list = None, dates: list = None) -> dict:
        """
        完整β分析入口

        Args:
            code: 股票代码
            stock_returns: 个股日收益率序列（已对齐指数）
            index_returns: 上证指数日收益率序列
            stock_prices: 个股价格序列
            index_prices: 上证指数价格序列
            dates: 日期序列

        Returns:
            dict: 包含β强度/稳定性/α超额收益等全部结果
        """
        if stock_returns is None or index_returns is None:
            stock_returns, index_returns, stock_prices, index_prices, dates = load_aligned_klines(code, self.window + 60)

        if len(stock_returns) < 20:
            return {'error': f'{code}: 数据不足(需≥20个交易日)', 'code': code}

        n = len(stock_returns)
        current_price = stock_prices[-1] if stock_prices else 0
        current_index = index_prices[-1] if index_prices else 0

        # ========== 维度A：β强度 ==========
        dim_a = self._dim_a(stock_returns, index_returns, n)

        # ========== 维度B：β稳定性 ==========
        dim_b = self._dim_b(stock_returns, index_returns, n)

        # ========== 维度C：α超额收益 ==========
        dim_c = self._dim_c(stock_returns, index_returns, n)

        # ========== 综合牵引等级 ==========
        traction_rating = self._rate_traction(dim_a, dim_b)

        # ========== 预测 ==========
        prediction = self._predict(dim_a, dim_b, dim_c)

        result = {
            'code': code,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'current_price': current_price,
            'current_index': current_index,
            'data_days': n,
            'date_range': f"{dates[0]} ~ {dates[-1]}" if dates else "",
            'traction_rating': traction_rating,
            'dim_a_beta': dim_a,
            'dim_b_stability': dim_b,
            'dim_c_alpha': dim_c,
            'prediction': prediction,
        }
        return result

    def _dim_a(self, sr: list, ir: list, n: int) -> dict:
        """维度A：β强度"""
        window = min(self.window, n)

        # ① 全量β
        full_beta, full_alpha, full_r2, full_resid = linreg(ir[-window:], sr[-window:])

        # ② 上涨β（大盘涨的日子）
        up_idx = [i for i in range(len(ir)) if ir[i] > 0]
        up_beta, up_alpha, up_r2, up_resid = 0, 0, 0, 0
        if len(up_idx) >= 10:
            up_ir = [ir[i] for i in up_idx]
            up_sr = [sr[i] for i in up_idx]
            up_beta, up_alpha, up_r2, up_resid = linreg(up_ir[-window//2:], up_sr[-window//2:])

        # ③ 下跌β（大盘跌的日子）
        dn_idx = [i for i in range(len(ir)) if ir[i] < 0]
        dn_beta, dn_alpha, dn_r2, dn_resid = 0, 0, 0, 0
        if len(dn_idx) >= 10:
            dn_ir = [ir[i] for i in dn_idx]
            dn_sr = [sr[i] for i in dn_idx]
            dn_beta, dn_alpha, dn_r2, dn_resid = linreg(dn_ir[-window//2:], dn_sr[-window//2:])

        # ④ 不对称性 = 上涨β / 下跌β（绝对值）
        asymmetry = 0
        if dn_beta != 0:
            asymmetry = up_beta / abs(dn_beta)
        elif up_beta != 0:
            asymmetry = 10  # 极端：只跟涨不跟跌
        else:
            asymmetry = 1

        # 评级
        if full_beta > 1.5:
            beta_label = '🔥 高β（激进型）'
        elif full_beta > 0.8:
            beta_label = '➡️ 中β（同步型）'
        elif full_beta > 0.3:
            beta_label = '🟢 低β（稳健型）'
        elif full_beta > -0.3:
            beta_label = '🌀 独立行情'
        else:
            beta_label = '🔴 反向指标'

        # 不对称性评级
        if asymmetry > 1.5:
            asym_label = '🟢 跟涨不跟跌（优质）'
        elif asymmetry > 0.8:
            asym_label = '➡️ 对称跟随'
        elif asymmetry > 0.3:
            asym_label = '⚠️ 跟跌不跟涨（风险）'
        else:
            asym_label = '🔴 纯粹跟跌'

        return {
            'full_beta': round(full_beta, 3),
            'up_beta': round(up_beta, 3),
            'down_beta': round(dn_beta, 3),
            'asymmetry': round(asymmetry, 2),
            'beta_label': beta_label,
            'asym_label': asym_label,
            'score': min(100, max(0, abs(full_beta) * 40)),
        }

    def _dim_b(self, sr: list, ir: list, n: int) -> dict:
        """维度B：β稳定性"""
        window = min(self.window, n)

        # ⑤ R²解释力
        _, _, full_r2, full_resid = linreg(ir[-window:], sr[-window:])

        # ⑥ β滚动变异系数（分4段）
        segment_size = max(20, window // 4)
        betas = []
        for i in range(0, window - segment_size, segment_size):
            seg_ir = ir[-(window-i):-(window-i-segment_size)]
            seg_sr = sr[-(window-i):-(window-i-segment_size)]
            if len(seg_ir) >= 10:
                b, _, _, _ = linreg(seg_ir, seg_sr)
                betas.append(b)

        beta_cv = std(betas) / mean(betas) if mean(betas) != 0 else 0

        # ⑦ 近60日β vs 全量β（近期变化）
        if n >= 60:
            recent_beta, _, _, _ = linreg(ir[-60:], sr[-60:])
        else:
            recent_beta = betas[-1] if betas else 0

        full_beta, _, _, _ = linreg(ir[-window:], sr[-window:])
        beta_change = recent_beta - full_beta if full_beta != 0 else 0

        # ⑧ 残差标准差（不可解释波动）
        # 已在full_resid中

        # 稳定性评级
        if full_r2 > 0.5 and beta_cv < 0.5:
            stability = '🟢 稳定牵引'
        elif full_r2 > 0.3 and beta_cv < 1.0:
            stability = '➡️ 一般稳定'
        elif full_r2 > 0.1:
            stability = '⚠️ 弱牵引'
        else:
            stability = '🔴 无牵引关系'

        return {
            'r2': round(full_r2, 3),
            'resid_std': round(full_resid, 3),
            'beta_cv': round(beta_cv, 2),
            'recent_beta_60d': round(recent_beta, 3),
            'full_beta': round(full_beta, 3),
            'beta_change': round(beta_change, 3),
            'stability_label': stability,
            'score': min(100, max(0, full_r2 * 100 + (1 - min(1, beta_cv)) * 30)),
        }

    def _dim_c(self, sr: list, ir: list, n: int) -> dict:
        """维度C：α超额收益"""
        window = min(self.window, n)

        # ⑨ α截距
        _, alpha, _, resid_std = linreg(ir[-window:], sr[-window:])

        # ⑩ α滚动稳定性
        segment_size = max(20, window // 4)
        alphas = []
        for i in range(0, window - segment_size, segment_size):
            seg_ir = ir[-(window-i):-(window-i-segment_size)]
            seg_sr = sr[-(window-i):-(window-i-segment_size)]
            if len(seg_ir) >= 10:
                _, a, _, _ = linreg(seg_ir, seg_sr)
                alphas.append(a)

        alpha_cv = std(alphas) / mean(alphas) if mean(alphas) != 0 else 0

        # ⑪ 近60日α
        if n >= 60:
            _, recent_alpha, _, _ = linreg(ir[-60:], sr[-60:])
        else:
            recent_alpha = alpha

        # ⑫ 夏普α比 = α / 残差标准差
        alpha_sharpe = alpha / resid_std if resid_std > 0 else 0

        # α评级
        if alpha > 0.1 and alpha_sharpe > 0.5:
            alpha_label = '🔥 显著跑赢'
        elif alpha > 0.05:
            alpha_label = '🟢 小幅跑赢'
        elif alpha > -0.05:
            alpha_label = '➡️ 跟随大盘'
        elif alpha > -0.15:
            alpha_label = '🟡 小幅跑输'
        else:
            alpha_label = '🔴 持续跑输'

        return {
            'alpha': round(alpha, 4),
            'alpha_sharpe': round(alpha_sharpe, 2),
            'recent_alpha_60d': round(recent_alpha, 4),
            'alpha_cv': round(alpha_cv, 2),
            'alpha_label': alpha_label,
            'score': min(100, max(0, (alpha + 0.3) * 100)),
        }

    def _rate_traction(self, dim_a: dict, dim_b: dict) -> str:
        """综合牵引等级"""
        beta = dim_a.get('full_beta', 0)
        r2 = dim_b.get('r2', 0)
        asym = dim_a.get('asymmetry', 1)

        if r2 > 0.5 and abs(beta) > 0.8:
            if asym > 1.3:
                return '🟢🟢 强正牵引（跟涨不跟跌）'
            return '🟢 强正牵引'
        elif r2 > 0.3 and abs(beta) > 0.3:
            if asym < 0.5:
                return '⚠️ 负向牵引（跟跌为主）'
            return '➡️ 一般牵引'
        elif r2 > 0.1:
            return '🟤 弱牵引'
        else:
            return '🌀 独立行情（不与大盘联动）'

    def _predict(self, dim_a: dict, dim_b: dict, dim_c: dict) -> dict:
        """预测功能：给定大盘涨跌幅，预测个股"""
        beta = dim_a.get('full_beta', 0)
        alpha = dim_c.get('alpha', 0)
        resid = dim_b.get('resid_std', 1)

        scenarios = {}
        for idx_chg in [-3, -2, -1, -0.5, 0.5, 1, 2, 3]:
            stock_pred = beta * idx_chg + alpha
            scenarios[f'{idx_chg:+.1f}%'] = {
                'predicted': f'{stock_pred:+.2f}%',
                'range_68': f'{stock_pred - resid:+.2f}% ~ {stock_pred + resid:+.2f}%',
                'range_95': f'{stock_pred - 2*resid:+.2f}% ~ {stock_pred + 2*resid:+.2f}%',
            }

        return {
            'formula': f'个股涨跌幅 = {beta:.3f} × 大盘涨跌幅 + {alpha:.4f}',
            'r2_explain': f'{dim_b.get("r2", 0)*100:.0f}%',
            'scenarios': scenarios,
        }


# ============================================================
# 报告生成
# ============================================================

def generate_report(result: dict) -> str:
    """生成微信推送格式报告"""
    if 'error' in result:
        return f"❌ {result.get('code', '')}: {result['error']}"

    lines = []

    # 头部
    lines.append(f"📊 **上证牵引面 — {result['code']}**")
    lines.append(f"   ⏰ {result['timestamp']}")
    lines.append(f"   数据: {result['data_days']}个交易日 ({result.get('date_range', '')})")
    if result.get('current_price'):
        lines.append(f"   现价: {result['current_price']}  |  上证: {result['current_index']}")
    lines.append("")

    # 牵引评级
    lines.append(f"   📋 **{result['traction_rating']}**")
    lines.append("")

    # 维度A：β强度
    da = result['dim_a_beta']
    lines.append(f"   ┌─ **维度A: β强度 — {da['beta_label']}**")
    lines.append(f"   │  全量β: {da['full_beta']:.3f}")
    lines.append(f"   │  上涨β: {da['up_beta']:.3f}  |  下跌β: {da['down_beta']:.3f}")
    lines.append(f"   │  不对称性: {da['asymmetry']:.2f} — {da['asym_label']}")
    lines.append(f"   │  大盘+1% → 个股{da['full_beta']*1+da.get('alpha', 0):+.2f}% (纯β部分)")
    lines.append("")

    # 维度B：稳定性
    db = result['dim_b_stability']
    lines.append(f"   ├─ **维度B: 稳定性 — {db['stability_label']}**")
    lines.append(f"   │  R²: {db['r2']:.3f}  |  残差σ: {db['resid_std']:.3f}%")
    lines.append(f"   │  近60日β: {db.get('recent_beta_60d', 'N/A')}  | 全量β: {db['full_beta']:.3f}")
    lines.append(f"   │  β变化: {db.get('beta_change', 0):+.3f}  |  β变异: {db['beta_cv']:.2f}")
    lines.append("")

    # 维度C：α超额收益
    dc = result['dim_c_alpha']
    lines.append(f"   └─ **维度C: α超额收益 — {dc['alpha_label']}**")
    lines.append(f"      α: {dc['alpha']:.4f}%/日  |  夏普α比: {dc['alpha_sharpe']:.2f}")
    lines.append(f"      近60日α: {dc.get('recent_alpha_60d', 'N/A')}%/日")
    lines.append("")

    # 预测表
    pred = result.get('prediction', {})
    if pred:
        lines.append(f"   🔮 **大盘→个股预测**")
        lines.append(f"   公式: {pred.get('formula', '')}")
        lines.append(f"   解释力R²: {pred.get('r2_explain', '')}")
        lines.append(f"   {'':─<45s}")
        lines.append(f"   {'大盘涨幅':^10s} | {'个股(均值)':^12s} | {'68%区间':^18s}")
        lines.append(f"   {'':─<45s}")
        for sc, val in pred.get('scenarios', {}).items():
            lines.append(f"   {sc:^10s} | {val['predicted']:^12s} | {val['range_68']:^18s}")
        lines.append("")

    lines.append(f"   {'─'*40}")
    lines.append(f"   💡 牵引面核心原则：R²>0.3的标的才值得用β做预测")

    return '\n'.join(lines)


# ============================================================
# 命令行入口
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description='📊 上证牵引面量化模型')
    parser.add_argument('--code', type=str, help='个股代码，如603986')
    parser.add_argument('--period', type=int, default=120, help='回归窗口天数(默认120)')
    parser.add_argument('--predict', type=float, help='预测：大盘涨跌幅%')
    parser.add_argument('--sector', type=str, help='板块分析')
    parser.add_argument('--json', action='store_true', help='输出JSON格式')
    args = parser.parse_args()

    engine = BetaEngine(window=args.period)

    if args.code:
        sr, ir, sp, ip, dates = load_aligned_klines(args.code, args.period + 60)
        if len(sr) < 20:
            print(f"❌ {args.code}: 数据不足({len(sr)}个交易日，需至少20天)")
            return

        result = engine.analyze(args.code, sr, ir, sp, ip, dates)

        # 如果指定了预测
        if args.predict is not None:
            beta = result['dim_a_beta']['full_beta']
            alpha = result['dim_c_alpha']['alpha']
            resid = result['dim_b_stability']['resid_std']
            pred = beta * args.predict + alpha
            result['custom_prediction'] = {
                'index_change': f'{args.predict:+.2f}%',
                'stock_predicted': f'{pred:+.2f}%',
                'range_68': f'{pred - resid:+.2f}% ~ {pred + resid:+.2f}%',
                'range_95': f'{pred - 2*resid:+.2f}% ~ {pred + 2*resid:+.2f}%',
            }

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            report = generate_report(result)
            print(report)
            if args.predict:
                cp = result['custom_prediction']
                print(f"\n🎯 **预测结果**")
                print(f"   若上证{cp['index_change']} → {result['code']}预期{cp['stock_predicted']}")
                print(f"   68%置信区间: {cp['range_68']}")
                print(f"   95%置信区间: {cp['range_95']}")

    elif args.sector:
        print(f"📊 板块β扫描: {args.sector}")
        print("   功能建设中，暂用个股模式")
        print("   python3 scripts/beta.py --code 603986")

    else:
        # 默认扫描核心标的
        codes = ['603986', '601138', '603019', '002281', '603893', '600519']
        print(f"📊 上证牵引面 — 核心标的批量扫描")
        print(f"   ⏰ {datetime.now().strftime('%H:%M:%S')}")
        print(f"   {'─'*50}")
        print()

        for code in codes:
            sr, ir, sp, ip, dates = load_aligned_klines(code, args.period + 60)
            if len(sr) >= 20:
                result = engine.analyze(code, sr, ir, sp, ip, dates)
                name = ''
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT name FROM stock_info WHERE code=?", (code,))
                row = cur.fetchone()
                if row:
                    name = row[0]
                conn.close()

                da = result['dim_a_beta']
                db = result['dim_b_stability']
                dc = result['dim_c_alpha']
                rating = result['traction_rating']
                print(f"   {code} {name}")
                print(f"   牵引: {rating}")
                print(f"   β={da['full_beta']:.3f}(涨{da['up_beta']:.3f}/跌{da['down_beta']:.3f}) 不对称={da['asymmetry']:.2f}")
                print(f"   R²={db['r2']:.3f}  α={dc['alpha']:.4f}%/日  {dc['alpha_label']}")
                print(f"   {'─'*50}")
                print()


if __name__ == '__main__':
    main()
