#!/usr/bin/env python3
"""
Amazon 销量预测系统
==================

架构:
  - 周模型: 全局 XGB+LGBM 均值 (纯ML, 48个特征)
  - 日模型: ML+WMA 动态融合 (40个ML特征, 权重根据趋势和数据量自适应)

流程:
  service = ForecastService()
  service.init('订单.xlsx', '库存.xlsx')   # 一次性 (~90秒)
  service.train()                          # 训练全局模型 (~11秒)
  service.append(新数据)                    # 每周追加
  result = service.predict(sku, site, ...)  # 预测 (~120ms)
  service.backtest('SKU', 'US', 8)         # 回测验证

命令行:
  python forecast.py init                           # 初始化
  python forecast.py train                          # 训练
  python forecast.py predict <SKU> <站点> [day|week] [天数]
  python forecast.py backtest [SKU] [站点] [周数]   # 回测
  python forecast.py status                         # 状态
"""

import pandas as pd
import numpy as np
from datetime import timedelta, datetime
from xgboost import XGBRegressor
from lightgbm import LGBMRegressor
from sklearn.preprocessing import LabelEncoder
import pickle, os, json, re, warnings, sys
from pathlib import Path

warnings.filterwarnings('ignore')

# ====================================================================
# 1. 常量与配置
# ====================================================================

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = PROJECT_ROOT / 'forecast_assets' / 'state'
TRAINING_DATA_DIR = PROJECT_ROOT / 'forecast_assets' / 'training_data'
CACHE_PATH = str(STATE_DIR / 'cache_final.pkl')

WEEK_FEATURES = [
    'week_of_year', 'month', 'quarter', 'year',
    'week_sin', 'week_cos', 'month_sin', 'month_cos',
    'lag_1w', 'lag_2w', 'lag_3w', 'lag_4w', 'lag_8w', 'lag_12w', 'lag_52w',
    'roll_2w_mean', 'roll_4w_mean', 'roll_8w_mean', 'roll_12w_mean',
    'roll_2w_std', 'roll_4w_std', 'roll_8w_std',
    'roll_2w_median', 'roll_4w_median', 'roll_8w_median',
    'roll_4w_max', 'roll_4w_min',
    'trend_2v2', 'trend_4v4', 'yoy_ratio',
    'avg_price', 'price_change', 'price_relative', 'has_discount', 'avg_discount',
    'inventory', 'in_stock', 'stock_weeks', 'cv_4w',
    'event_impact', 'is_major_promo', 'is_warmup', 'is_post_holiday',
    # 全局模型额外特征
    'sku_site_id', 'site_id', 'sku_mean', 'sku_std', 'sku_price',
]

DAY_FEATURES = [
    'day_of_week', 'day_of_month', 'month', 'week_of_year', 'quarter', 'year',
    'is_weekend', 'dow_sin', 'dow_cos', 'month_sin', 'month_cos',
    'lag_1d', 'lag_2d', 'lag_3d', 'lag_7d', 'lag_14d', 'lag_21d', 'lag_28d',
    'roll_7d_mean', 'roll_14d_mean', 'roll_28d_mean',
    'roll_7d_median', 'roll_14d_median',
    'roll_7d_std', 'roll_14d_std',
    'nonzero_ratio_7d',
    'trend_7v28',
    'avg_price', 'price_change_pct', 'price_relative', 'has_discount', 'avg_discount',
    'event_impact', 'is_major_promo', 'is_warmup', 'is_post_holiday',
    # 全局模型额外特征
    'sku_site_id', 'site_id', 'sku_mean_daily', 'sku_std_daily',
]

MARKETPLACE_MAP = {
    'amazon.com': 'US', 'Amazon.com': 'US', 'sim1.stores.amazon.com': 'US',
    'amazon.ca': 'CA', 'siprod.stores.amazon.ca': 'CA',
    'amazon.de': 'DE', 'si-prod-marketplace-de.stores.amazon.de': 'DE',
    'amazon.fr': 'FR', 'si-prod-fr-marketplace.stores.amazon.fr': 'FR',
    'amazon.it': 'IT', 'siprod.stores.amazon.it': 'IT',
    'amazon.es': 'ES', 'siprod.stores.amazon.es': 'ES',
    'amazon.nl': 'NL',
}
ORDER_TYPES = {'Order', 'Bestellung', 'Commande', 'Ordine', 'Bestelling', 'Pedido'}
MONTH_MAP = {
    'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
    'mär':3,'mai':5,'okt':10,'dez':12,'ene':1,'abr':4,'ago':8,'sept':9,'dic':12,
    'janv':1,'févr':2,'mars':3,'avr':4,'juin':6,'juil':7,'août':8,'déc':12,
    'mrt':3,'mei':5,'gen':1,'mag':5,'giu':6,'lug':7,'set':9,'ott':10,
}


# ====================================================================
# 2. 促销日历
# ====================================================================

def _black_friday(year):
    """计算Black Friday日期 (11月第4个星期四的后一天)"""
    # 11月1日的星期几 (0=Mon, 3=Thu)
    nov1 = pd.Timestamp(year, 11, 1)
    # 第一个星期四
    first_thu = nov1 + pd.Timedelta(days=(3 - nov1.dayofweek) % 7)
    # 第四个星期四
    fourth_thu = first_thu + pd.Timedelta(weeks=3)
    return fourth_thu + pd.Timedelta(days=1)  # Friday


# Prime Day 和 Fall Prime 已知日期 (Amazon 每年公布，无固定规则)
_PRIME_DAY = {
    2022: ('2022-07-12', '2022-07-13'),
    2023: ('2023-07-11', '2023-07-12'),
    2024: ('2024-07-16', '2024-07-17'),
    2025: ('2025-07-08', '2025-07-09'),
}
_FALL_PRIME = {
    2022: ('2022-10-11', '2022-10-12'),
    2023: ('2023-10-10', '2023-10-11'),
    2024: ('2024-10-08', '2024-10-09'),
    2025: ('2025-10-07', '2025-10-08'),
}


def _promo_events(year_start=None, year_end=None):
    """返回所有促销事件列表 [(start, end, type, impact), ...]

    Parameters:
        year_start: 起始年份，默认2022
        year_end: 结束年份(含)，默认为当前年份+2
    """
    ys = year_start or 2022
    ye = year_end or (datetime.now().year + 2)
    events = []
    for y in range(ys, ye + 1):
        # --- Prime Day (已知年份用实际日期, 未来用7月第2周估算) ---
        if y in _PRIME_DAY:
            ps, pe = _PRIME_DAY[y]
        else:
            ps, pe = f'{y}-07-15', f'{y}-07-16'  # 估算
        events.append((ps, pe, 'promo', 3.0))
        # warmup: Prime Day 前5天
        warmup_start = (pd.to_datetime(ps) - pd.Timedelta(days=5)).strftime('%Y-%m-%d')
        warmup_end = (pd.to_datetime(ps) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        events.append((warmup_start, warmup_end, 'warmup', 1.5))

        # --- Black Friday (动态计算) ---
        bf = _black_friday(y)
        bf_s = bf.strftime('%Y-%m-%d')
        bf_e = bf.strftime('%Y-%m-%d')
        events.append((bf_s, bf_e, 'promo', 2.5))
        # BF warmup: 前4天
        bf_warmup_s = (bf - pd.Timedelta(days=4)).strftime('%Y-%m-%d')
        bf_warmup_e = (bf - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        events.append((bf_warmup_s, bf_warmup_e, 'warmup', 1.5))
        # Cyber Monday: BF + 2天 (周一)
        cm = bf + pd.Timedelta(days=2)
        events.append((cm.strftime('%Y-%m-%d'), cm.strftime('%Y-%m-%d'), 'promo', 2.0))

        # --- 圣诞季 ---
        events.append((f'{y}-12-01', f'{y}-12-20', 'promo', 1.5))
        events.append((f'{y}-12-25', f'{y}-12-31', 'post', 0.5))
        events.append((f'{y}-01-01', f'{y}-01-07', 'post', 0.7))

        # --- Fall Prime Day (已知年份用实际日期, 未来用10月第2周估算) ---
        if y in _FALL_PRIME:
            fps, fpe = _FALL_PRIME[y]
        else:
            fps, fpe = f'{y}-10-14', f'{y}-10-15'  # 估算
        events.append((fps, fpe, 'promo', 2.0))
        fp_warmup_s = (pd.to_datetime(fps) - pd.Timedelta(days=5)).strftime('%Y-%m-%d')
        fp_warmup_e = (pd.to_datetime(fps) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        events.append((fp_warmup_s, fp_warmup_e, 'warmup', 1.3))

        # --- 春促 ---
        events.append((f'{y}-03-25', f'{y}-03-31', 'promo', 1.3))

    return events


def _get_event_features(date, is_weekly=False):
    """获取某一天/周的事件特征

    Parameters:
        date: 日期 (日粒度=具体日期, 周粒度=week_start周一)
        is_weekly: 如果为True, 检查整周[date, date+6]是否与事件重叠
    """
    r = {'event_impact': 1.0, 'is_major_promo': 0, 'is_warmup': 0, 'is_post_holiday': 0}
    if is_weekly:
        week_end = date + pd.Timedelta(days=6)
    # 只需检查该日期所在年份的事件 (跨年边界多取1年)
    y = date.year
    for s, e, typ, imp in _promo_events(y - 1, y + 1):
        es, ee = pd.to_datetime(s), pd.to_datetime(e)
        if is_weekly:
            hit = (date <= ee) and (week_end >= es)
        else:
            hit = (es <= date <= ee)
        if hit:
            if typ == 'warmup':
                r['is_warmup'] = 1
                r['event_impact'] = max(r['event_impact'], imp)
            elif typ == 'post':
                r['is_post_holiday'] = 1
                r['event_impact'] = min(r['event_impact'], imp)
            else:
                r['is_major_promo'] = 1
                r['event_impact'] = max(r['event_impact'], imp)
    return r


def _add_events(df, date_col='date', is_weekly=False):
    """批量为DataFrame添加事件特征列

    Parameters:
        is_weekly: 如果为True, date_col是week_start(周一), 检查整周[周一, 周日]是否与事件重叠
    """
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df['event_impact'] = 1.0
    df['is_major_promo'] = 0
    df['is_warmup'] = 0
    df['is_post_holiday'] = 0
    # 根据数据的实际时间范围生成事件
    year_start = df[date_col].min().year
    year_end = df[date_col].max().year
    for s, e, typ, imp in _promo_events(year_start, year_end):
        es, ee = pd.to_datetime(s), pd.to_datetime(e)
        if is_weekly:
            # 周数据: 检查 [week_start, week_start+6] 是否与 [es, ee] 有交集
            week_end = df[date_col] + pd.Timedelta(days=6)
            m = (df[date_col] <= ee) & (week_end >= es)
        else:
            m = (df[date_col] >= es) & (df[date_col] <= ee)
        if typ == 'warmup':
            df.loc[m, 'is_warmup'] = 1
            df.loc[m, 'event_impact'] = np.maximum(df.loc[m, 'event_impact'], imp)
        elif typ == 'post':
            df.loc[m, 'is_post_holiday'] = 1
            df.loc[m, 'event_impact'] = np.minimum(df.loc[m, 'event_impact'], imp)
        else:
            df.loc[m, 'is_major_promo'] = 1
            df.loc[m, 'event_impact'] = np.maximum(df.loc[m, 'event_impact'], imp)
    return df


# ====================================================================
# 3. 日期解析
# ====================================================================

def _parse_date(s):
    if pd.isna(s): return pd.NaT
    s = str(s).strip()
    m = re.match(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', s)
    if m:
        try: return pd.Timestamp(year=int(m.group(3)), month=int(m.group(2)), day=int(m.group(1)))
        except: return pd.NaT
    m = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', s)
    if m:
        mo = MONTH_MAP.get(m.group(1).lower()[:3])
        if mo:
            try: return pd.Timestamp(year=int(m.group(3)), month=mo, day=int(m.group(2)))
            except: return pd.NaT
    m = re.match(r'(\d{1,2})\s+([A-Za-zÀ-ÿ.]+)\s+(\d{4})', s)
    if m:
        ms = m.group(2).lower().rstrip('.')[:4]
        mo = MONTH_MAP.get(ms) or MONTH_MAP.get(ms[:3])
        if mo:
            try: return pd.Timestamp(year=int(m.group(3)), month=mo, day=int(m.group(1)))
            except: return pd.NaT
    try: return pd.Timestamp(re.sub(r'\s+(PST|PDT|UTC|CET|CEST|GMT)$', '', s))
    except: return pd.NaT


# ====================================================================
# 4. 数据加载与处理 (从原始xlsx到特征工程)
# ====================================================================

def _load_and_process(force=False, cache_path=None, orders_file=None, inventory_file=None):
    """
    从原始xlsx加载、清洗、特征工程，返回 {'daily': DataFrame, 'weekly': DataFrame}

    Parameters:
        force: 是否强制重新处理(忽略缓存)
        cache_path: 缓存路径，默认使用 CACHE_PATH
        orders_file: 订单文件路径，默认使用目录下的销售订单历史记录
        inventory_file: 库存文件路径，默认使用目录下的库存明细
    """
    cp = cache_path or CACHE_PATH
    if not force and os.path.exists(cp):
        with open(cp, 'rb') as f:
            return pickle.load(f)

    print("  加载原始数据 (首次约3-5分钟) ...")
    orders_path = orders_file or str(TRAINING_DATA_DIR / '销售订单历史记录0709.xlsx')
    inv_path = inventory_file or str(TRAINING_DATA_DIR / '2022-20260630每日库存明细.xlsx')

    # --- 订单 ---
    df = pd.read_excel(orders_path, engine='openpyxl')
    print(f"  订单: {len(df)} 行")
    df = df[df['type'].isin(ORDER_TYPES)].copy()
    for c in ['product sales', 'promotional rebates', 'shipping credits']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0).astype(int)
    df = df[df['product sales'] != 0]
    df = df[~((df['promotional rebates'].abs() == df['product sales'].abs()) & (df['promotional rebates'] != 0))]
    print(f"  有效订单: {len(df)} 行")

    df['datetime'] = df['date/time'].apply(_parse_date)
    df = df.dropna(subset=['datetime'])
    df['date'] = df['datetime'].dt.date
    df['site'] = df['marketplace'].map(MARKETPLACE_MAP).fillna('Unknown')

    # 单品售价
    osku = df.groupby(['order id', 'sku']).agg({
        'product sales': 'sum', 'quantity': 'sum',
        'promotional rebates': 'sum', 'shipping credits': 'sum'
    }).reset_index()
    osku['unit_price'] = osku['product sales'] / osku['quantity'].replace(0, np.nan)
    osku['actual_discount'] = (osku['promotional rebates'].abs() - osku['shipping credits'].abs()).clip(lower=0)
    osku['discount_rate'] = (osku['actual_discount'] / osku['product sales'].replace(0, np.nan)).fillna(0).clip(0, 1)
    df = df.merge(osku[['order id', 'sku', 'unit_price', 'discount_rate']],
                  on=['order id', 'sku'], how='left', suffixes=('', '_c'))

    # 账号不属于预测粒度，最终聚合不能保留账号，否则同一天会产生重复行。
    ol = df.groupby(['date', 'order id', 'sku', 'site']).agg({
        'quantity': 'sum', 'product sales': 'sum',
        'unit_price': 'first', 'discount_rate': 'first'
    }).reset_index()
    daily = ol.groupby(['date', 'sku', 'site']).agg({
        'quantity': 'sum', 'product sales': 'sum',
        'unit_price': 'mean', 'discount_rate': 'mean', 'order id': 'nunique'
    }).reset_index()
    daily.rename(columns={
        'quantity': 'daily_sales', 'product sales': 'daily_revenue',
        'unit_price': 'avg_price', 'discount_rate': 'avg_discount', 'order id': 'order_count'
    }, inplace=True)
    daily['date'] = pd.to_datetime(daily['date'])

    # 填充缺失日期
    filled = []
    for (sku, site), g in daily.groupby(['sku', 'site']):
        dr = pd.date_range(g['date'].min(), g['date'].max(), freq='D')
        fg = pd.DataFrame({'date': dr}).merge(g, on='date', how='left')
        fg['sku'], fg['site'] = sku, site
        fg['daily_sales'] = fg['daily_sales'].fillna(0)
        fg['daily_revenue'] = fg['daily_revenue'].fillna(0)
        fg['order_count'] = fg['order_count'].fillna(0)
        fg['avg_price'] = fg['avg_price'].ffill().bfill()
        fg['avg_discount'] = fg['avg_discount'].fillna(0)
        filled.append(fg)
    daily = pd.concat(filled, ignore_index=True)

    # 异常值 (极端保守)
    for (sku, site), g in daily.groupby(['sku', 'site']):
        if len(g) < 60: continue
        nz = g['daily_sales'][g['daily_sales'] > 0]
        if len(nz) < 20: continue
        Q3, IQR = nz.quantile(0.75), nz.quantile(0.75) - nz.quantile(0.25)
        upper = Q3 + 4 * IQR
        rm = g['daily_sales'].rolling(30, min_periods=7, center=True).mean()
        outliers = (g['daily_sales'] > upper) & (g['daily_sales'] > rm * 5) & (g['daily_sales'] > 0)
        if outliers.any():
            med = g['daily_sales'].rolling(7, center=True, min_periods=1).median()
            daily.loc[g.index[outliers], 'daily_sales'] = med[outliers]

    daily = _add_events(daily)

    # --- 周聚合 ---
    daily['week_start'] = daily['date'] - pd.to_timedelta(daily['date'].dt.dayofweek, unit='D')
    weekly = daily.groupby(['sku', 'site', 'week_start']).agg({
        'daily_sales': 'sum', 'avg_price': 'mean', 'avg_discount': 'mean',
        'order_count': 'sum', 'daily_revenue': 'sum'
    }).reset_index()
    weekly.rename(columns={
        'daily_sales': 'weekly_sales', 'daily_revenue': 'weekly_revenue', 'week_start': 'date'
    }, inplace=True)
    weekly = _add_events(weekly, is_weekly=True)

    # --- 周特征 ---
    weekly = weekly.sort_values(['sku', 'site', 'date'])
    gc = ['sku', 'site']
    tgt = 'weekly_sales'
    weekly['week_of_year'] = weekly['date'].dt.isocalendar().week.astype(int)
    weekly['month'] = weekly['date'].dt.month
    weekly['quarter'] = weekly['date'].dt.quarter
    weekly['year'] = weekly['date'].dt.year
    weekly['week_sin'] = np.sin(2 * np.pi * weekly['week_of_year'] / 52)
    weekly['week_cos'] = np.cos(2 * np.pi * weekly['week_of_year'] / 52)
    weekly['month_sin'] = np.sin(2 * np.pi * weekly['month'] / 12)
    weekly['month_cos'] = np.cos(2 * np.pi * weekly['month'] / 12)
    for lag in [1, 2, 3, 4, 8, 12, 52]:
        weekly[f'lag_{lag}w'] = weekly.groupby(gc)[tgt].shift(lag)
    for w in [2, 4, 8, 12]:
        weekly[f'roll_{w}w_mean'] = weekly.groupby(gc)[tgt].transform(
            lambda x: x.shift(1).rolling(w, min_periods=1).mean())
        weekly[f'roll_{w}w_std'] = weekly.groupby(gc)[tgt].transform(
            lambda x: x.shift(1).rolling(w, min_periods=2).std())
        weekly[f'roll_{w}w_median'] = weekly.groupby(gc)[tgt].transform(
            lambda x: x.shift(1).rolling(w, min_periods=1).median())
    weekly['roll_4w_max'] = weekly.groupby(gc)[tgt].transform(
        lambda x: x.shift(1).rolling(4, min_periods=1).max())
    weekly['roll_4w_min'] = weekly.groupby(gc)[tgt].transform(
        lambda x: x.shift(1).rolling(4, min_periods=1).min())
    weekly['trend_2v2'] = (weekly['roll_2w_mean'] / weekly.groupby(gc)[tgt].transform(
        lambda x: x.shift(3).rolling(2, min_periods=1).mean()).replace(0, np.nan) - 1).clip(-2, 5).fillna(0)
    weekly['trend_4v4'] = (weekly['roll_4w_mean'] / weekly.groupby(gc)[tgt].transform(
        lambda x: x.shift(5).rolling(4, min_periods=1).mean()).replace(0, np.nan) - 1).clip(-2, 5).fillna(0)
    weekly['yoy_ratio'] = (weekly['lag_1w'] / weekly['lag_52w'].replace(0, np.nan)).clip(0, 10).fillna(1)
    weekly['price_lag1'] = weekly.groupby(gc)['avg_price'].shift(1)
    weekly['price_change'] = ((weekly['avg_price'] - weekly['price_lag1']) /
                              weekly['price_lag1'].replace(0, np.nan)).clip(-0.5, 0.5).fillna(0)
    weekly['price_relative'] = (weekly['avg_price'] / weekly.groupby(gc)['avg_price'].transform(
        lambda x: x.shift(1).rolling(12, min_periods=4).mean()).replace(0, np.nan)).clip(0.5, 2).fillna(1)
    weekly['has_discount'] = (weekly['avg_discount'] > 0.01).astype(int)
    weekly['cv_4w'] = (weekly['roll_4w_std'] / weekly['roll_4w_mean'].replace(0, np.nan)).clip(0, 5).fillna(1)

    # --- 库存 (EU→五站) ---
    print("  加载库存 (EU→DE/FR/IT/ES/NL) ...")
    inv = pd.read_excel(inv_path, engine='openpyxl')
    inv.columns = ['date', 'sku', 'inventory', 'account', 'site_code']
    inv['date'] = pd.to_datetime(inv['date'])
    inv['inventory'] = pd.to_numeric(inv['inventory'], errors='coerce')
    inv['site_code'] = inv['site_code'].astype(str).str.upper()
    eu_sites = ['DE', 'FR', 'IT', 'ES', 'NL']
    eu = inv[inv['site_code'] == 'EU'][['date', 'sku', 'inventory']]
    non_eu = inv[inv['site_code'] != 'EU'][
        ['date', 'sku', 'inventory', 'site_code']
    ].rename(columns={'site_code': 'site'})
    inv_df = pd.concat(
        [non_eu] + [eu.assign(site=site) for site in eu_sites], ignore_index=True)
    inv_df = inv_df.sort_values('date')

    # 日、周两条链路都使用库存特征。
    inv_d = inv_df.groupby(['sku', 'site', 'date'], as_index=False)['inventory'].last()
    daily = daily.merge(inv_d, on=['sku', 'site', 'date'], how='left')
    daily = daily.sort_values(['sku', 'site', 'date'])
    daily['inventory'] = daily.groupby(['sku', 'site'])['inventory'].ffill()
    daily['in_stock'] = (daily['inventory'].fillna(0) > 0).astype(int)

    inv_df['week_start'] = inv_df['date'] - pd.to_timedelta(inv_df['date'].dt.dayofweek, unit='D')
    inv_w = inv_df.groupby(['sku', 'site', 'week_start'])['inventory'].last().reset_index()
    inv_w.rename(columns={'week_start': 'date'}, inplace=True)
    weekly = weekly.merge(inv_w, on=['sku', 'site', 'date'], how='left')
    weekly['inventory'] = weekly.groupby(['sku', 'site'])['inventory'].ffill()
    weekly['in_stock'] = (weekly['inventory'].fillna(0) > 0).astype(int)
    weekly['stock_weeks'] = (weekly['inventory'] / weekly['roll_4w_mean'].replace(0, np.nan)).clip(0, 52).fillna(0)
    print(f"  库存匹配率: {weekly['inventory'].notna().mean()*100:.1f}%")

    data = {'daily': daily, 'weekly': weekly}
    with open(cp, 'wb') as f:
        pickle.dump(data, f)
    print(f"  缓存已保存: {cp}")
    return data


# ====================================================================
# 5. 主服务类
# ====================================================================

class ForecastService:
    """
    销量预测服务

    使用流程:
        service = ForecastService()
        service.init('订单.xlsx', '库存.xlsx')    # 一次性
        service.train()                           # 训练全局模型
        service.append(新数据)                     # 每周追加
        result = service.predict('SKU', 'US')     # 预测
    """

    def __init__(self, state_dir=STATE_DIR):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self._weekly = None    # 周历史
        self._daily = None     # 日历史
        self._week_model = None  # 全局周模型 (dict)
        self._day_model = None   # 全局日模型 (dict)
        self._le_sku = None    # SKU label encoder
        self._le_site = None   # 站点 label encoder
        self._meta = {}

        # 加载已有状态
        self._load_all()

    # ================================================================
    # init: 初始化 (一次性)
    # ================================================================

    def init(self, orders_path=None, inventory_path=None):
        """用全量历史数据初始化"""
        print("=" * 60)
        print("  初始化预测服务")
        print("=" * 60)

        tmp_cache = str(self.state_dir / '_tmp_cache.pkl')
        data = _load_and_process(force=True, cache_path=tmp_cache,
                                 orders_file=orders_path, inventory_file=inventory_path)

        self._weekly = data['weekly']
        self._daily = data['daily']

        # SKU/站点编码
        self._weekly['sku_site'] = self._weekly['sku'] + '_' + self._weekly['site']
        self._le_sku = LabelEncoder()
        self._weekly['sku_site_id'] = self._le_sku.fit_transform(self._weekly['sku_site'])
        self._le_site = LabelEncoder()
        self._weekly['site_id'] = self._le_site.fit_transform(self._weekly['site'])

        # SKU统计量 (周)
        stats_w = self._weekly.groupby('sku_site').agg({
            'weekly_sales': ['mean', 'std'], 'avg_price': 'mean'
        }).reset_index()
        stats_w.columns = ['sku_site', 'sku_mean', 'sku_std', 'sku_price']
        self._weekly = self._weekly.merge(stats_w, on='sku_site', how='left')

        # 日数据也加编码和统计
        self._daily['sku_site'] = self._daily['sku'] + '_' + self._daily['site']
        self._daily['sku_site_id'] = self._daily['sku_site'].map(
            dict(zip(self._le_sku.classes_, range(len(self._le_sku.classes_)))))
        self._daily['sku_site_id'] = self._daily['sku_site_id'].fillna(-1).astype(int)
        self._daily['site_id'] = self._daily['site'].map(
            dict(zip(self._le_site.classes_, range(len(self._le_site.classes_)))))
        self._daily['site_id'] = self._daily['site_id'].fillna(-1).astype(int)

        stats_d = self._daily.groupby('sku_site')['daily_sales'].agg(['mean', 'std']).reset_index()
        stats_d.columns = ['sku_site', 'sku_mean_daily', 'sku_std_daily']
        self._daily = self._daily.merge(stats_d, on='sku_site', how='left')

        # 日特征构建
        print("  构建日级别特征...")
        self._build_day_features()

        self._save_state()
        self._meta = {
            'last_date': str(self._daily['date'].max().date()),
            'n_skus': int(self._weekly['sku'].nunique()),
            'initialized': True,
        }
        self._save_meta()

        # 清理临时缓存
        tmp = Path(tmp_cache)
        if tmp.exists(): tmp.unlink()

        print(f"\n  初始化完成! SKU: {self._meta['n_skus']}, 截止: {self._meta['last_date']}")
        print(f"  下一步: service.train()")

    # ================================================================
    # train: 训练全局模型 (周+日)
    # ================================================================

    def train(self, granularity=None):
        """训练模型

        Parameters:
            granularity: 'week'=只训练周模型, 'day'=只训练日模型, None=训练两个
        """
        self._check_init()
        if granularity not in (None, 'week', 'day'):
            raise ValueError("granularity 必须是 'week'、'day' 或 None")
        print("\n" + "=" * 60)
        print("  训练全局模型")
        print("=" * 60)

        if granularity in (None, 'week'):
            self._train_week_model()
        if granularity in (None, 'day'):
            self._train_day_model()

        self._save_models()
        print(f"\n  训练完成! 模型已保存.")

    def _train_week_model(self):
        """训练全局周模型"""
        print("\n  --- 周模型 ---")
        lc = self._last_complete_week()
        df = self._weekly[self._weekly['date'] < lc].copy()

        # 只用>=20周数据的SKU
        counts = df.groupby('sku_site').size()
        valid_skus = counts[counts >= 20].index
        df = df[df['sku_site'].isin(valid_skus)]

        vf = [f for f in WEEK_FEATURES if f in df.columns and df[f].notna().mean() > 0.3]
        df_clean = df.dropna(subset=[f for f in vf if 'lag_' in f][:2])
        df_clean = df_clean.sort_values('date').reset_index(drop=True)

        if df_clean.empty or not vf:
            raise ValueError("没有足够的周训练数据（每个 SKU+站点至少需要 20 周）")

        print(f"  训练数据: {len(df_clean)}行, {df_clean['sku_site'].nunique()}个SKU+站点")
        print(f"  特征: {len(vf)}个")

        X = df_clean[vf].fillna(0)
        y = df_clean['weekly_sales']
        n = len(X)
        sw = np.array([0.998 ** (n - i - 1) for i in range(n)])
        sw = sw / sw.sum() * n

        xgb = XGBRegressor(n_estimators=500, max_depth=6, learning_rate=0.03,
            subsample=0.7, colsample_bytree=0.7, min_child_weight=10,
            reg_alpha=1.0, reg_lambda=3.0, random_state=42, verbosity=0)
        xgb.fit(X, y, sample_weight=sw)

        lgbm = LGBMRegressor(n_estimators=500, max_depth=6, learning_rate=0.03,
            subsample=0.7, colsample_bytree=0.7, min_child_weight=10,
            reg_alpha=1.0, reg_lambda=3.0, random_state=42, verbose=-1)
        lgbm.fit(X, y, sample_weight=sw)

        self._week_model = {'xgb': xgb, 'lgbm': lgbm, 'features': vf,
                            'trained_at': str(datetime.now())}
        print(f"  周模型训练完成")

    def _train_day_model(self):
        """训练全局日模型"""
        print("\n  --- 日模型 ---")
        df = self._daily.copy()

        # 只用有足够数据的SKU
        counts = df.groupby('sku_site').size()
        valid_skus = counts[counts >= 90].index
        df = df[df['sku_site'].isin(valid_skus)]

        vf = [f for f in DAY_FEATURES if f in df.columns and df[f].notna().mean() > 0.3]
        df_clean = df.dropna(subset=[f for f in vf if 'lag_' in f][:2])

        # 采样 (日数据太大, 采样加速), 采样后按date排序以保证时间衰减权重正确
        if len(df_clean) > 500000:
            df_clean = df_clean.sample(500000, random_state=42)
        df_clean = df_clean.sort_values('date').reset_index(drop=True)

        if df_clean.empty or not vf:
            raise ValueError("没有足够的日训练数据（每个 SKU+站点至少需要 90 天）")

        print(f"  训练数据: {len(df_clean)}行, {df_clean['sku_site'].nunique()}个SKU+站点")
        print(f"  特征: {len(vf)}个")

        X = df_clean[vf].fillna(0)
        y = df_clean['daily_sales']
        n = len(X)
        sw = np.array([0.9995 ** (n - i - 1) for i in range(n)])
        sw = sw / sw.sum() * n

        xgb = XGBRegressor(n_estimators=400, max_depth=6, learning_rate=0.03,
            subsample=0.7, colsample_bytree=0.7, min_child_weight=10,
            reg_alpha=1.0, reg_lambda=3.0, random_state=42, verbosity=0)
        xgb.fit(X, y, sample_weight=sw)

        lgbm = LGBMRegressor(n_estimators=400, max_depth=6, learning_rate=0.03,
            subsample=0.7, colsample_bytree=0.7, min_child_weight=10,
            reg_alpha=1.0, reg_lambda=3.0, random_state=42, verbose=-1)
        lgbm.fit(X, y, sample_weight=sw)

        self._day_model = {'xgb': xgb, 'lgbm': lgbm, 'features': vf,
                           'trained_at': str(datetime.now())}
        print(f"  日模型训练完成")

    # ================================================================
    # append: 追加新数据 (每周)
    # ================================================================

    def append(self, new_data, inventory_data=None):
        """
        追加新销量数据

        Parameters:
            new_data: DataFrame 或 xlsx路径
                格式A: Amazon结算报告 (29列)
                格式B: 简化 (至少: date, sku, site, sales)
            inventory_data: 库存数据 (可选)
        """
        self._check_init()
        before_daily_rows = len(self._daily)
        before_weekly_rows = len(self._weekly)
        before_last_date = str(pd.to_datetime(self._daily['date'].max()).date())
        if isinstance(new_data, (str, Path)):
            new_data = pd.read_excel(str(new_data), engine='openpyxl')

        # 解析为日数据
        if 'product sales' in new_data.columns:
            new_daily = self._parse_orders(new_data)
        else:
            new_daily = self._parse_simple(new_data)

        if new_daily.empty:
            print("  无有效新数据")
            return {
                'parsed_rows': 0, 'inserted_rows': 0, 'overwritten_rows': 0,
                'before_last_date': before_last_date,
                'after_last_date': before_last_date,
            }

        new_daily['sku'] = new_daily['sku'].astype(str).str.strip()
        new_daily['site'] = new_daily['site'].astype(str).str.strip().str.upper()

        new_keys = pd.MultiIndex.from_frame(new_daily[['date', 'sku', 'site']])
        old_keys = pd.MultiIndex.from_frame(self._daily[['date', 'sku', 'site']])
        overwritten_rows = int(new_keys.isin(old_keys).sum())

        # 给新数据补上 sku_site 列、编码ID 和事件特征
        new_daily['sku_site'] = new_daily['sku'] + '_' + new_daily['site']
        # 用已有 LabelEncoder 映射 ID (已有SKU取已有ID, 新SKU暂为-1)
        if self._le_sku is not None:
            sku_map = dict(zip(self._le_sku.classes_, range(len(self._le_sku.classes_))))
            new_daily['sku_site_id'] = new_daily['sku_site'].map(sku_map).fillna(-1).astype(int)
        if self._le_site is not None:
            site_map = dict(zip(self._le_site.classes_, range(len(self._le_site.classes_))))
            new_daily['site_id'] = new_daily['site'].map(site_map).fillna(-1).astype(int)
        new_daily = _add_events(new_daily)

        # 追加日数据
        self._daily = pd.concat([self._daily, new_daily], ignore_index=True)
        self._daily = self._daily.drop_duplicates(subset=['date', 'sku', 'site'], keep='last')

        # 重算日特征 (受影响SKU)
        affected = new_daily['sku_site'].unique()
        self._build_day_features(only_skus=affected)

        # 重聚合周数据
        new_dates = new_daily['date'].unique()
        affected_weeks = set()
        for d in new_dates:
            d = pd.to_datetime(d)
            affected_weeks.add(d - pd.Timedelta(days=d.dayofweek))

        for ws in affected_weeks:
            we = ws + pd.Timedelta(days=6)
            mask = (self._daily['date'] >= ws) & (self._daily['date'] <= we)
            wk = self._daily[mask].groupby(['sku', 'site']).agg({
                'daily_sales': 'sum', 'avg_price': 'mean', 'avg_discount': 'mean',
                'order_count': 'sum', 'daily_revenue': 'sum'
            }).reset_index()
            wk['date'] = ws
            wk.rename(columns={'daily_sales': 'weekly_sales', 'daily_revenue': 'weekly_revenue'}, inplace=True)
            wk = _add_events(wk, is_weekly=True)

            for _, row in wk.iterrows():
                em = ((self._weekly['sku'] == row['sku']) &
                      (self._weekly['site'] == row['site']) &
                      (self._weekly['date'] == ws))
                if em.any():
                    idx = self._weekly[em].index[0]
                    self._weekly.at[idx, 'weekly_sales'] = row['weekly_sales']
                    if 'avg_price' in row.index:
                        self._weekly.at[idx, 'avg_price'] = row['avg_price']
                    if 'avg_discount' in row.index:
                        self._weekly.at[idx, 'avg_discount'] = row['avg_discount']
                else:
                    new_row = row.copy()
                    new_row['sku_site'] = f"{row['sku']}_{row['site']}"
                    # 补齐编码和统计列 (已知SKU用已有值, 新SKU用默认值)
                    sku_site_key = new_row['sku_site']
                    if self._le_sku is not None and sku_site_key in self._le_sku.classes_:
                        new_row['sku_site_id'] = int(np.where(self._le_sku.classes_ == sku_site_key)[0][0])
                    else:
                        new_row['sku_site_id'] = -1
                    if self._le_site is not None and row['site'] in self._le_site.classes_:
                        new_row['site_id'] = int(np.where(self._le_site.classes_ == row['site'])[0][0])
                    else:
                        new_row['site_id'] = -1
                    self._weekly = pd.concat([self._weekly, pd.DataFrame([new_row])], ignore_index=True)

        # 重算周特征
        self._rebuild_week_features(affected_weeks)

        # 更新库存 (如果提供了库存数据)
        if inventory_data is not None:
            self._update_inventory(inventory_data)

        # 更新SKU统计量和编码
        self._update_sku_stats(new_daily['sku_site'].unique())

        self._save_state()
        # 补录历史数据时不能让状态中的数据截止日期倒退。
        self._meta['last_date'] = str(pd.to_datetime(self._daily['date'].max()).date())
        self._save_meta()

        print(f"  追加完成: {len(new_daily)}行日数据, 影响{len(affected_weeks)}周")
        return {
            'parsed_rows': int(len(new_daily)),
            'inserted_rows': int(len(self._daily) - before_daily_rows),
            'overwritten_rows': overwritten_rows,
            'before_daily_rows': before_daily_rows,
            'after_daily_rows': int(len(self._daily)),
            'before_weekly_rows': before_weekly_rows,
            'after_weekly_rows': int(len(self._weekly)),
            'affected_weeks': len(affected_weeks),
            'upload_min_date': str(pd.to_datetime(new_daily['date'].min()).date()),
            'upload_max_date': str(pd.to_datetime(new_daily['date'].max()).date()),
            'before_last_date': before_last_date,
            'after_last_date': self._meta['last_date'],
        }

    # ================================================================
    # predict: 预测
    # ================================================================

    def predict(self, sku, site, granularity='week', days=7, weeks=1,
                start_date=None, price=None, discount=None, inventory=None,
                is_promotion=False, promotion_impact=None,
                baseline=None, reference_sku=None):
        """
        预测销量

        Parameters:
            sku: SKU编码
            site: 站点 (US/CA/DE/FR/IT/ES/NL)
            granularity: 'week' (周总量) 或 'day' (逐日)
            days: 日粒度时预测天数
            weeks: 周粒度时预测周数 (默认1)
            start_date: 预测起始日期 (默认=数据末尾的下一个周期)
                        周粒度: 会对齐到周一; 日粒度: 从该日开始
            price: 未来计划售价
            discount: 折扣力度 (0~1)
            inventory: 当前库存
            is_promotion: 是否促销
            promotion_impact: 促销流量倍数
            baseline: 基准日销量 (用于新品/断货恢复, 替代无效的lag/rolling特征)
            reference_sku: 参考SKU编码 (借用该SKU的历史模式做预测)

        Returns:
            dict: weekly_total, daily_avg, predictions, confidence_interval, reliability
        """
        self._check_init()

        sku = str(sku).strip()
        site = str(site).strip().upper()
        if not sku or not site:
            raise ValueError("sku 和 site 不能为空")
        if granularity not in ('week', 'day'):
            raise ValueError("granularity 必须是 'week' 或 'day'")
        if not isinstance(days, (int, np.integer)) or days <= 0:
            raise ValueError("days 必须是正整数")
        if not isinstance(weeks, (int, np.integer)) or weeks <= 0:
            raise ValueError("weeks 必须是正整数")
        if discount is not None and not 0 <= discount <= 1:
            raise ValueError("discount 必须在 0 到 1 之间")
        if inventory is not None and inventory < 0:
            raise ValueError("inventory 不能为负数")
        if baseline is not None and baseline < 0:
            raise ValueError("baseline 不能为负数")
        if promotion_impact is not None and promotion_impact <= 0:
            raise ValueError("promotion_impact 必须大于 0")

        # 如果指定了 reference_sku, 用参考SKU的历史做预测基础
        effective_sku = sku
        if reference_sku:
            effective_sku = str(reference_sku).strip()

        if granularity == 'week':
            if self._week_model is None:
                raise RuntimeError("周模型未训练。请先调用 service.train()")
            return self._predict_week(effective_sku, site, weeks, start_date, price, discount,
                                      inventory, is_promotion, promotion_impact, baseline, sku)
        else:
            if self._day_model is None:
                raise RuntimeError("日模型未训练。请先调用 service.train()")
            return self._predict_day(effective_sku, site, days, start_date, price, discount,
                                     inventory, is_promotion, promotion_impact, baseline, sku)

    def _predict_week(self, sku, site, weeks, start_date, price, discount, inventory,
                      is_promotion, promotion_impact, baseline=None, original_sku=None):
        """周粒度预测 (支持多周递归, baseline冷启动)"""
        # 确定预测起点
        if start_date is not None:
            start = pd.to_datetime(start_date, errors='raise').normalize()
            start = start - pd.Timedelta(days=start.dayofweek)
        else:
            start = self._last_complete_week()

        mask = (self._weekly['sku'] == sku) & (self._weekly['site'] == site)
        hist_df = self._weekly[mask].sort_values('date')
        hist_df = hist_df[hist_df['date'] < start]

        # 冷启动: 无历史或历史无效时, 用baseline构造虚拟历史
        use_baseline = False
        if len(hist_df) == 0 and baseline is None:
            raise ValueError(
                f"SKU '{original_sku or sku}' 在站点 '{site}' 无历史数据。"
                f"请提供 baseline(日均销量) 或 reference_sku(参考SKU)。")
        if len(hist_df) < 2 and baseline is None:
            return {'weekly_total': 0, 'daily_avg': 0, 'predictions': [],
                    'confidence_interval': (0, 0), 'reliability': 'D'}

        if baseline is not None:
            use_baseline = True
            baseline_weekly = baseline * 7
            # 构造虚拟历史 (52周平稳序列, 带轻微随机波动模拟真实场景)
            np.random.seed(42)
            virtual_hist = baseline_weekly * (1 + np.random.normal(0, 0.1, 52))
            virtual_hist = np.clip(virtual_hist, 0, None)
            hist = virtual_hist
            last_price = price if price is not None else 0
        else:
            hist = hist_df['weekly_sales'].values
            last_row = hist_df.iloc[-1]
            last_price = last_row.get('avg_price', 0)
            if pd.isna(last_price): last_price = 0

        vf = self._week_model['features']
        hist_ext = list(hist)
        predictions = []
        std_recent = (np.std(hist[-8:], ddof=1) if len(hist) >= 8
                      else np.std(hist, ddof=1) if len(hist) >= 2
                      else max(float(np.mean(hist)) * 0.3, 1))

        # gap 填充 (仅非baseline模式, 且有历史数据时)
        if not use_baseline and len(hist_df) > 0:
            last_week_date = hist_df['date'].max()
            gap_weeks = max(0, int((start - last_week_date).days / 7) - 1)
            if gap_weeks > 4:
                print(f"  [警告] 预测起点距数据末尾 {gap_weeks} 周, 中间用递归填充, 精度会下降。"
                      f"建议先 append() 补充最新数据。")
            for g in range(gap_weeks):
                gap_date = last_week_date + pd.Timedelta(weeks=g + 1)
                feat_gap = self._build_week_predict_features(
                    np.array(hist_ext), hist_df, gap_date, last_price,
                    price, discount, None, False, None)
                X_gap = pd.DataFrame([feat_gap]).reindex(columns=vf, fill_value=0).fillna(0)
                pred_gap = max(0, 0.5 * self._week_model['xgb'].predict(X_gap)[0] +
                                  0.5 * self._week_model['lgbm'].predict(X_gap)[0])
                hist_ext.append(pred_gap)

        # 正式预测
        for w in range(weeks):
            pred_date = start + pd.Timedelta(weeks=w)

            feat = self._build_week_predict_features(
                np.array(hist_ext), hist_df, pred_date, last_price,
                price, discount, inventory, is_promotion, promotion_impact)
            X = pd.DataFrame([feat]).reindex(columns=vf, fill_value=0).fillna(0)

            wp = max(0, 0.5 * self._week_model['xgb'].predict(X)[0] +
                        0.5 * self._week_model['lgbm'].predict(X)[0])

            # 后处理
            if is_promotion:
                wp *= (promotion_impact or 1.5)
            if inventory is not None:
                remaining = inventory - sum(p['sales'] for p in predictions)
                if remaining <= 0:
                    wp = 0
                else:
                    wp = min(wp, remaining)
            wp = max(0, wp)

            # Longer recursive horizons carry more uncertainty because earlier
            # forecast values become inputs to later forecast values.
            horizon_scale = np.sqrt(w + 1)
            lo_w = max(0, wp - 1.3 * std_recent * horizon_scale)
            hi_w = wp + 1.3 * std_recent * horizon_scale

            predictions.append({
                'week_start': str(pred_date.date()),
                'sales': float(round(wp, 1)),
                'lower': float(round(lo_w, 1)),
                'upper': float(round(hi_w, 1)),
            })

            # 递归: 将预测值加入历史供下一周使用
            hist_ext.append(wp)

        total = sum(p['sales'] for p in predictions)
        lo = max(0, total - 1.3 * std_recent * np.sqrt(weeks))
        hi = total + 1.3 * std_recent * np.sqrt(weeks)

        cv = np.std(hist[-6:]) / max(np.mean(hist[-6:]), 0.01) if len(hist) >= 6 else 1
        reliability = self._reliability(hist, cv)

        return {
            'weekly_total': float(round(total, 1)),
            'daily_avg': float(round(total / (weeks * 7), 1)),
            'predictions': predictions,
            'confidence_interval': (float(round(lo, 1)), float(round(hi, 1))),
            'reliability': reliability,
        }

    def _predict_day(self, sku, site, days, start_date, price, discount, inventory,
                     is_promotion, promotion_impact, baseline=None, original_sku=None):
        """日粒度预测"""
        # 确定预测起点
        if start_date is not None:
            start = pd.to_datetime(start_date, errors='raise').normalize()
        else:
            start = self._daily['date'].max().normalize() + pd.Timedelta(days=1)

        mask = (self._daily['sku'] == sku) & (self._daily['site'] == site)
        hist_df = self._daily[mask].sort_values('date')
        hist_df = hist_df[hist_df['date'] < start]

        # 冷启动: 无历史或历史无效时, 用baseline构造虚拟历史
        use_baseline = False
        if len(hist_df) == 0 and baseline is None:
            raise ValueError(
                f"SKU '{original_sku or sku}' 在站点 '{site}' 无历史数据。"
                f"请提供 baseline(日均销量) 或 reference_sku(参考SKU)。")
        if len(hist_df) < 7 and baseline is None:
            return {'predictions': [], 'weekly_total': 0, 'daily_avg': 0,
                    'confidence_interval': (0, 0), 'reliability': 'D'}

        if baseline is not None:
            use_baseline = True
            # 构造虚拟日历史 (56天, 带星期几模式: 周末略低)
            np.random.seed(42)
            dow_pattern = np.array([1.0, 1.05, 1.05, 1.0, 0.95, 0.85, 0.90])  # Mon~Sun
            dow_pattern = dow_pattern / dow_pattern.mean()  # 归一化使均值=1
            virtual_hist = []
            for d in range(56):
                dow = d % 7
                val = baseline * dow_pattern[dow] * (1 + np.random.normal(0, 0.15))
                virtual_hist.append(max(0, val))
            hist = np.array(virtual_hist)
            last_date = start - timedelta(days=1)
            last_price = price if price is not None else 0
        else:
            last_date = hist_df['date'].max()
            hist = hist_df['daily_sales'].values
            last_row = hist_df.iloc[-1]
            last_price = last_row.get('avg_price', 0)
            if pd.isna(last_price): last_price = 0

        vf = self._day_model['features']
        predictions = []
        hist_ext = list(hist)

        dn = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

        # gap 填充 (仅非baseline模式)
        if not use_baseline:
            gap_days = (start - last_date).days - 1
            if gap_days > 14:
                print(f"  [警告] 预测起点距数据末尾 {gap_days} 天, 中间用递归填充, 精度会下降。"
                      f"建议先 append() 补充最新数据。")
            for g in range(max(0, gap_days)):
                gap_date = last_date + timedelta(days=g + 1)
                feat_gap = self._build_day_predict_features(
                    np.array(hist_ext), hist_df, gap_date, last_price,
                    price, discount, None, False, None)
                X_gap = pd.DataFrame([feat_gap]).reindex(columns=vf, fill_value=0).fillna(0)
                pred_gap = max(0, 0.5 * self._day_model['xgb'].predict(X_gap)[0] +
                                  0.5 * self._day_model['lgbm'].predict(X_gap)[0])
                hist_ext.append(pred_gap)

        # gap 只用于递归特征，返回结果必须严格从 start 开始。
        pred_start = start - timedelta(days=1)
        for d in range(days):
            pred_date = pred_start + timedelta(days=d + 1)

            # ML特征
            feat = self._build_day_predict_features(
                np.array(hist_ext), hist_df, pred_date, last_price,
                price, discount, inventory, is_promotion, promotion_impact)

            X = pd.DataFrame([feat]).reindex(columns=vf, fill_value=0).fillna(0)
            pred_ml = max(0, 0.5 * self._day_model['xgb'].predict(X)[0] +
                           0.5 * self._day_model['lgbm'].predict(X)[0])

            # WMA 只从真实历史取同星期数据，不能被 gap/递归预测污染。
            if use_baseline:
                offset = (pred_start.dayofweek - pred_date.dayofweek) % 7 or 7
                indexes = (len(hist) - offset - 7 * k for k in range(6))
                same_dow_vals = [hist[idx] for idx in indexes if idx >= 0]
            else:
                same_dow_vals = hist_df.loc[
                    hist_df['date'].dt.dayofweek == pred_date.dayofweek,
                    'daily_sales'
                ].tail(6).tolist()[::-1]

            if len(same_dow_vals) >= 2:
                # 用中位数 (对异常值稳健) 而非加权平均
                pred_wma = float(np.median(same_dow_vals))
            elif len(same_dow_vals) == 1:
                pred_wma = same_dow_vals[0]
            else:
                pred_wma = pred_ml

            # 动态融合权重
            n_dow = len(same_dow_vals)
            if n_dow < 2:
                w_ml = 0.70  # WMA数据极少, 主要靠ML
            elif n_dow < 4:
                w_ml = 0.35  # WMA数据较少
            else:
                w_ml = 0.20  # WMA数据充足, 默认信赖WMA

            # 趋势越强, ML越有价值 (WMA 天然滞后于趋势)
            trend_strength = abs(feat.get('trend_7v28', 0))
            if trend_strength > 0.3:
                w_ml = min(0.60, w_ml + 0.20)
            elif trend_strength > 0.15:
                w_ml = min(0.50, w_ml + 0.10)

            pred = w_ml * pred_ml + (1 - w_ml) * pred_wma

            # 后处理
            if is_promotion:
                pred *= (promotion_impact or 1.5)
            if inventory is not None:
                remaining = inventory - sum(p['sales'] for p in predictions)
                if remaining <= 0:
                    pred = 0
                else:
                    pred = min(pred, remaining)
            pred = max(0, pred)

            # 置信区间
            std_d = np.std(hist_ext[-14:], ddof=1) if len(hist_ext) >= 14 else max(pred * 0.3, 1)
            lo_d = max(0, pred - 1.3 * std_d)
            hi_d = pred + 1.3 * std_d

            predictions.append({
                'date': pred_date.strftime('%Y-%m-%d'),
                'dow': dn[pred_date.dayofweek],
                'sales': float(round(pred, 1)),
                'lower': float(round(lo_d, 1)),
                'upper': float(round(hi_d, 1)),
            })

            hist_ext.append(pred)

        total = sum(p['sales'] for p in predictions)
        return {
            'predictions': predictions,
            'weekly_total': float(round(total, 1)),
            'daily_avg': float(round(total / days, 1)),
            'confidence_interval': (float(round(sum(p['lower'] for p in predictions), 1)),
                                    float(round(sum(p['upper'] for p in predictions), 1))),
            'reliability': self._reliability(hist, np.std(hist[-28:], ddof=1) / max(np.mean(hist[-28:]), 0.01) if len(hist) >= 28 else 2),
        }

    # ================================================================
    # 辅助: 特征构建
    # ================================================================

    def _build_week_predict_features(self, hist, hist_df, pred_date, last_price,
                                      price, discount, inventory, is_promotion, promotion_impact):
        n = len(hist)
        r = {}
        # 时间
        r['week_of_year'] = pred_date.isocalendar()[1]
        r['month'] = pred_date.month
        r['quarter'] = (pred_date.month - 1) // 3 + 1
        r['year'] = pred_date.year
        r['week_sin'] = np.sin(2 * np.pi * r['week_of_year'] / 52)
        r['week_cos'] = np.cos(2 * np.pi * r['week_of_year'] / 52)
        r['month_sin'] = np.sin(2 * np.pi * r['month'] / 12)
        r['month_cos'] = np.cos(2 * np.pi * r['month'] / 12)
        # lag
        for lag in [1, 2, 3, 4, 8, 12, 52]:
            r[f'lag_{lag}w'] = hist[-lag] if n >= lag else 0
        # rolling
        for w in [2, 4, 8, 12]:
            v = hist[-w:] if n >= w else hist
            r[f'roll_{w}w_mean'] = np.mean(v)
            r[f'roll_{w}w_std'] = np.std(v, ddof=1) if len(v) >= 2 else 0
            r[f'roll_{w}w_median'] = np.median(v)
        v4 = hist[-4:] if n >= 4 else hist
        r['roll_4w_max'] = np.max(v4) if len(v4) else 0
        r['roll_4w_min'] = np.min(v4) if len(v4) else 0
        # trend
        r2 = np.mean(hist[-2:]) if n >= 2 else 0
        r2p = np.mean(hist[-4:-2]) if n >= 4 else r2
        r['trend_2v2'] = np.clip(r2 / max(r2p, 0.01) - 1, -2, 5)
        r4 = np.mean(hist[-4:]) if n >= 4 else 0
        r4p = np.mean(hist[-8:-4]) if n >= 8 else r4
        r['trend_4v4'] = np.clip(r4 / max(r4p, 0.01) - 1, -2, 5)
        r['yoy_ratio'] = np.clip(hist[-1] / hist[-52] if n >= 52 and hist[-52] > 0 else 1, 0, 10)
        # price
        use_price = price if price is not None else last_price
        r['avg_price'] = use_price
        r['price_change'] = (use_price - last_price) / last_price if last_price > 0 and price is not None else 0
        hp = hist_df['avg_price'].tail(12).mean() if len(hist_df) > 0 and 'avg_price' in hist_df.columns else last_price
        r['price_relative'] = use_price / hp if hp > 0 else 1
        r['has_discount'] = 1 if discount and discount > 0.01 else 0
        r['avg_discount'] = discount or 0
        # inventory
        li = hist_df['inventory'].iloc[-1] if 'inventory' in hist_df.columns and len(hist_df) > 0 else 0
        if pd.isna(li): li = 0
        ui = inventory if inventory is not None else li
        r['inventory'] = ui
        r['in_stock'] = 1 if ui > 0 else 0
        r['stock_weeks'] = np.clip(ui / max(r['roll_4w_mean'], 0.1), 0, 52)
        r['cv_4w'] = np.clip(r['roll_4w_std'] / max(r['roll_4w_mean'], 0.01), 0, 5)
        # event (周粒度: 检查整周范围)
        ev = _get_event_features(pred_date, is_weekly=True)
        if is_promotion:
            ev = {'event_impact': promotion_impact or 1.5, 'is_major_promo': 1, 'is_warmup': 0, 'is_post_holiday': 0}
        r.update(ev)
        # sku id
        r['sku_site_id'] = hist_df.iloc[-1].get('sku_site_id', 0) if len(hist_df) > 0 else 0
        r['site_id'] = hist_df.iloc[-1].get('site_id', 0) if len(hist_df) > 0 else 0
        r['sku_mean'] = hist_df.iloc[-1].get('sku_mean', np.mean(hist)) if len(hist_df) > 0 else np.mean(hist)
        r['sku_std'] = hist_df.iloc[-1].get('sku_std', np.std(hist)) if len(hist_df) > 0 else np.std(hist)
        r['sku_price'] = hist_df.iloc[-1].get('sku_price', last_price) if len(hist_df) > 0 else last_price
        return r

    def _build_day_predict_features(self, hist, hist_df, pred_date, last_price,
                                     price, discount, inventory, is_promotion, promotion_impact):
        n = len(hist)
        r = {}
        r['day_of_week'] = pred_date.dayofweek
        r['day_of_month'] = pred_date.day
        r['month'] = pred_date.month
        r['week_of_year'] = pred_date.isocalendar()[1]
        r['quarter'] = (pred_date.month - 1) // 3 + 1
        r['year'] = pred_date.year
        r['is_weekend'] = 1 if pred_date.dayofweek >= 5 else 0
        r['dow_sin'] = np.sin(2 * np.pi * pred_date.dayofweek / 7)
        r['dow_cos'] = np.cos(2 * np.pi * pred_date.dayofweek / 7)
        r['month_sin'] = np.sin(2 * np.pi * pred_date.month / 12)
        r['month_cos'] = np.cos(2 * np.pi * pred_date.month / 12)
        for lag in [1, 2, 3, 7, 14, 21, 28]:
            r[f'lag_{lag}d'] = hist[-lag] if n >= lag else 0
        for w in [7, 14, 28]:
            v = hist[-w:] if n >= w else hist
            r[f'roll_{w}d_mean'] = np.mean(v)
            if w <= 14:
                r[f'roll_{w}d_median'] = np.median(v)
        r['roll_7d_std'] = np.std(hist[-7:], ddof=1) if n >= 7 else 0
        r['roll_14d_std'] = np.std(hist[-14:], ddof=1) if n >= 14 else 0
        r['nonzero_ratio_7d'] = np.mean([1 if x > 0 else 0 for x in hist[-7:]]) if n >= 7 else 1
        r7 = r.get('roll_7d_mean', 1)
        r28 = r.get('roll_28d_mean', 1)
        r['trend_7v28'] = np.clip(r7 / max(r28, 0.01) - 1, -2, 5)
        # price
        use_price = price if price is not None else last_price
        r['avg_price'] = use_price
        r['price_change_pct'] = (use_price - last_price) / last_price if last_price > 0 and price is not None else 0
        hp = hist_df['avg_price'].tail(28).mean() if 'avg_price' in hist_df.columns and len(hist_df) > 0 else last_price
        r['price_relative'] = use_price / hp if hp > 0 else 1
        r['has_discount'] = 1 if discount and discount > 0.01 else 0
        r['avg_discount'] = discount or 0
        # inventory
        li = hist_df['inventory'].iloc[-1] if 'inventory' in hist_df.columns and len(hist_df) > 0 else 0
        if pd.isna(li): li = 0
        r['inventory'] = inventory if inventory is not None else li
        r['in_stock'] = 1 if r['inventory'] > 0 else 0
        # event
        ev = _get_event_features(pred_date)
        if is_promotion:
            ev = {'event_impact': promotion_impact or 1.5, 'is_major_promo': 1, 'is_warmup': 0, 'is_post_holiday': 0}
        r.update(ev)
        # sku id
        r['sku_site_id'] = hist_df.iloc[-1].get('sku_site_id', 0) if len(hist_df) > 0 else 0
        r['site_id'] = hist_df.iloc[-1].get('site_id', 0) if len(hist_df) > 0 else 0
        r['sku_mean_daily'] = hist_df.iloc[-1].get('sku_mean_daily', np.mean(hist[-28:])) if len(hist_df) > 0 else 0
        r['sku_std_daily'] = hist_df.iloc[-1].get('sku_std_daily', np.std(hist[-28:])) if len(hist_df) > 0 else 0
        return r

    def _reliability(self, hist, cv):
        n = len(hist)
        score = (2 if n >= 52 else 1 if n >= 20 else 0)
        score += (2 if cv < 0.4 else 1 if cv < 0.7 else 0)
        return {4: 'A', 3: 'B', 2: 'B', 1: 'C'}.get(score, 'D')

    # ================================================================
    # 辅助: 日特征构建
    # ================================================================

    def _build_day_features(self, only_skus=None):
        """构建日级别特征 (lag, rolling等)"""
        df = self._daily.sort_values(['sku', 'site', 'date'])
        gc = ['sku', 'site']
        tgt = 'daily_sales'

        # 如果是增量更新(append), 删除旧的计算列强制重算
        if only_skus is not None and len(only_skus) > 0:
            drop_cols = [c for c in df.columns if c.startswith(('lag_', 'roll_'))
                         or c in ('nonzero_ratio_7d', 'trend_7v28', 'day_of_week',
                                  'day_of_month', 'is_weekend', 'dow_sin', 'dow_cos',
                                  'month_sin', 'month_cos', 'price_change_pct',
                                  'price_relative', 'has_discount')]
            df = df.drop(columns=[c for c in drop_cols if c in df.columns])

        # 滞后
        for lag in [1, 2, 3, 7, 14, 21, 28]:
            col = f'lag_{lag}d'
            if col not in df.columns:
                df[col] = df.groupby(gc)[tgt].shift(lag)

        # 滚动
        for w in [7, 14, 28]:
            col = f'roll_{w}d_mean'
            if col not in df.columns:
                df[col] = df.groupby(gc)[tgt].transform(lambda x: x.shift(1).rolling(w, min_periods=1).mean())
        for w in [7, 14]:
            col = f'roll_{w}d_median'
            if col not in df.columns:
                df[col] = df.groupby(gc)[tgt].transform(lambda x: x.shift(1).rolling(w, min_periods=1).median())
            col = f'roll_{w}d_std'
            if col not in df.columns:
                df[col] = df.groupby(gc)[tgt].transform(lambda x: x.shift(1).rolling(w, min_periods=2).std())

        # 非零比例
        if 'nonzero_ratio_7d' not in df.columns:
            df['nonzero_ratio_7d'] = df.groupby(gc)[tgt].transform(
                lambda x: x.shift(1).rolling(7, min_periods=1).apply(lambda s: (s > 0).mean(), raw=True))

        # 趋势
        if 'trend_7v28' not in df.columns:
            df['trend_7v28'] = (df['roll_7d_mean'] / df['roll_28d_mean'].replace(0, np.nan) - 1).clip(-2, 5).fillna(0)

        # 时间
        if 'day_of_week' not in df.columns:
            df['day_of_week'] = df['date'].dt.dayofweek
            df['day_of_month'] = df['date'].dt.day
            df['month'] = df['date'].dt.month
            df['week_of_year'] = df['date'].dt.isocalendar().week.astype(int)
            df['quarter'] = df['date'].dt.quarter
            df['year'] = df['date'].dt.year
            df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
            df['dow_sin'] = np.sin(2 * np.pi * df['day_of_week'] / 7)
            df['dow_cos'] = np.cos(2 * np.pi * df['day_of_week'] / 7)
            df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
            df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)

        # 价格
        if 'price_change_pct' not in df.columns:
            df['price_change_pct'] = df.groupby(gc)['avg_price'].pct_change().clip(-0.5, 0.5).fillna(0)
        if 'price_relative' not in df.columns:
            df['price_relative'] = (df['avg_price'] / df.groupby(gc)['avg_price'].transform(
                lambda x: x.shift(1).rolling(28, min_periods=7).mean()).replace(0, np.nan)).clip(0.5, 2).fillna(1)
        if 'has_discount' not in df.columns:
            df['has_discount'] = (df['avg_discount'] > 0.01).astype(int)

        self._daily = df

    def _rebuild_week_features(self, affected_weeks):
        """重算受影响 SKU 的周特征，支持补录任意历史周。"""
        self._weekly = self._weekly.sort_values(['sku', 'site', 'date']).copy()
        affected_skus = set()
        for ws in affected_weeks:
            mask = self._weekly['date'] == ws
            for sku_site in self._weekly[mask]['sku_site'].unique():
                affected_skus.add(sku_site)

        for ss in affected_skus:
            idx = self._weekly.index[self._weekly['sku_site'] == ss]
            if len(idx) == 0:
                continue
            sales = self._weekly.loc[idx, 'weekly_sales']
            prices = self._weekly.loc[idx, 'avg_price']
            shifted = sales.shift(1)
            for lag in [1, 2, 3, 4, 8, 12, 52]:
                self._weekly.loc[idx, f'lag_{lag}w'] = sales.shift(lag).values
            for w in [2, 4, 8, 12]:
                self._weekly.loc[idx, f'roll_{w}w_mean'] = shifted.rolling(w, min_periods=1).mean().values
                self._weekly.loc[idx, f'roll_{w}w_std'] = shifted.rolling(w, min_periods=2).std().values
                self._weekly.loc[idx, f'roll_{w}w_median'] = shifted.rolling(w, min_periods=1).median().values
            self._weekly.loc[idx, 'roll_4w_max'] = shifted.rolling(4, min_periods=1).max().values
            self._weekly.loc[idx, 'roll_4w_min'] = shifted.rolling(4, min_periods=1).min().values
            recent_2 = shifted.rolling(2, min_periods=1).mean()
            recent_4 = shifted.rolling(4, min_periods=1).mean()
            previous_2 = sales.shift(3).rolling(2, min_periods=1).mean()
            previous_4 = sales.shift(5).rolling(4, min_periods=1).mean()
            self._weekly.loc[idx, 'trend_2v2'] = (
                recent_2 / previous_2.replace(0, np.nan) - 1
            ).clip(-2, 5).fillna(0).values
            self._weekly.loc[idx, 'trend_4v4'] = (
                recent_4 / previous_4.replace(0, np.nan) - 1
            ).clip(-2, 5).fillna(0).values
            self._weekly.loc[idx, 'yoy_ratio'] = (
                sales.shift(1) / sales.shift(52).replace(0, np.nan)
            ).clip(0, 10).fillna(1).values
            price_lag = prices.shift(1)
            self._weekly.loc[idx, 'price_lag1'] = price_lag.values
            self._weekly.loc[idx, 'price_change'] = (
                (prices - price_lag) / price_lag.replace(0, np.nan)
            ).clip(-0.5, 0.5).fillna(0).values
            self._weekly.loc[idx, 'price_relative'] = (
                prices / price_lag.rolling(12, min_periods=4).mean().replace(0, np.nan)
            ).clip(0.5, 2).fillna(1).values
            roll_mean = self._weekly.loc[idx, 'roll_4w_mean']
            roll_std = self._weekly.loc[idx, 'roll_4w_std']
            self._weekly.loc[idx, 'cv_4w'] = (
                roll_std / roll_mean.replace(0, np.nan)
            ).clip(0, 5).fillna(1).values
            inventory = self._weekly.loc[idx, 'inventory']
            self._weekly.loc[idx, 'in_stock'] = (inventory.fillna(0) > 0).astype(int).values
            self._weekly.loc[idx, 'stock_weeks'] = (
                inventory / roll_mean.replace(0, np.nan)
            ).clip(0, 52).fillna(0).values

    def _update_inventory(self, inventory_data):
        """更新库存数据到周表

        Parameters:
            inventory_data: DataFrame 或 xlsx路径
                格式: date, sku, inventory, site (或 site_code)
        """
        if isinstance(inventory_data, (str, Path)):
            inventory_data = pd.read_excel(str(inventory_data), engine='openpyxl')

        inv = inventory_data.copy()
        # 兼容列名
        col_map = {
            'site_code': 'site', '站点': 'site',
            '日期': 'date', '时间': 'date',
            '库存': 'inventory', '库存数量': 'inventory',
            'SKU': 'sku',
        }
        inv.rename(columns={k: v for k, v in col_map.items() if k in inv.columns}, inplace=True)
        # 兼容全量库存表中无标题的第5列（实际内容为 US/EU 等站点代码）。
        if 'site' not in inv.columns:
            unnamed = [c for c in inv.columns if str(c).startswith('Unnamed:')]
            if unnamed:
                inv.rename(columns={unnamed[-1]: 'site'}, inplace=True)
        required = {'date', 'sku', 'inventory', 'site'}
        missing = required.difference(inv.columns)
        if missing:
            raise ValueError(f"库存数据缺少字段: {', '.join(sorted(missing))}")
        inv['date'] = pd.to_datetime(inv['date'], errors='raise')
        inv['sku'] = inv['sku'].astype(str).str.strip()
        inv['site'] = inv['site'].astype(str).str.strip().str.upper()
        inv['inventory'] = pd.to_numeric(inv['inventory'], errors='coerce')
        inv = inv.dropna(subset=['inventory'])

        # EU → 五站展开
        eu_sites = ['DE', 'FR', 'IT', 'ES', 'NL']
        if 'site' in inv.columns and (inv['site'] == 'EU').any():
            eu_rows = inv[inv['site'] == 'EU']
            non_eu = inv[inv['site'] != 'EU']
            expanded = pd.concat([eu_rows.assign(site=s) for s in eu_sites])
            inv = pd.concat([non_eu, expanded], ignore_index=True)

        # 更新日表中已有日期；日特征会直接读取这两列。
        inv_d = inv.sort_values('date').groupby(
            ['sku', 'site', 'date'], as_index=False
        )['inventory'].last()
        inv_map = inv_d.set_index(['sku', 'site', 'date'])['inventory']
        daily_keys = pd.MultiIndex.from_frame(self._daily[['sku', 'site', 'date']])
        updated_daily = inv_map.reindex(daily_keys)
        hit = updated_daily.notna().to_numpy()
        if hit.any():
            self._daily.loc[hit, 'inventory'] = updated_daily.to_numpy()[hit]
        self._daily = self._daily.sort_values(['sku', 'site', 'date'])
        self._daily['inventory'] = self._daily.groupby(['sku', 'site'])['inventory'].ffill()
        self._daily['in_stock'] = (self._daily['inventory'].fillna(0) > 0).astype(int)

        # 聚合到周 (取每周最后一天的库存)
        inv['week_start'] = inv['date'] - pd.to_timedelta(inv['date'].dt.dayofweek, unit='D')
        inv_w = inv.groupby(['sku', 'site', 'week_start'])['inventory'].last().reset_index()
        inv_w.rename(columns={'week_start': 'date'}, inplace=True)

        # 更新到周表
        for _, row in inv_w.iterrows():
            mask = ((self._weekly['sku'] == row['sku']) &
                    (self._weekly['site'] == row['site']) &
                    (self._weekly['date'] == row['date']))
            if mask.any():
                idx = self._weekly[mask].index[0]
                self._weekly.at[idx, 'inventory'] = row['inventory']
                self._weekly.at[idx, 'in_stock'] = 1 if row['inventory'] > 0 else 0
                roll_4w = self._weekly.at[idx, 'roll_4w_mean']
                if pd.notna(roll_4w) and roll_4w > 0:
                    self._weekly.at[idx, 'stock_weeks'] = min(52, row['inventory'] / roll_4w)

        print(f"  库存更新: {len(inv_w)}条记录")

    # ================================================================
    # 辅助: 数据解析
    # ================================================================

    def _parse_orders(self, df):
        """解析结算报告"""
        df = df[df['type'].isin(ORDER_TYPES)].copy()
        for c in ['product sales', 'promotional rebates', 'shipping credits']:
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
        df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0).astype(int)
        df = df[df['product sales'] != 0]
        df = df[~((df['promotional rebates'].abs() == df['product sales'].abs()) & (df['promotional rebates'] != 0))]
        df['datetime'] = df['date/time'].apply(_parse_date)
        df = df.dropna(subset=['datetime'])
        df['date'] = pd.to_datetime(df['datetime'].dt.date)
        df['site'] = df['marketplace'].map(MARKETPLACE_MAP).fillna('Unknown')
        daily = df.groupby(['date', 'sku', 'site']).agg({'quantity': 'sum', 'product sales': 'sum'}).reset_index()
        daily.rename(columns={'quantity': 'daily_sales', 'product sales': 'daily_revenue'}, inplace=True)
        daily['avg_price'] = daily['daily_revenue'] / daily['daily_sales'].replace(0, np.nan)
        daily['avg_discount'] = 0
        daily['order_count'] = 0
        return daily

    def _parse_simple(self, df):
        """解析简化格式"""
        df = df.copy()
        col_map = {'日期': 'date', '销量': 'sales', '站点': 'site', 'daily_sales': 'sales'}
        df.rename(columns=col_map, inplace=True)
        required = {'date', 'sku', 'site', 'sales'}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(f"销量数据缺少字段: {', '.join(sorted(missing))}")
        df['date'] = pd.to_datetime(df['date'], errors='raise')
        df['sku'] = df['sku'].astype(str).str.strip()
        df['site'] = df['site'].astype(str).str.strip().str.upper()
        df['sales'] = pd.to_numeric(df['sales'], errors='coerce')
        df = df.dropna(subset=['sales'])
        daily = df.rename(columns={'sales': 'daily_sales'})
        if 'avg_price' not in daily.columns: daily['avg_price'] = np.nan
        if 'avg_discount' not in daily.columns: daily['avg_discount'] = 0
        if 'order_count' not in daily.columns: daily['order_count'] = 0
        if 'daily_revenue' not in daily.columns: daily['daily_revenue'] = 0
        aggregations = {
            'daily_sales': 'sum', 'avg_price': 'mean', 'avg_discount': 'mean',
            'order_count': 'sum', 'daily_revenue': 'sum'
        }
        if 'inventory' in daily.columns:
            aggregations['inventory'] = 'last'
        return daily.groupby(['date', 'sku', 'site'], as_index=False).agg(aggregations)

    # ================================================================
    # 辅助: 存储
    # ================================================================

    def _save_state(self):
        with open(self.state_dir / 'weekly.pkl', 'wb') as f: pickle.dump(self._weekly, f)
        with open(self.state_dir / 'daily.pkl', 'wb') as f: pickle.dump(self._daily, f)
        with open(self.state_dir / 'encoders.pkl', 'wb') as f:
            pickle.dump({'le_sku': self._le_sku, 'le_site': self._le_site}, f)

    def _save_models(self):
        with open(self.state_dir / 'week_model.pkl', 'wb') as f: pickle.dump(self._week_model, f)
        with open(self.state_dir / 'day_model.pkl', 'wb') as f: pickle.dump(self._day_model, f)

    def _load_all(self):
        if (self.state_dir / 'weekly.pkl').exists():
            with open(self.state_dir / 'weekly.pkl', 'rb') as f: self._weekly = pickle.load(f)
            with open(self.state_dir / 'daily.pkl', 'rb') as f: self._daily = pickle.load(f)
            with open(self.state_dir / 'encoders.pkl', 'rb') as f:
                enc = pickle.load(f)
                self._le_sku = enc['le_sku']
                self._le_site = enc['le_site']
        if (self.state_dir / 'week_model.pkl').exists():
            with open(self.state_dir / 'week_model.pkl', 'rb') as f: self._week_model = pickle.load(f)
        if (self.state_dir / 'day_model.pkl').exists():
            with open(self.state_dir / 'day_model.pkl', 'rb') as f: self._day_model = pickle.load(f)
        self._load_meta()

    def _save_meta(self):
        with open(self.state_dir / 'meta.json', 'w') as f: json.dump(self._meta, f, indent=2, default=str)

    def _load_meta(self):
        p = self.state_dir / 'meta.json'
        if p.exists():
            with open(p) as f: self._meta = json.load(f)

    def _check_init(self):
        if self._weekly is None:
            raise RuntimeError("未初始化。请先: service.init()")

    def _last_complete_week(self):
        ld = self._daily['date'].max()
        return ld - pd.Timedelta(days=ld.dayofweek)

    def status(self):
        if self._weekly is None:
            result = {'initialized': False, 'week_model': False, 'day_model': False}
            print("  未初始化")
            return result
        result = {
            'initialized': True,
            'last_date': self._meta.get('last_date'),
            'n_skus': int(self._weekly['sku'].nunique()),
            'week_model': self._week_model is not None,
            'day_model': self._day_model is not None,
        }
        print(f"\n  === 状态 ===")
        print(f"  数据截止: {result['last_date'] or '?'}")
        print(f"  SKU数: {result['n_skus']}")
        print(f"  周模型: {'已训练' if result['week_model'] else '未训练'}")
        print(f"  日模型: {'已训练' if result['day_model'] else '未训练'}")
        return result

    def list_skus(self, site=None):
        """列出可用的SKU

        Parameters:
            site: 站点过滤 (可选)
        Returns:
            list of (sku, site, weeks_of_data)
        """
        self._check_init()
        df = self._weekly.groupby(['sku', 'site']).size().reset_index(name='weeks')
        if site:
            df = df[df['site'] == site]
        return df.sort_values('weeks', ascending=False).values.tolist()

    # ================================================================
    # backtest: 回测评估
    # ================================================================

    def backtest(self, sku=None, site=None, test_weeks=8, granularity='week'):
        """
        回测评估模型精度

        Parameters:
            sku: SKU编码 (None=批量回测Top SKU)
            site: 站点
            test_weeks: 回测周数
            granularity: 'week' 或 'day'

        Returns:
            dict: results (逐周结果), mae, median_error_pct
        """
        self._check_init()
        if granularity == 'week' and self._week_model is None:
            raise RuntimeError("周模型未训练。请先调用 service.train('week')")
        if granularity == 'day' and self._day_model is None:
            raise RuntimeError("日模型未训练。请先调用 service.train('day')")

        if sku is None:
            return self._backtest_batch(test_weeks, granularity)

        return self._backtest_single(sku, site or 'US', test_weeks, granularity)

    def _backtest_single(self, sku, site, test_weeks, granularity):
        """单SKU回测"""
        if granularity == 'day':
            return self._backtest_single_day(sku, site, test_weeks)
        return self._backtest_single_week(sku, site, test_weeks)

    def _backtest_single_week(self, sku, site, test_weeks):
        """单SKU周粒度回测"""
        lc = self._last_complete_week()
        mask = (self._weekly['sku'] == sku) & (self._weekly['site'] == site)
        hist_df = self._weekly[mask].sort_values('date')
        hist_df = hist_df[hist_df['date'] < lc]

        if len(hist_df) < 20 + test_weeks:
            raise ValueError(f"数据不足: {sku}/{site} 仅 {len(hist_df)} 周, 需要至少 {20+test_weeks} 周")

        results = []
        for t in range(test_weeks, 0, -1):
            actual_row = hist_df.iloc[-t]
            actual = actual_row['weekly_sales']

            hist_slice = hist_df.iloc[:-t]
            hist_vals = hist_slice['weekly_sales'].values
            pred_date = actual_row['date']
            last_price = hist_slice.iloc[-1].get('avg_price', 0)
            if pd.isna(last_price): last_price = 0

            feat = self._build_week_predict_features(
                hist_vals, hist_slice, pred_date, last_price,
                price=None, discount=None, inventory=None,
                is_promotion=False, promotion_impact=None)

            vf = self._week_model['features']
            X = pd.DataFrame([feat]).reindex(columns=vf, fill_value=0).fillna(0)
            pred_val = max(0, 0.5 * self._week_model['xgb'].predict(X)[0] +
                              0.5 * self._week_model['lgbm'].predict(X)[0])

            err_pct = abs(pred_val - actual) / max(actual, 0.1) * 100
            results.append({
                'week': test_weeks - t + 1,
                'date': str(pred_date.date()),
                'actual': float(round(actual, 1)),
                'predicted': float(round(pred_val, 1)),
                'error_pct': float(round(err_pct, 1)),
            })

        actuals = [r['actual'] for r in results]
        preds = [r['predicted'] for r in results]
        mae = float(round(np.mean(np.abs(np.array(preds) - np.array(actuals))), 1))
        median_err = float(round(np.median([r['error_pct'] for r in results]), 1))

        print(f"\n  回测 [周]: {sku} ({site}) - {len(results)}周")
        print(f"  {'周次':<5} {'日期':<12} {'实际':<8} {'预测':<8} {'误差%':<8}")
        print(f"  {'-'*40}")
        for r in results:
            print(f"  {r['week']:<5} {r['date']:<12} {r['actual']:<8} {r['predicted']:<8} {r['error_pct']:<8}")
        print(f"  {'-'*40}")
        print(f"  MAE: {mae} | 中位误差: {median_err}%")

        return {'results': results, 'mae': mae, 'median_error_pct': median_err}

    def _backtest_single_day(self, sku, site, test_weeks):
        """单SKU日粒度回测 (每周预测7天, 对比实际日销量)"""
        lc = self._last_complete_week()
        mask_d = (self._daily['sku'] == sku) & (self._daily['site'] == site)
        daily_df = self._daily[mask_d].sort_values('date')
        daily_df = daily_df[daily_df['date'] < lc]

        if len(daily_df) < 90 + test_weeks * 7:
            raise ValueError(f"日数据不足: {sku}/{site} 仅 {len(daily_df)} 天")

        if self._day_model is None:
            raise RuntimeError("日模型未训练")

        vf = self._day_model['features']
        results = []

        for t in range(test_weeks, 0, -1):
            # 测试周: 从末尾往回数第 t 周
            week_end_idx = len(daily_df) - (t - 1) * 7
            week_start_idx = week_end_idx - 7
            if week_start_idx < 28:
                continue

            actual_week = daily_df.iloc[week_start_idx:week_end_idx]['daily_sales'].values
            hist_slice = daily_df.iloc[:week_start_idx]
            hist = hist_slice['daily_sales'].values
            last_date = hist_slice['date'].max()
            last_price = hist_slice.iloc[-1].get('avg_price', 0)
            if pd.isna(last_price): last_price = 0

            # 逐日预测7天
            n_hist = len(hist)
            hist_ext = list(hist)
            preds_7d = []
            for d in range(7):
                pred_date = last_date + timedelta(days=d + 1)
                feat = self._build_day_predict_features(
                    np.array(hist_ext), hist_slice, pred_date, last_price,
                    None, None, None, False, None)
                X = pd.DataFrame([feat]).reindex(columns=vf, fill_value=0).fillna(0)
                pred_ml = max(0, 0.5 * self._day_model['xgb'].predict(X)[0] +
                                 0.5 * self._day_model['lgbm'].predict(X)[0])

                # WMA: 只从真实历史取同星期中位数
                target_dow = pred_date.dayofweek
                offset = (last_date.dayofweek - target_dow) % 7
                if offset == 0: offset = 7
                same_dow_vals = []
                for k in range(6):
                    idx = n_hist - offset - 7 * k
                    if idx >= 0:
                        same_dow_vals.append(hist[idx])

                if len(same_dow_vals) >= 2:
                    pred_wma = float(np.median(same_dow_vals))
                elif len(same_dow_vals) == 1:
                    pred_wma = same_dow_vals[0]
                else:
                    pred_wma = pred_ml

                n_dow = len(same_dow_vals)
                w_ml = 0.70 if n_dow < 2 else 0.35 if n_dow < 4 else 0.20
                trend_strength = abs(feat.get('trend_7v28', 0))
                if trend_strength > 0.3:
                    w_ml = min(0.60, w_ml + 0.20)
                elif trend_strength > 0.15:
                    w_ml = min(0.50, w_ml + 0.10)

                pred = max(0, w_ml * pred_ml + (1 - w_ml) * pred_wma)
                preds_7d.append(pred)
                hist_ext.append(pred)

            # 汇总该周
            pred_sum = sum(preds_7d)
            actual_sum = sum(actual_week)
            week_err = abs(pred_sum - actual_sum) / max(actual_sum, 0.1) * 100
            daily_mae = np.mean(np.abs(np.array(preds_7d) - actual_week))
            week_date = str((last_date + timedelta(days=1)).date())

            results.append({
                'week': test_weeks - t + 1,
                'date': week_date,
                'actual_sum': float(round(actual_sum, 1)),
                'predicted_sum': float(round(pred_sum, 1)),
                'week_error_pct': float(round(week_err, 1)),
                'daily_mae': float(round(daily_mae, 1)),
            })

        if not results:
            raise ValueError(f"日数据不足以完成回测")

        avg_week_err = np.mean([r['week_error_pct'] for r in results])
        avg_daily_mae = np.mean([r['daily_mae'] for r in results])

        print(f"\n  回测 [日]: {sku} ({site}) - {len(results)}周 (逐日预测7天)")
        print(f"  {'周次':<5} {'起始日':<12} {'实际周总':<10} {'预测周总':<10} {'周误差%':<9} {'日MAE':<8}")
        print(f"  {'-'*55}")
        for r in results:
            print(f"  {r['week']:<5} {r['date']:<12} {r['actual_sum']:<10} {r['predicted_sum']:<10} "
                  f"{r['week_error_pct']:<9} {r['daily_mae']:<8}")
        print(f"  {'-'*55}")
        print(f"  平均周误差: {avg_week_err:.1f}% | 平均日MAE: {avg_daily_mae:.1f}")

        return {'results': results, 'mae': avg_daily_mae, 'median_error_pct': float(round(
            np.median([r['week_error_pct'] for r in results]), 1))}

    def _backtest_batch(self, test_weeks, granularity):
        """批量回测 (自动选取数据充足的Top SKU)"""
        lc = self._last_complete_week()
        counts = self._weekly[self._weekly['date'] < lc].groupby(['sku', 'site']).size()
        valid = counts[counts >= 20 + test_weeks].reset_index(name='weeks')
        valid = valid.sort_values('weeks', ascending=False).head(20)

        print(f"\n  批量回测 ({len(valid)}个SKU, {test_weeks}周)")
        print(f"  {'SKU':<20} {'站点':<5} {'MAE':<8} {'中位误差%':<10}")
        print(f"  {'-'*45}")

        all_results = []
        for _, row in valid.iterrows():
            try:
                r = self._backtest_single(row['sku'], row['site'], test_weeks, granularity)
                all_results.append({
                    'sku': row['sku'], 'site': row['site'],
                    'mae': r['mae'], 'median_error_pct': r['median_error_pct']
                })
            except Exception as e:
                all_results.append({'sku': row['sku'], 'site': row['site'], 'error': str(e)})

        print(f"\n  === 汇总 ===")
        valid_results = [r for r in all_results if 'mae' in r]
        if valid_results:
            avg_mae = np.mean([r['mae'] for r in valid_results])
            avg_med = np.mean([r['median_error_pct'] for r in valid_results])
            print(f"  平均MAE: {avg_mae:.1f} | 平均中位误差: {avg_med:.1f}%")

        return all_results

    # ================================================================
    # _update_sku_stats: 更新SKU统计量和编码
    # ================================================================

    def _update_sku_stats(self, new_sku_sites):
        """append后更新受影响SKU的统计量和LabelEncoder

        Parameters:
            new_sku_sites: 新增/更新的 sku_site 列表
        """
        # 扩展 LabelEncoder (如有新 SKU) — 追加到末尾，不重排已有ID
        existing_classes = list(self._le_sku.classes_) if self._le_sku else []
        new_classes = [s for s in new_sku_sites if s not in set(existing_classes)]
        if new_classes:
            extended = existing_classes + sorted(new_classes)
            self._le_sku.classes_ = np.array(extended)
            # 只给新SKU分配ID，已有SKU的ID不变
            sku_map = dict(zip(self._le_sku.classes_, range(len(self._le_sku.classes_))))
            for ss in new_classes:
                new_id = sku_map[ss]
                self._weekly.loc[self._weekly['sku_site'] == ss, 'sku_site_id'] = new_id
                self._daily.loc[self._daily['sku_site'] == ss, 'sku_site_id'] = new_id

        existing_sites = list(self._le_site.classes_) if self._le_site else []
        new_sites = []
        for ss in new_sku_sites:
            site = ss.rsplit('_', 1)[-1]
            if site not in set(existing_sites) and site not in new_sites:
                new_sites.append(site)
        if new_sites:
            extended = existing_sites + sorted(new_sites)
            self._le_site.classes_ = np.array(extended)
            site_map = dict(zip(self._le_site.classes_, range(len(self._le_site.classes_))))
            for s in new_sites:
                new_id = site_map[s]
                self._weekly.loc[self._weekly['site'] == s, 'site_id'] = new_id
                self._daily.loc[self._daily['site'] == s, 'site_id'] = new_id

        # 重算受影响SKU的统计量 (周)
        for ss in new_sku_sites:
            wk_mask = self._weekly['sku_site'] == ss
            if wk_mask.any():
                sales = self._weekly.loc[wk_mask, 'weekly_sales']
                self._weekly.loc[wk_mask, 'sku_mean'] = sales.mean()
                self._weekly.loc[wk_mask, 'sku_std'] = sales.std()
                prices = self._weekly.loc[wk_mask, 'avg_price']
                self._weekly.loc[wk_mask, 'sku_price'] = prices.mean()

            # 日统计
            dy_mask = self._daily['sku_site'] == ss
            if dy_mask.any():
                d_sales = self._daily.loc[dy_mask, 'daily_sales']
                self._daily.loc[dy_mask, 'sku_mean_daily'] = d_sales.mean()
                self._daily.loc[dy_mask, 'sku_std_daily'] = d_sales.std()


# ====================================================================
# 命令行
# ====================================================================

if __name__ == '__main__':
    args = sys.argv[1:]
    service = ForecastService()

    if not args:
        print(__doc__)
        service.status()
        sys.exit(0)

    cmd = args[0]
    if cmd == 'init':
        service.init()
    elif cmd == 'train':
        gran = args[1] if len(args) > 1 else None
        service.train(granularity=gran)
    elif cmd == 'predict':
        sku = args[1] if len(args) > 1 else 'FBA1LED908'
        site = args[2] if len(args) > 2 else 'US'
        gran = args[3] if len(args) > 3 else 'day'
        n = int(args[4]) if len(args) > 4 else 7
        start = args[5] if len(args) > 5 else None
        if gran == 'week':
            r = service.predict(sku, site, granularity='week', weeks=n, start_date=start)
        else:
            r = service.predict(sku, site, granularity='day', days=n, start_date=start)
        print(f"\n  周总量: {r['weekly_total']}, 日均: {r['daily_avg']}")
        print(f"  置信区间: {r['confidence_interval']}")
        print(f"  可靠度: {r['reliability']}")
        if 'predictions' in r and r['predictions']:
            p0 = r['predictions'][0]
            if 'dow' in p0:
                print(f"\n  {'日期':<12} {'星期':<5} {'预测':<8} {'下界':<8} {'上界':<8}")
                for p in r['predictions']:
                    print(f"  {p['date']:<12} {p['dow']:<5} {p['sales']:<8} {p['lower']:<8} {p['upper']:<8}")
            else:
                print(f"\n  {'周起始':<12} {'预测':<8}")
                for p in r['predictions']:
                    print(f"  {p['week_start']:<12} {p['sales']:<8}")
    elif cmd == 'backtest':
        sku = args[1] if len(args) > 1 else None
        site = args[2] if len(args) > 2 else 'US'
        tw = int(args[3]) if len(args) > 3 else 8
        gran = args[4] if len(args) > 4 else 'week'
        if sku:
            service.backtest(sku, site, test_weeks=tw, granularity=gran)
        else:
            service.backtest(test_weeks=tw, granularity=gran)
    elif cmd == 'status':
        service.status()
