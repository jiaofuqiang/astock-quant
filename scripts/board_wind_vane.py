#!/usr/bin/env python3
"""打板赚钱/风险风向标算法 v1
从bundle读取实时数据，综合历史回测规律判断今日打板胜率与风险

输出: /home/ubuntu/V2board/data/board_wind_vane.json
"""

import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
ASTOCK = os.path.expanduser('~/astock')
V2BOARD = os.path.join(ASTOCK, '..', 'V2board')
BUNDLE_PATH = os.path.join(V2BOARD, 'dashboard_bundle.json')
OUTPUT_PATH = os.path.join(V2BOARD, 'data', 'board_wind_vane.json')


def safe_float(v, default=0.0):
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def safe_int(v, default=0):
    if v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def parse_board_dist(bd_str):
    """解析board_dist_json（可能是字符串或dict）"""
    if isinstance(bd_str, dict):
        return bd_str
    if isinstance(bd_str, str):
        try:
            return json.loads(bd_str)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def get_max_board(md, bundle):
    """获取最高连板数"""
    # 1. 从board_dist_json
    bd = parse_board_dist(md.get('board_dist_json', '{}'))
    if bd:
        boards = [int(k) for k in bd.keys()]
        if boards:
            return max(boards)

    # 2. 从watch_dashboard ladder_stocks
    wd = bundle.get('watch_dashboard', {})
    ls = wd.get('ladder_stocks', [])
    if ls:
        bs = [s.get('boards', 0) for s in ls]
        if bs:
            return max(bs)

    # 3. fallback
    return safe_int(md.get('max_board', 0))


def count_alerts_by_type(bundle, alert_type=None):
    """统计板块预警数量"""
    alerts = bundle.get('sector_alerts', [])
    if not alerts:
        return 0
    if alert_type:
        return sum(1 for a in alerts if a.get('type') == alert_type)
    return len(alerts)


def extract_retail_sentiment(bundle):
    """从散户情绪文本提取接盘信号数量"""
    rs = bundle.get('retail_sentiment', '')
    if not isinstance(rs, str):
        return 50  # 默认中等
    # 解析接盘信号数量
    import re
    match = re.search(r'接盘信号[：:\s]*(\d+)', rs)
    if match:
        return int(match.group(1))
    match = re.search(r'🔴接盘信号[：:\s]*(\d+)', rs)
    if match:
        return int(match.group(1))
    return 50  # 默认


def get_sector_mainline_score(bundle):
    """判断板块主线清晰度：有主线加分"""
    # 从time_slice的dim_cluster看l4_板块
    ts = bundle.get('time_slice', {})
    dc = ts.get('dim_cluster', [])
    if dc:
        l4 = dc[0].get('l4_板块', '')
        # 如果l4包含"无主线"或"无主题"则得分低
        if '无主线' in l4 or '无主题' in l4 or '无' in l4:
            return 0  # 无主线
        if '主线' in l4 or '主题' in l4:
            return 2  # 有主线
        return 1  # 模糊

    # 降级：从watch_dashboard看是否有明确主线
    wd = bundle.get('watch_dashboard', {})
    main_lines = wd.get('main_lines', [])
    if main_lines and len(main_lines) > 0:
        return 2  # 有主线
    return 0


def compute_profit_vane(md, bundle):
    """赚钱风向标计算"""
    signals = []
    score = 50  # 基准分

    # 1. 隔夜溢价环境
    pretoday_avg_change = safe_float(md.get('pretoday_avg_change', 0))
    if pretoday_avg_change > 1.5:
        score += 12
        signals.append({'name': '隔夜溢价', 'value': f'{pretoday_avg_change:.1f}%', 'impact': 'positive', 'weight': 12})
    elif pretoday_avg_change > 0.5:
        score += 6
        signals.append({'name': '隔夜溢价', 'value': f'{pretoday_avg_change:.1f}%', 'impact': 'slightly_positive', 'weight': 6})
    elif pretoday_avg_change < 0:
        score -= 10
        signals.append({'name': '隔夜溢价', 'value': f'{pretoday_avg_change:.1f}%', 'impact': 'negative', 'weight': -10})
    else:
        signals.append({'name': '隔夜溢价', 'value': f'{pretoday_avg_change:.1f}%', 'impact': 'neutral', 'weight': 0})

    # 2. 炸板率环境
    zhaban_rate = safe_float(md.get('zhaban_rate', 50))
    # 注意：zhaban_rate可能为0（盘前无数据），此时用discard
    if zhaban_rate > 0 and zhaban_rate < 20:
        score += 12
        signals.append({'name': '封板率', 'value': f'{100-zhaban_rate:.0f}%', 'impact': 'positive', 'weight': 12})
    elif zhaban_rate > 35:
        score -= 12
        signals.append({'name': '封板率', 'value': f'{100-zhaban_rate:.0f}%', 'impact': 'negative', 'weight': -12})
    elif zhaban_rate > 0:
        score += 3
        signals.append({'name': '封板率', 'value': f'{100-zhaban_rate:.0f}%', 'impact': 'slightly_positive', 'weight': 3})
    else:
        signals.append({'name': '封板率', 'value': '暂无数据', 'impact': 'neutral', 'weight': 0})

    # 3. 连板高度
    max_board = get_max_board(md, bundle)
    if max_board >= 5:
        score += 10
        signals.append({'name': '连板高度', 'value': f'{max_board}板', 'impact': 'positive', 'weight': 10})
    elif max_board >= 3:
        score += 5
        signals.append({'name': '连板高度', 'value': f'{max_board}板', 'impact': 'slightly_positive', 'weight': 5})
    elif max_board <= 2:
        score -= 10
        signals.append({'name': '连板高度', 'value': f'{max_board}板', 'impact': 'negative', 'weight': -10})
    else:
        signals.append({'name': '连板高度', 'value': f'{max_board}板', 'impact': 'neutral', 'weight': 0})

    # 4. 板块主线清晰度
    mainline_score = get_sector_mainline_score(bundle)
    if mainline_score >= 2:
        score += 12
        signals.append({'name': '板块主线', 'value': '有清晰主线', 'impact': 'positive', 'weight': 12})
    elif mainline_score >= 1:
        score += 4
        signals.append({'name': '板块主线', 'value': '主题模糊', 'impact': 'slightly_positive', 'weight': 4})
    else:
        score -= 8
        signals.append({'name': '板块主线', 'value': '无主线/轮动', 'impact': 'negative', 'weight': -8})

    # 5. 涨停数量
    limit_up = safe_int(md.get('limit_up', 0))
    if limit_up >= 40:
        score += 10
        signals.append({'name': '涨停数量', 'value': f'{limit_up}只', 'impact': 'positive', 'weight': 10})
    elif limit_up >= 25:
        score += 5
        signals.append({'name': '涨停数量', 'value': f'{limit_up}只', 'impact': 'slightly_positive', 'weight': 5})
    elif limit_up < 20:
        score -= 10
        signals.append({'name': '涨停数量', 'value': f'{limit_up}只', 'impact': 'negative', 'weight': -10})
    else:
        signals.append({'name': '涨停数量', 'value': f'{limit_up}只', 'impact': 'neutral', 'weight': 0})

    # 6. 涨跌比（zh_ratio）
    zh_ratio = safe_float(md.get('zh_ratio', 50))
    if zh_ratio >= 55:
        score += 10
        signals.append({'name': '涨跌比', 'value': f'{zh_ratio:.1f}%', 'impact': 'positive', 'weight': 10})
    elif zh_ratio >= 45:
        score += 4
        signals.append({'name': '涨跌比', 'value': f'{zh_ratio:.1f}%', 'impact': 'slightly_positive', 'weight': 4})
    elif zh_ratio < 40:
        score -= 10
        signals.append({'name': '涨跌比', 'value': f'{zh_ratio:.1f}%', 'impact': 'negative', 'weight': -10})
    else:
        signals.append({'name': '涨跌比', 'value': f'{zh_ratio:.1f}%', 'impact': 'neutral', 'weight': 0})

    # 7. 竞价强弱
    auction = bundle.get('auction', {})
    strong_signals = auction.get('strong_signals', {})
    bid_buy = safe_int(strong_signals.get('buy', 0))
    bid_sell = safe_int(strong_signals.get('sell', 0))
    # 竞价数据可能没有，用market_daily的bid_trend
    bid_trend = md.get('bid_trend', '')
    bid_gaokai_rate = safe_float(md.get('bid_gaokai_rate', 0))
    bid_limit_count = safe_int(md.get('bid_limit_count', 0))

    bid_score = 0
    if bid_buy > bid_sell:
        bid_score += 5
    if bid_gaokai_rate > 50:
        bid_score += 5
    if bid_limit_count >= 10:
        bid_score += 5
    if '强' in str(bid_trend) or '积极' in str(bid_trend):
        bid_score += 5

    if bid_score >= 10:
        score += 10
        signals.append({'name': '竞价强弱', 'value': '竞价强势', 'impact': 'positive', 'weight': 10})
    elif bid_score >= 5:
        score += 5
        signals.append({'name': '竞价强弱', 'value': '竞价偏强', 'impact': 'slightly_positive', 'weight': 5})
    elif bid_score <= 0 and (bid_buy > 0 or bid_gaokai_rate > 0):
        score -= 5
        signals.append({'name': '竞价强弱', 'value': '竞价偏弱', 'impact': 'negative', 'weight': -5})
    else:
        signals.append({'name': '竞价强弱', 'value': '暂无数据', 'impact': 'neutral', 'weight': 0})

    # 8. 大盘涨跌
    env = bundle.get('market_env', {})
    changes = env.get('changes', {})
    sh_change = safe_float(changes.get('000001', {}).get('change_pct', 0))
    if sh_change > 0:
        score += 8
        signals.append({'name': '大盘涨跌', 'value': f'上证{sh_change:+.1f}%', 'impact': 'positive', 'weight': 8})
    elif sh_change > -0.5:
        score += 2
        signals.append({'name': '大盘涨跌', 'value': f'上证{sh_change:+.1f}%', 'impact': 'slightly_positive', 'weight': 2})
    elif sh_change < -0.5:
        score -= 8
        signals.append({'name': '大盘涨跌', 'value': f'上证{sh_change:+.1f}%', 'impact': 'negative', 'weight': -8})
    else:
        signals.append({'name': '大盘涨跌', 'value': f'上证{sh_change:+.1f}%', 'impact': 'neutral', 'weight': 0})

    # 9. 游资活跃度
    yz = bundle.get('youzi_signal', {})
    yz_sentiment = yz.get('sentiment_score', 50)
    if isinstance(yz_sentiment, (int, float)):
        if yz_sentiment >= 60:
            score += 8
            signals.append({'name': '游资活跃', 'value': f'得分{yz_sentiment:.0f}', 'impact': 'positive', 'weight': 8})
        elif yz_sentiment >= 40:
            signals.append({'name': '游资活跃', 'value': f'得分{yz_sentiment:.0f}', 'impact': 'neutral', 'weight': 0})
        else:
            score -= 8
            signals.append({'name': '游资活跃', 'value': f'得分{yz_sentiment:.0f}', 'impact': 'negative', 'weight': -8})
    else:
        # 用sentiment文本判断
        yz_label = yz.get('sentiment', '')
        if '狂热' in str(yz_label) or '活跃' in str(yz_label):
            score += 8
            signals.append({'name': '游资活跃', 'value': str(yz_label), 'impact': 'positive', 'weight': 8})
        elif '冰点' in str(yz_label) or '低迷' in str(yz_label):
            score -= 8
            signals.append({'name': '游资活跃', 'value': str(yz_label), 'impact': 'negative', 'weight': -8})
        else:
            signals.append({'name': '游资活跃', 'value': str(yz_label), 'impact': 'neutral', 'weight': 0})

    # 裁剪分数到0-100
    score = max(0, min(100, score))

    # 等级判定
    if score >= 80:
        level = '🟢极好'
    elif score >= 65:
        level = '🔵良好'
    elif score >= 45:
        level = '🟡一般'
    elif score >= 25:
        level = '🟠较差'
    else:
        level = '🔴极差'

    # 推荐策略
    if score >= 80:
        top_strategy = '🔥 全面出击——M08连板缩量/M01隔夜溢价/总龙头均可参与'
    elif score >= 65:
        top_strategy = '✅ 精选参与——优先M08连板缩量·2板、M01隔夜溢价·优'
    elif score >= 45:
        top_strategy = '⚠️ 谨慎打板——仅参与M01隔夜溢价·极品、M08·3板 高确定性机会'
    elif score >= 25:
        top_strategy = '🛡️ 防守为主——M12超卖反弹首板、M01隔夜溢价·极品(缩量极小)'
    else:
        top_strategy = '🚫 空仓观望——打板亏钱概率高，不宜出手'

    return {
        'score': score,
        'level': level,
        'signals': signals,
        'top_strategy_today': top_strategy,
    }


def compute_risk_vane(md, bundle):
    """风险风向标计算"""
    signals = []
    danger_signals = []
    score = 50  # 基准分（越高越安全，越低越危险）

    # 1. 炸板率高 → 亏钱风险
    zhaban_rate = safe_float(md.get('zhaban_rate', 50))
    if zhaban_rate > 35 and zhaban_rate <= 100:
        score -= 15
        danger_signals.append(f'⚠️ 炸板率{zhaban_rate:.0f}%＞35%，封板失败率高')
        signals.append({'name': '炸板率', 'value': f'{zhaban_rate:.0f}%', 'severity': 'high', 'weight': -15})
    elif zhaban_rate > 20:
        score -= 8
        danger_signals.append(f'⚡ 炸板率{zhaban_rate:.0f}%偏高')
        signals.append({'name': '炸板率', 'value': f'{zhaban_rate:.0f}%', 'severity': 'medium', 'weight': -8})
    elif zhaban_rate > 0:
        signals.append({'name': '炸板率', 'value': f'{zhaban_rate:.0f}%', 'severity': 'low', 'weight': 0})
    else:
        signals.append({'name': '炸板率', 'value': '暂无数据', 'severity': 'low', 'weight': 0})

    # 2. 跌停数 > 30 → 恐慌
    limit_down = safe_int(md.get('limit_down', 0))
    if limit_down > 30:
        score -= 15
        danger_signals.append(f'⚠️ 跌停{limit_down}只＞30，恐慌蔓延')
        signals.append({'name': '跌停数量', 'value': f'{limit_down}只', 'severity': 'high', 'weight': -15})
    elif limit_down > 15:
        score -= 8
        danger_signals.append(f'⚡ 跌停{limit_down}只偏多')
        signals.append({'name': '跌停数量', 'value': f'{limit_down}只', 'severity': 'medium', 'weight': -8})
    else:
        signals.append({'name': '跌停数量', 'value': f'{limit_down}只', 'severity': 'low', 'weight': 0})

    # 3. 散户情绪狂热 → 反指亏钱
    retail_count = extract_retail_sentiment(bundle)
    if retail_count > 60:
        score -= 12
        danger_signals.append(f'⚠️ 散户接盘信号{retail_count}只＞60，情绪过热反指')
        signals.append({'name': '散户情绪', 'value': f'{retail_count}只接盘', 'severity': 'high', 'weight': -12})
    elif retail_count > 30:
        score -= 6
        danger_signals.append(f'⚡ 散户接盘信号{retail_count}只偏多')
        signals.append({'name': '散户情绪', 'value': f'{retail_count}只接盘', 'severity': 'medium', 'weight': -6})
    else:
        signals.append({'name': '散户情绪', 'value': f'{retail_count}只接盘', 'severity': 'low', 'weight': 0})

    # 4. 板块预警多 → 板块退潮
    alert_count = count_alerts_by_type(bundle)
    if alert_count >= 5:
        score -= 10
        danger_signals.append(f'⚠️ 板块预警{alert_count}条，板块退潮信号')
        signals.append({'name': '板块预警', 'value': f'{alert_count}条', 'severity': 'high', 'weight': -10})
    elif alert_count >= 3:
        score -= 5
        signals.append({'name': '板块预警', 'value': f'{alert_count}条', 'severity': 'medium', 'weight': -5})
    else:
        signals.append({'name': '板块预警', 'value': f'{alert_count}条', 'severity': 'low', 'weight': 0})

    # 5. 最高板只有2板以下 → 无龙头
    max_board = get_max_board(md, bundle)
    if max_board <= 2:
        score -= 12
        danger_signals.append(f'⚠️ 最高仅{max_board}板，无龙头带队')
        signals.append({'name': '龙头高度', 'value': f'{max_board}板', 'severity': 'high', 'weight': -12})
    elif max_board <= 3:
        score -= 5
        signals.append({'name': '龙头高度', 'value': f'{max_board}板', 'severity': 'medium', 'weight': -5})
    else:
        signals.append({'name': '龙头高度', 'value': f'{max_board}板', 'severity': 'low', 'weight': 0})

    # 6. 昨日涨停隔日溢价 < 0 → 亏钱效应
    pretoday_avg_change = safe_float(md.get('pretoday_avg_change', 0))
    if pretoday_avg_change < 0:
        score -= 12
        danger_signals.append(f'⚠️ 昨日涨停隔日溢价{pretoday_avg_change:.1f}%＜0，亏钱效应')
        signals.append({'name': '隔日溢价', 'value': f'{pretoday_avg_change:.1f}%', 'severity': 'high', 'weight': -12})
    elif pretoday_avg_change < 0.5:
        score -= 5
        signals.append({'name': '隔日溢价', 'value': f'{pretoday_avg_change:.1f}%', 'severity': 'medium', 'weight': -5})
    else:
        signals.append({'name': '隔日溢价', 'value': f'{pretoday_avg_change:.1f}%', 'severity': 'low', 'weight': 0})

    # 7. 游资活跃度低
    yz = bundle.get('youzi_signal', {})
    yz_score = yz.get('sentiment_score', 50)
    if isinstance(yz_score, (int, float)):
        if yz_score < 30:
            score -= 10
            danger_signals.append(f'⚠️ 游资活跃度低(得分{yz_score:.0f})，不出手')
            signals.append({'name': '游资活跃度', 'value': f'得分{yz_score:.0f}', 'severity': 'high', 'weight': -10})
        elif yz_score < 45:
            score -= 5
            signals.append({'name': '游资活跃度', 'value': f'得分{yz_score:.0f}', 'severity': 'medium', 'weight': -5})
        else:
            signals.append({'name': '游资活跃度', 'value': f'得分{yz_score:.0f}', 'severity': 'low', 'weight': 0})
    else:
        yz_label = yz.get('sentiment', '')
        if '冰点' in str(yz_label) or '低迷' in str(yz_label):
            score -= 10
            danger_signals.append(f'⚠️ 游资情绪冰点，不出手')
            signals.append({'name': '游资活跃度', 'value': str(yz_label), 'severity': 'high', 'weight': -10})
        elif '观望' in str(yz_label):
            score -= 5
            signals.append({'name': '游资活跃度', 'value': str(yz_label), 'severity': 'medium', 'weight': -5})
        else:
            signals.append({'name': '游资活跃度', 'value': str(yz_label), 'severity': 'low', 'weight': 0})

    # 8. 涨跌比 < 40 → 市场宽度差
    zh_ratio = safe_float(md.get('zh_ratio', 50))
    if zh_ratio < 40:
        score -= 14
        danger_signals.append(f'⚠️ 涨跌比{zh_ratio:.1f}%＜40，市场宽度极差')
        signals.append({'name': '市场宽度', 'value': f'{zh_ratio:.1f}%', 'severity': 'high', 'weight': -14})
    elif zh_ratio < 45:
        score -= 7
        signals.append({'name': '市场宽度', 'value': f'{zh_ratio:.1f}%', 'severity': 'medium', 'weight': -7})
    else:
        signals.append({'name': '市场宽度', 'value': f'{zh_ratio:.1f}%', 'severity': 'low', 'weight': 0})

    # 裁剪分数到0-100（这里score越高越安全，需要反转表示风险）
    # 对于风险风向标，score=0表示极度危险，score=100表示无风险
    # 我们用 100 - score 表示风险等级
    score = max(0, min(100, score))
    risk_score = 100 - score  # 风险分数

    # 风险等级判定
    if risk_score >= 70:
        level = '极高'
    elif risk_score >= 50:
        level = '高'
    elif risk_score >= 30:
        level = '中'
    else:
        level = '低'

    return {
        'score': risk_score,
        'level': level,
        'signals': signals,
        'danger_signals': danger_signals,
    }


def compute_verdict(profit_vane, risk_vane):
    """综合结论"""
    ps = profit_vane['score']
    rs = risk_vane['score']

    if ps >= 70 and rs < 30:
        return '🟢 赚钱概率高 + 风险低 → 积极打板日！优先M01/M08高胜率策略'
    elif ps >= 55 and rs < 45:
        return '🔵 赚钱概率中等偏高 + 风险可控 → 精选标的打板，控制仓位'
    elif ps >= 45 and rs < 55:
        return '🟡 赚钱概率一般 + 风险中等 → 仅参与最高确定性机会，轻仓试错'
    elif ps < 45 and rs >= 50:
        return '🟠 赚钱概率低 + 风险偏高 → 防守为主，不打板或少打板'
    elif rs >= 60:
        return '🔴 风险极高 → 空仓观望，不参与打板'
    else:
        return '🟡 市场环境中性 → 精选策略，控制仓位'


def main():
    # 读取bundle
    if not os.path.exists(BUNDLE_PATH):
        # 尝试fallback路径
        fallback = os.path.join(V2BOARD, 'data', 'dashboard_bundle.json')
        if os.path.exists(fallback):
            bundle_path = fallback
        else:
            print(f"[board_wind_vane] 错误: bundle文件未找到", file=sys.stderr)
            result = {
                'engine': 'board-wind-vane-v1',
                'timestamp': datetime.now().isoformat(),
                'error': f'bundle未找到: {BUNDLE_PATH}',
                'profit_vane': {'score': 50, 'level': '🟡一般', 'signals': [], 'top_strategy_today': '数据不足，等待bundle'},
                'risk_vane': {'score': 50, 'level': '中', 'signals': [], 'danger_signals': ['数据不足']},
                'verdict': '等待数据...'
            }
            os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
            with open(OUTPUT_PATH, 'w') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            return result
    else:
        bundle_path = BUNDLE_PATH

    try:
        with open(bundle_path) as f:
            bundle = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[board_wind_vane] 错误: 读取bundle失败: {e}", file=sys.stderr)
        return None

    md = bundle.get('market_daily', {})

    # 计算
    profit_vane = compute_profit_vane(md, bundle)
    risk_vane = compute_risk_vane(md, bundle)
    verdict = compute_verdict(profit_vane, risk_vane)

    result = {
        'engine': 'board-wind-vane-v1',
        'timestamp': datetime.now().isoformat(),
        'profit_vane': profit_vane,
        'risk_vane': risk_vane,
        'verdict': verdict,
    }

    # 写入输出
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[board_wind_vane] 已输出 → {OUTPUT_PATH}")
    print(f"  赚钱风向标: {profit_vane['score']}分 | {profit_vane['level']}")
    print(f"  风险风向标: {risk_vane['score']}分 | {risk_vane['level']}")
    print(f"  综合结论: {verdict}")

    return result


if __name__ == '__main__':
    result = main()
    if result:
        # 输出JSON到stdout供dashboard_aggregator捕获
        print('---JSON---')
        print(json.dumps(result, ensure_ascii=False))
