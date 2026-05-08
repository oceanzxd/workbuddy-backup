#!/usr/bin/env python3
"""
main.py - A股行情分析 CLI 入口 v3
支持全市场/板块/个股/机会发现/科创板/龙虎榜/资金流六大分析模式

用法:
  python main.py                           # 全市场全景（默认）
  python main.py --sector 有色金属        # 板块深度分析
  python main.py --stock 601899 --kline   # 个股分析（含K线）
  python main.py --opportunity             # 机会发现（技术选股+涨跌停）
  python main.py --kcb                      # 科创板全景
  python main.py --lhb                      # 龙虎榜分析
  python main.py --fund-flow               # 资金流分析
  python main.py --hot                      # 当日热门股票
  python main.py --boards                   # 列出所有板块
  python main.py --refresh                   # 强制刷新缓存
"""

# ── 必须在任何 akshare/requests 导入之前清除代理 ──────────────────────────────
import sys as _sys, os as _os
for _k in list(_os.environ.keys()):
    if 'proxy' in _k.lower():
        _os.environ.pop(_k)
try:
    import requests as _req
    # Patch Session.send（akshare 用 session.get/post 走的路径）
    _orig_send = _req.Session.send
    def _no_proxy_send(self, request, **kw):
        kw.pop('proxies', None)
        return _orig_send(self, request, **kw)
    _req.Session.send = _no_proxy_send
    # Patch requests.api（akshare 有些接口用 requests.get）
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
# ─────────────────────────────────────────────────────────────────────────────

import sys
import argparse
import time
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper import fetch_data
from sector_map import filter_by_board, list_boards
from analyzer import analyze_market, analyze_sector, analyze_stock, analyze_stock_comparison, compare_stocks
from report import gen_report

# 新模块
try:
    from opportunity import find_opportunities, get_hot_stocks, analyze_limit_up, summarize_opportunities
    OPPORTUNITY_ENABLED = True
except ImportError:
    OPPORTUNITY_ENABLED = False

try:
    from kcb import analyze_kcb, get_kcb_spot, get_kcb_kline, analyze_kcb_stock
    KCB_ENABLED = True
except ImportError:
    KCB_ENABLED = False

try:
    from lhb import analyze_lhb, get_lhb_detail, get_lhb_statistics
    LHB_ENABLED = True
except ImportError:
    LHB_ENABLED = False

try:
    from fund_flow import analyze_fund_flow, get_industry_flow, get_concept_flow, get_hsgt_hold
    FUND_FLOW_ENABLED = True
except ImportError:
    FUND_FLOW_ENABLED = False

try:
    from news_search import search_stock_news, search_sector_news, search_market_news, check_searxng
    NEWS_ENABLED = True
except ImportError:
    NEWS_ENABLED = False
    print("  [WARN] news_search.py 未找到，新闻功能将不可用")

# 东财资金流（基于 aiohttp，并发高性能）
try:
    from capital_flow import CapitalFlowAnalyzer, StockFlowResult
    CAPITAL_FLOW_ENABLED = True
except ImportError:
    CAPITAL_FLOW_ENABLED = False

# 轻量回测引擎
try:
    from strategy import SimpleBacktestEngine
    STRATEGY_ENABLED = True
except ImportError:
    STRATEGY_ENABLED = False

try:
    from star_stocks import find_star_stocks, get_star_report, analyze_star_stock
    STARS_ENABLED = True
except ImportError:
    STARS_ENABLED = False
    print("  [WARN] star_stocks.py 未找到，星标股票功能不可用")

DESKTOP = "/mnt/c/Users/negan/Desktop"


def _clean_reports():
    import glob
    reports_dir = "/tmp/stock_skill/reports"
    for f in glob.glob(f"{reports_dir}/*.md"):
        try:
            os.remove(f)
        except Exception:
            pass


def ts_filename(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"


def save_report(content: str, prefix: str, silent: bool = False) -> Path:
    if silent:
        return Path(f"silent_{prefix}.md")
    fname = ts_filename(prefix)
    path = Path(f"{DESKTOP}/{fname}")
    path.write_text(content, encoding="utf-8")
    return path


def _clear_proxy():
    """清除代理环境变量 + 强制 patch requests.Session，确保 akshare 直连"""
    for k in list(os.environ.keys()):
        if 'proxy' in k.lower():
            os.environ.pop(k, None)
    # 必须在任何模块导入 akshare 之前，把 requests.Session 替换为无代理版本
    try:
        import requests
        _orig_init = requests.Session.__init__
        def _no_proxy_init(self, *a, **kw):
            _orig_init(self, *a, **kw)
            self.trust_env = False
        requests.Session.__init__ = _no_proxy_init
    except Exception:
        pass


def main():
    _clear_proxy()
    parser = argparse.ArgumentParser(description="A股行情分析工具 v3")
    parser.add_argument("--sector",     help="板块名称，如：'有色金属'")
    parser.add_argument("--stock",      help="股票代码，支持逗号分隔多只")
    parser.add_argument("--kline",       action="store_true", help="个股分析时同时获取K线（近320日）")
    parser.add_argument("--refresh",     action="store_true", help="强制刷新数据缓存")
    parser.add_argument("--boards",      action="store_true", help="列出所有可用板块")
    parser.add_argument("--no-news",     action="store_true", help="跳过新闻搜索（快速模式）")
    parser.add_argument("--silent",       action="store_true", help="静默模式：不保存报告到桌面")
    # 新增模式
    parser.add_argument("--opportunity", action="store_true", help="机会发现：技术选股+涨跌停")
    parser.add_argument("--hot",         action="store_true", help="当日热门股票（涨停+强势+量价）")
    parser.add_argument("--kcb",         action="store_true", help="科创板全景分析")
    parser.add_argument("--lhb",         action="store_true", help="龙虎榜分析")
    parser.add_argument("--fund-flow",   action="store_true", help="资金流分析（行业+概念+北向）")
    parser.add_argument("--limit-up",    action="store_true", help="涨停板专项分析")
    parser.add_argument("--stars",        action="store_true", help="综合星标股票（多维度评分）")
    args = parser.parse_args()

    print("=" * 62)
    print("  A股行情分析系统 v3")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 62)

    # ── 新闻服务状态 ───────────────────────────────────────────
    if NEWS_ENABLED and not args.no_news:
        print("\n[0/3] 检查新闻服务...")
        if check_searxng():
            print("  SearXNG: ✅ 在线")
        else:
            print("  SearXNG: ⚠️  未运行（新闻功能将跳过）")
    else:
        print("\n[0/3] 新闻服务: 跳过（--no-news 模式）")

    t_start = time.time()

    # ═══════════════════════════════════════════════════════════
    # 模式：星标股票
    # ═══════════════════════════════════════════════════════════
    if args.stars:
        if not STARS_ENABLED:
            print("\n  [ERROR] star_stocks.py 未找到")
            return
        print("\n[1/2] 综合扫描 + 多维度评分...")
        t0 = time.time()
        report_data = get_star_report(top_n=5)
        print(f"  完成，耗时 {time.time()-t0:.1f}s")

        stars_df = report_data.get("stars_df")
        if stars_df is None or stars_df.empty:
            print(f"\n  ⚠️ 今日未发现综合评分≥6的星标股票")
            return

        print(f"\n  {report_data.get('summary', '')}")

        # 分层展示
        strong = [s for s in report_data["stars"] if s.get("综合评分", 0) >= 14.0]
        focus  = [s for s in report_data["stars"] if 9.0 <= s.get("综合评分", 0) < 14.0]
        watch  = [s for s in report_data["stars"] if 4.0 <= s.get("综合评分", 0) < 9.0]

        if strong:
            print(f"\n  🔴 强烈推荐 ({len(strong)}只)：")
            for s in strong[:10]:
                print(f"    {s['名称']}({s['代码']}) {s.get('涨跌幅','')} 评分{s['综合评分']:.1f} | {s.get('机会标签','')}")
        if focus:
            print(f"\n  🟡 建议关注 ({len(focus)}只)：")
            for s in focus[:10]:
                print(f"    {s['名称']}({s['代码']}) {s.get('涨跌幅','')} 评分{s['综合评分']:.1f} | {s.get('机会标签','')}")
        if watch:
            print(f"\n  ⚪ 可观察 ({len(watch)}只)：")
            for s in watch[:10]:
                print(f"    {s['名称']}({s['代码']}) {s.get('涨跌幅','')} 评分{s['综合评分']:.1f} | {s.get('机会标签','')}")

        print("\n[2/2] 生成报告...")
        content = gen_report(report_data, "star_stocks", None)
        path = save_report(content, "星标股票综合评级")
        print(f"\n  📄 报告已生成: {path}")
        print(f"\n  ✅ 完成，耗时 {time.time()-t_start:.1f}s")
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：板块列表
    # ═══════════════════════════════════════════════════════════
    if args.boards:
        print("\n[1/1] 加载行情数据...")
        realtime_df, board_df, industry_map = fetch_data(use_cache=True)
        boards = list_boards(board_df)
        print(f"\n  可分析板块 ({len(boards)} 个)：")
        for i, b in enumerate(sorted(boards), 1):
            row = board_df[board_df["板块名称"] == b]
            pct = row["涨跌幅(%)"].values[0] if not row.empty else 0
            sign = "+" if pct > 0 else ""
            print(f"  {i:2d}. {b:<12s}  {sign}{pct:.2f}%")
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：机会发现
    # ═══════════════════════════════════════════════════════════
    if args.opportunity:
        if not OPPORTUNITY_ENABLED:
            print("\n  [ERROR] opportunity.py 未找到")
            return
        print("\n[1/2] 扫描全市场交易机会...")
        t0 = time.time()
        opp = find_opportunities()
        print(f"  扫描完成，耗时 {time.time()-t0:.1f}s")
        summary = summarize_opportunities(opp)
        print(f"\n{summary}")

        print("\n[2/2] 生成报告...")
        content = gen_report(
            {"opportunities": opp, "summary": summary},
            "opportunity",
            None  # 内部生成
        )
        path = save_report(content, "个股机会发现")
        print(f"\n  📄 报告已生成: {path}")
        print(f"\n  ✅ 完成，耗时 {time.time()-t_start:.1f}s")
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：热门股票
    # ═══════════════════════════════════════════════════════════
    if args.hot:
        if not OPPORTUNITY_ENABLED:
            print("\n  [ERROR] opportunity.py 未找到")
            return
        print("\n[1/2] 扫描热门股票...")
        t0 = time.time()
        hot = get_hot_stocks(limit=30)
        print(f"  完成，耗时 {time.time()-t0:.1f}s")

        if hot.empty:
            print("  未发现热门股票（可能今日非交易日或数据不可用）")
            return

        print(f"\n  当日热门股票 Top{len(hot)}：")
        for i, (_, row) in enumerate(hot.iterrows(), 1):
            name = row.get("名称", row.get("股票简称", "?"))
            code = row.get("代码", "?")
            chg = row.get("涨跌幅(%)", row.get("涨跌幅", "?"))
            cat = row.get("_来源分类", "")
            try:
                chg_str = f"+{float(chg):.2f}%" if chg != "?" else ""
            except:
                chg_str = ""
            print(f"  {i:2d}. {name}({code}) {chg_str}  [{cat}]")

        print("\n[2/2] 生成报告...")
        content = gen_report(
            {"hot_stocks": hot.to_dict("records")},
            "hot",
            None
        )
        path = save_report(content, "当日热门股票")
        print(f"\n  📄 报告已生成: {path}")
        print(f"\n  ✅ 完成，耗时 {time.time()-t_start:.1f}s")
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：涨停专项分析
    # ═══════════════════════════════════════════════════════════
    if args.limit_up:
        if not OPPORTUNITY_ENABLED:
            print("\n  [ERROR] opportunity.py 未找到")
            return
        print("\n[1/2] 分析涨停板情况...")
        t0 = time.time()
        result = analyze_limit_up()
        print(f"  完成，耗时 {time.time()-t0:.1f}s")
        print(f"\n  {result.get('summary', '')}")

        zt = result.get("zt_df")
        if zt is not None and not zt.empty:
            print(f"\n  涨停股列表（前20只）：")
            for i, (_, row) in enumerate(zt.head(20).iterrows(), 1):
                name = row.get("名称", "?")
                code = row.get("代码", "?")
                chg = row.get("涨跌幅(%)", row.get("涨跌幅", "?"))
                try:
                    chg_str = f"+{float(chg):.2f}%"
                except:
                    chg_str = str(chg)
                print(f"  {i:2d}. {name}({code}) {chg_str}")

        print("\n[2/2] 生成报告...")
        content = gen_report(result, "limit_up", None)
        path = save_report(content, "涨停板分析")
        print(f"\n  📄 报告已生成: {path}")
        print(f"\n  ✅ 完成，耗时 {time.time()-t_start:.1f}s")
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：科创板
    # ═══════════════════════════════════════════════════════════
    if args.kcb:
        if not KCB_ENABLED:
            print("\n  [ERROR] kcb.py 未找到")
            return
        print("\n[1/2] 获取科创板数据...")
        t0 = time.time()
        result = analyze_kcb()
        print(f"  完成，耗时 {time.time()-t0:.1f}s")

        if "error" in result:
            print(f"\n  [ERROR] {result['error']}")
            return

        print(f"\n  科创板概况：{result['total']}只 | 均涨幅 {result['avg_chg']:+.3f}% | "
              f"涨停 {result['zt_count']} | 跌停 {result['dt_count']} | PE均值 {result['pe_avg']}")
        print(f"  情绪：{result['sentiment']}")

        print("\n[2/2] 生成报告...")
        content = gen_report(result, "kcb", None)
        path = save_report(content, "科创板全景分析")
        print(f"\n  📄 报告已生成: {path}")
        print(f"\n  ✅ 完成，耗时 {time.time()-t_start:.1f}s")
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：龙虎榜
    # ═══════════════════════════════════════════════════════════
    if args.lhb:
        if not LHB_ENABLED:
            print("\n  [ERROR] lhb.py 未找到")
            return
        print("\n[1/2] 获取龙虎榜数据...")
        t0 = time.time()
        result = analyze_lhb(days=10)
        print(f"  完成，耗时 {time.time()-t0:.1f}s")

        if "error" in result:
            print(f"\n  [ERROR] {result['error']}")
            return

        print(f"\n  {result.get('summary', '')}")
        if result.get("hot_stocks"):
            print("  近期高频上榜：")
            for s in result["hot_stocks"][:5]:
                print(f"    {s['名称']}({s['代码']}) 上榜{s['上榜次数']}次")

        print("\n[2/2] 生成报告...")
        content = gen_report(result, "lhb", None)
        path = save_report(content, "龙虎榜分析")
        print(f"\n  📄 报告已生成: {path}")
        print(f"\n  ✅ 完成，耗时 {time.time()-t_start:.1f}s")
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：资金流
    # ═══════════════════════════════════════════════════════════
    if args.fund_flow:
        if not FUND_FLOW_ENABLED:
            print("\n  [ERROR] fund_flow.py 未找到")
            return
        print("\n[1/2] 获取资金流数据...")
        t0 = time.time()
        result = analyze_fund_flow()
        print(f"  完成，耗时 {time.time()-t0:.1f}s")

        print(f"\n  总流入 {result.get('total_inflow')} | 总流出 {result.get('total_outflow')} | "
              f"净流入 {result.get('net_flow')}")
        print(f"  资金情绪：{result.get('flow_sentiment')}")

        print("\n  行业净流入Top5：")
        for item in result.get("ind_top10", [])[:5]:
            print(f"    {item['行业']} {item['净流入']} ({item['涨跌幅']})")

        print("\n[2/2] 生成报告...")
        content = gen_report(result, "fund_flow", None)
        path = save_report(content, "资金流分析")
        print(f"\n  📄 报告已生成: {path}")
        print(f"\n  ✅ 完成，耗时 {time.time()-t_start:.1f}s")
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：个股（必须在最后，因为可能和上面模式冲突）
    # ═══════════════════════════════════════════════════════════
    if args.stock:
        stock_names = [s.strip() for s in args.stock.split(",")]
        print(f"\n[1/3] 加载行情数据...")
        t0 = time.time()
        realtime_df, board_df, industry_map = fetch_data(use_cache=True)
        print(f"  加载完成，覆盖 {len(realtime_df)} 只股票，耗时 {time.time()-t0:.1f}s")

        symbols_out = []
        for name in stock_names:
            row = realtime_df[realtime_df["名称"] == name]
            if row.empty:
                code = name if name.startswith(("sh", "sz")) else None
                if not code:
                    code = f"sh{name}" if name.startswith(("6", "68")) else f"sz{name}"
                row = realtime_df[realtime_df["代码"] == code]
            if row.empty:
                print(f"  [WARN] 未找到: {name}")
                continue
            sym   = row.iloc[0]["代码"]
            sname = row.iloc[0]["名称"]
            print(f"  ✅ {sname} ({sym})")
            symbols_out.append(sym)

        if not symbols_out:
            print("\n  [ERROR] 未找到任何有效股票")
            return

        if len(symbols_out) == 1:
            sym, sname = symbols_out[0], realtime_df[realtime_df["代码"] == symbols_out[0]].iloc[0]["名称"]

            kline_data = []
            if args.kline:
                print("  [2/3] 获取K线...")
                try:
                    from scraper import get_kline
                    kline_data = get_kline(sym, days=320)
                    print(f"        K线获取完成: {len(kline_data)} 条")
                except Exception as e:
                    print(f"        K线获取失败: {e}")

            news = []
            if NEWS_ENABLED and not args.no_news:
                print("  [3/4] 获取新闻舆情...")
                try:
                    news = search_stock_news(sname, sym, n=10)
                    print(f"        新闻获取完成: {len(news)} 条")
                except Exception as e:
                    print(f"        新闻获取失败: {e}")

            print("  [4/4] 生成报告...")
            result = analyze_stock(
                sym, sname, realtime_df,
                kline_data=kline_data,
                stock_industry_map=industry_map,
                board_df=board_df,
                news=news
            )
            if result:
                fname = ts_filename(f"个股分析_{sname}")
                path = save_report(
                    gen_report(result, "stock", None),
                    f"个股分析_{sname}",
                    silent=args.silent
                )
                print(f"\n  📄 报告已生成: {path}")

        else:
            # ── 多股深度对比（K线 + 技术指标）──────────────────────
            print(f"\n  === {len(symbols_out)} 只股票深度对比 ===")
            from scraper import get_kline
            stock_results = []
            for sym in symbols_out:
                row = realtime_df[realtime_df["代码"] == sym].iloc[0]
                name = row["名称"]
                kline_data = []
                if args.kline:
                    try:
                        kline_data = get_kline(sym, days=320)
                        print(f"        {name}: K线 {len(kline_data)} 条")
                    except Exception as e:
                        print(f"        {name}: K线获取失败 {e}")
                stock_results.append({
                    "symbol": sym, "name": name,
                    "realtime": row, "kline": kline_data,
                })

            # 调用深度对比分析
            cmp_result = analyze_stock_comparison(stock_results, realtime_df)
            content = gen_report(cmp_result, "stock_compare", None)
            path = save_report(content, "多股深度对比分析")
            print(f"\n  📄 报告已生成: {path}")

        print(f"\n  ✅ 分析完成，耗时 {time.time()-t_start:.1f}s")
        _clean_reports()
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：板块分析
    # ═══════════════════════════════════════════════════════════
    if args.sector:
        board_name = args.sector
        print(f"\n[1/3] 加载行情数据...")
        t0 = time.time()
        realtime_df, board_df, industry_map = fetch_data(use_cache=True)
        print(f"  加载完成，覆盖 {len(realtime_df)} 只股票，耗时 {time.time()-t0:.1f}s")

        print(f"\n[2/3] 板块: {board_name}")
        print("  [2/3] 获取板块成分股...")
        from sector_map import filter_by_board, get_sina_board_stocks
        sector_df = filter_by_board(board_name, realtime_df, industry_map)
        if sector_df.empty:
            symbols = get_sina_board_stocks(board_name)
            if symbols:
                sector_df = realtime_df[realtime_df["代码"].isin(symbols)]
                print(f"  通过新浪成分接口找到 {len(sector_df)} 只")
            else:
                print(f"  [WARN] 未找到板块 '{board_name}' 的成分股数据")
                return

        print(f"  成分股: {len(sector_df)} 只")

        news = []
        if NEWS_ENABLED and not args.no_news:
            print("  [3/4] 获取新闻舆情...")
            try:
                news = search_sector_news(board_name, n=8)
                print(f"        新闻获取完成: {len(news)} 条")
            except Exception as e:
                print(f"        新闻获取失败: {e}")

        # 板块机会票：从星标池中过滤属于该板块的股票（轻量版，15s超时）
        board_star_stocks = []
        try:
            from star_stocks import find_star_stocks
            import threading

            _stars_df = [None]

            def _scan_stars():
                try:
                    _stars_df[0] = find_star_stocks(top_n=30, use_cache=True)
                except Exception:
                    _stars_df[0] = None

            t = threading.Thread(target=_scan_stars, daemon=True)
            t.start()
            t.join(timeout=15)

            if _stars_df[0] is not None and not _stars_df[0].empty:
                stars_df = _stars_df[0]
                # 提取星标股的代码（无前缀）
                star_codes = set(stars_df["代码"].astype(str).str.zfill(6).tolist())
                # 板块成分股代码
                sector_codes = set(sector_df["代码"].astype(str).str.zfill(6).tolist())
                # 取交集
                matched = star_codes & sector_codes
                if matched:
                    board_stars = stars_df[
                        stars_df["代码"].astype(str).str.zfill(6).isin(matched)
                    ].head(5)
                    board_star_stocks = board_stars.to_dict("records")
                    print(f"        板块机会票: 找到 {len(board_star_stocks)} 只")
                else:
                    # 没有交集时，用板块内自身数据评分 Top5
                    if not sector_df.empty:
                        chg_col = next((c for c in sector_df.columns if "涨跌幅" in c), None)
                        if chg_col:
                            scored = sector_df.copy()
                            scored["_score"] = scored[chg_col].abs()
                            top5 = scored.nlargest(5, "_score")
                            for _, row in top5.iterrows():
                                p_col = next((c for c in sector_df.columns if "最新价" in c or "现价" in c), None)
                                board_star_stocks.append({
                                    "代码": str(row.get("代码", "")).zfill(6),
                                    "名称": row.get("名称", row.get("股票名称", "")),
                                    "综合评分": 5.0,
                                    "涨跌幅": f"{row.get(chg_col, 0):.2f}%",
                                    "推荐理由": "板块强势股",
                                    "机会标签": "板块内强势",
                                    "最新价": row.get(p_col, ""),
                                })
        except Exception as e:
            print(f"        板块机会票获取失败: {e}")

        print("  [4/4] 生成报告...")
        result = analyze_sector(board_name, sector_df, board_df, industry_map, news=news)
        result["board_star_stocks"] = board_star_stocks

        path = save_report(gen_report(result, "sector", None), f"板块深度分析_{board_name}")
        print(f"\n  📄 报告已生成: {path}")
        print(f"\n  ✅ 分析完成，耗时 {time.time()-t_start:.1f}s")
        _clean_reports()
        return

    # ═══════════════════════════════════════════════════════════
    # 模式：全市场（默认）
    # ═══════════════════════════════════════════════════════════
    print("\n[1/2] 加载行情数据...")
    t0 = time.time()
    realtime_df, board_df, industry_map = fetch_data(use_cache=True)
    print(f"  加载完成，覆盖 {len(realtime_df)} 只股票，耗时 {time.time()-t0:.1f}s")

    print("\n[2/2] 执行全市场分析...")
    t1 = time.time()
    result = analyze_market(realtime_df, board_df, industry_map)
    path = save_report(gen_report(result, "market", None), "A股市场全景分析报告")
    print(f"\n  📄 报告已生成: {path}")
    print(f"\n  ✅ 分析完成，耗时 {time.time()-t1:.1f}s")
    print(f"\n  总耗时: {time.time()-t_start:.1f}s")
    _clean_reports()


if __name__ == "__main__":
    main()
