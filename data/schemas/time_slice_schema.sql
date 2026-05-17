-- =============================================
-- 📊 时间片基因库 Schema  v1.0
-- =============================================
-- 存储市场每5分钟的快照，包含：
--   1. 实时采集（交易时段每5分钟）
--   2. 历史模拟（日K反推）
--   3. 九维穿透标签
--   4. 特征向量（用于历史匹配）
-- =============================================

-- 时间片快照主表
CREATE TABLE IF NOT EXISTS time_slices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,           -- 日期 '2026-05-13'
    ts TEXT NOT NULL,             -- 时间戳 '09:35' (取整到5分钟)
    source TEXT DEFAULT 'live',   -- 'live'=实时采集, 'simulated'=日K模拟, 'backfill'=回填
    UNIQUE(date, ts, source)
);

-- 维度1: 大盘指数层
CREATE TABLE IF NOT EXISTS dim_market (
    slice_id INTEGER PRIMARY KEY,
    sh_chg REAL,                  -- 上证涨幅%
    sz_chg REAL,                  -- 深证涨幅%
    cy_chg REAL,                  -- 创业板涨幅%
    sh_amount REAL,               -- 上证成交额(亿)
    market_main_net REAL,         -- 主力净流入(亿)
    avg_ma20_dev REAL,            -- 平均偏离MA20%
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 维度2: 涨跌与涨停层
CREATE TABLE IF NOT EXISTS dim_limit (
    slice_id INTEGER PRIMARY KEY,
    up_count INTEGER,             -- 上涨家数
    down_count INTEGER,           -- 下跌家数
    zh_ratio REAL,                -- 涨跌比%
    limit_up INTEGER,             -- 涨停数
    limit_down INTEGER,           -- 跌停数
    max_board INTEGER,            -- 最高板数
    yizi INTEGER,                 -- 一字板数
    suoliang INTEGER,             -- 缩量板数
    fangliang INTEGER,            -- 放量板数
    total_seal_wan REAL,          -- 总封单额(万)
    zhaban INTEGER,               -- 炸板数
    zhaban_rate REAL,             -- 炸板率%
    huifeng INTEGER,              -- 回封数
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 维度3: 量价结构层
CREATE TABLE IF NOT EXISTS dim_volume (
    slice_id INTEGER PRIMARY KEY,
    vol_lt_05 INTEGER,            -- 量比<0.5
    vol_05_07 INTEGER,            -- 量比0.5~0.7
    vol_07_1 INTEGER,             -- 量比0.7~1
    vol_1_3 INTEGER,              -- 量比1~3
    vol_3_5 INTEGER,              -- 量比3~5
    vol_gt_5 INTEGER,             -- 量比>5
    gap_1_3 INTEGER,              -- 高开1~3%
    gap_3_5 INTEGER,              -- 高开3~5%
    gap_5_7 INTEGER,              -- 高开5~7%
    gap_gt_7 INTEGER,             -- 高开>7%
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 维度4: 板块结构层
CREATE TABLE IF NOT EXISTS dim_sector (
    slice_id INTEGER PRIMARY KEY,
    sector_boom_count INTEGER,    -- 板块爆发数(≥3涨停板块数)
    sector_total INTEGER,         -- 有涨停的板块数
    top1_lu INTEGER,              -- TOP1板块涨停数
    top1_name TEXT,               -- TOP1板块名
    top2_lu INTEGER,              -- TOP2板块涨停数
    top2_name TEXT,               -- TOP2板块名
    top3_lu INTEGER,              -- TOP3板块涨停数
    top3_name TEXT,               -- TOP3板块名
    top1_concentration REAL,      -- TOP1集中度%
    sector_main_top TEXT,         -- 主力板块TOP
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 维度5: 资金博弈层
CREATE TABLE IF NOT EXISTS dim_fund (
    slice_id INTEGER PRIMARY KEY,
    youzi_buy_wan REAL,           -- 游资买入(万)
    youzi_sell_wan REAL,          -- 游资卖出(万)
    youzi_net_wan REAL,           -- 游资净额(万)
    jigou_buy_wan REAL,           -- 机构买入(万)
    jigou_sell_wan REAL,          -- 机构卖出(万)
    jigou_net_wan REAL,           -- 机构净额(万)
    sanhu_buy_wan REAL,           -- 散户买入(万)
    sanhu_sell_wan REAL,          -- 散户卖出(万)
    sanhu_net_wan REAL,           -- 散户净额(万)
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 维度6: 情绪与动量层
CREATE TABLE IF NOT EXISTS dim_sentiment (
    slice_id INTEGER PRIMARY KEY,
    market_mood TEXT,             -- 市场情绪: 恐慌/谨慎/活跃/亢奋
    panic_score REAL,             -- 恐慌指数
    boom_score REAL,              -- 亢奋指数
    surge_count INTEGER,          -- 大涨数(>7%)
    spike_count INTEGER,          -- 脉冲数
    crash_count INTEGER,          -- 大跌数(<-7%)
    avg_60d_retrace REAL,         -- 均60日回撤%
    avg_5d_momentum REAL,         -- 均5日动量%
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 维度7: 竞价层 (仅09:15-09:25有效)
CREATE TABLE IF NOT EXISTS dim_auction (
    slice_id INTEGER PRIMARY KEY,
    bid_trend TEXT,               -- 竞价趋势: 走强/走弱/横盘
    bid_gaokai_rate REAL,         -- 竞价高开率%
    bid_limit_count INTEGER,      -- 竞价涨停数
    bid_amount REAL,              -- 竞价金额
    bid_trend_score REAL,         -- 竞价趋势分数(-1~1)
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 九维穿透标签 (实时计算的9层分类)
CREATE TABLE IF NOT EXISTS dim_cluster (
    slice_id INTEGER PRIMARY KEY,
    l1_热度 TEXT,                  -- 标签名 如 ☀️狂热
    l1_score INTEGER,              -- 得分 0-4
    l2_风格 TEXT,                  -- 放量游资/缩量惜售/机构趋势/散户博弈
    l2_score INTEGER,
    l3_效应 TEXT,                  -- 龙头接力/首板套利/板块集群/轮动打地鼠
    l3_score INTEGER,
    l4_板块 TEXT,                  -- 集中主线/双线并行/散乱多线/无主线
    l4_score INTEGER,
    l5_量价 TEXT,                  -- 缩量惜售/量价健康/量价温和/量价虚胖
    l5_score INTEGER,
    l6_趋势 TEXT,                  -- 加速冲顶/强势延续/震荡筑底/超跌反弹
    l6_score INTEGER,
    l7_情绪 TEXT,                  -- 绝望/恐慌/悲观/怀疑/乐观/狂热/幻灭/平衡
    l7_score INTEGER,
    l8_轮动 TEXT,                  -- 单主线/双线轮动/高速轮动/混沌
    l8_score INTEGER,
    l9_博弈 TEXT,                  -- 合力做多/游资主导/机构主导/分歧
    l9_score INTEGER,
    full_tag TEXT,                 -- 完整标签串
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 特征向量 (归一化数值向量, 用于欧氏距离匹配)
CREATE TABLE IF NOT EXISTS feature_vector (
    slice_id INTEGER PRIMARY KEY,
    -- 12维特征向量 F0~F11
    f00 REAL,  -- 热度值  (归一化0-1)
    f01 REAL,  -- 涨停密度 (涨停数/总股票数, 归一化)
    f02 REAL,  -- 涨跌比 (归一化)
    f03 REAL,  -- 板块集中度 (TOP1板块涨停数/总涨停数)
    f04 REAL,  -- 龙头高度 (最高板归一化 /7)
    f05 REAL,  -- 缩量比 (缩量板/涨停总数)
    f06 REAL,  -- 炸板率 (归一化)
    f07 REAL,  -- 大资金方向 (主力净入, sign(-1~1))
    f08 REAL,  -- 量比分布 (放量/缩量倾向)
    f09 REAL,  -- 情绪动量 (MA20偏移归一化)
    f10 REAL,  -- 板块宽度 (有涨停板块数/总板块数)
    f11 REAL,  -- 高开强度 (高开>3%占比)
    FOREIGN KEY(slice_id) REFERENCES time_slices(id)
);

-- 今日快照索引 (用于读取今天已采集的点)
CREATE TABLE IF NOT EXISTS snapshot_log (
    date TEXT NOT NULL,
    ts TEXT NOT NULL,
    slice_id INTEGER,
    status TEXT DEFAULT 'ok',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(date, ts)
);

CREATE INDEX IF NOT EXISTS idx_slices_date_ts ON time_slices(date, ts);
CREATE INDEX IF NOT EXISTS idx_snapshot_log_date ON snapshot_log(date, ts);
CREATE INDEX IF NOT EXISTS idx_feature_slice ON feature_vector(slice_id);
CREATE INDEX IF NOT EXISTS idx_cluster_slice ON dim_cluster(slice_id);

-- 集群匹配结果表 (引擎运行结果)
CREATE TABLE IF NOT EXISTS match_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    ts TEXT NOT NULL,
    target_slice_id INTEGER,
    -- 匹配到的历史
    match_slice_id INTEGER,
    similarity REAL,
    -- 后续走势
    next_30min_chg REAL,
    next_60min_chg REAL,
    next_120min_chg REAL,
    close_chg REAL,
    -- 后续标签演变
    next_tag TEXT,
    -- 策略推荐
    best_strategy TEXT,
    strategy_score REAL,
    strategy_n INTEGER,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 趋势预测结果表
CREATE TABLE IF NOT EXISTS prediction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    ts TEXT NOT NULL,
    current_tag TEXT,
    -- 预测路径 (JSON)
    predicted_path TEXT,
    -- 各路径概率 (JSON)
    path_probs TEXT,
    -- 推荐策略
    recommend_strategy TEXT,
    recommend_detail TEXT,
    accuracy_check TEXT,           -- 事后校验
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
