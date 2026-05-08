"""
fund_flow.py - 资金流分析模块 v2
数据源（均通过 akshare）：
  - stock_fund_flow_industry_ths()   → 行业资金流（同花顺，90行业）
  - stock_fund_flow_concept_ths()  → 概念资金流（同花顺，387概念）
  - stock_individual_fund_flow()    → 个股资金流（东财，按日）
  - stock_hsgt_hold_stock_em()      → 北向持股排行（东财，1336只）

使用示例：
  get_industry_flow()   -> DataFrame  # 行业资金流
  get_concept_flow()    -> DataFrame  # 概念资金流
  get_stock_flow(code)  -> DataFrame  # 个股资金流
  analyze_fund_flow()   -> dict       # 综合分析
"""

import pandas as pd
import numpy as np
import warnings
import os

# WSL 代理
os.environ.pop("http_proxy",  None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY",  None)
os.environ.pop("HTTPS_PROXY", None)
warnings.filterwarnings("ignore")

_ak = None

def _akshare():
    global _ak
    if _ak is None:
        import akshare as _mod
        _ak = _mod
    return _ak


def _fmt_amt(v):
    """格式化金额（亿元）"""
    try:
        v = float(v)
        if abs(v) >= 1e8:
            sign = "+" if v > 0 else ""
            return f"{sign}{v/1e8:.2f}亿"
        if abs(v) >= 1e6:
            sign = "+" if v > 0 else ""
            return f"{sign}{v/1e6:.2f}万"
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.0f}"
    except:
        return "N/A"


def get_industry_flow() -> pd.DataFrame:
    """
    行业资金流（90个行业，按今日资金净流入排序）
    akshare 顶级函数: ak.stock_fund_flow_industry()
    字段：序号、行业、行业指数、行业-涨跌幅、流入资金、流出资金、净额、公司家数、领涨股...
    """
    ak = _akshare()
    df = ak.stock_fund_flow_industry()
    if df is None or df.empty:
        return pd.DataFrame()

    rename = {}
    for col in df.columns:
        c = str(col)
        if c == "行业" and "板块" not in c:
            rename[col] = "行业名称"
        elif c == "概念" or ("板块" in c and "概念" not in c):
            rename[col] = "概念名称"
        elif "涨跌幅" in c:
            rename[col] = "涨跌幅(%)"
        elif "流入资金" == c:
            rename[col] = "流入资金(万)"
        elif "流出资金" == c:
            rename[col] = "流出资金(万)"
        elif c in ("净额", "净流入"):
            rename[col] = "净流入(万)"

    df = df.rename(columns=rename)

    # 去除 rename 产生的重复列名
    keep_pos, seen = [], set()
    for i, c in enumerate(df.columns):
        if c not in seen:
            seen.add(c); keep_pos.append(i)
    if len(keep_pos) < len(df.columns):
        df = df.iloc[:, keep_pos]

    def _safe_num(col):
        if col not in df.columns:
            return
        raw = df[col]
        if isinstance(raw, pd.DataFrame):
            raw = raw.iloc[:, 0]
        df[col] = pd.to_numeric(raw, errors="coerce")

    for col in ["流入资金(万)", "流出资金(万)", "净流入(万)", "涨跌幅(%)"]:
        _safe_num(col)

    return df.sort_values("净流入(万)", ascending=False).reset_index(drop=True)


def get_hot_boards(n=30) -> dict:
    """
    综合识别热门板块，返回：
      {
        "概念": DataFrame(概念名称/涨跌幅/净流入/得分),
        "行业": DataFrame(行业名称/涨跌幅/净流入/得分),
        "混合": DataFrame(板块名称/涨跌幅/净流入/类型/得分)  # 类型="概念"或"行业"
      }
    评分算法：z-score标准化涨跌幅 + z-score标准化净流入，两者等权相加
    """
    ind_flow = get_industry_flow()
    con_flow = get_concept_flow()

    def _score(df, name_col, net_col, pct_col, board_type):
        if df.empty:
            return pd.DataFrame()
        df = df.copy()
        net = pd.to_numeric(df[net_col], errors="coerce").fillna(0)
        pct = pd.to_numeric(df[pct_col], errors="coerce").fillna(0)
        net_z = (net - net.mean()) / (net.std() + 1e-9)
        pct_z = (pct - pct.mean()) / (pct.std() + 1e-9)
        df["_score"] = net_z + pct_z
        df["类型"] = board_type
        return df[[name_col, pct_col, net_col, "_score", "类型"]].copy()

    ind_scored = _score(ind_flow, "行业名称", "净流入(万)", "涨跌幅(%)", "行业")
    con_scored = _score(con_flow, "概念名称", "净流入(万)", "涨跌幅(%)", "概念")

    # 统一列名再 concat（行业名称/概念名称 → 板块名称）
    ind_std = ind_scored.rename(columns={"行业名称": "板块名称"})
    con_std = con_scored.rename(columns={"概念名称": "板块名称"})

    mixed = pd.concat([ind_std, con_std], ignore_index=True)
    mixed = mixed.sort_values("_score", ascending=False).reset_index(drop=True)
    mixed.columns = ["板块名称", "涨跌幅(%)", "净流入(万)", "得分", "类型"]
    return {
        "概念": con_scored.sort_values("_score", ascending=False).head(n).reset_index(drop=True)
                if not con_scored.empty else pd.DataFrame(),
        "行业": ind_scored.sort_values("_score", ascending=False).head(n).reset_index(drop=True)
                if not ind_scored.empty else pd.DataFrame(),
        "混合": mixed.head(n),
    }


def get_concept_flow() -> pd.DataFrame:
    """
    概念资金流
    akshare 顶级函数: ak.stock_fund_flow_concept()
    """
    ak = _akshare()
    df = ak.stock_fund_flow_concept()
    if df is None or df.empty:
        return pd.DataFrame()

    rename = {}
    for col in df.columns:
        c = str(col)
        if c == "行业" and "板块" not in c:
            rename[col] = "概念名称"
        elif "涨跌幅" in c:
            rename[col] = "涨跌幅(%)"
        elif "流入资金" == c:
            rename[col] = "流入资金(万)"
        elif "流出资金" == c:
            rename[col] = "流出资金(万)"
        elif c in ("净额", "净流入"):
            rename[col] = "净流入(万)"

    df = df.rename(columns=rename)

    # 去除 rename 产生的重复列名
    keep_pos, seen = [], set()
    for i, c in enumerate(df.columns):
        if c not in seen:
            seen.add(c); keep_pos.append(i)
    if len(keep_pos) < len(df.columns):
        df = df.iloc[:, keep_pos]

    def _safe_num(col):
        if col not in df.columns:
            return
        raw = df[col]
        if isinstance(raw, pd.DataFrame):
            raw = raw.iloc[:, 0]
        df[col] = pd.to_numeric(raw, errors="coerce")

    for col in ["流入资金(万)", "流出资金(万)", "净流入(万)", "涨跌幅(%)"]:
        _safe_num(col)

    return df.sort_values("净流入(万)", ascending=False).reset_index(drop=True)


def get_stock_flow(code: str, period: int = 5) -> pd.DataFrame:
    """
    个股资金流（近N日）

    参数：
        code: 6位代码，如 "000001"
        period: 天数，默认5
    akshare: ak.stock_individual_fund_flow(stock, market)
             market: sh=1, sz=0
    """
    code_clean = code.replace("sh", "").replace("sz", "").zfill(6)
    ak = _akshare()

    # 判断市场
    if code_clean.startswith("6") or code_clean.startswith("688"):
        market = "sh"
    else:
        market = "sz"

    try:
        df = ak.stock_individual_fund_flow(code_clean, market)
        if df is None or df.empty:
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

    rename = {}
    for col in df.columns:
        c = str(col)
        if "日期" in c:
            rename[col] = "日期"
        elif "收盘" in c:
            rename[col] = "收盘价"
        elif "涨跌幅" in c:
            rename[col] = "涨跌幅(%)"
        elif "净流入" in c:
            rename[col] = "净流入(万元)"
        elif "流入" in c and "主力" not in c:
            rename[col] = "流入(万元)"
        elif "流出" in c:
            rename[col] = "流出(万元)"
        elif "主力" in c:
            rename[col] = "主力净流入(万元)"

    df = df.rename(columns=rename)
    return df.reset_index(drop=True)


def get_hsgt_hold() -> pd.DataFrame:
    """
    北向持股排行（沪股通+深股通持股最多）
    akshare: ak.stock_hsgt_hold_stock_em()
    字段：序号、代码、名称、今日收盘价、今日涨跌幅、今日持股-股数、今日持股-市值、
          今日持股-占流通股比、今日持股-占总股本比...
    """
    ak = _akshare()
    df = ak.stock_hsgt_hold_stock_em()
    if df is None or df.empty:
        return pd.DataFrame()

    rename = {}
    for col in df.columns:
        c = str(col)
        if "代码" == c:
            rename[col] = "代码"
        elif "名称" == c:
            rename[col] = "名称"
        elif "收盘" in c or "最新" in c:
            rename[col] = "收盘价"
        elif "涨跌幅" in c:
            rename[col] = "涨跌幅(%)"
        elif "持股-股数" in c:
            rename[col] = "持股数量"
        elif "持股-占" in c or "持股比例" in c:
            rename[col] = "持股比例"

    df = df.rename(columns=rename)

    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.zfill(6)

    return df.reset_index(drop=True)


def analyze_fund_flow() -> dict:
    """
    综合资金流分析，返回 dict：
      - 行业净流入Top10 / Bottom10
      - 概念净流入Top10 / Bottom10
      - 北向持股Top10
      - 整体市场资金情绪
    """
    # 行业资金流
    ind_flow = get_industry_flow()

    # 概念资金流
    con_flow = get_concept_flow()

    # 北向持股
    hsgt = get_hsgt_hold()

    # 行业汇总统计
    if not ind_flow.empty:
        total_inflow = float(ind_flow["流入资金(万)"].sum())
        total_outflow = float(ind_flow["流出资金(万)"].sum())
        net_flow = float(ind_flow["净流入(万)"].sum())
        inflow_ind = int((ind_flow["净流入(万)"] > 0).sum())
        outflow_ind = int((ind_flow["净流入(万)"] < 0).sum())
    else:
        total_inflow = total_outflow = net_flow = 0
        inflow_ind = outflow_ind = 0

    # 行业净流入Top10
    ind_top = []
    if not ind_flow.empty:
        for _, row in ind_flow.head(10).iterrows():
            ind_top.append({
                "行业":      row.get("行业名称", ""),
                "涨跌幅":    f"{row.get('涨跌幅(%)', 0):+.2f}%",
                "净流入":    _fmt_amt(row.get("净流入(万)", 0) * 1e4),
                "流入资金":  _fmt_amt(row.get("流入资金(万)", 0) * 1e4),
            })

    # 行业净流出Top10
    ind_bot = []
    if not ind_flow.empty:
        for _, row in ind_flow.tail(10).iloc[::-1].iterrows():
            ind_bot.append({
                "行业":      row.get("行业名称", ""),
                "涨跌幅":    f"{row.get('涨跌幅(%)', 0):+.2f}%",
                "净流入":    _fmt_amt(row.get("净流入(万)", 0) * 1e4),
                "流出资金":  _fmt_amt(row.get("流出资金(万)", 0) * 1e4),
            })

    # 概念净流入Top10
    con_top = []
    if not con_flow.empty:
        for _, row in con_flow.head(10).iterrows():
            con_top.append({
                "概念":      row.get("概念名称", ""),
                "涨跌幅":    f"{row.get('涨跌幅(%)', 0):+.2f}%",
                "净流入":    _fmt_amt(row.get("净流入(万)", 0) * 1e4),
            })

    # 北向持股Top10
    hsgt_top = []
    if not hsgt.empty:
        for _, row in hsgt.head(10).iterrows():
            hsgt_top.append({
                "代码":    row.get("代码", ""),
                "名称":    row.get("名称", ""),
                "收盘价":  row.get("收盘价", "N/A"),
                "涨跌幅":  f"{row.get('涨跌幅(%)', 0):+.2f}%",
                "持股比例": f"{row.get('持股比例', 'N/A')}",
            })

    # 资金情绪
    if net_flow > 5e8:
        flow_sentiment = "积极（主力净流入）"
    elif net_flow < -5e8:
        flow_sentiment = "谨慎（主力净流出）"
    else:
        flow_sentiment = "中性（多空平衡）"

    return {
        "total_inflow":   _fmt_amt(total_inflow * 1e4),
        "total_outflow":  _fmt_amt(total_outflow * 1e4),
        "net_flow":       _fmt_amt(net_flow * 1e4),
        "inflow_inds":    inflow_ind,
        "outflow_inds":   outflow_ind,
        "flow_sentiment": flow_sentiment,
        "ind_top10":      ind_top,
        "ind_bottom10":   ind_bot,
        "con_top10":      con_top,
        "hsgt_top10":     hsgt_top,
    }
