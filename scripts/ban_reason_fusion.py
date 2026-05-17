#!/usr/bin/env python3
"""
📡 涨停原因双源融合器 v1.0

将自有涨停归因引擎 + 韭研人工异动数据 两面融合：
1. 两边涨停列表匹配（谁有谁没有）
2. 题材标签对比（自有引擎AI推理 vs 韭研人工编写）
3. 差异报告 → 哪些票我们漏了/分析错了
4. 输出融合数据 → 作战面板 + 微信推送

依赖：
  - scripts/limit_up_attribution.py（自有引擎，输出json）
  - data/jiuyuan_actions.json（韭研数据）
  - data/jiuyuan_reasons.json（韭研索引版）

输出：
  - data/ban_reasons_merged.json（融合数据 → 面板读取）
  - data/ban_reasons_diff.txt（差异报告）

用法：
  python3 scripts/ban_reason_fusion.py              # 融合今日
  python3 scripts/ban_reason_fusion.py --date 2026-05-08  # 指定日期
  python3 scripts/ban_reason_fusion.py --push       # 推送到微信
"""
import os, sys, json, re, subprocess
from datetime import datetime

BASE = os.path.expanduser("~/astock")
DATA_DIR = os.path.join(BASE, "data")
FUSION_FILE = os.path.join(DATA_DIR, "ban_reasons_merged.json")
DIFF_FILE = os.path.join(DATA_DIR, "ban_reasons_diff.txt")

# 我们的产业链板块名 → 韭研板块名 映射（用于交叉匹配）
SECTOR_MAP = {
    '芯片': ['芯片', '存储芯片', 'AI芯片', '半导体', 'PCB'],
    '算力': ['算力', 'Token工厂', '服务器', '液冷'],
    '光通信': ['光通信', '光纤', '光模块', '通信'],
    '机器人': ['机器人', '人形机器人', '减速器', '丝杠'],
    '商业航天': ['商业航天', '卫星', '航天', '火箭'],
    'AI应用': ['AI应用', 'AI', '软件', '大模型', 'Token'],
    '低空经济': ['低空', '飞行汽车', '无人机', 'eVTOL'],
    '电池': ['电池', '新能源', '锂电', '固态', '碳酸锂', '盐湖提锂'],
}

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr)


def load_own_engine(trade_date):
    """调用自有涨停归因引擎获取数据"""
    r = subprocess.run(
        ['python3', f'{BASE}/scripts/limit_up_attribution.py',
         '--date', trade_date, '--json'],
        capture_output=True, text=True, timeout=60, cwd=BASE
    )
    if r.returncode != 0 or not r.stdout.strip():
        log(f"⚠️ 自有引擎无输出: {r.stderr[:100]}")
        return None
    try:
        data = json.loads(r.stdout)
        return data
    except json.JSONDecodeError as e:
        log(f"⚠️ 自有引擎JSON解析失败: {e}")
        return None


def load_jiuyuan(trade_date):
    """加载韭研数据"""
    jy_file = os.path.join(DATA_DIR, 'jiuyuan_actions.json')
    reasons_file = os.path.join(DATA_DIR, 'jiuyuan_reasons.json')
    
    if not os.path.exists(jy_file) or not os.path.exists(reasons_file):
        log("⚠️ 韭研数据文件不存在")
        return None, None
    
    with open(jy_file) as f:
        actions = json.load(f)
    with open(reasons_file) as f:
        reasons = json.load(f)
    
    return actions, reasons


def match_stocks(own_data, jiuyuan_reasons):
    """
    匹配两个数据源的涨停列表
    
    返回:
      matched: [{code, name, own_concept, jiuyuan_tags, match_status}]
    """
    # 自有引擎的涨停列表
    own_stocks = {}  # code -> {name, concept}
    if own_data:
        for s in own_data.get('stocks', []):
            code = s.get('code', '')
            if code:
                # 统一格式：去掉sh/sz前缀
                code_clean = code.replace('SH','').replace('SZ','').replace('sh','').replace('sz','')
                own_stocks[code_clean] = {
                    'name': s.get('name', ''),
                    'concept': s.get('concept', '') or s.get('reason', ''),
                }
        # 也检查concept_groups
        for cg_name, cg_data in own_data.get('concept_groups', {}).items():
            for s in cg_data.get('stocks', []):
                code = s.get('code', '').replace('SH','').replace('SZ','').replace('sh','').replace('sz','')
                if code and code not in own_stocks:
                    own_stocks[code] = {'name': s.get('name', ''), 'concept': cg_name}
    
    # 韭研的涨停列表
    jiuyuan_stocks = {}  # code -> {name, tags, section}
    for rcode, rinfo in jiuyuan_reasons.items():
        code_clean = rcode.replace('SH','').replace('SZ','').replace('BJ','').replace('sh','').replace('sz','').replace('bj','')
        jiuyuan_stocks[code_clean] = {
            'name': rinfo.get('name', ''),
            'tags': rinfo.get('tags', ''),
            'section': rinfo.get('section', ''),
            'theme': rinfo.get('theme', ''),
        }
    
    # 所有涉及的code
    all_codes = set(own_stocks.keys()) | set(jiuyuan_stocks.keys())
    
    results = []
    for code in sorted(all_codes):
        own = own_stocks.get(code, {})
        jy = jiuyuan_stocks.get(code, {})
        
        if own and jy:
            status = '✅ 双方匹配'
        elif own and not jy:
            status = '⚠️ 仅有自有引擎'
        else:
            status = '❌ 仅有韭研'
        
        # 题材对比（自有引擎概念 vs 韭研标签）
        own_concept = own.get('concept', '')
        jy_tags = jy.get('tags', '')
        jy_section = jy.get('section', '')
        
        # 模糊判断是否一致（两者的题材描述是否指向同一个方向）
        concept_match = None
        if own_concept and jy_tags:
            # 检查关键词重叠
            own_keywords = set(re.findall(r'[\u4e00-\u9fa5a-zA-Z]+', own_concept))
            jy_keywords = set(re.findall(r'[\u4e00-\u9fa5a-zA-Z]+', jy_tags))
            overlap = own_keywords & jy_keywords
            if overlap:
                concept_match = '✅'  # 有共同关键词
            else:
                # 检查板块映射
                matched_sector = False
                for our_sector, jy_sectors in SECTOR_MAP.items():
                    our_match = any(k in own_concept for k in [our_sector] + jy_sectors)
                    jy_match = any(k in jy_tags or k in jy_section for k in jy_sectors)
                    if our_match and jy_match:
                        matched_sector = True
                        break
                concept_match = '🟡' if matched_sector else '🔴 方向不一致'
        elif own_concept and not jy_tags:
            concept_match = '⚠️ 自有引擎分析、暂无韭研'
        elif not own_concept and jy_tags:
            concept_match = '🔵 韭研有人工标签、自有引擎未覆盖'
        else:
            concept_match = '---'
        
        results.append({
            'code': code,
            'name': own.get('name', jy.get('name', '')),
            'status': status,
            'own_concept': own_concept,
            'jiuyuan_tags': jy_tags,
            'jiuyuan_section': jy_section,
            'jiuyuan_theme': jy.get('theme', ''),
            'concept_match': concept_match,
        })
    
    return results


def generate_fusion_report(matched_stocks, trade_date):
    """生成融合报告文本"""
    lines = []
    lines.append(f"📡 **涨停原因双源融合 | {trade_date}**")
    lines.append("=" * 60)
    
    total = len(matched_stocks)
    both = sum(1 for s in matched_stocks if '双方匹配' in s['status'])
    only_own = sum(1 for s in matched_stocks if '仅有自有' in s['status'])
    only_jiuyuan = sum(1 for s in matched_stocks if '仅有韭研' in s['status'])
    
    lines.append(f"📊 共{total}只涨停 | 双方匹配{both} | 仅有自有引擎{only_own} | 仅有韭研{only_jiuyuan}")
    lines.append("")
    
    # 板块聚合
    section_groups = {}
    for s in matched_stocks:
        section = s.get('jiuyuan_section', s.get('jiuyuan_theme', '其他'))
        if section not in section_groups:
            section_groups[section] = []
        section_groups[section].append(s)
    
    # 按韭研板块展示（如果韭研有数据）
    if section_groups:
        lines.append("**📂 今日涨停板块全景**")
        lines.append("")
        for section, stocks in sorted(section_groups.items(), key=lambda x: -len(x[1])):
            if len(stocks) < 2:
                continue  # 只显示2只以上的板块
            theme = stocks[0].get('jiuyuan_theme', '') if hasattr(stocks[0], 'get') else ''
            lines.append(f"**{section} ({len(stocks)}只)**")
            if theme:
                lines.append(f"  💡 {theme[:80]}")
            for s in stocks[:8]:
                tags = s.get('jiuyuan_tags', '')
                tags_str = f" → {tags[:40]}" if tags else ""
                lines.append(f"  {s['name']}({s['code'][-6:]}){tags_str}")
            if len(stocks) > 8:
                lines.append(f"  ...还有{len(stocks)-8}只")
            lines.append("")
    
    # 差异分析
    diffs = [s for s in matched_stocks if '仅有韭研' in s['status']]
    if diffs:
        lines.append(f"**⚠️ 自有引擎遗漏 {len(diffs)}只**（韭研有但我们没覆盖到）")
        for s in diffs[:10]:
            lines.append(f"  {s['name']}({s['code'][-6:]}) {s['jiuyuan_tags'][:50]}")
        if len(diffs) > 10:
            lines.append(f"  ...还有{len(diffs)-10}只")
        lines.append("")
    
    return '\n'.join(lines)


def format_push(matched_stocks, trade_date):
    """生成微信推送（精简版）"""
    lines = []
    lines.append(f"📡 **涨停原因双源融合 | {trade_date}**")
    
    total = len(matched_stocks)
    both = sum(1 for s in matched_stocks if '双方匹配' in s['status'])
    only_jy = sum(1 for s in matched_stocks if '仅有韭研' in s['status'])
    lines.append(f"共{total}只涨停，匹配{both}只，韭研独家{only_jy}只")
    lines.append("")
    
    # 板块聚合
    section_stocks = {}
    for s in matched_stocks:
        section = s.get('jiuyuan_section', '其他')
        if section not in section_stocks:
            section_stocks[section] = []
        section_stocks[section].append(s)
    
    for section, stocks in sorted(section_stocks.items(), key=lambda x: -len(x[1])):
        if len(stocks) < 2:
            continue
        theme = stocks[0].get('jiuyuan_theme', '')
        lines.append(f"**{section} ({len(stocks)}只)**")
        if theme:
            lines.append(f"  {theme[:60]}")
        lines.append("")
    
    # 差异提醒
    diffs = [s for s in matched_stocks if '仅有韭研' in s['status']]
    if diffs:
        lines.append(f"⚠️ 自有引擎遗漏{diffs[0]['name']}等{len(diffs)}只涨停原因")
    
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='📡 涨停原因双源融合')
    parser.add_argument('--date', help='日期 YYYY-MM-DD')
    parser.add_argument('--push', action='store_true', help='推送微信')
    args = parser.parse_args()
    
    trade_date = args.date or datetime.now().strftime('%Y-%m-%d')
    
    log(f"📡 涨停原因双源融合: {trade_date}")
    
    # 1. 加载两边数据
    own_data = load_own_engine(trade_date)
    jiuyuan_actions, jiuyuan_reasons = load_jiuyuan(trade_date)
    
    if not jiuyuan_reasons:
        log("⚠️ 无韭研数据，跳过融合")
        print(json.dumps({'date': trade_date, 'error': 'no_jiuyuan_data', 'stocks': [], 'report': ''}))
        return
    
    # 2. 执行匹配
    matched = match_stocks(own_data, jiuyuan_reasons)
    
    # 3. 生成报告
    report = generate_fusion_report(matched, trade_date)
    
    # 4. 保存
    output = {
        'date': trade_date,
        'timestamp': datetime.now().isoformat(),
        'total': len(matched),
        'both_match': sum(1 for s in matched if '双方匹配' in s['status']),
        'only_own': sum(1 for s in matched if '仅有自有' in s['status']),
        'only_jiuyuan': sum(1 for s in matched if '仅有韭研' in s['status']),
        'stocks': matched,
        'report': report,
    }
    
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(FUSION_FILE, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    with open(DIFF_FILE, 'w') as f:
        f.write(report)
    
    log(f"✅ 融合完成: 共{len(matched)}只, 双方匹配{output['both_match']}, 自有独家{output['only_own']}, 韭研独家{output['only_jiuyuan']}")
    
    # 输出摘要
    print(f"\n📊 双源融合报告: {trade_date}")
    print(f"  涨停: {len(matched)}只 | 双方匹配: {output['both_match']} | 自有独家: {output['only_own']} | 韭研独家: {output['only_jiuyuan']}")
    
    # 显示部分差异
    diffs = [s for s in matched if '仅有韭研' in s['status']]
    if diffs:
        print(f"\n  ⚠️ 自有引擎遗漏 {len(diffs)} 只:")
        for s in diffs[:5]:
            print(f"    {s['name']}({s['code'][-6:]}) {s['jiuyuan_tags'][:40]}")
    
    # 输出完整报告
    print(f"\n{report}")
    
    if args.push:
        push_msg = format_push(matched, trade_date)
        print(f"\n{'='*40}")
        print(push_msg)


if __name__ == '__main__':
    main()
