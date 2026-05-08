"""
opportunity.py - 个股机会发现 v2
基于同花顺技术选股数据 + 东方财富涨跌停数据，发现各类交易机会

数据源（均通过 akshare）：
  - 同花顺技术选股：创新高 / 连续上涨 / 持续放量 / 量价齐升 / 向上突破 / 创新低 / 连续下跌
  - 东方财富涨跌停：涨停股池 / 跌停股池 / 昨日涨停 / 强势股池 / 次新股池 / 炸板股池
  - 新股上市首日（同花顺）

使用示例：
  find_opportunities() -> dict of {category: DataFrame}
  get_hot_stocks(limit=20) -> DataFrame
  analyze_limit_up() -> dict
"""

import pandas as pd
import numpy as np
import warnings
import os

# WSL 代理（必须在 import akshare 之前设置）
os.environ.pop("http_proxy",  None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY",  None)
os.environ.pop("HTTPS_PROXY", None)

warnings.filterwarnings("ignore")

# ── akshare 懒加载（避免启动时卡顿）──────────────────────────────

_ak = None

def _akshare():
    global _ak
    if _ak is None:
        import akshare as _mod
        _ak = _mod
    return _ak


def _today_str() -> str:
    """返回今日日期字符串（YYYYMMDD 格式，akshare 涨停接口需要此格式）"""
    from datetime import date
    return date.today().strftime("%Y%m%d")


# ── 工具函数 ───────────────────────────────────────────────────

def _fmt_chg(v):
    try:
        v = float(v)
        if np.isnan(v): return "N/A"
        return f"+{v:.2f}%" if v > 0 else f"{v:.2f}%"
    except:
        return "N/A"


def _fmt_turn(v):
    try:
        v = float(v)
        if np.isnan(v): return "N/A"
        return f"{v:.2f}%"
    except:
        return "N/A"


# ── 核心接口 ───────────────────────────────────────────────────

def find_opportunities(max_staleness_minutes: int = 60) -> dict:
    """
    综合扫描所有类型的机会，发现当前可关注个股。
    返回 dict: {分类名: DataFrame}

    优先级排序（从强到弱）：
      🔴 涨停股池（当日最强）
      🟡 强势股池（近期持续强势）
      🟡 昨日涨停（人气延续）
      🟢 创新高（趋势启动）
      🟢 连续上涨（动量延续）
      🟢 持续放量（资金介入）
      🟢 量价齐升（量价配合）
      🟢 向上突破（技术突破）
      🔵 次新股池（新股溢价）
      ⚪ 炸板股池（炸板关注）
      🔵 跌停股池（恐慌情绪）
    """
    results = {}

    # 1. 涨停股池（东方财富）
    try:
        df = _akshare().stock_zt_pool_em(date=_today_str())
        if df is not None and not df.empty:
            df = _clean_zt(df)
            results["涨停股池"] = df
    except Exception as e:
        results["涨停股池"] = pd.DataFrame()

    # 2. 强势股池（东方财富）
    try:
        df = _akshare().stock_zt_pool_strong_em(date=_today_str())
        if df is not None and not df.empty:
            results["强势股池"] = _clean_strong(df)
    except:
        results["强势股池"] = pd.DataFrame()

    # 3. 昨日涨停（东方财富）
    try:
        df = _akshare().stock_zt_pool_previous_em(date=_today_str())
        if df is not None and not df.empty:
            results["昨日涨停"] = _clean_zt(df)
    except:
        results["昨日涨停"] = pd.DataFrame()

    # 4. 次新股池（东方财富）
    try:
        df = _akshare().stock_zt_pool_sub_new_em(date=_today_str())
        if df is not None and not df.empty:
            results["次新股池"] = _clean_zt(df)
    except:
        results["次新股池"] = pd.DataFrame()

    # 5. 炸板股池（东方财富）
    try:
        df = _akshare().stock_zt_pool_zbgc_em(date=_today_str())
        if df is not None and not df.empty:
            results["炸板股池"] = _clean_strong(df)
    except:
        results["炸板股池"] = pd.DataFrame()

    # 6. 跌停股池（东方财富）
    try:
        df = _akshare().stock_zt_pool_dtgc_em(date=_today_str())
        if df is not None and not df.empty:
            results["跌停股池"] = _clean_dt(df)
    except:
        results["跌停股池"] = pd.DataFrame()

    # ── 同花顺技术选股（按实用性排列）───────────────────────────

    # 7. 量价齐升（最实用：量价配合上涨）
    try:
        df = _akshare().stock_rank_ljqs_ths()
        if df is not None and not df.empty:
            results["量价齐升"] = _clean_tech(df)
    except:
        results["量价齐升"] = pd.DataFrame()

    # 8. 持续放量
    try:
        df = _akshare().stock_rank_cxfl_ths()
        if df is not None and not df.empty:
            results["持续放量"] = _clean_tech(df)
    except:
        results["持续放量"] = pd.DataFrame()

    # 9. 连续上涨
    try:
        df = _akshare().stock_rank_lxsz_ths()
        if df is not None and not df.empty:
            results["连续上涨"] = _clean_tech(df)
    except:
        results["连续上涨"] = pd.DataFrame()

    # 10. 创新高
    try:
        df = _akshare().stock_rank_cxg_ths()
        if df is not None and not df.empty:
            results["创新高"] = _clean_tech(df)
    except:
        results["创新高"] = pd.DataFrame()

    # 11. 向上突破
    try:
        df = _akshare().stock_rank_xstp_ths()
        if df is not None and not df.empty:
            results["向上突破"] = _clean_tech(df)
    except:
        results["向上突破"] = pd.DataFrame()

    # 12. 连续下跌（逆向机会）
    try:
        df = _akshare().stock_rank_lxxd_ths()
        if df is not None and not df.empty:
            results["连续下跌"] = _clean_tech(df)
    except:
        results["连续下跌"] = pd.DataFrame()

    # 13. 创新低（逆向机会）
    try:
        df = _akshare().stock_rank_cxd_ths()
        if df is not None and not df.empty:
            results["创新低"] = _clean_tech(df)
    except:
        results["创新低"] = pd.DataFrame()

    # 14. 持续缩量
    try:
        df = _akshare().stock_rank_cxsl_ths()
        if df is not None and not df.empty:
            results["持续缩量"] = _clean_tech(df)
    except:
        results["持续缩量"] = pd.DataFrame()

    # 15. 量价齐跌（逆向机会）
    try:
        df = _akshare().stock_rank_ljqd_ths()
        if df is not None and not df.empty:
            results["量价齐跌"] = _clean_tech(df)
    except:
        results["量价齐跌"] = pd.DataFrame()

    # 16. 向下突破（逆向参考）
    try:
        df = _akshare().stock_rank_xxtp_ths()
        if df is not None and not df.empty:
            results["向下突破"] = _clean_tech(df)
    except:
        results["向下突破"] = pd.DataFrame()

    # 清理空结果
    return {k: v for k, v in results.items() if v is not None and not v.empty}


# ── 数据清洗 ───────────────────────────────────────────────────

def _clean_zt(df: pd.DataFrame) -> pd.DataFrame:
    """清洗涨停/跌停股池数据（适配东方财富列名）"""
    # 东财返回列名：名称、代码、涨跌幅、连板数、流通市值、总市值、换手率、成交额、所属行业
    # 注意：东财列名是"名称"不是"简称"，"流通市值"/"总市值"不带括号
    keep = []
    for col in df.columns:
        if any(k in col for k in ["代码", "名称", "简称", "涨跌幅", "换手率", "流通", "市值", "连板", "成交额", "所属行业"]):
            keep.append(col)
    if keep:
        df = df[keep].copy()

    # 统一列名（东财用"名称"，同花顺用"简称"；东财"流通市值"不带括号）
    rename = {}
    for col in df.columns:
        if "涨跌幅" in col and "涨跌幅(%)" not in rename:
            rename[col] = "涨跌幅(%)"
        elif "换手率" in col and "换手率(%)" not in rename:
            rename[col] = "换手率(%)"
        elif "流通市值" in col and "流通市值(亿)" not in rename:
            rename[col] = "流通市值(亿)"
        elif "总市值" in col and "总市值(亿)" not in rename:
            rename[col] = "总市值(亿)"
        elif "成交额" in col and "成交额(元)" not in rename:
            rename[col] = "成交额(元)"
        elif "所属行业" in col:
            rename[col] = "所属行业"

    if rename:
        seen = set()
        final_rename = {}
        for col in df.columns:
            target = rename.get(col, col)
            if target not in seen:
                seen.add(target)
                if target != col:
                    final_rename[col] = target
        df = df.rename(columns=final_rename)

    # 统一代码列
    for col in ["代码", "股票代码"]:
        if col in df.columns:
            df = df.rename(columns={col: "代码"})
            break

    # 统一名称列
    for col in ["名称", "简称", "股票简称"]:
        if col in df.columns:
            df = df.rename(columns={col: "名称"})
            break

    # 清理代码格式
    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.zfill(6)

    return df.reset_index(drop=True)


def _clean_strong(df: pd.DataFrame) -> pd.DataFrame:
    """清洗强势股/炸板数据"""
    return _clean_zt(df)


def _clean_dt(df: pd.DataFrame) -> pd.DataFrame:
    """清洗跌停数据"""
    return _clean_zt(df)


def _clean_tech(df: pd.DataFrame) -> pd.DataFrame:
    """清洗同花顺技术选股数据"""
    # 同花顺技术选股字段：序号、股票代码、股票简称、涨跌幅、换手率、最新价、前期高点...
    rename = {}
    for col in df.columns:
        cn = col.lower()
        if "代码" in col and col not in rename:
            rename[col] = "代码"
        elif "简称" in col and col not in rename:
            rename[col] = "名称"
        elif "涨跌幅" in col:
            rename[col] = "涨跌幅(%)"
        elif "换手率" in col:
            rename[col] = "换手率(%)"
        elif "最新价" in col or "收盘" in col or "最新" in col:
            rename[col] = "最新价"
        elif "成交量" in col:
            rename[col] = "成交量"
        elif "成交额" in col:
            rename[col] = "成交额"
        elif "连涨" in col:
            rename[col] = "连涨天数"
        elif "阶段" in col:
            rename[col] = "阶段涨幅"
        elif "量价" in col:
            rename[col] = "量价齐升天数"
        elif "前期" in col and "高点" in col:
            rename[col] = "前期高点"
        elif "前期" in col and "低" in col:
            rename[col] = "前期低点"

    df = df.rename(columns=rename)

    # 统一代码格式
    if "代码" in df.columns:
        df["代码"] = df["代码"].astype(str).str.zfill(6)

    # 去掉序号列
    for col in ["序号"]:
        if col in df.columns:
            df = df.drop(columns=[col])

    return df.reset_index(drop=True)


# ── 便捷接口 ───────────────────────────────────────────────────

def get_hot_stocks(limit: int = 20) -> pd.DataFrame:
    """
    返回当前最热的股票（涨停股 + 强势股 + 量价齐升优先合并）
    """
    all_hot = []
    opp = find_opportunities()

    priority_order = ["涨停股池", "强势股池", "昨日涨停", "次新股池",
                      "量价齐升", "持续放量", "连续上涨", "创新高"]

    seen = set()
    for cat in priority_order:
        if cat in opp and not opp[cat].empty:
            df = opp[cat].copy()
            if "代码" in df.columns and "名称" in df.columns:
                for _, row in df.iterrows():
                    code = str(row.get("代码", ""))
                    if code not in seen:
                        seen.add(code)
                        row_data = row.to_dict()
                        row_data["_来源分类"] = cat
                        all_hot.append(row_data)
                        if len(all_hot) >= limit:
                            break
        if len(all_hot) >= limit:
            break

    if not all_hot:
        return pd.DataFrame()

    result = pd.DataFrame(all_hot)
    if "涨跌幅(%)" in result.columns:
        result = result.sort_values("涨跌幅(%)", ascending=False)
    return result.reset_index(drop=True)


def analyze_limit_up() -> dict:
    """
    专门分析当日涨停情况：涨停数量、连板情况、热门涨停板块分布
    """
    opp = find_opportunities()
    zt_df = opp.get("涨停股池", pd.DataFrame())

    if zt_df.empty:
        return {
            "zt_count": 0,
            "lianban_stocks": [],
            "hot_boards": [],
            "zbgc_count": 0,
            "dt_count": 0,
            "summary": "今日涨停数据暂不可用",
        }

    # 找连板股（字段含"连板"或"连续"）
    lianban_cols = [c for c in zt_df.columns if "连板" in c or "连续" in c]
    lianban_stocks = []
    if lianban_cols:
        for _, row in zt_df.iterrows():
            for col in lianban_cols:
                val = row.get(col)
                try:
                    if float(val) >= 2:
                        lianban_stocks.append({
                            "代码": row.get("代码", ""),
                            "名称": row.get("名称", ""),
                            "连板数": val,
                            "涨跌幅": row.get("涨跌幅(%)", row.get("涨跌幅", "N/A")),
                        })
                except:
                    pass

    # 炸板统计
    zbgc_count = 0
    dt_count = 0
    if "炸板股池" in opp and not opp["炸板股池"].empty:
        zbgc_count = len(opp["炸板股池"])
    if "跌停股池" in opp and not opp["跌停股池"].empty:
        dt_count = len(opp["跌停股池"])

    return {
        "zt_count": len(zt_df),
        "lianban_stocks": lianban_stocks,
        "zbgc_count": zbgc_count,
        "dt_count": dt_count,
        "zt_df": zt_df,
        "summary": f"涨停 {len(zt_df)} 只 | 炸板 {zbgc_count} | 跌停 {dt_count} | 连板 {len(lianban_stocks)}"
    }


def summarize_opportunities(opp: dict = None) -> str:
    """
    生成机会摘要文本（用于报告）
    """
    if opp is None:
        opp = find_opportunities()

    lines = []
    priority_order = [
        ("🔴 涨停股池", "涨停股池"),
        ("🟡 强势股池", "强势股池"),
        ("🟡 昨日涨停", "昨日涨停"),
        ("🟢 量价齐升", "量价齐升"),
        ("🟢 持续放量", "持续放量"),
        ("🟢 连续上涨", "连续上涨"),
        ("🟢 创新高", "创新高"),
        ("🟢 向上突破", "向上突破"),
        ("🔵 次新股池", "次新股池"),
        ("⚪ 炸板股池", "炸板股池"),
        ("🔵 跌停股池", "跌停股池"),
    ]

    for emoji, key in priority_order:
        if key in opp and not opp[key].empty:
            n = len(opp[key])
            # 取前3只代表性股票
            top3 = []
            df_slice = opp[key]

            # 安全地获取涨跌幅列（可能存在重复列名）
            chg_col = None
            for cn in ["涨跌幅(%)", "涨跌幅", "涨跌幅\n(%)"]:
                if cn in df_slice.columns:
                    chg_col = cn
                    break

            if chg_col is not None:
                try:
                    numeric_col = pd.to_numeric(df_slice[chg_col], errors="coerce")
                    top3_idx = numeric_col.nlargest(3).dropna().index
                    top3_rows = df_slice.loc[top3_idx]
                except Exception:
                    top3_rows = df_slice.head(3)
            else:
                top3_rows = df_slice.head(3)

            for _, r in top3_rows.iterrows():
                name = r.get("名称") or r.get("股票简称") or "?"
                code = r.get("代码") or "?"
                chg_val = r[chg_col] if chg_col else None
                # 处理：chg_val可能是Series/标量/NaN
                try:
                    if chg_val is not None and not (isinstance(chg_val, float) and pd.isna(chg_val)):
                        chg_fmt = _fmt_chg(chg_val)
                        top3.append(f"{name}({code}){chg_fmt}")
                    else:
                        top3.append(f"{name}({code})")
                except Exception:
                    top3.append(f"{name}({code})")

            lines.append(f"{emoji} **{key}** ({n}只): {', '.join(top3)}")

    if not lines:
        return "⚠️ 暂未发现明显交易机会"

    return "\n".join(lines)
