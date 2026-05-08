"""
kcb.py - 科创板分析模块 v2
数据源：
  - akshare.stock_zh_kcb_spot       实时行情
  - akshare.stock_zh_kcb_daily      历史K线
  - akshare.stock_zh_kcb_report_em  科创板报告

使用示例：
  get_kcb_spot() -> DataFrame        # 科创板全市场行情
  get_kcb_kline(code, days=320) -> list  # 单只K线
  analyze_kcb() -> dict              # 科创板全景分析
"""

import warnings
import os
import json

warnings.filterwarnings("ignore")

_ak = None

def _akshare():
    global _ak
    if _ak is None:
        import akshare as _mod
        _ak = _mod
    return _ak


# ── 核心接口 ───────────────────────────────────────────────────

def get_kcb_spot():
    """
    获取科创板全市场实时行情
    数据源：akshare.stock_zh_kcb_spot()
    返回字段：代码、名称、最新价、涨跌幅、换手率、市盈率、市净率、流通市值、总市值...
    返回：DataFrame（标准化列名）
    """
    import pandas as pd
    ak = _akshare()
    df = ak.stock_zh_kcb_spot()
    if df is None or df.empty:
        return pd.DataFrame()

    # 标准化列名（真实列：代码/名称/最新价/涨跌额/涨跌幅/买入/卖出/昨收/今开/最高/最低/成交量/成交额/时点/市盈率/市净率/流通市值/总市值/换手率）
    rename = {}
    for col in df.columns:
        if "代码" in col:
            rename[col] = "代码"
        elif "名称" in col:
            rename[col] = "名称"
        elif "最新" in col or "现价" in col:
            rename[col] = "最新价"
        elif "涨跌额" in col:
            rename[col] = "涨跌额"
        elif "涨跌幅" in col:
            rename[col] = "涨跌幅(%)"
        elif "换手率" in col:
            rename[col] = "换手率(%)"
        elif "市盈率" in col:
            rename[col] = "市盈率"
        elif "市净率" in col:
            rename[col] = "市净率"
        elif "流通" in col and "市值" in col:
            rename[col] = "流通市值"
        elif "总市值" in col:
            rename[col] = "总市值"
        elif "成交量" in col:
            rename[col] = "成交量"
        elif "成交额" in col:
            rename[col] = "成交额"
        elif "最高" in col:
            rename[col] = "最高"
        elif "最低" in col:
            rename[col] = "最低"
        elif "今开" in col or "开盘" in col:
            rename[col] = "今开"
        elif "昨收" in col:
            rename[col] = "昨收"

    df = df.rename(columns=rename)

    # 清理代码格式（去除 sh/sz 前缀）
    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.replace(r"^(sh|sz)", "", regex=True).str.zfill(6)

    # 市值单位从元转为亿
    for col in ["流通市值", "总市值"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce") / 1e8

    # 成交额从元转为亿
    if "成交额" in df.columns:
        df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce") / 1e8

    return df.reset_index(drop=True)


def get_kcb_kline(code, days=320):
    """
    获取科创板单只股票的历史日K线
    参数：
        code: 6位代码，如 "688001"（不含前缀）
    返回：
        list of dict: [{日期, 开盘, 最高, 最低, 收盘, 成交量, 成交额, ...}, ...]
    """
    import pandas as pd
    code = code.replace("sh", "").replace("sz", "")
    ak = _akshare()
    try:
        df = ak.stock_zh_kcb_daily(symbol=code)
        if df is None or df.empty:
            return []
    except Exception:
        return []

    # 标准化列名
    rename_map = {}
    for col in df.columns:
        c = str(col).lower()
        if "日期" in col:
            rename_map[col] = "日期"
        elif "开盘" in col:
            rename_map[col] = "开盘"
        elif "最高" in col:
            rename_map[col] = "最高"
        elif "最低" in col:
            rename_map[col] = "最低"
        elif "收盘" in col:
            rename_map[col] = "收盘"
        elif "成交量" in col:
            rename_map[col] = "成交量"
        elif "成交额" in col:
            rename_map[col] = "成交额"
        elif "振幅" in col:
            rename_map[col] = "振幅"
        elif "涨跌幅" in col:
            rename_map[col] = "涨跌幅"
        elif "涨跌额" in col:
            rename_map[col] = "涨跌额"
        elif "换手率" in col:
            rename_map[col] = "换手率"

    df = df.rename(columns=rename_map)
    df = df.tail(days)
    return df.to_dict("records")


def analyze_kcb():
    """
    科创板全景分析，返回 dict 包含：
      - 涨跌幅分布统计
      - 成交额Top10
      - 换手率Top10（活跃度）
      - 市盈率分布
    """
    import pandas as pd
    df = get_kcb_spot()
    if df.empty:
        return {"error": "无法获取科创板数据"}

    total = len(df)
    chg_col = "涨跌幅(%)"
    for col in [chg_col, "换手率(%)"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    up   = int((df[chg_col] > 0).sum())
    down = int((df[chg_col] < 0).sum())
    flat = total - up - down
    avg_chg = round(float(df[chg_col].mean()), 3)
    med_chg = round(float(df[chg_col].median()), 3)

    # 涨跌幅分布
    zt_count = int((df[chg_col] >= 9.9).sum())
    dt_count = int((df[chg_col] <= -9.9).sum())
    chg_bins = [-100, -9.9, -5, -2, 0, 2, 5, 9.9, 100]
    chg_labels = ["跌停(-10%)", "暴跌(-10~-5%)", "大跌(-5~-2%)", "小跌(-2~0%)",
                  "小涨(0~2%)", "大涨(2~5%)", "暴涨(5~10%)", "涨停(+10%)"]
    df_copy = df.copy()
    df_copy["区间"] = pd.cut(df_copy[chg_col], bins=chg_bins, labels=chg_labels)
    chg_dist = df_copy["区间"].value_counts().reindex(chg_labels).to_dict()

    # 成交额Top10（列名"成交额"已是亿元单位）
    amt_col = "成交额"
    amt_top = df.dropna(subset=[amt_col]).nlargest(10, amt_col)

    # 换手率Top10
    turn_col = "换手率(%)"
    turn_top = df.dropna(subset=[turn_col]).nlargest(10, turn_col)

    # 市盈率分布
    pe_col = "市盈率"
    for col in [pe_col, "市净率"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    pe_df = df.dropna(subset=[pe_col])
    pe_df = pe_df[pe_df[pe_col] > 0]
    if not pe_df.empty:
        pe_avg = round(float(pe_df[pe_col].mean()), 1)
        pe_med = round(float(pe_df[pe_col].median()), 1)
        pe_low = round(float(pe_df[pe_col].min()), 1)
        pe_high = round(float(pe_df[pe_col].max()), 1)
    else:
        pe_avg = pe_med = pe_low = pe_high = None

    # 市值分布
    mkt_col = "总市值"
    if mkt_col in df.columns:
        df[mkt_col] = pd.to_numeric(df[mkt_col], errors="coerce")
    mkt = df.dropna(subset=[mkt_col])
    mkt_avg = round(float(mkt[mkt_col].mean()), 1) if not mkt.empty else None
    mkt_large = mkt[mkt[mkt_col] > 500] if not mkt.empty else pd.DataFrame()
    mkt_mid   = mkt[(mkt[mkt_col] > 100) & (mkt[mkt_col] <= 500)] if not mkt.empty else pd.DataFrame()
    mkt_small = mkt[mkt[mkt_col] <= 100] if not mkt.empty else pd.DataFrame()

    # 强势/弱势Top5
    gainers = df.nlargest(5, chg_col)
    losers  = df.nsmallest(5, chg_col)

    # 市场情绪
    sentiment = "强势" if avg_chg > 1 else ("弱势" if avg_chg < -1 else "震荡")

    return {
        "total":       total,
        "up":          up,
        "down":        down,
        "flat":        flat,
        "avg_chg":     avg_chg,
        "median_chg":  med_chg,
        "zt_count":    zt_count,
        "dt_count":    dt_count,
        "chg_dist":    chg_dist,
        "pe_avg":      pe_avg,
        "pe_med":      pe_med,
        "pe_low":      pe_low,
        "pe_high":     pe_high,
        "mkt_avg":     mkt_avg,
        "mkt_large_n": len(mkt_large),
        "mkt_mid_n":   len(mkt_mid),
        "mkt_small_n": len(mkt_small),
        "gainers":     gainers[["名称","代码",chg_col,"最新价",turn_col,mkt_col]].to_dict("records"),
        "losers":      losers[["名称","代码",chg_col,"最新价",turn_col,mkt_col]].to_dict("records"),
        "amt_top":     amt_top[["名称","代码",chg_col,"成交额","最新价",turn_col]].to_dict("records"),
        "turn_top":    turn_top[["名称","代码",chg_col,turn_col,"最新价","成交额"]].to_dict("records"),
        "sentiment":   sentiment,
    }


def analyze_kcb_stock(code):
    """
    科创板单只股票深度分析
    """
    import pandas as pd
    df = get_kcb_spot()
    code_clean = code.replace("sh", "").replace("sz", "")
    row = df[df["代码"].str.contains(code_clean)] if not df.empty else pd.DataFrame()
    if row.empty:
        return {"error": f"未找到科创板股票 {code}"}

    row = row.iloc[0]

    # 获取K线
    kline = get_kcb_kline(code_clean, days=320)
    if len(kline) >= 20:
        closes = [float(k["收盘"]) for k in kline]
        ma5  = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 20 else ma5
        cur  = closes[-1]
        trend = "上升" if ma5 > ma20 else "下降"
    else:
        ma5 = ma20 = ma60 = cur = None
        trend = "震荡"

    return {
        "code":   code,
        "name":   row.get("名称", ""),
        "price":  row.get("最新价"),
        "chg":    row.get("涨跌幅(%)"),
        "turn":   row.get("换手率(%)"),
        "pe":     row.get("市盈率"),
        "pb":     row.get("市净率"),
        "mkt":    row.get("总市值"),
        "float_mkt": row.get("流通市值"),
        "ma5":    round(ma5, 2) if ma5 else None,
        "ma20":   round(ma20, 2) if ma20 else None,
        "ma60":   round(ma60, 2) if ma60 else None,
        "trend":  trend,
        "kline":  kline,
    }
