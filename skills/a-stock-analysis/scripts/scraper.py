"""
scraper.py - A股数据抓取模块
数据源:
  实时行情: 新浪HTTP(主力) + 腾讯行情HTTPS(补充市值)
  历史K线:  ifzq腾讯系(优先) → 同花顺(兜底，含换手率)
  行业板块: 新浪 + akshare证监会行业映射
同花顺K线优势: 历史最深(贵州茅台5900+条)、自带换手率、sz/sh自动兼容
"""

import requests
import pandas as pd
import time
import re
import json
import os
import pickle
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 代理配置（已在 main.py 顶层清除，不需要在 scraper 中重新设置）──
# scraper.py 走直连，无需代理

import akshare as ak

# ── 配置 ──────────────────────────────────────────────────
SINA_HQ   = "http://hq.sinajs.cn/list="
TX_HQ     = "https://qt.gtimg.cn/q="
SINA_KL   = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
SINA_BD   = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
HEADERS_SINA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}
HEADERS_TX = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.qq.com",
}
TIMEOUT    = 12
BATCH_SINA = 200
BATCH_TX   = 100
DELAY      = 0.15
CACHE_DIR  = "/tmp/a_stock_cache"
os.makedirs(CACHE_DIR, exist_ok=True)
MAX_WORKERS = 8   # 并发线程数（新浪23批 + 腾讯46批，可同时跑）

# scraper.py 走直连，不使用代理
_PROXY = None
_PROXY_HTTPS = None


# ── 工具函数 ───────────────────────────────────────────────
def sf(v, default=0.0):
    try: return float(v)
    except: return default

def si(v, default=0):
    try: return int(float(v))
    except: return default

def fmt_pct(v):
    try:
        v = float(v)
        return f"{'+' if v > 0 else ''}{v:.2f}%"
    except:
        return "  0.00%"

def retry_request(url, headers, params=None, timeout=12, max_retries=3):
    """带重试的HTTP请求（WSL走直连，禁用环境变量代理）"""
    for attempt in range(max_retries):
        try:
            session = requests.Session()
            session.trust_env = False  # 忽略 http_proxy/https_proxy 环境变量
            r = session.get(url, headers=headers, params=params, timeout=timeout)
            return r
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1.5 ** attempt)
            else:
                raise


# ── 1. 股票列表 ─────────────────────────────────────────────
def get_stock_list():
    """获取全量A股代码列表，返回 {symbol: {code, name}}"""
    print("  [scraper] 获取股票列表...")
    df = ak.stock_info_a_code_name()
    # 强制性纠错映射（针对上游数据源错位 Bug）
    CORRECTION_MAP = {
        "002709": "天赐材料",
        "300037": "新宙邦",
    }
    for code, correct_name in CORRECTION_MAP.items():
        if code in df["code"].values:
            df.loc[df["code"] == code, "name"] = correct_name
    
    # 过滤A股: 00=深圳主板, 30=创业板, 60=上海主板, 68=科创板
    mask = df["code"].str.match(r"^(00|30|60|68|8|9)\d{4}$")
    df = df[mask].reset_index(drop=True)
    stocks = {}
    for _, row in df.iterrows():
        code = row["code"]
        sym  = f"sh{code}" if code.startswith(("6", "68")) else f"sz{code}"
        stocks[sym] = {"code": code, "name": row["name"]}
    print(f"  [scraper] 共 {len(stocks)} 只A股")
    return stocks


# ── 2. 新浪实时行情（批量）───────────────────────────────────
def fetch_batch_sina(symbols):
    """批量获取新浪实时行情，返回行列表"""
    url = SINA_HQ + ",".join(symbols)
    try:
        r = retry_request(url, HEADERS_SINA, timeout=TIMEOUT)
        r.encoding = "gbk"
        rows = []
        for line in r.text.strip().split("\n"):
            m = re.search(r'hq_str_(\w+)="([^"]+)"', line)
            if not m: continue
            sym   = m.group(1)
            parts = m.group(2).split(",")
            if len(parts) < 32: continue
            name  = parts[0]
            
            # ── 强制修正名称映射（修复上游数据错位 Bug） ──
            CORRECTION_MAP = {"sz002709": "天赐材料", "sz300037": "新宙邦"}
            if sym in CORRECTION_MAP:
                name = CORRECTION_MAP[sym]
            # ───────────────────────────────────────────
            op    = sf(parts[1])
            yest  = sf(parts[2])
            cur   = sf(parts[3])
            high  = sf(parts[4])
            low   = sf(parts[5])
            vol   = si(parts[8])
            amt   = sf(parts[9])
            date  = parts[30] if len(parts) > 30 else ""
            tm    = parts[31] if len(parts) > 31 else ""
            chg   = round(cur - yest, 3)
            pct   = round((cur / yest - 1) * 100, 2) if yest else 0
            turn  = sf(parts[38]) if len(parts) > 38 else 0
            pe    = sf(parts[39]) if len(parts) > 39 else None
            # 新浪自带市值字段（部分股票有）
            mktcap= sf(parts[44]) / 1e8 if len(parts) > 44 and parts[44] else None
            nmc   = sf(parts[45]) / 1e8 if len(parts) > 45 and parts[45] else None
            # ── 数据校验：过滤无效行情 ──────────────────────────
            # 新浪接口有时返回昨收=今价=0的"虚假"数据（网络抖动或接口未开盘）
            # 条件：最新价>0，成交额>0（避免停牌/未上市股票污染数据）
            if cur <= 0 or amt <= 0:
                continue
            # 若换手率异常（>100%且非新股）标记为数据可疑
            suspicious = turn > 100 if turn > 0 else False

            rows.append({
                "代码":        sym,
                "名称":        name,
                "最新价":      cur,
                "昨收":        yest,
                "今开":        op,
                "今高":        high,
                "今低":        low,
                "涨跌幅(%)":   pct,
                "涨跌额":      chg,
                "成交量(手)":  vol,
                "成交额(元)":  amt,
                "换手率(%)":   round(turn, 3),
                "市盈率TTM":   pe if pe and 0 < pe < 10000 else None,
                "总市值(亿)":  round(mktcap, 2) if mktcap else None,
                "流通市值(亿)":round(nmc, 2) if nmc else None,
                "时间":        f"{date} {tm}".strip(),
                "_可疑":       suspicious,
            })
        return rows
    except Exception as e:
        print(f"    [WARN] 新浪批量请求失败: {e}")
        return []


# ── 3. 腾讯实时行情（补充市值）───────────────────────────────
def fetch_batch_tx(symbols):
    """批量获取腾讯行情，返回 {symbol: (总市值亿, 流通市值亿)}"""
    if not symbols:
        return {}
    url = TX_HQ + ",".join(symbols)
    try:
        r = retry_request(url, HEADERS_TX, timeout=TIMEOUT)
        r.encoding = "gbk"
        mktcaps = {}
        for line in r.text.strip().split("\n"):
            # Tencent uses market flags: "1~ for SH, "51~ for SZ/CB
            if '="1~' not in line and '="51~' not in line:
                continue
            parts = line.split("~")
            if len(parts) < 46: continue
            sym = parts[2]
            prefix = "sh" if sym.startswith(("6", "68")) else "sz"
            full = f"{prefix}{sym}"
            mc = sf(parts[44]) if len(parts) > 44 and parts[44] else None
            nc = sf(parts[45]) if len(parts) > 45 and parts[45] else None
            mktcaps[full] = (round(mc, 2) if mc else None, round(nc, 2) if nc else None)
        return mktcaps
    except Exception as e:
        print(f"    [WARN] 腾讯批量请求失败: {e}")
        return {}


# ── 2c. 单只股票实时价格（用于K线交叉验证）────────────────────
def get_realtime_price(symbol):
    """从新浪获取单只股票实时价格（昨收/今价/今高/今低/成交额）
    返回 dict: {price, prev_close, pct, high, low, amount}
    用于验证K线收盘价是否为最新数据。
    若请求失败或价格为0，返回 None（不阻塞K线数据返回）。
    """
    try:
        url = SINA_HQ + symbol
        r = retry_request(url, HEADERS_SINA, timeout=TIMEOUT)
        r.encoding = "gbk"
        m = re.search(r'hq_str_\w+="([^"]+)"', r.text)
        if not m or len(m.group(1).split(",")) < 10:
            return None
        p = m.group(1).split(",")
        cur  = sf(p[3])
        yest = sf(p[2])
        high = sf(p[4])
        low  = sf(p[5])
        amt  = sf(p[9])
        pct  = round((cur / yest - 1) * 100, 2) if yest > 0 else 0
        if cur <= 0 or amt <= 0:
            return None
        return {"price": cur, "prev_close": yest, "high": high, "low": low,
                "amount": amt, "pct": pct}
    except Exception:
        return None


# ── 4a. 历史日K线：同花顺接口（优先，含换手率，历史最深）──────
THS_HQ = "http://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"
HEADERS_THS = {
    "Referer": "http://www.10jqka.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

def get_kline_ths(symbol, days=320):
    """同花顺日K线（支持 sz/sh，统一 hs_ 前缀）
    字段: [日期, 收盘, 开盘, 高, 低, 成交量, 成交额, 换手率(%), ...]
    优势: 历史最深（贵州茅台5900+条）、自带换手率、sz/sh自动兼容
    """
    # symbol 格式: sz000001 / sh600000 → 提取纯代码
    code = symbol[2:] if symbol.startswith(("sz", "sh")) else symbol
    url = THS_HQ.format(code=code)
    try:
        # 同花顺 HTTP 接口走直连（no_proxy 已设置但 retry_request 显式传 proxies 会覆盖）
        session = requests.Session()
        session.trust_env = False
        r = session.get(url, headers=HEADERS_THS, timeout=TIMEOUT)
        text = r.text
        # JSONP: quotebridge_v6_line_hs_600519_01_last({...})
        m = re.search(r'\((\{.+\})\)', text, re.DOTALL)
        if not m:
            return []
        j = json.loads(m.group(1))
        all_data = j.get("data", "")
        if not all_data:
            return []
        rows = []
        for raw in all_data.split(";"):
            if not raw:
                continue
            parts = raw.split(",")
            if len(parts) < 8:
                continue
            rows.append({
                "日期":      parts[0][:10],
                "前复权收盘": sf(parts[1]),
                "开盘":      sf(parts[2]),
                "最高":      sf(parts[3]),
                "最低":      sf(parts[4]),
                "成交量":    si(parts[5]),
                "换手率(%)": sf(parts[7]),
            })
        # 取最近 days 条
        return rows[-days:] if len(rows) > days else rows
    except Exception:
        return []


# ── 4b. 历史日K线：腾讯 ifzq 接口（主用，备用同花顺）─────────
def get_kline(symbol, days=7):
    """获取单只股票近N个交易日日K（前复权）
    优先 ifzq；失败或数据不足5条时自动切换同花顺（同花顺含换手率）

    数据校验规则（解决接口返回陈旧收盘价的致命 Bug）：
      1. 最新K线日期必须在最近3个交易日内，否则判定为缓存过期
      2. 若接口数据过旧，自动 fallback 到同花顺
      3. 若同花顺也过旧，发出 [WARN] 并返回数据（不再递归）

    ifzq格式: ["日期", 收盘(前复权), 开盘, 高, 低, 成交量]
    同花顺格式: [日期, 收盘, 开盘, 高, 低, 成交量, 成交额, 换手率]
    """
    import random as _rnd
    from datetime import datetime, timedelta

    def _is_recent(date_str):
        """判断K线日期是否在最近N个交易日内（容错3天窗口）"""
        try:
            kdate = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            today = datetime.now()
            return (today - kdate).days <= 3
        except:
            return False

    rnd = ''.join(str(_rnd.randint(0, 9)) for _ in range(16))
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{days},qfq&_={rnd}"

    try:
        r = retry_request(url, {}, HEADERS_SINA, timeout=TIMEOUT)
        j = r.json()
        sym_key = symbol
        if sym_key not in j.get("data", {}):
            print(f"    [WARN] {symbol}: ifzq 无数据，切换同花顺")
            return get_kline_ths(symbol, days)
        klines = j["data"][sym_key].get("qfqday") or j["data"][sym_key].get("day") or []
        if not klines:
            print(f"    [WARN] {symbol}: ifzq 返回空，切换同花顺")
            return get_kline_ths(symbol, days)

        rows = []
        for k in klines:
            if len(k) < 6:
                continue
            rows.append({
                "日期":    k[0][:10],
                "前复权收盘": sf(k[1]),
                "开盘":    sf(k[2]),
                "最高":    sf(k[3]),
                "最低":    sf(k[4]),
                "成交量":  si(k[5]),
                "换手率(%)": None,
            })

        # ── 关键修复：日期校验 ───────────────────────────────
        latest_date = rows[-1]["日期"] if rows else ""
        if not _is_recent(latest_date):
            print(f"    [WARN] {symbol}: ifzq 最新K线日期={latest_date} 已过期（>3天），切换同花顺")
            ths = get_kline_ths(symbol, days)
            if ths and _is_recent(ths[-1]["日期"] if ths else ""):
                return ths
            else:
                # 同花顺也过期，不再递归，直接返回（避免死循环）
                print(f"    [WARN] {symbol}: 同花顺最新日期={ths[-1]['日期'] if ths else 'N/A'} 也已过期，返回 ifzq 数据（请注意数据可能不完整）")
                return rows

        # ── 关键修复2：实时价格交叉验证 ──────────────────────
        # 即使K线日期是今天，ifzq的"收盘价"也可能仍是昨收（收盘后约15-30分钟才刷新）
        # 验证逻辑（阈值5%，覆盖昨收涨幅≥10%的全部涨停股）：
        #   - 差异 < 5%：正常，不打印
        #   - 差异 >= 5%：强制用实时价格替换最后一条K线收盘
        rt = get_realtime_price(symbol)
        if rt and rows:
            kline_close = rows[-1]["前复权收盘"]
            diff_pct = abs(kline_close - rt["price"]) / rt["price"] * 100
            if diff_pct >= 5:
                rows[-1] = {
                    "日期":        latest_date,
                    "前复权收盘":  rt["price"],
                    "开盘":        rt["price"],
                    "最高":        rt["high"],
                    "最低":        rt["low"],
                    "成交量":      rows[-1]["成交量"],
                    "换手率(%)":  rows[-1].get("换手率(%)"),
                    "_实时校验":  True,
                }

        if len(rows) < 5:
            ths = get_kline_ths(symbol, days)
            if ths:
                return ths
        return rows

    except Exception as e:
        print(f"    [WARN] {symbol}: ifzq 请求异常({e})，切换同花顺")
        ths = get_kline_ths(symbol, days)
        if not ths:
            print(f"    [ERROR] {symbol}: 同花顺也失败，数据获取完全失败")
        return ths or []


# ── 5. 新浪行业板块 ─────────────────────────────────────────
def get_sina_boards():
    """获取新浪行业板块实时行情
    实际格式（GBK）: node_code:"板块名,股票数,平均价,涨跌幅,...,领涨股代码,领涨现价,领涨涨幅,领涨股名"
    """
    print("  [scraper] 获取新浪行业板块...")
    try:
        r = retry_request(SINA_BD, HEADERS_SINA, timeout=TIMEOUT)
        r.encoding = "gbk"
        rows = []
        # 匹配: "new_xxx":"板块名,股票数,平均价,涨跌幅,...,领涨代码,领涨现价,领涨幅,领涨名"
        for m in re.finditer(r'"(new_\w+)":"([^"]+)"', r.text):
            fields = m.group(2).split(",")
            if len(fields) < 12: continue
            name = fields[1]
            num  = int(fields[2]) if fields[2].isdigit() else 0
            avg_price = sf(fields[3])
            pct       = sf(fields[4])
            # 领涨股在倒数第3,2,1个字段（往前数）
            n = len(fields)
            lead_code = fields[n-3]
            lead_price= sf(fields[n-2])
            lead_pct  = sf(fields[n-1])
            rows.append({
                "板块名称":   name,
                "股票数":     num,
                "平均价":     avg_price,
                "涨跌幅(%)":  pct,
                "领涨股":     fields[-1] if len(fields) > 0 else "",
                "领涨涨幅(%)": lead_pct,
                "领涨股代码":  lead_code,
                "_node":      m.group(1),  # 内部用：新浪节点代码
            })
        print(f"  [scraper]  获取 {len(rows)} 个行业板块")
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  [WARN] 新浪行业板块获取失败: {e}")
        return pd.DataFrame()


# ── 6. akshare 证监会行业分类 ────────────────────────────────
_PROXY = os.environ.get("https_proxy") or os.environ.get("http_proxy") or "http://127.0.0.1:7897"


def _ak_try(func, *args, max_retries=3, **kwargs):
    """akshare 调用包装：自动重试处理网络不稳定"""
    import time
    last_err = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last_err


def get_industry_map():
    """用 akshare 获取沪深两市证监会行业分类作为股票→行业映射"""
    print("  [scraper] 获取证监会行业分类（沪深两市）...")
    industry_map = {}

    # ── 深交所 ────────────────────────────────────────────────
    try:
        df_sz = _ak_try(ak.stock_info_sz_name_code, symbol="A股列表")
        for _, row in df_sz.iterrows():
            code = str(row.iloc[1]).zfill(6)
            ind  = str(row.iloc[6]).strip()
            if ind and ind != "nan":
                industry_map[f"sz{code}"] = ind
                industry_map[code]         = ind
        print(f"  [scraper]  深交所: {len([k for k in industry_map if k.startswith('sz')])} 只")
    except Exception as e:
        print(f"  [WARN] 深交所行业获取失败: {e}")

    # ── 同花顺行业成分股（并发拉取所有行业）─────────────────────
    # 同花顺按证监会行业分类，同一行业下包含沪深两市所有成分股
    try:
        board_names = _ak_try(ak.stock_board_industry_name_em)
        print(f"  [scraper]  同花顺行业: {len(board_names)} 个（并发{MAX_WORKERS}线程拉成分股）...")

        def _fetch_board_cons(bname):
            """线程安全：拉单个行业成分股，返回 (bname, [(code, ...), ...])"""
            try:
                cons = _ak_try(ak.stock_board_industry_cons_em, symbol=bname)
                codes = []
                for _, row in cons.iterrows():
                    code = str(row.get("代码", row.iloc[0])).zfill(6)
                    if not code.isdigit():
                        code = code[-6:]
                    codes.append(code)
                return (bname, codes)
            except Exception:
                return (bname, [])

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(_fetch_board_cons, brow.get("板块名称") or brow.iloc[0]): brow
                       for _, brow in board_names.iterrows()}
            done = 0
            for fut in as_completed(futures):
                bname, codes = fut.result()
                for code in codes:
                    industry_map[code] = bname
                    industry_map[f"sh{code}"] = bname
                    industry_map[f"sz{code}"] = bname
                done += 1
                if done % 20 == 0 or done == len(board_names):
                    print(f"    行业进度 {done}/{len(board_names)}")
        print(f"  [scraper]  行业映射合计 {len(industry_map)//2} 只股票（已合并去重）")
    except Exception as e:
        print(f"  [WARN] 同花顺行业获取失败: {e}")

    print(f"  [scraper]  行业映射合计 {len(industry_map)//2} 只股票")
    return industry_map


# ── 7. 全量抓取入口 ─────────────────────────────────────────
def fetch_data(use_cache=True, force_cache=False):
    """
    抓取全量A股实时行情 + 行业数据
    返回: (realtime_df, board_df, stock_industry_map)
    force_cache=True: 跳过10分钟过期检查，直接读缓存（网络不稳时使用）
    """
    cache_realtime = f"{CACHE_DIR}/realtime.pkl"
    cache_board    = f"{CACHE_DIR}/board.pkl"
    cache_industry = f"{CACHE_DIR}/industry_map.pkl"
    # 尝试从缓存加载（force_cache=True 时跳过过期检查）
    if use_cache:
        for path in [cache_realtime, cache_board, cache_industry]:
            if not os.path.exists(path):
                break  # 缓存不存在，跳出改为抓取
        else:
            if force_cache:
                print("  [scraper] 强制使用缓存，跳过过期检查")
            else:
                age = time.time() - os.path.getmtime(cache_realtime)
                # 智能 TTL：交易时段 10 分钟，收盘后延长到 24 小时
                now = datetime.now()
                is_trading_hour = (
                    now.weekday() < 5  # 周一到周五
                    and (
                        (9 <= now.hour < 15)  # 正常交易时段 9:00-14:59
                        or (now.hour == 15 and now.minute <= 5)  # 尾盘5分钟宽限
                        or (now.hour == 9 and now.minute >= 15)  # 开场后15分钟
                    )
                )
                ttl = 600 if is_trading_hour else 86400  # 盘中10min，盘后24h
                if age > ttl:
                    print(f"  [scraper] 缓存已过期({age//60:.0f}min)，重新抓取...")
                else:
                    # 缓存有效，全部加载
                    with open(cache_realtime, "rb") as f: df = pickle.load(f)
                    print(f"  [scraper] 从缓存加载实时行情: {len(df)} 只 (缓存{age//60:.0f}min前)")
                    with open(cache_board, "rb") as f: board_df = pickle.load(f)
                    with open(cache_industry, "rb") as f: industry_map = pickle.load(f)
                    return df, board_df, industry_map
            if force_cache:
                # 强制用缓存，不检查过期
                with open(cache_realtime, "rb") as f: df = pickle.load(f)
                print(f"  [scraper] 从缓存加载实时行情: {len(df)} 只")
                with open(cache_board, "rb") as f: board_df = pickle.load(f)
                with open(cache_industry, "rb") as f: industry_map = pickle.load(f)
                return df, board_df, industry_map

    # Step 1: 股票列表
    stock_map = get_stock_list()
    symbols   = list(stock_map.keys())

    # Step 2: 全量实时行情（新浪批量，并发抓取）
    print(f"  [scraper] 批量获取实时行情 (~{len(symbols)}只, 新浪，并发{MAX_WORKERS}线程)...")
    all_rows = []
    total    = len(symbols)
    total_b  = (total + BATCH_SINA - 1) // BATCH_SINA

    def _fetch_sina_batch(args):
        n, batch = args
        return fetch_batch_sina(batch)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_sina_batch, (n, symbols[i:i+BATCH_SINA])): n
                   for n, i in enumerate(range(0, total, BATCH_SINA), 1)}
        done = 0
        for fut in as_completed(futures):
            rows = fut.result()
            all_rows.extend(rows)
            done += 1
            if done % 5 == 0 or done == total_b:
                print(f"    新浪进度 {done}/{total_b} ({100*done//total_b}%)")

    time.sleep(0.3)  # 礼貌间隔

    df = pd.DataFrame(all_rows)
    valid = df[df["最新价"].notna() & (df["最新价"] > 0)].copy()
    print(f"  [scraper] 新浪实时: {len(valid)}/{total} 只有效")

    # Step 2b: 腾讯补充市值（无市值或市值缺失的股票，并发）
    missing = valid[valid["总市值(亿)"].isna()]["代码"].tolist()
    print(f"  [scraper] 腾讯补充市值: {len(missing)} 只缺失（并发{MAX_WORKERS}线程）...")
    filled = 0

    def _fetch_tx_batch(batch):
        return fetch_batch_tx(batch)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_tx_batch, missing[i:i+BATCH_TX]): i
                   for i in range(0, len(missing), BATCH_TX)}
        done = 0
        for fut in as_completed(futures):
            caps = fut.result()
            for sym, (mc, nc) in caps.items():
                mask = valid["代码"] == sym
                if mask.any() and pd.isna(valid.loc[mask, "总市值(亿)"]).any():
                    valid.loc[mask, "总市值(亿)"]   = mc
                    valid.loc[mask, "流通市值(亿)"] = nc
                    filled += 1
            done += 1
            if done % 10 == 0:
                print(f"    腾讯市值进度 {done}/{len(missing)//BATCH_TX + 1}")
    print(f"  [scraper] 补充市值: {filled} 只，最终有市值 {valid['总市值(亿)'].notna().sum()}/{len(valid)}")

    # Step 3: 新浪行业板块（含节点代码）
    board_df = get_sina_boards()

    # Step 4: akshare 证监会行业映射
    industry_map = get_industry_map()

    # 保存缓存
    with open(cache_realtime, "wb") as f:
        pickle.dump(valid, f)
    with open(cache_board, "wb") as f:
        pickle.dump(board_df, f)
    with open(cache_industry, "wb") as f:
        pickle.dump(industry_map, f)
    print(f"  [scraper] 缓存已保存至 {CACHE_DIR}")

    return valid, board_df, industry_map
