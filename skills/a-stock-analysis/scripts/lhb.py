"""
lhb.py - 龙虎榜分析模块 v3
数据源（akshare）：
  - stock_lhb_jgmmtj_em()      → 机构买卖统计（近300+条，有2024年日期）
  - stock_lhb_jgstatistic_em() → 机构龙虎榜统计（400+只，有上榜次数）
  - stock_lhb_detail_em()       → 龙虎榜详情（637条，注意：数据截至2023年，需配合jgmmtj使用）

使用示例：
  get_lhb_detail(days=90)     -> DataFrame  # 近N日龙虎榜明细（自动兼容jgmmtj）
  get_lhb_statistics()        -> DataFrame  # 个股统计（谁上的多）
  analyze_lhb(days=90)         -> dict
"""

import pandas as pd
import numpy as np
import warnings
import os

# 清除代理
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


def _fmt_mkt(v):
    try:
        v = float(v)
        if v >= 1e8: return f"{v/1e8:.2f}亿"
        if v >= 1e6: return f"{v/1e6:.2f}万"
        return f"{v:.0f}"
    except:
        return "N/A"


def _try_date(v):
    """把各种日期格式转为 pd.Timestamp，失败返回 NaT"""
    try:
        if hasattr(v, 'date'):
            v = v.date()
        return pd.Timestamp(v)
    except:
        return pd.NaT


def get_lhb_detail(days: int = 90) -> pd.DataFrame:
    """
    龙虎榜详情，返回近 N 日有上榜的个股（基于统计表的上榜日）。
    注意：akshare 龙虎榜明细数据截至约2024年，无法获取实时今日数据。
    此函数返回统计表中有上榜记录的股票作为参考。
    """
    ak = _akshare()
    # 用 jgstatistic_em（有2024年近期上榜日）作为主数据源
    try:
        df = ak.stock_lhb_jgstatistic_em()
    except Exception:
        try:
            df = ak.stock_lhb_detail_em()
        except Exception:
            return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # 第一步：列名去重（去除 akshare 返回的原始重复列）
    keep_pos, seen = [], set()
    for i, c in enumerate(df.columns):
        cn = str(c)
        if cn not in seen:
            seen.add(cn); keep_pos.append(i)
    if len(keep_pos) < len(df.columns):
        df = df.iloc[:, keep_pos]

    # 第二步：rename（jgstatistic_em 列名：代码/名称/收盘价/涨跌幅(今日)/龙虎榜成交金额/上榜次数/机构买入额/机构买入次数/机构卖出额/机构卖出次数/机构净买额/近1/3/6个月涨跌幅/近1年涨跌幅）
    rename = {}
    for col in df.columns:
        c = str(col)
        if c in ("代码", "股票代码"):
            rename[col] = "代码"
        elif c in ("名称", "股票简称"):
            rename[col] = "名称"
        elif "上榜日" in c or "最近上榜" in c:
            rename[col] = "上榜日期"
        elif c == "收盘价":
            rename[col] = "收盘价"
        elif c == "涨跌幅":
            # 今日涨跌幅精确匹配，优先于近N月涨跌幅
            rename[col] = "涨跌幅(%)"
        elif c in ("解读",):
            rename[col] = "解读"
        elif c in ("上榜原因",):
            rename[col] = "上榜原因"
        elif c in ("换手率",):
            rename[col] = "换手率(%)"
        elif "龙虎榜" in c and ("净买" in c or "净额" in c):
            rename[col] = "龙虎榜净买额"
        elif c == "机构买入次数":
            rename[col] = "买方机构数"
        elif c == "机构卖出次数":
            rename[col] = "卖方机构数"
        elif c == "机构买入额":
            rename[col] = "机构买入额"
        elif c == "机构卖出额":
            rename[col] = "机构卖出额"
        elif c in ("龙虎榜成交金额",):
            rename[col] = "龙虎榜成交金额"
        elif c in ("上榜次数",):
            rename[col] = "上榜次数"
    df = df.rename(columns=rename)

    # 第三步：去除 rename 后产生的重复列名（多个源列映射到同一目标，如'涨跌幅'+'近1月涨跌幅'→'涨跌幅(%)'）
    keep_pos, seen = [], set()
    for i, c in enumerate(df.columns):
        if c not in seen:
            seen.add(c); keep_pos.append(i)
    if len(keep_pos) < len(df.columns):
        df = df.iloc[:, keep_pos]

    # 按上榜日期过滤（仅对有日期列的生效）
    date_col = "上榜日期"
    if date_col in df.columns:
        df["_dt"] = df[date_col].apply(_try_date)
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
        # 如果过滤后为空，保留全量（数据本身可能较旧）
        filtered = df[df["_dt"] >= cutoff].drop(columns=["_dt"])
        if not filtered.empty:
            df = filtered

    # 统一代码格式
    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        df["代码"] = df["代码"].apply(
            lambda x: ("sh" + x) if x.startswith(("6", "688")) else ("sz" + x)
        )

    return df.reset_index(drop=True)


def get_lhb_statistics() -> pd.DataFrame:
    """
    龙虎榜个股统计：每只股票的上榜次数、平均涨幅、机构参与情况
    """
    ak = _akshare()
    df = ak.stock_lhb_jgstatistic_em()
    if df is None or df.empty:
        return pd.DataFrame()

    rename = {}
    for col in df.columns:
        c = str(col)
        if c == "代码":
            rename[col] = "代码"
        elif c == "名称":
            rename[col] = "名称"
        elif "上榜日" in c or "最近上榜" in c:
            rename[col] = "最近上榜日"
        elif "收盘" in c:
            rename[col] = "收盘价"
        elif "涨跌幅" in c:
            rename[col] = "涨跌幅(%)"
        elif "上榜次数" in c:
            rename[col] = "上榜次数"
        elif "机构" in c and ("买方" in c or "次数" in c):
            rename[col] = "买方机构次数"
        elif "机构" in c and "卖方" in c:
            rename[col] = "卖方机构次数"
        elif "机构买入净额" in c:
            rename[col] = "机构买入净额"
    df = df.rename(columns=rename)

    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        df["代码"] = df["代码"].apply(
            lambda x: ("sh" + x) if x.startswith(("6", "688")) else ("sz" + x)
        )

    return df.reset_index(drop=True)


def analyze_lhb(days: int = 90) -> dict:
    """
    龙虎榜综合分析，返回 dict：
      - 近期龙虎榜概览
      - 机构净买入Top10
      - 游资活跃榜（频繁上榜但无机构）
      - 近期上榜股票池
    """
    detail = get_lhb_detail(days=days)
    stats  = get_lhb_statistics()

    # ── 概览 ──────────────────────────────────────────────────
    n_stocks = detail["代码"].nunique() if "代码" in detail.columns and not detail.empty else 0
    n_entries = len(detail)

    # ── 机构净买 Top10 ────────────────────────────────────────
    inst_rows = []
    if not detail.empty:
        # 优先用机构净买额排序；其次用买方机构数
        net_col = "机构净买额" if "机构净买额" in detail.columns else None
        count_col = "买方机构数" if "买方机构数" in detail.columns else None
        sort_col = net_col or count_col

        if sort_col:
            inst_df = detail.copy()
            raw = inst_df[sort_col]
            if isinstance(raw, pd.DataFrame):
                raw = raw.iloc[:, 0]
            inst_df[sort_col] = pd.to_numeric(raw, errors="coerce")

            if net_col:
                # 机构净买额：正值表示机构净买入，按净买额降序
                pos = inst_df[inst_df[net_col] > 0].nlargest(10, net_col)
            else:
                # 买方机构数：按次数降序
                pos = inst_df.nlargest(10, sort_col)

            for _, row in pos.iterrows():
                inst_rows.append({
                    "代码":   row.get("代码", ""),
                    "名称":   row.get("名称", ""),
                    "收盘价": row.get("收盘价", "N/A"),
                    "涨跌幅(%)": row.get("涨跌幅(%)", "N/A"),
                    "机构数": int(row.get(count_col or net_col, 0)) if count_col else f"{row.get(net_col, 0)/1e8:.2f}亿",
                    "解读":   "",
                })

    # ── 上榜次数最多 Top10 ────────────────────────────────────
    hot_stocks = []
    if not stats.empty and "代码" in stats.columns:
        count_col = "上榜次数" if "上榜次数" in stats.columns else stats.columns[0]
        if count_col in stats.columns:
            stats_top = stats.sort_values(count_col, ascending=False).head(10)
            for _, row in stats_top.iterrows():
                hot_stocks.append({
                    "代码":     row.get("代码", ""),
                    "名称":     row.get("名称", ""),
                    "上榜次数": row.get(count_col, "N/A"),
                    "最近上榜": row.get("最近上榜日", "N/A"),
                    "收盘价":   row.get("收盘价", "N/A"),
                    "涨跌幅":   row.get("涨跌幅(%)", "N/A"),
                })

    # ── 解读关键词分析 ────────────────────────────────────────
    interpretation_summary = {}
    if "解读" in detail.columns and not detail.empty:
        for _, row in detail.iterrows():
            text = str(row.get("解读", ""))
            if text and text not in ("nan", "None"):
                for kw in ["机构", "游资", "成功", "失败"]:
                    if kw in text:
                        interpretation_summary[kw] = interpretation_summary.get(kw, 0) + 1

    # ── recent_entries ─────────────────────────────────────────
    recent_entries = []
    if not detail.empty:
        disp_cols = ["代码", "名称", "上榜日期", "涨跌幅(%)", "收盘价", "解读"]
        avail = [c for c in disp_cols if c in detail.columns]
        for _, row in detail.head(30).iterrows():
            recent_entries.append({c: row.get(c, "") for c in avail})

    return {
        "total_entries": n_entries,
        "total_stocks": n_stocks,
        "period_days":  days,
        "inst_buy_top": inst_rows,
        "hot_stocks":   hot_stocks,
        "recent_entries": recent_entries,
        "interpretation_kw": interpretation_summary,
        "summary": f"近{days}日龙虎榜共{n_entries}条记录，{n_stocks}只个股上榜",
    }


def get_lhb_stock_detail(code: str) -> dict:
    """
    查询单只股票的龙虎榜历史
    """
    stats = get_lhb_statistics()
    code_clean = code.replace("sh", "").replace("sz", "").zfill(6)
    row = stats[stats["代码"].str.contains(code_clean)] if not stats.empty else pd.DataFrame()
    if row.empty:
        return {"error": f"未找到 {code} 的龙虎榜记录"}

    row = row.iloc[0]
    return {
        "代码":     row.get("代码", ""),
        "名称":     row.get("名称", ""),
        "上榜次数": row.get("上榜次数", "N/A"),
        "最近上榜": row.get("最近上榜日", "N/A"),
        "收盘价":   row.get("收盘价", "N/A"),
        "涨跌幅":   row.get("涨跌幅(%)", "N/A"),
        "龙虎榜净额": row.get("龙虎榜净额", "N/A"),
        "上榜日期列表": [],
    }
