#!/usr/bin/env python3
"""
📰 A股公告概念监控 v2.0 (精度优化版)
========================
改进：
1. 只匹配≥3字且有明确概念意义的复合概念
2. 过滤掉公告标题中的常见干扰词
3. 优先匹配长概念（长匹配优先）
"""

import os, sys, json, time, subprocess
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, 'data', 'stock_profiles.db')

HEADERS = 'User-Agent: Mozilla/5.0'

# ⚠️ 噪音黑名单：公告标题中高频出现但没有概念意义的短词
NOISE_WORDS = {
    '公司', '资金', '投资', '证券', '发行', '金融', '材料', '银行',
    '开发', '技术', '电子', '国际', '能源', '企业', '配套', '医疗',
    '汽车', '医药', '制药', '创新', '商品', '装备', '集成', '系统',
    '机械', '化学', '药品', '电器', '境外', '制造', '工业', '器械',
    '矿业', '设计', '物流', '航空', '石化', '服务', '销售', '贸易',
    '工程', '电力', '零售', '农业', '期货', '信用', '设备', '检测',
    '资源', '管理', '咨询', '信息', '数字', '数据', '网络', '科技',
    '智能', '绿色', '生态', '城市', '农村', '上海', '北京', '深圳',
    '市场', '交易', '指数', '标准', '价值', '成长', '大盘', '小盘',
    '中盘', '能源', '环境', '保险', '地产', '电站', '运营',
}

# 复合概念白名单：≥3字且有意义的概念关键词
# 这些词在公告标题中出现时才是真的有概念关联
COMPOUND_CONCEPTS = {
    # AI/芯片
    '人工智能', 'AI芯片', '算力芯片', '存储芯片', '国产芯片', '汽车芯片',
    '半导体', '功率半导体', '第三代半导体', '碳化硅', '先进封装',
    '光芯片', 'MCU芯片', '模拟芯片', '数字芯片',
    '光模块', '光通信', '光器件', 'CPO', '液冷', '服务器',
    '数据中心', '算力', '云计算', 'AI服务器', '交换机',
    # 新能源
    '锂电池', '固态电池', '钠电池', '钙钛矿', '氢能源', '燃料电池',
    '新能源汽车', '充电桩', '换电', '光伏', '储能', '风电', '核电',
    '新能源', '太阳能', '锂矿', '盐湖提锂',
    # 机器人/智能
    '机器人', '人形机器人', '工业机器人', '机器视觉',
    '智能驾驶', '自动驾驶', '无人驾驶', '激光雷达', '毫米波雷达',
    '智能家居', '智能穿戴', '物联网',
    # 医药
    '创新药', '生物医药', '中药', 'CXO', 'CRO', 'CDMO',
    '医疗器械', '体外诊断', '基因测序', '疫苗',
    # 消费
    '白酒', '食品饮料', '预制菜', '免税', '跨境电商', '直播电商',
    '新零售', '旅游',
    # 周期
    '有色金属', '稀土永磁', '黄金', '煤炭', '钢铁', '石油',
    '化工', '磷化工', '氟化工', '有机硅', '钛白粉',
    # 军工
    '军工', '航天', '航空', '卫星互联网', '低空经济', '商业航天',
    '大飞机', '无人机', 'eVTOL',
    # 科技
    '信创', '鸿蒙', '操作系统', '大数据', '国产软件',
    '5G', '6G', '卫星导航', '北斗',
    '区块链', '数字货币', '元宇宙', 'AIGC', 'ChatGPT',
    'DeepSeek', 'Kimi', '智谱AI',
    # 基建
    '房地产', '基建', '水利', '环保', '碳中和', '碳交易',
    '特高压', '智能电网', '虚拟电厂', '抽水蓄能',
    # 金融
    '银行', '券商', '保险', '互联网金融', '数字货币',
    # 其他
    '国企改革', '中特估', '一带一路', '华为', '苹果', '特斯拉', '英伟达',
    '小米', '腾讯', '阿里', '百度',
    '专精特新', '独角兽', '并购重组',
}


def curl_get(url, timeout=8):
    try:
        r = subprocess.run(
            ['curl', '-s', url, '-H', HEADERS, '--connect-timeout', str(timeout), '--max-time', str(timeout+5)],
            capture_output=True, timeout=timeout+8
        )
        return r.stdout.decode('utf-8', errors='replace')
    except:
        return None

def run_sql(sql):
    r = subprocess.run(['sqlite3', DB_PATH], input=sql.encode(), capture_output=True, timeout=120)
    return r.stdout.decode().strip()

def match_concepts(title):
    """从公告标题中匹配有意义的复合概念"""
    matched = set()
    title_lower = title.lower()
    
    # 先匹配长概念（≥4字），避免短概念被误匹配
    long_first = sorted(COMPOUND_CONCEPTS, key=len, reverse=True)
    
    for concept in long_first:
        if len(concept) < 3:
            continue
        cl = concept.lower()
        if cl in title_lower:
            # 只有长概念（≥4字）或出现在有意义上下文才匹配
            if len(concept) >= 4:
                matched.add(concept)
            elif len(concept) == 3:
                # 3字概念需要边界检查
                idx = title_lower.find(cl)
                if idx > 0:
                    prev_char = title_lower[idx-1]
                    if not ('\u4e00' <= prev_char <= '\u9fff'):
                        matched.add(concept)
                elif idx == 0:
                    matched.add(concept)
    
    return matched

def get_stock_list():
    out = run_sql("SELECT code, name FROM stock_basic ORDER BY code")
    stocks = []
    for line in out.split('\n'):
        if '|' in line:
            p = line.split('|')
            stocks.append((p[0].strip(), p[1].strip()))
    return stocks

def fetch_announcements(code, pages=1):
    titles = []
    for page in range(1, pages + 1):
        url = (f"https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1"
               f"&page_size=10&page_index={page}&ann_type=A&stock_list={code}"
               f"&f_node=0&s_node=0")
        raw = curl_get(url)
        if not raw:
            break
        try:
            data = json.loads(raw)
            items = data.get('data', {}).get('list', [])
            if not items:
                break
            for item in items:
                titles.append(item.get('title_ch', ''))
        except:
            break
    return code, titles

def batch_fetch(stocks, max_workers=10, pages=1):
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(fetch_announcements, code, pages): code for code, _ in stocks}
        for f in as_completed(futures):
            try:
                code, titles = f.result()
                if titles:
                    results[code] = titles
            except:
                pass
    return results

def main():
    print(f"📰 公告概念监控 v2.0 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    # 加载已知概念（只保留有意义的概念名）
    qc = run_sql("SELECT concept_name FROM concept_quality WHERE source_type IN ('standard', 'auto_clean')")
    db_concepts = set(l.strip() for l in qc.split('\n') if l.strip() and len(l.strip()) >= 3)
    db_concepts.update(COMPOUND_CONCEPTS)
    # 过滤噪音
    db_concepts = {c for c in db_concepts if c not in NOISE_WORDS}
    # 按长度排序
    sorted_concepts = sorted(db_concepts, key=len, reverse=True)
    
    stocks = get_stock_list()
    print(f"📋 股票: {len(stocks)} 只, 概念库: {len(sorted_concepts)} 个")
    
    BATCH = 200
    total_new = 0
    
    for i in range(0, len(stocks), BATCH):
        batch = stocks[i:i+BATCH]
        end = min(i+BATCH, len(stocks))
        
        ann_data = batch_fetch(batch, max_workers=10, pages=1)
        
        new_maps = []
        for code, titles in ann_data.items():
            matched = set()
            title_text = ' '.join(titles)
            title_lower = title_text.lower()
            
            # 使用排序后的长概念优先匹配
            for c in sorted_concepts:
                cl = c.lower()
                if cl in title_lower and len(cl) >= 3:
                    matched.add(c)
            
            for c in matched:
                new_maps.append((code, c))
        
        if new_maps:
            seen = set()
            unique_maps = []
            for code, c in new_maps:
                key = f"{code}|{c}"
                if key not in seen:
                    seen.add(key)
                    unique_maps.append((code, c))
            
            inserts = []
            for code, c in unique_maps:
                cn = c.replace("'", "''")
                inserts.append(f"INSERT OR IGNORE INTO concepts (code, concept_name, source) VALUES ('{code}', '{cn}', 'ann');")
            
            if inserts:
                sql = '\n'.join(inserts)
                run_sql(sql)
                total_new += len(inserts)
        
        batch_pct = (i // BATCH + 1) * 100 // ((len(stocks) + BATCH - 1) // BATCH)
        print(f"   {batch_pct}% ({end}/{len(stocks)}) 新增+{len([x for x in ann_data]) if 'ann_data' in dir() else 0}")
    
    print(f"\n{'='*50}")
    print(f"✅ 完成! 新增: {total_new} 条")
    
    final = run_sql("SELECT COUNT(DISTINCT concept_name) || '概念, ' || COUNT(*) || '映射, ' || COUNT(DISTINCT code) || '股票' FROM concepts")
    print(f"📌 当前: {final}")
    
    ann_src = run_sql("SELECT COUNT(*) FROM concepts WHERE source='ann'")
    print(f"📰 ann来源: {ann_src} 条")

if __name__ == '__main__':
    main()
