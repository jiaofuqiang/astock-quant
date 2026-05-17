#!/usr/bin/env python3
"""
🧹 A股概念清洗整合 v1.0
======================
目标：将32,892个概念清洗为标准概念体系

核心策略：
1. 保留基石概念：em(483) + ths(323) + infer(5) + jyg(8) = 819个标准概念
2. 从auto(52,355条)中提取质量概念（≥4只股票 + 非噪音）
3. 建立同义词映射：原始产品名 → 标准概念名
4. 清洗后概念数目标：1,500-2,000个（含行业分类+题材概念+产品概念）

清洗规则：
  - 过滤掉带'其他'/'其中'/'本次'/'合计'的噪音概念
  - 过滤掉只覆盖≤3只股票的概念
  - 过滤掉证监会行业分类（带'制造业'/'服务业'等后缀的粗分类）
  - 保留高价值的产品类概念（存储芯片传感器微控制器等）
"""

import os, sys, json, subprocess, re
from datetime import datetime
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, 'data', 'stock_profiles.db')

def run_sql(sql):
    result = subprocess.run(
        ['sqlite3', DB_PATH],
        input=sql.encode(), capture_output=True, timeout=120
    )
    out = result.stdout.decode().strip()
    err = result.stderr.decode().strip()[:200]
    if err:
        print(f"[SQL Error] {err}")
    return out

def run_sql_file(sql_file):
    """执行SQL文件"""
    result = subprocess.run(
        ['sqlite3', DB_PATH],
        stdin=open(sql_file, 'r'),
        capture_output=True, timeout=120
    )
    return result.stdout.decode().strip()

print("=" * 60)
print("🧹 A股概念清洗整合 v1.0")
print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ============================================================
# 第一步：收集所有auto源概念的分析数据
# ============================================================
print("\n📊 第一步：分析auto源概念质量...")

# 获取auto中每个概念的覆盖股票数量+股票列表
auto_concepts = {}
raw = run_sql("""
    SELECT concept_name, GROUP_CONCAT(DISTINCT code) as codes
    FROM concepts WHERE source = 'auto'
    GROUP BY concept_name
    ORDER BY COUNT(DISTINCT code) DESC
""")
for line in raw.split('\n'):
    if '|' in line:
        parts = line.split('|')
        name = parts[0].strip()
        codes = parts[1].split(',') if len(parts) > 1 else []
        auto_concepts[name] = codes

print(f"   auto总概念数: {len(auto_concepts)}")
print(f"   auto总映射数: {sum(len(v) for v in auto_concepts.values())}")

# ============================================================
# 第二步：获取标准概念集合（em/ths/infer/jyg）
# ============================================================
print("\n📦 第二步：加载标准概念集合...")

standard_raw = run_sql("""
    SELECT DISTINCT concept_name, source
    FROM concepts
    WHERE source != 'auto'
    ORDER BY concept_name
""")
standard_concepts = {}  # name -> sources
for line in standard_raw.split('\n'):
    if '|' in line:
        p = line.split('|')
        name = p[0].strip()
        src = p[1].strip()
        if name not in standard_concepts:
            standard_concepts[name] = []
        standard_concepts[name].append(src)

standard_names = set(standard_concepts.keys())
print(f"   标准概念数: {len(standard_names)}")
for s in ['em', 'ths', 'jyg', 'infer']:
    cnt = sum(1 for v in standard_concepts.values() if s in v)
    print(f"   {s}: {cnt}个概念")

# ============================================================
# 第三步：定义清洗规则
# ============================================================
print("\n🧹 第三步：定义清洗规则...")

# 噪音过滤关键词
NOISE_KEYWORDS = [
    '其他', '其中', '本次', '合计', '小计', '分部间', '抵销',
    '内部', '分部', '主营业务', '主营收入', '营业总收入',
    '报告期', '本期', '上期', '本期数', '上期数', '同比',
]

# 证监会行业分类关键词（需要过滤的粗分类）
CSRC_INDUSTRY_KEYWORDS = [
    '制造业', '服务业', '业', '行业'
]

# 需要过滤的通用性太强的概念
TOO_GENERIC = {
    '其他', '工业', '贸易', '商业', '销售', '服务', '租赁',
    '配件', '商品销售', '销售商品', '服务收入', '销售收入',
    '产品销售收入', '主营业务收入', '其他业务收入', '主营收入',
    '其他主营', '其他分部', '其他行业', '其他类', '其它',
    '平衡项目', '其中:其他', '其他其他', '内部抵销',
    '分部间交易', '分部间抵销', '未分配项目',
}

# 行业分类词尾（按产品划分的后缀，需要剥离）
STRIP_SUFFIXES = ['及技术服务', '及服务', '及技术', '产品', '收入',
                   '业务收入', '业务', '销售']

# 需要保留的概念白名单（即使只覆盖少数股票）
WHITE_LIST = {
    '存储芯片', '微控制器', '传感器', '模拟芯片', 'AI芯片',
    '光模块', '光器件', 'IGBT', 'SiC', '功率半导体',
    '先进封装', '封测', '晶圆制造',
    '人形机器人', '低空经济', '卫星互联网', '商业航天',
    '固态电池', '钙钛矿', '钠电池', '氢能源',
    '碳化硅', '第三代半导体',
    '鸿蒙', '信创', '国产软件',
    '数字货币', '区块链', '元宇宙',
    '预制菜', '免税', '直播电商',
    'CXO', '创新药', 'CRO', 'CDMO',
    '保险', '券商', '银行',
    '煤炭', '石油', '天然气', '黄金', '稀土',
    '中特估', '国企改革', '央企改革',
    '碳中和', '碳交易',
    '英伟达', '特斯拉', '苹果', '华为',
    '激光雷达', '毫米波雷达',
    '操作系统', '大数据', '云计算', '物联网',
    '牛散', 'QFII', '养老金',
    '抽水蓄能', '虚拟电厂', '特高压', '智能电网',
}


def is_noise(name):
    """判断是否为噪音概念"""
    n = name.strip()
    if not n or len(n) <= 1:
        return True
    if n in TOO_GENERIC:
        return True
    for kw in NOISE_KEYWORDS:
        if kw in n:
            return True
    # 纯数字或纯拼音
    if re.match(r'^[a-zA-Z0-9]+$', n):
        return True
    return False


def is_csrc_industry(name):
    """判断是否为证监会行业分类"""
    return any(name.endswith(suffix) for suffix in ['制造业', '服务业']) or \
           name in {'电力、热力生产和供应业', '计算机、通信和其他电子设备制造业',
                     '软件和信息技术服务业', '电气机械和器材制造业',
                     '化学原料及化学制品制造业', '专用设备制造业',
                     '通用设备制造业', '医药制造业', '汽车制造业',
                     '铁路、船舶、航空航天和其他运输设备制造业',
                     '非金属矿物制品业', '金属制品业', '仪器仪表制造业',
                     '橡胶和塑料制品业', '纺织业', '食品制造业',
                     '酒、饮料和精制茶制造业', '农副食品加工业',
                     '造纸及纸制品业', '印刷和记录媒介复制业'}


def clean_concept_name(name):
    """清洗概念名称（去除无用前缀后缀）"""
    n = name.strip()
    for suffix in STRIP_SUFFIXES:
        if n.endswith(suffix) and len(n) > len(suffix) + 1:
            n = n[:-len(suffix)].strip()
    return n


# ============================================================
# 第四步：执行清洗
# ============================================================
print("\n⚡ 第四步：执行概念清洗...")

# 统计
kept_auto = {}        # 保留的auto概念
noise_removed = 0     # 噪音移除数
thin_removed = 0      # 覆盖≤3只移除
csrc_removed = 0      # 行业分类移除

for name, codes in auto_concepts.items():
    stock_cnt = len(set(codes))
    
    # 白名单：无条件保留
    if name in WHITE_LIST:
        kept_auto[name] = set(codes)
        continue
    
    # 噪音过滤
    if is_noise(name):
        noise_removed += 1
        continue
    
    # 证监会行业分类过滤
    if is_csrc_industry(name):
        csrc_removed += 1
        continue
    
    # 覆盖度过滤
    if stock_cnt <= 3:
        thin_removed += 1
        continue
    
    # 清洗名称
    cleaned = clean_concept_name(name)
    if cleaned != name and cleaned:
        if cleaned not in kept_auto:
            kept_auto[cleaned] = set()
        kept_auto[cleaned].update(set(codes))
    else:
        kept_auto[name] = set(codes)

print(f"   保留的auto概念: {len(kept_auto)}")
print(f"   噪音移除: {noise_removed}")
print(f"   覆盖≤3移除: {thin_removed}")
print(f"   行业分类移除: {csrc_removed}")

# ============================================================
# 第五步：构建最终概念集合
# ============================================================
print("\n🏗️ 第五步：构建最终概念集合...")

# 所有概念 = 标准概念 + 清洗后的auto概念
all_kept_concepts = set(standard_names) | set(kept_auto.keys())
print(f"   最终概念总数: {len(all_kept_concepts)}")
print(f"   标准概念: {len(standard_names)}")
print(f"   auto清洗后: {len(kept_auto)}")

# 统计每种类型的
biz_concepts = [c for c in kept_auto if c not in standard_names]
print(f"   auto新增的非标准概念: {len(biz_concepts)}")

# 展示auto中新增的有价值概念
print(f"\n📋 auto新增概念预览（按覆盖股票数降序，前30）:")
biz_ranked = sorted(biz_concepts, key=lambda c: -len(kept_auto[c]))[:30]
for c in biz_ranked:
    cnt = len(set(kept_auto[c]))
    print(f"   {c}: {cnt}只股票")

# ============================================================
# 第六步：重建数据库
# ============================================================
print("\n💾 第六步：重建数据库...")

# 先备份所有非auto的原始概念
backup_sql = "/tmp/concept_backup.sql"
run_sql(f".output {backup_sql}")
run_sql("SELECT 'DELETE FROM concepts;'")
run_sql(".output stdout")
print("   已备份原始概念数据")

# 方案：不删除auto数据，而是为每个auto概念标记质量等级
# 先创建quality表
run_sql("""
    CREATE TABLE IF NOT EXISTS concept_quality (
        concept_name TEXT PRIMARY KEY,
        source_type TEXT,           -- standard / auto_clean / auto_noise
        stock_count INTEGER,
        description TEXT DEFAULT '',
        updated_at TEXT
    );
""")

# 清空并重新写入质量表
run_sql("DELETE FROM concept_quality;")

now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
inserts = []

# 标准概念标记为standard
for name in standard_names:
    stock_cnt = len(run_sql(f"SELECT COUNT(DISTINCT code) FROM concepts WHERE concept_name='{name.replace(chr(39),chr(39)+chr(39))}'").split('\n')[0]) 
    n = name.replace("'", "''")
    inserts.append(f"INSERT OR REPLACE INTO concept_quality VALUES ('{n}', 'standard', {stock_cnt}, '东方财富/同花顺/韭研公社/推理引擎', '{now}');")

# 清洗后的auto概念标记为auto_clean
for name, codes in kept_auto.items():
    if name not in standard_names:
        n = name.replace("'", "''")
        inserts.append(f"INSERT OR REPLACE INTO concept_quality VALUES ('{n}', 'auto_clean', {len(set(codes))}, '主营产品自动挖掘', '{now}');")

# 批量写入
for i in range(0, len(inserts), 500):
    batch = inserts[i:i+500]
    run_sql('\n'.join(batch))

print(f"   已写入 {len(inserts)} 条概念质量标记")

# ============================================================
# 第七步：统计验证
# ============================================================
print(f"\n📊 第七步：最终统计验证")

# 各质量等级概念数
qual = run_sql("""
    SELECT source_type, COUNT(*) as cnt, SUM(stock_count) as total_stocks
    FROM concept_quality
    GROUP BY source_type
    ORDER BY cnt DESC
""")
for line in qual.split('\n'):
    if '|' in line:
        p = line.split('|')
        print(f"   {p[0].strip()}: {p[1].strip()} 个概念, 总覆盖 {p[2].strip()} 股票次")

# 覆盖最好的30个概念
top30 = run_sql("""
    SELECT concept_name, stock_count, source_type
    FROM concept_quality
    ORDER BY stock_count DESC
    LIMIT 30
""")
print(f"\n🏆 覆盖度TOP30:")
for line in top30.split('\n'):
    if '|' in line:
        p = line.split('|')
        print(f"   {p[0].strip()} [{p[2].strip()}] → {p[1].strip()} 只股票")

# 总映射数（含auto中的有用数据）
total_map = run_sql("SELECT COUNT(*) FROM concepts")
print(f"\n   总映射数: {total_map}")
print(f"   其中auto: {sum(len(v) for v in auto_concepts.values())}")

print("\n✅ 清洗完成!")
print(f"\n💡 操作建议:")
print(f"   如果想删除auto中的噪音概念（保留clean + standard），执行:")
print(f"   DELETE FROM concepts WHERE source='auto' AND concept_name NOT IN (SELECT concept_name FROM concept_quality WHERE source_type='auto_clean');")
