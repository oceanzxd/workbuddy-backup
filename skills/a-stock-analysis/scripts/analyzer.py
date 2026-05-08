"""
analyzer.py - A股分析引擎 v2
支持三种分析模式:
  - 全市场分析：49个行业逐一分析 + 每行业Top3
  - 板块分析：板块内所有个股 + 板块趋势 + 新闻舆情
  - 个股分析：技术面 + 基本面 + 买卖建议 + 新闻
"""

import pandas as pd
import numpy as np
import requests
import json
import time
from typing import Optional
from datetime import datetime


# ── 工具函数 ────────────────────────────────────────────────

def sf(v, default=np.nan):
    try:
        f = float(v)
        return f if np.isfinite(f) else default
    except:
        return default


def fmt_pct(v, signed=True):
    try:
        v = float(v)
        if np.isnan(v): return "N/A"
        sign = "+" if v > 0 and signed else ""
        return f"{sign}{v:.2f}%"
    except:
        return "N/A"


def fmt_mkt(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return "N/A"
    try:
        v = float(v)
        if v >= 1e4: return f"{v/1e4:.2f}万亿" if v >= 1e8 else f"{v:.2f}亿"
        return f"{v:.2f}亿"
    except:
        return "N/A"


def fmt_amt(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return "N/A"
    try:
        v = float(v)
        if v >= 1e8: return f"{v/1e8:.2f}亿"
        if v >= 1e6: return f"{v/1e6:.2f}万"
        return f"{v:.0f}元"
    except:
        return "N/A"


def rank_percentile(series: pd.Series, value: float) -> str:
    """返回 value 在 series 中的百分位（从强到弱排名）"""
    try:
        total = len(series.dropna())
        if total == 0: return "N/A"
        rank = (series.dropna() > value).sum() + 1
        pct = rank / total * 100
        return f"{rank}/{total} ({pct:.0f}%)"
    except:
        return "N/A"


# ── 1. 全市场分析 ─────────────────────────────────────────────

def analyze_market(realtime_df: pd.DataFrame, board_df: pd.DataFrame,
                   industry_map: dict) -> dict:
    """
    全市场 + 49个行业逐一分析 + 每行业Top3个股
    """
    n_total = len(realtime_df)
    up      = (realtime_df["涨跌幅(%)"] > 0).sum()
    down    = (realtime_df["涨跌幅(%)"] < 0).sum()
    flat    = (realtime_df["涨跌幅(%)"] == 0).sum()
    avg_all = realtime_df["涨跌幅(%)"].mean()
    med_all = realtime_df["涨跌幅(%)"].median()

    zt = realtime_df[realtime_df["涨跌幅(%)"] >= 9.9]
    dt = realtime_df[realtime_df["涨跌幅(%)"] <= -9.9]

    # 市值分布
    large = realtime_df[realtime_df["总市值(亿)"] > 1000]
    mid   = realtime_df[(realtime_df["总市值(亿)"] > 100) & (realtime_df["总市值(亿)"] <= 1000)]
    small = realtime_df[(realtime_df["总市值(亿)"] > 0)   & (realtime_df["总市值(亿)"] <= 100)]

    # 全市场成交额/换手率Top
    amt_top  = realtime_df.dropna(subset=["成交额(元)"]).nlargest(10, "成交额(元)")
    turn_top = realtime_df.dropna(subset=["换手率(%)"]).nlargest(10, "换手率(%)")

    # 涨跌幅分布桶
    bins   = [-100, -9.9, -7, -3, -1, 1, 3, 7, 9.9, 100]
    labels = ["跌停(-10%)", "暴跌(-9~-7%)", "大跌(-7~-3%)", "小跌(-3~-1%)",
              "平盘(-1~1%)", "小涨(1~3%)", "大涨(3~7%)", "暴涨(7~10%)", "涨停(+10%)"]
    tmp = realtime_df.copy()
    tmp["区间"] = pd.cut(tmp["涨跌幅(%)"], bins=bins, labels=labels)
    dist = tmp["区间"].value_counts().sort_index()

    # ── 行业板块逐一分析 ──────────────────────────────────────
    # 新浪节点代码通过动态接口验证确认（2026-04-25实测）
    SINA_BOARD_CODE_MAP = {
        "仪器仪表":"new_yqyb","交通运输":"new_jtys","传媒娱乐":"new_cmyl",
        "供水供气":"new_gsgq","公路桥梁":"new_glql","其它行业":"new_qtxy",
        "农林牧渔":"new_nlmy","农药化肥":"new_nyhf","化工行业":"new_hghy",
        "化纤行业":"new_hqhy","医疗器械":"new_ylqx","印刷包装":"new_ysbz",
        "发电设备":"new_fdsb","商业百货":"new_sybh","塑料制品":"new_slzp",
        "家具行业":"new_jjhy","家电行业":"new_jdhy","建筑建材":"new_jzjc",
        "开发区":"new_kfq","房地产":"new_fdc","摩托车":"new_mtc",
        "有色金属":"new_ysjs","服装鞋类":"new_fzxl","机械行业":"new_jxhy",
        "次新股":"new_stock","水泥行业":"new_snhy","汽车制造":"new_qczz",
        "煤炭行业":"new_mthy","物资外贸":"new_wzwm","环保行业":"new_hbhy",
        "玻璃行业":"new_blhy","生物制药":"new_swzz","电力行业":"new_dlhy",
        "电器行业":"new_dqhy","电子信息":"new_dzxx","电子器件":"new_dzqj",
        "石油行业":"new_syhy","纺织机械":"new_fzjx","纺织行业":"new_fzhy",
        "综合行业":"new_zhhy","船舶制造":"new_cbzz","造纸行业":"new_zzhy",
        "酒店旅游":"new_jdly","酿酒行业":"new_ljhy","金融行业":"new_jrhy",
        "钢铁行业":"new_gthy","陶瓷行业":"new_tchy","飞机制造":"new_fjzz",
        "食品行业":"new_sphy",
    }

    board_results = []

    # ── 模块级缓存：一次获取所有新浪成分股 ─────────────────────
    _SINA_CMP_URL = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    _board_syms_cache: dict[str, list] = {}  # {node: [纯数字代码列表]}

    def _fetch_board_syms(node: str) -> list:
        """拉取新浪板块成分股，模块级缓存，同一节点只调一次 API"""
        if node in _board_syms_cache:
            return _board_syms_cache[node]
        try:
            params = {"page": 1, "num": 500, "sort": "changepercent", "asc": 0,
                      "node": node, "_": int(time.time())}
            r = requests.get(_SINA_CMP_URL, params=params, timeout=10,
                           headers={"User-Agent": "Mozilla/5.0",
                                   "Referer": "https://finance.sina.com.cn"})
            r.encoding = "utf-8"
            data = json.loads(r.text)
            syms = []
            for item in (data if isinstance(data, list) else []):
                sym = str(item.get("symbol", "")).strip()
                syms.append(sym[2:] if sym.startswith(("sh","sz")) else sym)
            _board_syms_cache[node] = syms
            return syms
        except Exception:
            _board_syms_cache[node] = []
            return []

    for board_name in sorted(SINA_BOARD_CODE_MAP.keys()):
        node = SINA_BOARD_CODE_MAP[board_name]

        # 获取 board_df 中的板块元数据
        board_row = board_df[board_df["_node"] == node]
        if board_row.empty:
            board_row = board_df[board_df["板块名称"] == board_name]

        sina_avg_chg = float(board_row["涨跌幅(%)"].values[0]) if not board_row.empty else 0.0
        stock_count  = int(board_row["股票数"].values[0])       if not board_row.empty else 0
        lead_name    = str(board_row["领涨股"].values[0])       if not board_row.empty else "N/A"
        lead_chg_raw = float(board_row["领涨涨幅(%)"].values[0]) if not board_row.empty else 0.0

        # 用缓存的成分股代码获取个股数据
        board_syms = _fetch_board_syms(node)
        if board_syms:
            s_df = realtime_df[realtime_df["代码"].str[2:].isin(board_syms)]
        else:
            s_df = pd.DataFrame()

        if not s_df.empty:
            n = len(s_df)
            up_s   = int((s_df["涨跌幅(%)"] > 0).sum())
            down_s = int((s_df["涨跌幅(%)"] < 0).sum())
            avg_chg_s = round(s_df["涨跌幅(%)"].mean(), 3)
            top3 = s_df.nlargest(3, "涨跌幅(%)")
            top3_amt = s_df.dropna(subset=["成交额(元)"]).nlargest(3, "成交额(元)")
        else:
            n = stock_count
            up_s = down_s = 0
            avg_chg_s = sina_avg_chg
            top3 = pd.DataFrame()
            top3_amt = pd.DataFrame()

        board_results.append({
            "name":       board_name,
            "chg":        avg_chg_s,
            "chg_raw":    sina_avg_chg,
            "n":          n,
            "up":         up_s,
            "down":       down_s,
            "avg_chg":    avg_chg_s,
            "lead_name":  lead_name,
            "lead_chg":   lead_chg_raw,
            "top3": top3[["名称","涨跌幅(%)","最新价","总市值(亿)"]].to_dict("records") if not top3.empty else [],
            "top3_amt": top3_amt[["名称","涨跌幅(%)","成交额(元)"]].to_dict("records") if not top3_amt.empty else [],
        })

    # 按涨跌幅排序（强势板块排前面）
    board_results.sort(key=lambda x: x["avg_chg"], reverse=True)

    # 全市场情绪判断
    sentiment = "偏多" if avg_all > 0.5 else ("偏空" if avg_all < -0.5 else "震荡")

    return {
        "total":       n_total,
        "up":          int(up),
        "down":        int(down),
        "flat":        int(flat),
        "avg_all":     round(avg_all, 3),
        "med_all":     round(med_all, 3),
        "sentiment":   sentiment,
        "zt_count":    len(zt),
        "dt_count":    len(dt),
        "zt_stocks":   zt[["名称", "涨跌幅(%)", "最新价", "成交额(元)"]].head(20).to_dict("records"),
        "dt_stocks":   dt[["名称", "涨跌幅(%)", "最新价", "成交额(元)"]].head(20).to_dict("records"),
        "amt_top":     amt_top[["名称", "涨跌幅(%)", "最新价", "成交额(元)", "总市值(亿)"]].to_dict("records"),
        "turn_top":    turn_top[["名称", "涨跌幅(%)", "换手率(%)", "最新价"]].to_dict("records"),
        "mkt_dist": {
            "large":    (len(large), round(large["涨跌幅(%)"].mean(), 2) if not large.empty else 0),
            "mid":      (len(mid),   round(mid["涨跌幅(%)"].mean(), 2)   if not mid.empty   else 0),
            "small":    (len(small), round(small["涨跌幅(%)"].mean(), 2) if not small.empty else 0),
        },
        "pct_dist":    dist.to_dict(),
        "boards":      board_results,
        # 强势/弱势板块 Top10
        "top_boards":  board_results[:10],
        "bot_boards":   board_results[-10:],
    }


# ── 2. 板块分析 ──────────────────────────────────────────────

def analyze_sector(board_name: str, sector_df: pd.DataFrame,
                   board_df: pd.DataFrame, industry_map: dict,
                   news: list = None) -> dict:
    """
    板块深度分析
    news: SearXNG 返回的新闻列表 [{title, snippet, url}, ...]
    """
    board_row = board_df[board_df["板块名称"] == board_name]
    if board_row.empty:
        board_row = board_df[board_df["_node"].str.contains(
            board_name[:2], na=False, case=False)]

    board_info = board_row.iloc[0].to_dict() if not board_row.empty else {}

    n      = len(sector_df)
    up     = int((sector_df["涨跌幅(%)"] > 0).sum())
    down   = int((sector_df["涨跌幅(%)"] < 0).sum())
    avg    = round(sector_df["涨跌幅(%)"].mean(), 3)
    med    = round(sector_df["涨跌幅(%)"].median(), 3)
    std    = round(sector_df["涨跌幅(%)"].std(), 3) if n > 1 else 0

    gainers = sector_df.nlargest(15, "涨跌幅(%)")
    losers  = sector_df.nsmallest(15, "涨跌幅(%)")
    amt_top = sector_df.dropna(subset=["成交额(元)"]).nlargest(10, "成交额(元)")
    turn_top= sector_df.dropna(subset=["换手率(%)"]).nlargest(10, "换手率(%)")
    zt      = sector_df[sector_df["涨跌幅(%)"] >= 9.9]
    dt      = sector_df[sector_df["涨跌幅(%)"] <= -9.9]

    with_mkt = sector_df.dropna(subset=["总市值(亿)"])
    avg_mkt  = round(with_mkt["总市值(亿)"].mean(), 2) if not with_mkt.empty else 0
    tot_mkt  = round(with_mkt["总市值(亿)"].sum(), 2)  if not with_mkt.empty else 0

    pe_df  = sector_df.dropna(subset=["市盈率TTM"])
    avg_pe = round(pe_df["市盈率TTM"].mean(), 2) if not pe_df.empty else None

    # 板块内市值Top5（龙头），先转数值类型
    mkt_num = pd.to_numeric(sector_df["总市值(亿)"], errors="coerce")
    mkt_top5 = sector_df.loc[mkt_num.nlargest(5).index]

    # 相对全市场超额收益
    mkt_avg = sector_df["涨跌幅(%)"].mean()
    # （板块均值 - 市场均值）的方向已在 avg 中体现

    # 板块情绪
    sentiment = "强势领涨" if avg > 3 else ("弱势领跌" if avg < -3 else (
        "偏强" if avg > 0.5 else ("偏弱" if avg < -0.5 else "震荡")))

    return {
        "board_name":    board_name,
        "board_info":    board_info,
        "total_stocks":  n,
        "up":            up,
        "down":          down,
        "avg_chg":       avg,
        "median_chg":    med,
        "std_chg":       std,
        "sentiment":     sentiment,
        "zt_count":      len(zt),
        "dt_count":      len(dt),
        "avg_mkt":       avg_mkt,
        "total_mkt":     tot_mkt,
        "avg_pe":        avg_pe,
        "gainers":       gainers[["名称","涨跌幅(%)","最新价","换手率(%)","总市值(亿)"]].to_dict("records"),
        "losers":        losers[["名称","涨跌幅(%)","最新价","换手率(%)","总市值(亿)"]].to_dict("records"),
        "amt_top":       amt_top[["名称","涨跌幅(%)","成交额(元)","总市值(亿)"]].to_dict("records"),
        "turn_top":      turn_top[["名称","涨跌幅(%)","换手率(%)","最新价"]].to_dict("records"),
        "mkt_leaders":   mkt_top5[["名称","涨跌幅(%)","最新价","总市值(亿)"]].to_dict("records"),
        "news":          news or [],
        "board_star_stocks": [],  # 板块内机会票，由 main.py 填充
    }


# ── 3. 个股分析 ──────────────────────────────────────────────

def analyze_stock(symbol: str, name: str,
                  realtime_df: pd.DataFrame,
                  kline_data: list = None,
                  stock_industry_map: dict = None,
                  board_df: pd.DataFrame = None,
                  news: list = None) -> dict:
    """
    个股深度分析：行情 + 技术面评分 + 基本面 + 买卖建议 + 新闻
    news: SearXNG 返回的新闻列表
    """
    row = realtime_df[realtime_df["代码"] == symbol]
    if row.empty:
        row = realtime_df[realtime_df["名称"] == name]
    if row.empty:
        return None

    row = row.iloc[0]

    price     = float(row["最新价"])
    yest_c    = float(row["昨收"])
    open_p    = float(row["今开"])
    high_p    = float(row["今高"])
    low_p     = float(row["今低"])
    chg_pct   = float(row["涨跌幅(%)"])
    chg_amt   = float(row["涨跌额"])
    volume    = row["成交量(手)"]
    amount    = row["成交额(元)"]
    turnover  = row["换手率(%)"]
    pe        = row["市盈率TTM"]
    mkt_cap   = row["总市值(亿)"]
    float_cap = row["流通市值(亿)"]

    # ── 行业信息 ─────────────────────────────────────────────
    industry = None
    if stock_industry_map:
        industry = stock_industry_map.get(symbol) or stock_industry_map.get(symbol[2:])

    ind_stocks = realtime_df[realtime_df["代码"].isin(
        [k for k, v in (stock_industry_map or {}).items() if v == industry]
    )]
    ind_avg = ind_stocks["涨跌幅(%)"].mean() if not ind_stocks.empty else None
    if not ind_stocks.empty:
        ind_rank = (ind_stocks["涨跌幅(%)"] > chg_pct).sum() + 1
        ind_total = len(ind_stocks)
    else:
        ind_rank = ind_total = None

    # ── 技术面分析（基于K线，真实RSI/MACD）────────────────────
    tech_score = 0      # -10 ~ +10
    tech_signals = []
    trend = "震荡"
    support = None
    resistance = None

    if kline_data and len(kline_data) >= 3:
        closes  = [float(k["前复权收盘"]) for k in kline_data]
        opens   = [float(k["开盘"]) for k in kline_data]
        highs   = [float(k["最高"]) for k in kline_data]
        lows    = [float(k["最低"]) for k in kline_data]
        volumes = [float(k["成交量"]) for k in kline_data]

        # ── 均线 ──
        ma5  = sum(closes[-5:])  / min(5, len(closes))
        ma10 = sum(closes[-10:]) / min(10, len(closes)) if len(closes) >= 10 else ma5
        ma20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 20 else ma5
        ma60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 60 else ma5
        cur_close = closes[-1]

        # ── RSI(14) ──
        def calc_rsi(series, period=14):
            if len(series) < period + 1:
                return None
            gains, losses = [], []
            for i in range(1, len(series)):
                d = series[i] - series[i-1]
                gains.append(max(d, 0))
                losses.append(max(-d, 0))
            if len(gains) < period:
                return None
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0:
                return 100
            rs = avg_gain / avg_loss
            return round(100 - 100 / (1 + rs), 2)

        rsi = calc_rsi(closes, 14)

        # ── MACD(12,26,9) ──
        def ema(series, period):
            k = 2 / (period + 1)
            ema_val = series[0]
            for v in series[1:]:
                ema_val = v * k + ema_val * (1 - k)
            return ema_val

        if len(closes) >= 26:
            ema12 = ema(closes, 12)
            ema26 = ema(closes, 26)
            dif = round(ema12 - ema26, 4)
            # DEA = EMA(DIF, 9)
            # 简化：用 DIF 的9日均值近似 DEA
            dif_series = []
            for i in range(26, len(closes)):
                e12_i = ema(closes[:i+1], 12)
                e26_i = ema(closes[:i+1], 26)
                dif_series.append(e12_i - e26_i)
            dea = round(ema(dif_series, 9), 4) if len(dif_series) >= 9 else dif
            macd_hist = round((dif - dea) * 2, 4)
        else:
            dif, dea, macd_hist = None, None, None

        # ── 布林带(20,2σ) ──
        if len(closes) >= 20:
            import statistics
            recent20 = closes[-20:]
            mid = statistics.mean(recent20)
            std = statistics.stdev(recent20)
            bb_upper = round(mid + 2 * std, 2)
            bb_lower = round(mid - 2 * std, 2)
            bb_pos = round((cur_close - bb_lower) / (bb_upper - bb_lower) * 100, 1) if bb_upper != bb_lower else 50
        else:
            bb_upper = bb_lower = bb_pos = None

        # ── 评分 ──
        # 均线
        if ma5 > ma10 > ma20:
            tech_score += 3
            tech_signals.append(f"均线多头排列（MA5={ma5:.2f}>MA10={ma10:.2f}>MA20={ma20:.2f}）")
        elif ma5 < ma10 < ma20:
            tech_score -= 3
            tech_signals.append(f"均线空头排列（MA5={ma5:.2f}<MA10={ma10:.2f}<MA20={ma20:.2f}）")

        if cur_close > ma5 > ma10:
            trend = "上升趋势"
            tech_score += 2
            tech_signals.append(f"价格({cur_close:.2f})站上短期均线")
        elif cur_close < ma5 < ma10:
            trend = "下降趋势"
            tech_score -= 2
            tech_signals.append(f"价格({cur_close:.2f})跌破短期均线")

        # RSI
        if rsi is not None:
            if rsi > 70:
                tech_score -= 2
                tech_signals.append(f"RSI(14)={rsi}，超买区，有回调压力")
            elif rsi < 30:
                tech_score += 2
                tech_signals.append(f"RSI(14)={rsi}，超卖区，可能反弹")
            elif rsi > 50:
                tech_score += 1
                tech_signals.append(f"RSI(14)={rsi}，多头区域")

        # MACD
        if dif is not None and dea is not None:
            if dif > dea and macd_hist > 0:
                tech_score += 2
                tech_signals.append(f"MACD金叉（DIF={dif:.3f}>DEA={dea:.3f}，红柱={macd_hist:.3f}）")
            elif dif < dea and macd_hist < 0:
                tech_score -= 2
                tech_signals.append(f"MACD死叉（DIF={dif:.3f}<DEA={dea:.3f}，绿柱={macd_hist:.3f}）")
            else:
                tech_signals.append(f"MACD（DIF={dif:.3f}，DEA={dea:.3f}，柱={macd_hist:.3f}）")

        # 成交量
        avg_vol = sum(volumes) / len(volumes)
        if volumes[-1] > avg_vol * 1.5:
            if chg_pct > 0:
                tech_score += 1
                tech_signals.append(f"放量上涨（量比={volumes[-1]/avg_vol:.1f}x）")
            else:
                tech_score -= 1
                tech_signals.append(f"放量下跌（量比={volumes[-1]/avg_vol:.1f}x），抛压较大")
        elif volumes[-1] < avg_vol * 0.5 and abs(chg_pct) > 2:
            tech_signals.append("缩量异动，注意方向确认")

        # 支撑/压力
        if bb_lower:
            support = bb_lower
            resistance = bb_upper
        else:
            support = round(min(lows[-5:]), 2)
            resistance = round(max(highs[-5:]), 2)
        # 涨跌停判断
        if chg_pct >= 9.9:
            tech_score += 2
            tech_signals.append("涨停，动能强劲")
        elif chg_pct <= -9.9:
            tech_score -= 2
            tech_signals.append("跌停，动能极弱")

        # 支撑/压力
        support    = round(min(lows), 2)
        resistance = round(max(highs), 2)
    else:
        # 无K线时用当日价格
        support    = round(low_p, 2)
        resistance  = round(high_p, 2)

    # ── 基本面 ────────────────────────────────────────────────
    fundamental_signals = []
    fund_score = 0  # -5 ~ +5

    if pe is not None and not (isinstance(pe, float) and np.isnan(pe)):
        pe_v = float(pe)
        if 0 < pe_v < 15:
            fund_score += 2
            fundamental_signals.append(f"PE={pe_v:.1f}，估值偏低")
        elif 15 <= pe_v <= 30:
            fundamental_signals.append(f"PE={pe_v:.1f}，估值合理")
        elif pe_v > 60:
            fund_score -= 1
            fundamental_signals.append(f"PE={pe_v:.1f}，估值偏高")
        elif pe_v < 0:
            fundamental_signals.append("PE为负，亏损状态")
    else:
        fundamental_signals.append("PE数据暂无（新股/亏损股）")

    # 市值区间
    if mkt_cap and not (isinstance(mkt_cap, float) and np.isnan(mkt_cap)):
        mc = float(mkt_cap)
        if mc > 1000:
            fundamental_signals.append(f"超大盘（{mc:.0f}亿），稳健但弹性低")
        elif mc > 100:
            fundamental_signals.append(f"中大盘（{mc:.0f}亿），流动性好")
        else:
            fundamental_signals.append(f"小盘（{mc:.0f}亿），弹性大但风险高")
            fund_score -= 1

    # 换手率
    if turnover and not (isinstance(turnover, float) and np.isnan(turnover)):
        tr = float(turnover)
        if tr > 15:
            fundamental_signals.append(f"换手率{tr:.1f}%，交投极度活跃")
        elif tr > 5:
            fundamental_signals.append(f"换手率{tr:.1f}%，交投活跃")
        elif tr > 1:
            fundamental_signals.append(f"换手率{tr:.1f}%，交投一般")
        else:
            fundamental_signals.append(f"换手率{tr:.1f}%，交投冷清")

    # ── 综合评分 ───────────────────────────────────────────────
    # tech_score: -10~+10, fund_score: -5~+5, 归一化到 0~100
    total_score = int((tech_score + 10) / 20 * 50 + (fund_score + 5) / 10 * 30)
    momentum_bonus = 0
    if chg_pct > 5:  momentum_bonus += 10
    elif chg_pct > 2: momentum_bonus += 5
    elif chg_pct < -5: momentum_bonus -= 10
    elif chg_pct < -2: momentum_bonus -= 5
    total_score = max(0, min(100, total_score + momentum_bonus))

    # ── 买卖建议 ───────────────────────────────────────────────
    if total_score >= 75:
        action = "强烈推荐买入"
        action_detail = "技术面强势，基本面良好，短期动能充沛"
    elif total_score >= 60:
        action = "建议买入"
        action_detail = "偏多信号，可考虑逢低布局"
    elif total_score >= 45:
        action = "持券观望"
        action_detail = "方向不明，建议等待更明确信号"
    elif total_score >= 30:
        action = "建议减仓"
        action_detail = "偏空信号，建议控制仓位"
    else:
        action = "建议回避"
        action_detail = "空头趋势明显，注意风险"

    # 风险提示
    risk_level = "高" if abs(chg_pct) > 7 or (turnover and turnover > 15) else (
        "中" if abs(chg_pct) > 3 else "低")

    return {
        "symbol":         symbol,
        "name":           name,
        "industry":       industry,
        "price":          price,
        "yest_close":     yest_c,
        "open":            open_p,
        "high":            high_p,
        "low":             low_p,
        "chg_pct":         round(chg_pct, 2),
        "chg_amt":         round(chg_amt, 3),
        "volume":          volume,
        "amount":          amount,
        "turnover":        turnover,
        "pe":              pe,
        "mkt_cap":         mkt_cap,
        "float_cap":       float_cap,
        "time":            row.get("时间") or row.get("time") or "N/A",
        # 行业对比
        "industry_avg_chg": round(ind_avg, 2) if ind_avg else None,
        "industry_rank":    f"{ind_rank}/{ind_total}" if ind_rank else None,
        "above_industry":   round(chg_pct - ind_avg, 2) if ind_avg else None,
        # 技术面
        "tech_score":       tech_score,
        "tech_signals":     tech_signals,
        "trend":            trend,
        "support":          support,
        "resistance":       resistance,
        # 基本面
        "fund_score":       fund_score,
        "fundamental_signals": fundamental_signals,
        # 综合
        "total_score":      total_score,
        "action":            action,
        "action_detail":     action_detail,
        "risk_level":        risk_level,
        "kline":             kline_data or [],
        "news":              news or [],
    }


# ── 4. 多股深度对比 ────────────────────────────────────────

def _calc_tech_for_compare(kline_data: list) -> dict:
    """计算单只股票技术指标，用于多股对比
    kline_data: list of {日期, 前复权收盘, 开盘, 最高, 最低, 成交量, 换手率(%)}
    返回: dict with all indicators
    """
    import numpy as np
    if not kline_data or len(kline_data) < 3:
        return {}

    closes  = [float(k["前复权收盘"]) for k in kline_data]
    volumes = [float(k["成交量"]) for k in kline_data]
    highs   = [float(k["最高"]) for k in kline_data]
    lows    = [float(k["最低"]) for k in kline_data]

    # MA
    ma5  = sum(closes[-5:])  / min(5, len(closes))
    ma10 = sum(closes[-10:]) / min(10, len(closes)) if len(closes) >= 10 else ma5
    ma20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 20 else ma5
    ma60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 60 else ma5
    cur_close = closes[-1]
    prev_close = closes[-2] if len(closes) >= 2 else cur_close

    # RSI(14)
    def calc_rsi(s, period=14):
        if len(s) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(s)):
            d = s[i] - s[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        return round(100 - 100 / (1 + avg_gain / avg_loss), 1)

    # EMA
    def ema_func(s, period):
        k = 2 / (period + 1)
        ema_val = s[0]
        for v in s[1:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    # MACD
    dif = dea = macd_hist = None
    if len(closes) >= 26:
        e12 = ema_func(closes, 12)
        e26 = ema_func(closes, 26)
        dif = round(e12 - e26, 4)
        # DEA: 近似用 DIF 的 EMA(9)
        dif_series = []
        for i in range(26, len(closes)):
            e12_i = ema_func(closes[:i+1], 12)
            e26_i = ema_func(closes[:i+1], 26)
            dif_series.append(e12_i - e26_i)
        dea = round(ema_func(dif_series, 9), 4) if len(dif_series) >= 9 else dif
        macd_hist = round((dif - dea) * 2, 4)

    # 布林带
    bb_u = bb_m = bb_l = bb_pct = None
    if len(closes) >= 20:
        import statistics
        r20 = closes[-20:]
        m_ = statistics.mean(r20)
        s_ = statistics.stdev(r20)
        bb_m = round(m_, 2)
        bb_u = round(m_ + 2 * s_, 2)
        bb_l = round(m_ - 2 * s_, 2)
        if bb_u != bb_l:
            bb_pct = round((cur_close - bb_l) / (bb_u - bb_l) * 100, 1)

    # KDJ
    k_, d_, j_ = None, None, None
    if len(closes) >= 14:
        low14  = min(lows[-14:])
        high14 = max(highs[-14:])
        if high14 != low14:
            rsv = 100 * (cur_close - low14) / (high14 - low14)
            k_ = 50 if np.isnan(rsv) else round(rsv, 1)
            d_ = round(k_ * 0.9 + 50 * 0.1, 1)
            j_ = round(3 * k_ - 2 * d_, 1)

    # 动量
    chg_today = round((cur_close / prev_close - 1) * 100, 2)
    chg5  = round((cur_close / closes[-6]  - 1) * 100, 2) if len(closes) >= 6  else 0
    chg10 = round((cur_close / closes[-11] - 1) * 100, 2) if len(closes) >= 11 else chg5
    chg20 = round((cur_close / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else chg10

    # 量比
    vol5  = sum(volumes[-5:])  / min(5, len(volumes))
    vol20 = sum(volumes[-20:]) / min(20, len(volumes))
    vol_ratio = round(vol5 / vol20, 2) if vol20 > 0 else 1.0

    # 综合评分
    score = 0
    tags = []
    if ma5 > ma10 > ma20:
        score += 3; tags.append("均线多头")
    elif ma5 > ma10:
        score += 1; tags.append("短期向上")
    if 40 <= (calc_rsi(closes, 14) or 50) <= 65:
        score += 2; tags.append("RSI健康")
    elif (calc_rsi(closes, 14) or 50) > 70:
        score -= 1; tags.append("RSI超买")
    elif (calc_rsi(closes, 14) or 50) < 30:
        score += 1; tags.append("RSI超卖")
    if dif is not None and dea is not None:
        if dif > dea and macd_hist > 0:
            score += 2; tags.append("MACD金叉")
        elif dif < dea and macd_hist < 0:
            score -= 1; tags.append("MACD死叉")
    if vol_ratio > 1.2:
        score += 1; tags.append("量能放大")
    if 30 <= (bb_pct or 50) <= 80:
        score += 1; tags.append("布林安全")
    if chg5 > 5:
        score += 1; tags.append("短期强势")

    return {
        "最新价": cur_close,
        "今日涨跌": chg_today,
        "MA5": round(ma5, 2), "MA10": round(ma10, 2),
        "MA20": round(ma20, 2), "MA60": round(ma60, 2),
        "RSI": calc_rsi(closes, 14),
        "MACD": dif, "DEA": dea, "MACD柱": macd_hist,
        "KDJ_K": k_, "KDJ_D": d_, "KDJ_J": j_,
        "BOLL_UPPER": bb_u, "BOLL_MID": bb_m, "BOLL_LOWER": bb_l,
        "BOLL_PCT": bb_pct,
        "VOL_RATIO": vol_ratio,
        "近5日": chg5, "近10日": chg10, "近20日": chg20,
        "综合评分": score, "优势标签": tags,
    }


def analyze_stock_comparison(stock_results: list, realtime_df: pd.DataFrame) -> dict:
    """多股深度对比分析
    stock_results: list of {symbol, name, realtime, kline}
    返回: 包含对比数据和 report.py 所需格式的 dict
    """
    rows_out = []
    for s in stock_results:
        sym = s["symbol"]
        name = s["name"]
        rt = s["realtime"]
        kline = s["kline"]
        tech = _calc_tech_for_compare(kline) if kline else {}

        mkt = rt.get("总市值(亿)")
        try:
            mkt_str = f"{float(mkt):.2f}亿" if mkt and not (isinstance(mkt, float) and np.isnan(mkt)) else "N/A"
        except:
            mkt_str = "N/A"

        chg = rt.get("涨跌幅(%)", 0)
        try:
            chg_str = f"+{float(chg):.2f}%" if float(chg) > 0 else f"{float(chg):.2f}%"
        except:
            chg_str = str(chg)

        rows_out.append({
            "代码":     sym,
            "名称":     name,
            "最新价":   rt.get("最新价"),
            "涨跌幅":   chg_str,
            "总市值":   mkt_str,
            "流通市值": f"{rt.get('流通市值(亿)', 'N/A')}亿" if rt.get("流通市值(亿)") else "N/A",
            "tech":     tech,
        })

    # 找每项指标最优者
    best_for = {}   # 指标名 → 最佳的 stock index
    num_stats = ["RSI", "KDJ_K", "KDJ_D", "KDJ_J", "MACD柱", "MACD", "VOL_RATIO",
                 "近5日", "近10日", "近20日", "综合评分", "BOLL_PCT"]
    for key in num_stats:
        vals = [(i, rows_out[i]["tech"].get(key) or -999) for i in range(len(rows_out))]
        # 越大越好的指标
        best = max(vals, key=lambda x: x[1])
        if best[1] > -999:
            best_for[key] = best[0]

    return {
        "_type": "stock_compare",
        "stocks": rows_out,
        "tech_keys": list(best_for.keys()),
        "best_for": best_for,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def compare_stocks(symbols: list, realtime_df: pd.DataFrame) -> pd.DataFrame:
    mask = realtime_df["代码"].isin(symbols)
    df = realtime_df[mask].copy()
    return df.sort_values("涨跌幅(%)", ascending=False)
