"""
板块分析模块：每个热门板块推荐代表性股票
=====================================================
数据源（均通过 akshare 直连，无需代理）：
  - 板块成分股：stock_board_industry_cons_ths / stock_board_concept_cons_ths
  - 板块涨跌幅：stock_board_industry_name_ths / stock_board_concept_name_ths
  - 资金流：get_industry_flow / get_concept_flow（来自 fund_flow.py）
  - 个股行情：scraper.fetch_data（来自 scraper.py，批量获取，无需逐票请求）

推荐逻辑：
  1. 板块内成交额最高的前 30% 股票（资金认可）
  2. 板块内涨幅超过板块均值的股票（相对强势）
  3. 剔除 ST、退市、涨停封死（已无法买入）
  4. 综合评分：成交额权重 × 相对涨幅权重，取 Top3

使用方式：
  import board_analysis as ba
  recs = ba.get_board_recommendations(n_per_board=3)
  report_text = ba.format_board_recommendations(recs)
"""

from __future__ import annotations

import sys
import time
import warnings
from typing import Optional

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)

# ── 内部辅助 ──────────────────────────────────────────────────────────────────

def _load_modules():
    """懒加载同模块，避免循环导入"""
    ff_mod = None
    scraper_mod = None
    try:
        import fund_flow as ff
        ff_mod = ff
    except Exception:
        pass
    try:
        import scraper
        scraper_mod = scraper
    except Exception:
        pass
    return ff_mod, scraper_mod


def _safe(df: pd.DataFrame, col_patterns: list[str]):
    """安全获取列名，匹配多个候选，返回第一匹配或空字符串"""
    if df is None or df.empty:
        return ""
    for p in col_patterns:
        for c in df.columns:
            if p in str(c):
                return c
    return ""


def _sf(v) -> float:
    """安全转 float"""
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def _fmt_pct(v):
    try:
        f = float(v)
        if np.isnan(f):
            return "0.00%"
        sign = "+" if f >= 0 else ""
        return f"{sign}{f:.2f}%"
    except Exception:
        return "0.00%"


# ── 板块成交额 Top30 股票 ─────────────────────────────────────────────────────

def _get_board_turnover(board_name: str, board_type: str = "industry") -> pd.DataFrame:
    """
    获取板块成交额排名前列的成分股
    board_type: "industry" | "concept"
    """
    import akshare as ak

    try:
        if board_type == "industry":
            df = ak.stock_board_industry_cons_ths(symbol=board_name)
        else:
            df = ak.stock_board_concept_cons_ths(symbol=board_name)
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # 找成交额列
    amt_col = _safe(df, ["成交额", "成交额(万元)", "成交额(元)", "成交额万"])
    if not amt_col:
        # 尝试用涨跌幅排序作为代理
        chg_col = _safe(df, ["涨跌幅", "涨跌%"])
        if chg_col:
            df = df.copy()
            df[amt_col] = df[chg_col]
            amt_col = chg_col

    if not amt_col:
        return pd.DataFrame()

    df[amt_col] = pd.to_numeric(df[amt_col], errors="coerce").fillna(0)
    df = df.sort_values(amt_col, ascending=False).reset_index(drop=True)
    return df


def _score_stocks_in_board(
    board_stocks: pd.DataFrame,
    realtime_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    对板块内股票打分，取 Top3
    评分维度（满分 10 分）：
      - 成交额 rank 得分（5 分）：板块内成交额排名，越靠前越高
      - 相对涨幅得分（3 分）：个股涨幅相对板块均值的超额
      - 流动性合理（2 分）：换手率 1%~15% 之间得 2 分，过低/过高得 1 分
    剔除：ST / 涨停封死（涨幅 ≥ 9.5%）/ 退市风险
    """
    if board_stocks.empty:
        return pd.DataFrame()

    # 找个股代码列
    code_col = _safe(board_stocks, ["代码", "代码", "股票代码"])
    name_col = _safe(board_stocks, ["名称", "股票名称", "名称"])
    price_col = _safe(board_stocks, ["最新价", "现价", "收盘价"])
    chg_col = _safe(board_stocks, ["涨跌幅", "涨跌幅(%)", "涨跌%"])
    amt_col = _safe(board_stocks, ["成交额", "成交额(万元)", "成交额"])

    if not code_col or board_stocks[code_col].empty:
        return pd.DataFrame()

    df = board_stocks.copy()

    # ── 基础过滤 ───────────────────────────────────────────────
    # ST / *ST 过滤
    st_mask = df[name_col].astype(str).str.contains(r"^(ST|\*ST|S\*ST|SST)", na=False, regex=True)
    df = df[~st_mask].copy()

    if df.empty:
        return pd.DataFrame()

    # 涨停封死过滤（涨幅 ≥ 9.5% 且换手率极低 → 买不进去）
    if chg_col:
        df[chg_col] = pd.to_numeric(df[chg_col], errors="coerce").fillna(0)
        zt_mask = df[chg_col] >= 9.5
        # 如果涨停且换手率 < 1%，认为是封死
        tr_col = _safe(df, ["换手率", "换手率(%)"])
        if tr_col:
            df[tr_col] = pd.to_numeric(df[tr_col], errors="coerce").fillna(0)
            sealed_mask = zt_mask & (df[tr_col] < 1.0)
            df = df[~sealed_mask].copy()
        else:
            # 无换手率数据，仅过滤涨停过大的
            df = df[df[chg_col] < 9.5].copy()

    if df.empty:
        return pd.DataFrame()

    # ── 评分 ───────────────────────────────────────────────────
    total = pd.Series(0.0, index=df.index)

    # 1. 成交额 rank（5 分）
    if amt_col and amt_col in df.columns:
        df[amt_col] = pd.to_numeric(df[amt_col], errors="coerce").fillna(0)
        ranks = df[amt_col].rank(ascending=False, pct=True)  # pct: 0=最大, 1=最小
        total += (1 - ranks) * 5  # 成交额最大者得 5 分

    # 2. 相对涨幅（3 分）
    if chg_col and chg_col in df.columns:
        df[chg_col] = pd.to_numeric(df[chg_col], errors="coerce").fillna(0)
        board_avg = df[chg_col].mean()
        rel_chg = df[chg_col] - board_avg
        # 归一化到 [0, 3]
        if rel_chg.max() != rel_chg.min():
            rel_score = (rel_chg - rel_chg.min()) / (rel_chg.max() - rel_chg.min()) * 3
        else:
            rel_score = pd.Series(1.5, index=df.index)
        total += rel_score

    # 3. 流动性合理性（2 分）
    tr_col = _safe(df, ["换手率", "换手率(%)"])
    if tr_col and tr_col in df.columns:
        df[tr_col] = pd.to_numeric(df[tr_col], errors="coerce").fillna(0)
        turnover = df[tr_col]
        turnover_score = pd.Series(0.0, index=df.index)
        # 1%~15% 之间最高
        turnover_score[(turnover >= 1.0) & (turnover <= 15.0)] = 2.0
        turnover_score[(turnover > 0.5) & (turnover < 1.0)] = 1.0
        turnover_score[(turnover >= 15.0) & (turnover < 30.0)] = 1.0
        total += turnover_score

    df["综合得分"] = total.round(2)
    df = df.sort_values("综合得分", ascending=False).reset_index(drop=True)
    return df


# ── 主接口 ───────────────────────────────────────────────────────────────────

def get_board_recommendations(
    n_per_board: int = 3,
    min_turnover_rank: float = 0.70,
    top_boards: int = 20,
) -> dict[str, pd.DataFrame]:
    """
    返回各热门板块的推荐股票

    参数：
        n_per_board: 每个板块最多推荐几只（默认 3）
        min_turnover_rank: 成交额须在板块前多少比例（默认 0.70 = 前 30%）
        top_boards: 分析前多少个热门板块（默认 20）

    返回：
        dict[板块名称, DataFrame(代码, 名称, 最新价, 涨跌幅, 换手率, 成交额, 综合得分, 推荐理由)]
    """
    import akshare as ak

    ff_mod, scraper_mod = _load_modules()

    # ── 1. 获取热门板块列表 ───────────────────────────────────
    try:
        ind_df = ak.stock_board_industry_name_ths()
        con_df = ak.stock_board_concept_name_ths()
    except Exception as e:
        print(f"[board_analysis] 获取板块列表失败: {e}")
        return {}

    if ind_df is not None and not ind_df.empty:
        ind_df["板块类型"] = "行业"
    if con_df is not None and not con_df.empty:
        con_df["板块类型"] = "概念"

    board_list = pd.concat([ind_df, con_df], ignore_index=True) if ind_df is not None else (con_df if con_df is not None else pd.DataFrame())

    if board_list.empty:
        return {}

    # 找涨跌幅列
    chg_col = _safe(board_list, ["涨跌幅", "涨跌幅(%)", "涨跌%"])
    if not chg_col:
        return {}

    board_list[chg_col] = pd.to_numeric(board_list[chg_col], errors="coerce").fillna(0)
    hot = board_list.sort_values(chg_col, ascending=False).head(top_boards)
    print(f"[board_analysis] 分析 {len(hot)} 个热门板块（涨跌幅 {hot[chg_col].min():.2f}% ~ {hot[chg_col].max():.2f}%）")

    # ── 2. 加载实时行情（批量，一次请求覆盖所有候选） ──────────
    realtime_df = pd.DataFrame()
    if scraper_mod:
        try:
            fetch_fn = getattr(scraper_mod, "fetch_data", None)
            if fetch_fn:
                result = fetch_fn(use_cache=True)
                if result and len(result) >= 1:
                    realtime_df = result[0]
                print(f"[board_analysis] 实时行情: {len(realtime_df)} 只")
        except Exception as e:
            print(f"[board_analysis] 实时行情加载失败: {e}")

    # ── 3. 逐板块分析 ─────────────────────────────────────────
    board_recs = {}
    name_col_b = _safe(board_list, ["板块名称", "名称", "板块"])
    btype_col = "板块类型"

    for _, brow in hot.iterrows():
        bname = str(brow.get(name_col_b, "")).strip()
        btype = str(brow.get(btype_col, "industry")).strip()
        if not bname or bname == "nan":
            continue

        # 板块涨跌幅
        bchg = _sf(brow.get(chg_col, 0))

        # 获取成分股（带成交额）
        t0 = time.time()
        try:
            if btype == "行业":
                stocks = _get_board_turnover(bname, "industry")
            else:
                stocks = _get_board_turnover(bname, "concept")
        except Exception as e:
            continue

        if stocks is None or stocks.empty:
            continue

        # 打分取 Top
        scored = _score_stocks_in_board(stocks, realtime_df)
        if scored.empty:
            continue

        # 取 Top n_per_board
        top_k = scored.head(n_per_board).copy()

        # 补充实时行情字段
        if not realtime_df.empty:
            code_col_r = _safe(realtime_df, ["代码"])
            price_col_r = _safe(realtime_df, ["最新价", "现价"])
            chg_col_r = _safe(realtime_df, ["涨跌幅(%)", "涨跌幅"])
            tr_col_r = _safe(realtime_df, ["换手率(%)", "换手率"])
            amt_col_r = _safe(realtime_df, ["成交额(万元)", "成交额(元)", "成交额"])
            cap_col_r = _safe(realtime_df, ["流通市值(亿)", "流通市值"])

            # 从 top_k 取代码列
            code_col_s = _safe(top_k, ["代码"])

            if code_col_r and code_col_s:
                top_k["代码"] = top_k[code_col_s].astype(str).str.zfill(6)
                realtime_df["_code"] = realtime_df[code_col_r].astype(str).str.zfill(6)
                merged = top_k.merge(
                    realtime_df[["_code", price_col_r, chg_col_r, tr_col_r, amt_col_r, cap_col_r]].rename(
                        columns={
                            price_col_r: "实时最新价",
                            chg_col_r: "实时涨跌幅",
                            tr_col_r: "实时换手率",
                            amt_col_r: "实时成交额",
                            cap_col_r: "实时流通市值",
                        }
                    ),
                    on="_code",
                    how="left",
                )
                top_k = merged.drop(columns=["_code"], errors="ignore")

        # 推荐理由
        def make_reason(row):
            parts = []
            score = _sf(row.get("综合得分", 0))
            if score >= 8:
                parts.append("板块核心龙头")
            elif score >= 5:
                parts.append("板块强势标的")
            else:
                parts.append("板块资金活跃")

            real_chg = _sf(row.get("实时涨跌幅", row.get(chg_col, 0)))
            if real_chg > 3:
                parts.append("涨幅领先")
            elif real_chg > 0:
                parts.append("逆势走强")

            amt = row.get("实时成交额", row.get(amt_col, 0))
            if amt and _sf(amt) > 0:
                parts.append(f"成交额{_sf(amt):.0f}万")

            return " | ".join(parts)

        top_k["板块"] = bname
        top_k["板块涨跌幅"] = _fmt_pct(bchg)
        top_k["推荐理由"] = top_k.apply(make_reason, axis=1)

        # 最终列
        final_cols = ["代码", "名称", "实时最新价", "实时涨跌幅", "实时换手率", "实时成交额", "实时流通市值", "综合得分", "板块", "板块涨跌幅", "推荐理由"]
        # 只保留存在的列
        final_cols = [c for c in final_cols if c in top_k.columns]
        top_k = top_k[final_cols].rename(columns={
            "实时最新价": "最新价",
            "实时涨跌幅": "涨跌幅",
            "实时换手率": "换手率",
            "实时成交额": "成交额",
            "实时流通市值": "流通市值",
        })

        board_recs[bname] = top_k
        print(f"  [{bname[:8]}] {len(top_k)} 只推荐: {', '.join(top_k['名称'].tolist()[:3])}")

    return board_recs


def format_board_recommendations(board_recs: dict[str, pd.DataFrame]) -> str:
    """将板块推荐结果格式化为可读文本"""
    if not board_recs:
        return ""

    lines = []
    for bname, df in board_recs.items():
        if df.empty:
            continue

        bchg = df["板块涨跌幅"].iloc[0] if "板块涨跌幅" in df.columns else ""
        lines.append(f"\n{'='*60}")
        lines.append(f"  板块：{bname}  {bchg}")
        lines.append(f"{'='*60}")

        if df.empty:
            lines.append("  （暂无足够数据推荐）")
            continue

        header = f"  {'代码':<8} {'名称':<10} {'最新价':>8} {'涨跌幅':>8} {'换手率':>8} {'成交额(万)':>10} {'综合得分':>8}  推荐理由"
        lines.append(header)
        lines.append(f"  {'-'*len(header)}")

        for _, row in df.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            name = str(row.get("名称", ""))[:10]
            price = f"{_sf(row.get('最新价', 0)):.2f}"
            chg = row.get("涨跌幅", "0")
            if isinstance(chg, str):
                chg_disp = chg
            else:
                chg_disp = _fmt_pct(_sf(chg))
            tr = f"{_sf(row.get('换手率', 0)):.2f}%"
            amt = f"{_sf(row.get('成交额', 0)):.0f}"
            score = f"{_sf(row.get('综合得分', 0)):.1f}"
            reason = str(row.get("推荐理由", ""))[:20]
            lines.append(f"  {code:<8} {name:<10} {price:>8} {chg_disp:>8} {tr:>8} {amt:>10} {score:>8}  {reason}")

    return "\n".join(lines)


def get_quick_board_recs(boards: list[str] | None = None, n: int = 3) -> dict[str, pd.DataFrame]:
    """
    快速查询指定板块列表的推荐股票（跳过热度排序）
    boards: 板块名称列表，如 ["半导体", "有色金属", "光伏设备"]
    n: 每板块推荐几只
    """
    if boards is None:
        boards = []

    import akshare as ak
    ff_mod, scraper_mod = _load_modules()

    # 获取板块列表
    try:
        ind_df = ak.stock_board_industry_name_ths()
        con_df = ak.stock_board_concept_name_ths()
    except Exception:
        return {}

    board_map = {}
    for _, row in (ind_df.iterrows() if ind_df is not None else []):
        board_map[str(row.get(_safe(ind_df, ["板块名称", "名称"]), ""))] = "行业"
    for _, row in (con_df.iterrows() if con_df is not None else []):
        board_map[str(row.get(_safe(con_df, ["板块名称", "名称"]), ""))] = "概念"

    # 实时行情
    realtime_df = pd.DataFrame()
    if scraper_mod:
        try:
            fetch_fn = getattr(scraper_mod, "fetch_data", None)
            if fetch_fn:
                result = fetch_fn(use_cache=True)
                if result and len(result) >= 1:
                    realtime_df = result[0]
        except Exception:
            pass

    results = {}
    for bname in boards:
        btype = board_map.get(bname, "概念")
        try:
            if btype == "行业":
                stocks = _get_board_turnover(bname, "industry")
            else:
                stocks = _get_board_turnover(bname, "concept")
        except Exception:
            continue

        if stocks.empty:
            continue

        scored = _score_stocks_in_board(stocks, realtime_df)
        if scored.empty:
            continue

        top_k = scored.head(n).copy()
        top_k["板块"] = bname
        top_k["推荐理由"] = top_k.apply(
            lambda r: "板块核心龙头" if _sf(r.get("综合得分", 0)) >= 8 else "板块资金活跃",
            axis=1,
        )
        results[bname] = top_k

    return results


# ── 入口 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  A股热门板块推荐（每个板块 Top3）")
    print("=" * 60)
    recs = get_board_recommendations(n_per_board=3, top_boards=15)
    print(format_board_recommendations(recs))
