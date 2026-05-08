"""
star_stocks.py - 综合机会评分与星标股票发现 v3 (Optimized)
整合多数据源进行加权评分，从全市场筛选最具价值的 Top5 交易机会票。
"""
import threading

import pandas as pd
import numpy as np
import warnings
import os
import sys
import time

# ── 必须在任何 akshare/requests 导入之前清除代理 ────────────────────────────
for _k in list(os.environ.keys()):
    if 'proxy' in _k.lower():
        os.environ.pop(_k)
try:
    import requests as _req
    _orig_send = _req.Session.send
    def _no_proxy_send(self, request, **kw):
        kw.pop('proxies', None)
        return _orig_send(self, request, **kw)
    _req.Session.send = _no_proxy_send
    _orig_get = _req.api.get
    def _no_proxy_get(url, params=None, **kw):
        kw.pop('proxies', None)
        return _orig_get(url, params=params, **kw)
    _req.api.get = _no_proxy_get
    _orig_post = _req.api.post
    def _no_proxy_post(url, data=None, **kw):
        kw.pop('proxies', None)
        return _orig_post(url, data=data, **kw)
    _req.api.post = _no_proxy_post
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────

warnings.filterwarnings("ignore")

def _import(module_name: str, alias=None):
    try:
        mod = __import__(module_name, fromlist=[''])
        return mod if alias is None else getattr(mod, alias)
    except ImportError:
        return None

_opp_mod = None
def _load_opp():
    global _opp_mod
    if _opp_mod is None:
        _opp_mod = _import('opportunity') or _import('scripts.opportunity')
    return _opp_mod

_ff_mod = None
def _load_ff():
    global _ff_mod
    if _ff_mod is None:
        _ff_mod = _import('fund_flow') or _import('scripts.fund_flow')
    return _ff_mod

_sc_mod = None
def _load_scraper():
    global _sc_mod
    if _sc_mod is None:
        _sc_mod = _import('scraper') or _import('scripts.scraper')
    return _sc_mod

_lhb_mod = None
def _load_lhb():
    global _lhb_mod
    if _lhb_mod is None:
        _lhb_mod = _import('lhb') or _import('scripts.lhb')
    return _lhb_mod

_news_mod = None
def _load_news():
    global _news_mod
    if _news_mod is None:
        _news_mod = _import('news_search') or _import('scripts.news_search')
    return _news_mod

def _sf(v, default=np.nan):
    try:
        f = float(v)
        return f if not np.isnan(f) else default
    except:
        return default

def _fmt_chg(v):
    try:
        v = float(v)
        if np.isnan(v): return "N/A"
        return f"+{v:.2f}%" if v > 0 else f"{v:.2f}%"
    except:
        return "N/A"

def _fmt_cap(v):
    try:
        v = float(v)
        if np.isnan(v): return "N/A"
        if v >= 1e8: return f"{v/1e8:.1f}亿"
        if v >= 1e6: return f"{v/1e6:.1f}万"
        return f"{v:.0f}"
    except:
        return "N/A"

def _safe_col(df: pd.DataFrame, *names) -> str:
    for n in names:
        if n in df.columns: return n
    return None

def _score_momentum(code: str, opp: dict) -> tuple:
    score = 0.0
    tags = []
    positive = {
        "涨停股池": 3.0, "强势股池": 2.5, "昨日涨停": 2.0, "次新股池": 1.5,
        "量价齐升": 2.0, "持续放量": 1.5, "连续上涨": 1.5, "创新高": 2.0, "向上突破": 1.5,
        "炸板股池": 1.0,  # 炸板后关注
    }
    count = 0
    for cat, weight in positive.items():
        if cat in opp:
            df = opp[cat]
            if not df.empty and "代码" in df.columns:
                codes = df["代码"].astype(str).str.zfill(6)
                if code.zfill(6) in codes.values:
                    score += weight
                    count += 1
                    tags.append(cat)
    if count >= 3:
        score += 1.0
        tags.append("多重确认")
    return min(score, 5.0), tags

def _score_fund_flow_fast(code: str, ff_mod, ind_flow: pd.DataFrame, hsgt: pd.DataFrame) -> tuple:
    score = 0.0
    tags = []
    if hsgt is not None and not hsgt.empty:
        hsgt_codes = hsgt.get("代码", pd.Series(dtype=str)).astype(str).str.zfill(6)
        if code.zfill(6) in hsgt_codes.values:
            row = hsgt[hsgt_codes == code.zfill(6)].iloc[0]
            pct_col = _safe_col(hsgt, "持股比例", "持股比例(%)", "持股_占流通股比")
            if pct_col:
                pct = _sf(row.get(pct_col, 0))
                if pct > 3: score += 2.0; tags.append(f"北向{pct:.1f}%")
                elif pct > 1: score += 1.0; tags.append(f"北向{pct:.1f}%")
    if ind_flow is not None and not ind_flow.empty:
        top5_inds = ind_flow.head(5)["行业名称"].tolist() if "行业名称" in ind_flow.columns else []
        stock_ind = globals().get("_ind_flow_stock_map", {}).get(code, "")
        if stock_ind in top5_inds:
            score += 1.0; tags.append("资金热捧")
    return min(score, 3.0), tags

def _score_technical(code: str, opp: dict) -> tuple:
    score = 0.0
    tags = []
    tech = {"创新高": 2.0, "量价齐升": 1.5, "持续放量": 1.5, "连续上涨": 1.0, "向上突破": 1.0}
    for cat, weight in tech.items():
        if cat in opp:
            df = opp[cat]
            if not df.empty and "代码" in df.columns:
                codes = df["代码"].astype(str).str.zfill(6)
                if code.zfill(6) in codes.values:
                    score += weight
                    tags.append(cat)
    return min(score, 3.0), tags

def _score_market(realtime_df: pd.DataFrame, code: str) -> tuple:
    score = 0.0
    tags = []
    if realtime_df is None or realtime_df.empty: return 0.0, []
    code_col = _safe_col(realtime_df, "代码", "code")
    if not code_col: return 0.0, []
    # realtime_df 代码列可能含 sz/sh 前缀，统一截取后6位比较
    df = realtime_df.copy()
    df["_code_bare"] = df[code_col].astype(str).str[-6:]
    code_bare = code.zfill(6)
    df = df[df["_code_bare"] == code_bare]
    if df.empty: return 0.0, []
    row = df.iloc[0]
    cap_col = _safe_col(realtime_df, "流通市值", "流通市值(亿)", "流通市值(万元)", "流通市值(元)")
    if cap_col:
        cap_raw = _sf(row.get(cap_col, np.nan))
        if "万元" in str(cap_col): cap = cap_raw / 10000 if cap_raw > 0 else np.nan
        elif "元" in str(cap_col): cap = cap_raw / 1e8 if cap_raw > 0 else np.nan
        else: cap = cap_raw
        # 50-500亿中小盘弹性最佳
        if not np.isnan(cap) and 50 <= cap <= 500: score += 1.5; tags.append(f"{cap:.0f}亿")
        elif not np.isnan(cap) and cap > 0: score += 0.5; tags.append(f"{cap:.0f}亿")
    turn_col = _safe_col(realtime_df, "换手率", "换手率(%)")
    if turn_col:
        turn = _sf(row.get(turn_col, 0))
        if turn > 3: score += 0.5; tags.append(f"换手{turn:.1f}%")
        elif turn > 0: score += 0.25; tags.append(f"换手{turn:.1f}%")
    name_col = _safe_col(realtime_df, "名称", "股票名称")
    if name_col:
        name = str(row.get(name_col, ""))
        if not name.startswith(("ST", "*ST", "S*ST", "SST")): score += 0.5; tags.append("非ST")
        else: tags.append("ST风险")
    return min(score, 3.0), tags

def _score_lhb(code: str, lhb_result: dict) -> tuple:
    score = 0.0
    tags = []
    if not lhb_result: return 0.0, []
    hot = lhb_result.get("hot_stocks", [])
    for item in hot:
        if str(item.get("代码", "")).zfill(6) == code.zfill(6):
            score += 1.5; tags.append(f"上榜{item.get('上榜次数',1)}次"); break
    recent = lhb_result.get("recent_list", [])
    for record in recent:
        if str(record.get("代码", "")).zfill(6) == code.zfill(6):
            buy = record.get("买方席位", record.get("机构买入", ""))
            if buy and ("机构" in str(buy) or "专用" in str(buy)): score += 1.0; tags.append("机构买入"); break
    return min(score, 2.0), tags

_financial_cache = {}
def _load_financial_batch(candidate_codes: set) -> dict:
    """尝试加载财务数据，8秒超时则静默跳过，不阻塞主流程"""
    global _financial_cache
    _financial_cache = {}
    try:
        import akshare as ak
        import threading

        result_holder = [None]  # 用列表存结果，跨线程传递
        exc_holder = [None]

        def _fetch():
            try:
                result_holder[0] = ak.stock_financial_analysis_indicator_em(symbol="", start_year="2023")
            except Exception as e:
                exc_holder[0] = e

        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=8)
        if t.is_alive():
            # 超时，直接返回空缓存
            return _financial_cache
        if exc_holder[0]:
            return _financial_cache
        df = result_holder[0]
        if df is None or df.empty: return _financial_cache
        code_col = next((c for c in df.columns if "代码" in str(c)), None)
        if not code_col: return _financial_cache
        df[code_col] = df[code_col].astype(str).str.zfill(6)
        df = df[df[code_col].isin(candidate_codes)]
        date_col = next((c for c in df.columns if "日期" in str(c) or "报告期" in str(c)), None)
        if date_col:
            df = df.sort_values(date_col, ascending=False).drop_duplicates(subset=[code_col], keep="first")
        _financial_cache = {row[code_col]: row.to_dict() for _, row in df.iterrows()}
    except: pass
    return _financial_cache

def _score_financial(code: str, ff_mod) -> tuple:
    score = 0.0; tags = []
    if code not in _financial_cache: return 0.0, []
    row = _financial_cache[code]
    for col in ["净资产收益率(%)", "ROE(%)", "净资产收益率", "ROE"]:
        if col in row and row[col] is not None:
            roe = _sf(row.get(col, 0))
            if not np.isnan(roe) and roe != 0:
                if roe > 15: score += 1.5; tags.append(f"ROE{roe:.1f}%"); break
                elif roe > 10: score += 1.0; tags.append(f"ROE{roe:.1f}%"); break
                elif roe > 5: score += 0.5; tags.append(f"ROE{roe:.1f}%"); break
    for col in ["营收增长率(%)", "营业收入增长率", "营收增长率"]:
        if col in row and row[col] is not None:
            rev = _sf(row.get(col, 0))
            if not np.isnan(rev) and rev != 0:
                if rev > 20: score += 1.0; tags.append(f"营收+{rev:.0f}%"); break
                elif rev > 10: score += 0.5; tags.append(f"营收+{rev:.0f}%"); break
    for col in ["销售毛利率(%)", "毛利率", "销售毛利率"]:
        if col in row and row[col] is not None:
            gp = _sf(row.get(col, 0))
            if not np.isnan(gp) and gp > 0: tags.append(f"毛利率{gp:.1f}%"); break
    return min(score, 3.0), tags

def _score_sentiment(code: str, news_mod, name: str) -> tuple:
    score = 0.0; tags = []
    if news_mod is None: return 0.0, []
    try:
        search_fn = getattr(news_mod, 'search_stock_news', None)
        if search_fn is None: return 0.0, []
        results = search_fn(code=code, name=name, limit=10)
        if not results: return 0.5, ["无负面舆情"]
        pos = sum(1 for r in results if r.get("sentiment") == "positive")
        neg = sum(1 for r in results if r.get("sentiment") == "negative")
        if pos >= 3: score = 2.0; tags.append(f"正面+{pos}篇")
        elif pos == 2: score = 1.5; tags.append(f"正面+{pos}篇")
        elif pos == 1: score = 1.0; tags.append("正面+1")
        if neg == 0: score += 0.5; tags.append("无负面舆情")
        elif neg >= 3: score = max(0, score - 1.0); tags.append(f"负面-{neg}篇")
    except: pass
    return min(score, 2.0), tags

def _score_board(code: str, hot_boards: dict) -> tuple:
    """根据股票所属行业是否在热门板块中评分"""
    score = 0.0; tags = []
    if not hot_boards or hot_boards.get("混合", pd.DataFrame()).empty: return 0.0, []
    mixed = hot_boards["混合"]
    if "得分" not in mixed.columns and "涨跌幅" not in mixed.columns: return 0.0, []
    # 找板块名列
    bname_col = None
    for cn in ["板块名称", "名称", "行业名称", "概念名称"]:
        if cn in mixed.columns:
            bname_col = cn; break
    if not bname_col: return 0.0, []
    # 热门板块集合（TOP10/TOP20/TOP30）
    top10_names = set(mixed.head(10)[bname_col].astype(str).tolist())
    top20_names = set(mixed.head(20)[bname_col].astype(str).tolist())
    top30_names = set(mixed.head(30)[bname_col].astype(str).tolist())
    # 从 industry_map 查该股所属板块
    stock_board = globals().get("_ind_flow_stock_map", {}).get(code, "")
    if not stock_board: return 0.0, []
    for bn in top10_names:
        if bn in str(stock_board) or str(stock_board) in bn:
            return 2.0, [f"热门TOP10:{bn[:6]}"]
    for bn in top20_names:
        if bn in str(stock_board) or str(stock_board) in bn:
            return 1.0, [f"热门TOP20:{bn[:6]}"]
    for bn in top30_names:
        if bn in str(stock_board) or str(stock_board) in bn:
            return 0.5, [f"跟随板块:{bn[:6]}"]
    return 0.0, []

def find_star_stocks(top_n: int = 5, use_cache: bool = True) -> pd.DataFrame:
    scores = []; t0 = time.time(); all_codes = {}
    opp_mod = _load_opp()
    opp = opp_mod.find_opportunities() if opp_mod and hasattr(opp_mod, 'find_opportunities') else {}
    ff_mod = _load_ff()
    ind_flow = ff_mod.get_industry_flow() if ff_mod and hasattr(ff_mod, 'get_industry_flow') else pd.DataFrame()
    hsgt = ff_mod.get_hsgt_hold() if ff_mod and hasattr(ff_mod, 'get_hsgt_hold') else pd.DataFrame()
    hot_boards = ff_mod.get_hot_boards(n=30) if ff_mod and hasattr(ff_mod, 'get_hot_boards') else {}
    scraper_mod = _load_scraper()
    realtime_df = pd.DataFrame(); industry_map = {}
    if scraper_mod and hasattr(scraper_mod, 'fetch_data'):
        res = scraper_mod.fetch_data(use_cache=use_cache)
        if res: realtime_df, _, industry_map = res
    lhb_mod = _load_lhb()
    lhb_result = lhb_mod.analyze_lhb(days=10) if lhb_mod and hasattr(lhb_mod, 'analyze_lhb') else {}
    for cat, df in opp.items():
        if df is not None and not df.empty and "代码" in df.columns:
            for _, row in df.iterrows():
                c = str(row.get("代码", "")).zfill(6)
                if c and c != "000000": all_codes[c] = {"name": row.get("名称", ""), "code": c}
    if not hsgt.empty and "代码" in hsgt.columns:
        for _, row in hsgt.iterrows():
            c = str(row.get("代码", "")).zfill(6)
            if c and c not in all_codes: all_codes[c] = {"name": row.get("名称", ""), "code": c}
    # 构建行业映射 {板块名: 股票代码集合}，用于板块评分
    # industry_map key 可能是 sz000001/sh600000 格式，需支持前缀和无前缀两种查找
    board_stock_map: dict[str, set] = {}
    _ind_flow_stock_map_raw: dict[str, str] = {}  # 股票代码(无前缀) -> 板块名
    for code, bname in industry_map.items():
        if not bname or str(bname) in ("nan", "None", ""):
            continue
        # 去掉前缀得到无前缀代码（只对 sz/sh 前缀的key截取）
        if code.startswith(("sz", "sh")) and len(code) == 8:
            bare = code[2:]
        else:
            bare = code  # 已经是6位裸码
        # 避免重复：裸码已存在则跳过（字典已优先存入无前缀的key）
        if bare in _ind_flow_stock_map_raw:
            continue
        _ind_flow_stock_map_raw[bare] = bname
        if bname not in board_stock_map:
            board_stock_map[bname] = set()
        board_stock_map[bname].add(bare)
    global _ind_flow_stock_map
    _ind_flow_stock_map = _ind_flow_stock_map_raw

    news_mod = _load_news()
    news_available = news_mod is not None and hasattr(news_mod, 'search_stock_news')
    _load_financial_batch(set(all_codes.keys()))
    for code, info in all_codes.items():
        name = info["name"]
        m_s, m_t = _score_momentum(code, opp)
        f_s, f_t = _score_fund_flow_fast(code, ff_mod, ind_flow, hsgt)
        t_s, t_t = _score_technical(code, opp)
        mk_s, mk_t = _score_market(realtime_df, code)
        l_s, l_t = _score_lhb(code, lhb_result)
        b_s, b_t = _score_board(code, hot_boards)
        # 修复：传缓存字典而不是 None，让 _score_financial 能真正读取数据
        fi_s, fi_t = _score_financial(code, _financial_cache)
        s_s, s_t = _score_sentiment(code, news_mod, name) if news_available else (0.0, [])
        # 去掉硬过滤：允许所有候选股进入评分，哪怕市值分/财务分为0 
        weighted_total = (m_s*1.5 + f_s*1.2 + t_s*1.2 + fi_s*1.0 + s_s*1.0 + b_s*1.0 + l_s*0.8 + mk_s*0.5)
        high_scores = [m_s >= 2.0, f_s >= 1.5, t_s >= 1.5, fi_s >= 1.5]
        resonance_bonus = 2.0 if sum(high_scores) >= 3 else (1.0 if sum(high_scores) >= 2 else 0.0)
        total = weighted_total + resonance_bonus
        all_tags = m_t + f_t + t_t + mk_t + l_t + b_t + fi_t + s_t
        price, chg, tr, cap = "", "", "", ""
        if not realtime_df.empty:
            c_col = _safe_col(realtime_df, "代码", "code")
            if c_col:
                rt2 = realtime_df.copy()
                rt2["_code_bare"] = rt2[c_col].astype(str).str[-6:]
                sub = rt2[rt2["_code_bare"] == code.zfill(6)]
                if not sub.empty:
                    row = sub.iloc[0]
                    p_col = _safe_col(realtime_df, "最新价", "现价", "收盘")
                    c_col_chg = _safe_col(realtime_df, "涨跌幅(%)", "涨跌幅", "涨跌额")
                    tr_col = _safe_col(realtime_df, "换手率(%)", "换手率")
                    cp_col = _safe_col(realtime_df, "流通市值(亿)", "流通市值", "流通市值(万元)")
                    price = f"{_sf(row.get(p_col, np.nan)):.2f}" if not np.isnan(_sf(row.get(p_col, np.nan))) else ""
                    chg = _fmt_chg(row.get(c_col_chg, np.nan))
                    tr = f"{_sf(row.get(tr_col, 0)):.2f}%"
                    cap = _fmt_cap(_sf(row.get(cp_col, 0)))
        scores.append({
            "代码": code, "名称": name, "综合评分": round(total, 2),
            "动量分": round(m_s, 1), "资金分": round(f_s, 1), "技术分": round(t_s, 1),
            "市值分": round(mk_s, 1), "龙虎分": round(l_s, 1), "板块分": round(b_s, 1),
            "财务分": round(fi_s, 1), "舆情分": round(s_s, 1),
            "最新价": price, "涨跌幅": chg, "换手率": tr, "流通市值": cap,
            "机会标签": " | ".join(m_t), "资金标签": " | ".join(f_t), "技术标签": " | ".join(t_t),
            "板块标签": " | ".join(b_t), "财务标签": " | ".join(fi_t), "舆情标签": " | ".join(s_t),
            "推荐理由": "顶级精选" if total >= 14.0 else "高质量关注" if total >= 9.0 else "潜在机会"
        })
    if not scores: return pd.DataFrame()
    return pd.DataFrame(scores).sort_values("综合评分", ascending=False).reset_index(drop=True).head(top_n)

def analyze_star_stock(code: str) -> dict:
    code = str(code).zfill(6)
    opp_mod = _load_opp(); ff_mod = _load_ff(); scraper_mod = _load_scraper(); lhb_mod = _load_lhb()
    opp = opp_mod.find_opportunities() if opp_mod and hasattr(opp_mod, 'find_opportunities') else {}
    ind_flow = ff_mod.get_industry_flow() if ff_mod and hasattr(ff_mod, 'get_industry_flow') else pd.DataFrame()
    hsgt = ff_mod.get_hsgt_hold() if ff_mod and hasattr(ff_mod, 'get_hsgt_hold') else pd.DataFrame()
    realtime_df = pd.DataFrame()
    if scraper_mod and hasattr(scraper_mod, 'fetch_data'):
        res = scraper_mod.fetch_data(use_cache=True)
        if res: realtime_df = res[0]
    lhb_result = lhb_mod.analyze_lhb(days=10) if lhb_mod and hasattr(lhb_mod, 'analyze_lhb') else {}
    name = ""
    if not realtime_df.empty:
        cc = _safe_col(realtime_df, "代码", "code")
        if cc:
            sub = realtime_df[realtime_df[cc].astype(str).str.zfill(6) == code]
            if not sub.empty: name = str(sub.iloc[0].get("名称", ""))
    m_s, m_t = _score_momentum(code, opp)
    f_s, f_t = _score_fund_flow_fast(code, ff_mod, ind_flow, hsgt)
    t_s, t_t = _score_technical(code, opp)
    mk_s, mk_t = _score_market(realtime_df, code)
    l_s, l_t = _score_lhb(code, lhb_result)
    total = (m_s*0.30 + f_s*0.25 + t_s*0.20 + mk_s*0.15 + l_s*0.10)
    appeared_in = [cat for cat, df in opp.items() if df is not None and not df.empty and "代码" in df.columns and code.zfill(6) in df["代码"].astype(str).str.zfill(6).values]
    risks = []
    if name.startswith(("ST", "*ST", "S*ST", "SST")): risks.append("ST股票，风险较高")
    return {
        "代码": code, "名称": name, "综合评分": round(total, 2),
        "动量分": round(m_s, 1), "资金分": round(f_s, 1), "技术分": round(t_s, 1),
        "市值分": round(mk_s, 1), "龙虎分": round(l_s, 1), "机会分类": appeared_in,
        "动量标签": m_t, "资金标签": f_t, "技术标签": t_t, "市值标签": mk_t, "龙虎标签": l_t,
        "风险点": risks, "recommend": total >= 6,
    }

def get_star_report(top_n: int = 5) -> dict:
    t0 = time.time()

    # 整块扫描放线程里，加总超时保护
    _stars_result = [None]  # [0] = None | pd.DataFrame | Exception
    def _scan():
        try:
            _stars_result[0] = find_star_stocks(top_n=top_n, use_cache=True)
        except Exception as e:
            _stars_result[0] = e

    t = threading.Thread(target=_scan, daemon=True)
    t.start()
    t.join(timeout=120)  # 最多等120秒，超时则跳过
    elapsed = time.time() - t0

    if t.is_alive():
        return {
            "stars": [], "stars_df": pd.DataFrame(),
            "count": 0, "elapsed": round(elapsed, 1),
            "summary": f"扫描超时（>{120}s），请检查网络后重试",
            "board_recommendations": {}
        }
    result = _stars_result[0]
    if isinstance(result, Exception):
        return {
            "stars": [], "stars_df": pd.DataFrame(),
            "count": 0, "elapsed": round(elapsed, 1),
            "summary": f"扫描出错: {result}",
            "board_recommendations": {}
        }
    stars = result
    if stars.empty:
        return {"stars": [], "summary": "今日暂未发现星标股票", "count": 0, "elapsed": round(elapsed, 1), "board_recommendations": {}}

    strong = stars[stars["综合评分"] >= 14.0]
    focus  = stars[(stars["综合评分"] >= 9.0) & (stars["综合评分"] < 14.0)]
    watch  = stars[(stars["综合评分"] >= 4.0) & (stars["综合评分"] < 9.0)]

    board_dist = {}
    for _, row in stars.iterrows():
        ot = row.get("机会标签", "")
        if ot:
            for t2 in ot.split("|"):
                t2 = t2.strip()
                if t2: board_dist[t2] = board_dist.get(t2, 0) + 1

    summary_parts = [f"今日共发现 **{len(stars)} 只** 星标候选股票"]
    if not strong.empty:
        summary_parts.append(f"顶级精选 {len(strong)} 只：{', '.join(strong['名称'].head(5).tolist())}")
    if not focus.empty:
        summary_parts.append(f"高质量关注 {len(focus)} 只：{', '.join(focus['名称'].head(5).tolist())}")

    # 板块推荐（15秒超时）
    board_recs = {}
    try:
        import board_analysis as ba
        _br = [{}]
        def _fetch_boards():
            _br[0] = ba.get_board_recommendations(n_per_board=3, top_boards=15)
        tb = threading.Thread(target=_fetch_boards, daemon=True)
        tb.start()
        tb.join(timeout=15)
        if not tb.is_alive():
            board_recs = _br[0]
    except:
        pass

    return {
        "stars": stars.to_dict("records"),
        "stars_df": stars,
        "count": len(stars),
        "strong": strong.to_dict("records"),
        "focus": focus.to_dict("records"),
        "watch": watch.to_dict("records"),
        "board_dist": sorted(board_dist.items(), key=lambda x: -x[1])[:10],
        "board_recommendations": board_recs,
        "summary": " | ".join(summary_parts),
        "elapsed": round(elapsed, 1),
    }

