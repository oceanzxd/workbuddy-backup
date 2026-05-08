"""
report.py - Markdown 报告生成器 v2
三套完整报告模板：全市场全景 / 板块深度 / 个股评级
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd

DESKTOP = Path("/mnt/c/Users/negan/Desktop")


# ── 工具函数 ────────────────────────────────────────────────

def fmt_pct(v, signed=True):
    try:
        v = float(v)
        if v != v:  # NaN check
            return "N/A"
        sign = "+" if v > 0 and signed else ""
        return f"{sign}{v:.2f}%"
    except:
        return "N/A"


def fmt_mkt(v):
    if v is None: return "N/A"
    try:
        v = float(v)
        if v != v: return "N/A"
        if v >= 1e4: return f"{v/1e4:.2f}万亿"
        if v >= 1: return f"{v:.2f}亿"
        return f"{v:.2f}亿"
    except:
        return "N/A"


def fmt_amt(v):
    if v is None: return "N/A"
    try:
        v = float(v)
        if v != v: return "N/A"
        if v >= 1e8: return f"{v/1e8:.2f}亿"
        if v >= 1e6: return f"{v/1e6:.2f}万"
        return f"{v:.0f}元"
    except:
        return "N/A"


def _bar(count, total, width=20) -> str:
    """生成占比条形图"""
    if total == 0: return "▏" * width
    ratio = count / total
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)

def _sf(v, default=None):
    """安全转float"""
    try:
        f = float(v)
        if f != f:  # NaN check
            return default
        return f
    except:
        return default


def _tb(cols, rows, align="left", numeric_cols=None) -> str:
    """Markdown 表格"""
    if numeric_cols is None:
        numeric_cols = set()
    sep = "---:" if align == "right" else "---"
    hdr = "| " + " | ".join(cols) + " |"
    s_line = "|" + "|".join(f" {sep}" for _ in cols) + "|"
    lines = [hdr, s_line]
    for r in rows:
        cells = []
        for i, v in enumerate(r):
            cells.append(str(v) if v is not None else "N/A")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _score_bar(score: int) -> str:
    """评分转可视化条"""
    if score >= 80: color, label = "🟢", "强势"
    elif score >= 60: color, label = "🟡", "偏强"
    elif score >= 40: color, label = "🔵", "中性"
    elif score >= 20: color, label = "🟠", "偏弱"
    else: color, label = "🔴", "弱势"
    filled = min(10, max(1, score // 10))
    bar = "█" * filled + "░" * (10 - filled)
    return f"{color} {bar} {score}分 ({label})"


def _sentiment_emoji(avg):
    """情绪表情"""
    try:
        v = float(avg)
        if v >= 3: return "🟢 市场普涨"
        if v >= 1: return "🟡 偏多"
        if v >= -1: return "🔵 震荡"
        if v >= -3: return "🟠 偏空"
        return "🔴 市场普跌"
    except:
        return "🔵 震荡"


# ── 1. 全市场全景报告 ─────────────────────────────────────────

def gen_market_report(result: dict, ts: str) -> str:
    m = result
    dist = m.get("pct_dist", {})
    mkt  = m.get("mkt_dist", {})
    boards = m.get("boards", [])

    lines = [
        "# A股市场全景分析报告",
        "",
        f"**生成时间**: {ts}",
        f"**覆盖股票**: {m['total']} 只",
        "",
        "---",
        "",
        "## 一、市场情绪总览",
        "",
        f"| 指标 | 数值 | 说明 |",
        f"|------|------|------|",
        f"| 上涨 | **{m['up']} 只** ({m['up']/m['total']*100:.1f}%) | {_bar(m['up'], m['total'], 30)} |",
        f"| 下跌 | {m['down']} 只 ({m['down']/m['total']*100:.1f}%) | {_bar(m['down'], m['total'], 30)} |",
        f"| 平盘 | {m['flat']} 只 ({m['flat']/m['total']*100:.1f}%) | |",
        f"| 平均涨跌幅 | **{fmt_pct(m['avg_all'])}** | {m['sentiment']} |",
        f"| 中位数涨跌幅 | {fmt_pct(m['med_all'])} | |",
        f"| 涨停 | **{m['zt_count']} 只** | |",
        f"| 跌停 | {m['dt_count']} 只 | |",
        "",
        f"> {_sentiment_emoji(m['avg_all'])}  上涨/下跌比 = {m['up']/max(m['down'],1):.2f}",
        "",
        "### 涨跌幅分布",
        "",
        _tb(
            ["区间", "数量", "占比", "可视化"],
            [
                (label,
                 f"**{count}**" if count > m['total']*0.1 else str(count),
                 f"{count/m['total']*100:.1f}%",
                 _bar(count, m['total'], 25))
                for label, count in sorted(dist.items())
            ]
        ),
        "",
        "---",
        "",
        "## 二、强势板块 Top10",
        "",
        _tb(
            ["板块", "平均涨跌", "上涨/下跌", "领涨股", "领涨幅度"],
            [
                (
                    f"**{b['name']}**",
                    f"**{fmt_pct(b['avg_chg'])}**",
                    f"{b['up']}/{b['down']}",
                    b['lead_name'],
                    fmt_pct(b['lead_chg'])
                )
                for b in m.get("top_boards", [])[:10]
            ]
        ),
        "",
        "### 强势板块 — 每板块 Top3 个股",
        "",
    ]

    for b in m.get("top_boards", [])[:5]:
        lines += [
            f"#### {b['name']} {fmt_pct(b['avg_chg'])}",
            "",
            _tb(
                ["名称", "涨跌幅", "最新价", "总市值"],
                [
                    (s["名称"], fmt_pct(s["涨跌幅(%)"]), f"{s['最新价']:.2f}", fmt_mkt(s.get("总市值(亿)")))
                    for s in b.get("top3", [])
                ]
            ),
            "",
        ]

    lines += [
        "---",
        "",
        "## 三、弱势板块 Top10",
        "",
        _tb(
            ["板块", "平均涨跌", "上涨/下跌", "领涨股", "领涨幅度"],
            [
                (
                    f"**{b['name']}**",
                    f"**{fmt_pct(b['avg_chg'])}**",
                    f"{b['up']}/{b['down']}",
                    b['lead_name'],
                    fmt_pct(b['lead_chg'])
                )
                for b in m.get("bot_boards", [])[:10]
            ]
        ),
        "",
        "### 弱势板块 — 每板块 Top3 个股",
        "",
    ]

    for b in m.get("bot_boards", [])[:5]:
        lines += [
            f"#### {b['name']} {fmt_pct(b['avg_chg'])}",
            "",
            _tb(
                ["名称", "涨跌幅", "最新价", "总市值"],
                [
                    (s["名称"], fmt_pct(s["涨跌幅(%)"]), f"{s['最新价']:.2f}", fmt_mkt(s.get("总市值(亿)")))
                    for s in b.get("top3", [])
                ]
            ),
            "",
        ]

    lines += [
        "---",
        "",
        "## 四、全市场重要个股一览",
        "",
        "### 涨停 Top15",
        "",
        _tb(
            ["名称", "涨跌幅", "最新价", "成交额"],
            [
                (f"**{r['名称']}**", f"**{fmt_pct(r['涨跌幅(%)'])}**",
                 f"{r['最新价']:.2f}", fmt_amt(r.get('成交额(元)', r.get('成交额', 0))))
                for r in m.get("zt_stocks", [])[:15]
            ]
        ),
        "",
        "### 跌停 Top15",
        "",
        _tb(
            ["名称", "涨跌幅", "最新价", "成交额"],
            [
                (r['名称'], fmt_pct(r['涨跌幅(%)']), f"{r['最新价']:.2f}", fmt_amt(r.get('成交额(元)', r.get('成交额', 0))))
                for r in m.get("dt_stocks", [])[:15]
            ]
        ),
        "",
        "### 成交额 Top10（主力资金）",
        "",
        _tb(
            ["名称", "涨跌幅", "最新价", "成交额", "总市值"],
            [
                (f"**{r['名称']}**" if abs(r['涨跌幅(%)']) > 3 else r['名称'],
                 fmt_pct(r['涨跌幅(%)']), f"{r['最新价']:.2f}",
                 f"**{fmt_amt(r.get('成交额(元)', r.get('成交额', 0)))}**", fmt_mkt(r.get("总市值(亿)")))
                for r in m.get("amt_top", [])[:10]
            ]
        ),
        "",
        "### 换手率 Top10（活跃度）",
        "",
        _tb(
            ["名称", "涨跌幅", "最新价", "换手率"],
            [
                (r['名称'], fmt_pct(r['涨跌幅(%)']), f"{r['最新价']:.2f}",
                 f"**{r['换手率(%)']:.2f}%**")
                for r in m.get("turn_top", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 五、市值区间涨跌对比",
        "",
        _tb(
            ["市值区间", "股票数", "平均涨跌幅", "说明"],
            [
                ("> 1000亿（超大盘）", mkt["large"][0], fmt_pct(mkt["large"][1]),
                 "国家队/机构重仓，波动小"),
                ("100-1000亿（中盘）",  mkt["mid"][0],   fmt_pct(mkt["mid"][1]),
                 "主流资金关注，弹性适中"),
                ("< 100亿（小盘）",    mkt["small"][0], fmt_pct(mkt["small"][1]),
                 "游资/散户博弈，波动大"),
            ]
        ),
        "",
        "---",
        "",
        "## 六、全行业涨跌排行榜",
        "",
        _tb(
            ["排名", "行业板块", "平均涨跌", "上涨/下跌", "成分股"],
            [
                (f"**{i+1}**" if b['avg_chg'] > 0 else str(i+1),
                 f"**{b['name']}**" if b['avg_chg'] > 2 else b['name'],
                 fmt_pct(b['avg_chg']),
                 f"{b['up']}/{b['down']}",
                 str(b['n']))
                for i, b in enumerate(boards)
            ]
        ),
        "",
        f"_报告生成时间: {ts} _",
    ]
    return "\n".join(lines)


# ── 2. 板块深度报告 ─────────────────────────────────────────

def gen_sector_report(result: dict, ts: str) -> str:
    r = result
    lines = [
        f"# 板块深度分析报告: {r['board_name']}",
        "",
        f"**生成时间**: {ts}",
        f"**成分股数量**: {r['total_stocks']} 只",
        "",
        "---",
        "",
        "## 一、板块概况",
        "",
        _tb(
            ["维度", "数值", "说明"],
            [
                ("成分股总数", str(r['total_stocks']), ""),
                ("上涨", f"**{r['up']} 只** ({r['up']/max(r['total_stocks'],1)*100:.0f}%)", _bar(r['up'], max(r['total_stocks'],1))),
                ("下跌", f"**{r['down']} 只** ({r['down']/max(r['total_stocks'],1)*100:.0f}%)", _bar(r['down'], max(r['total_stocks'],1))),
                ("平均涨跌幅", f"**{fmt_pct(r['avg_chg'])}**", ""),
                ("中位数涨跌", fmt_pct(r['median_chg']), ""),
                ("波动率(标准差)", f"{r['std_chg']:.3f}", ""),
                ("涨停", f"**{r['zt_count']} 只**", ""),
                ("跌停", f"**{r['dt_count']} 只**", ""),
                ("平均总市值", fmt_mkt(r.get('avg_mkt')), ""),
                ("合计总市值", fmt_mkt(r.get('total_mkt')), ""),
                ("板块情绪", f"**{r['sentiment']}**", ""),
            ]
        ),
        "",
        "---",
        "",
        "## 二、强势股 Top15",
        "",
        _tb(
            ["名称", "涨跌幅", "最新价", "换手率", "总市值"],
            [
                (s["名称"], fmt_pct(s["涨跌幅(%)"]), f"{s['最新价']:.2f}",
                 f"{s.get('换手率(%)', s.get('换手率', 0)):.2f}%", fmt_mkt(s.get("总市值(亿)")))
                for s in r.get("gainers", [])[:15]
            ]
        ),
        "",
        "---",
        "",
        "## 三、弱势股 Top15",
        "",
        _tb(
            ["名称", "涨跌幅", "最新价", "换手率", "总市值"],
            [
                (s["名称"], fmt_pct(s["涨跌幅(%)"]), f"{s['最新价']:.2f}",
                 f"{s.get('换手率(%)', s.get('换手率', 0)):.2f}%", fmt_mkt(s.get("总市值(亿)")))
                for s in r.get("losers", [])[:15]
            ]
        ),
        "",
        "---",
        "",
        "## 四、成交额 Top10",
        "",
        _tb(
            ["名称", "涨跌幅", "成交额", "总市值"],
            [
                (s["名称"], fmt_pct(s["涨跌幅(%)"]),
                 f"**{fmt_amt(s.get('成交额(元)', 0))}**", fmt_mkt(s.get("总市值(亿)")))
                for s in r.get("amt_top", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 五、换手率 Top10（最活跃）",
        "",
        _tb(
            ["名称", "涨跌幅", "最新价", "换手率"],
            [
                (s["名称"], fmt_pct(s["涨跌幅(%)"]), f"{s['最新价']:.2f}",
                 f"**{s.get('换手率(%)', s.get('换手率', 0)):.2f}%**")
                for s in r.get("turn_top", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 六、龙头市值 Top5",
        "",
        _tb(
            ["名称", "涨跌幅", "最新价", "总市值"],
            [
                (s["名称"], fmt_pct(s["涨跌幅(%)"]), f"{s['最新价']:.2f}",
                 fmt_mkt(s.get("总市值(亿)")))
                for s in r.get("mkt_leaders", [])[:5]
            ]
        ),
        "",
        "---",
        "",
        "## 七、板块舆情",
        "",
    ]

    # 舆情
    news_list = r.get("news", [])
    if not news_list:
        lines.append("> 暂无板块舆情数据（可开启 SearXNG 服务以获取更多舆情）")
    else:
        for n in news_list[:5]:
            lines.append(f"- **{n.get('title', '')}**")
            snippet = n.get('snippet', '')
            if snippet:
                lines.append(f"  {snippet[:100]}")

    lines += [
        "",
        "---",
        "",
        "## 八、板块机会票 Top5",
        "",
    ]

    # 板块内机会票
    stars_in_board = r.get("board_star_stocks", [])
    if not stars_in_board:
        lines.append("> 暂无板块内机会票数据（使用 --stars 模式可获取全市场机会票）")
    else:
        lines.append(_tb(
            ["名称", "评分", "涨跌幅", "推荐理由", "核心亮点"],
            [
                (
                    f"**{s['名称']}**",
                    f"**{_sf(s.get('综合评分', 0)):.1f}**",
                    s.get('涨跌幅', 'N/A'),
                    s.get('推荐理由', '—'),
                    (s.get('机会标签', '') or s.get('资金标签', ''))[:50]
                )
                for s in list(stars_in_board[:5])
            ]
        ))

    lines.append("")
    return "\n".join(lines)


def gen_stock_report(result: dict, ts: str) -> str:
    r = result
    if not r:
        return "# 个股分析报告\n\n未找到该股票行情数据。"

    score = r.get("total_score", 50)
    lines = [
        f"# 个股深度分析报告: {r['name']} ({r['symbol']})",
        "",
        f"**生成时间**: {ts}",
        "",
        "---",
        "",
        "## 一、行情快照",
        "",
        _tb(
            ["指标", "数值", "参考"],
            [
                ("股票代码", r["symbol"], ""),
                ("股票名称", f"**{r['name']}**", ""),
                ("所属行业", r.get("industry") or "N/A", ""),
                ("最新价", f"**{r['price']:.2f} 元**", ""),
                ("昨收", f"{r['yest_close']:.2f} 元", ""),
                ("今开", f"{r['open']:.2f} 元", ""),
                ("今高", f"{r['high']:.2f} 元", f"{'突破' if r['price'] >= r['high'] else '未破'}今日高点"),
                ("今低", f"{r['low']:.2f} 元", f"{'跌破' if r['price'] <= r['low'] else '守住'}今日低点"),
                ("涨跌幅", f"**{fmt_pct(r['chg_pct'])}**", ""),
                ("涨跌额", f"{'+' if r['chg_amt']>0 else ''}{r['chg_amt']:.3f} 元", ""),
                ("成交量", f"{int(r['volume']):,} 手", ""),
                ("成交额", fmt_amt(r['amount']), ""),
                ("换手率", f"{r['turnover']:.2f}%" if r.get('turnover') else "N/A", ""),
                ("市盈率TTM", f"{r['pe']:.2f}" if r.get('pe') else "N/A", ""),
                ("总市值", fmt_mkt(r.get('mkt_cap')), ""),
                ("流通市值", fmt_mkt(r.get('float_cap')), ""),
                ("数据时间", r.get('time') or "N/A", ""),
            ]
        ),
        "",
        "---",
        "",
        "## 二、行业对比",
        "",
        _tb(
            ["维度", "数值", "说明"],
            [
                ("所属行业", r.get('industry') or "N/A", ""),
                ("行业平均涨跌幅", fmt_pct(r.get('industry_avg_chg')) if r.get('industry_avg_chg') else "N/A", ""),
                ("相对行业涨跌", f"**{fmt_pct(r.get('above_industry'))}**" if r.get('above_industry') else "N/A",
                 "正=跑赢行业" if (r.get('above_industry') or 0) > 0 else "负=跑输行业"),
                ("行业内排名", f"**{r.get('industry_rank')}**" if r.get('industry_rank') else "N/A",
                 "按涨跌幅排名"),
            ]
        ),
        "",
        "---",
        "",
        "## 三、技术面分析",
        "",
        f"**趋势**: {r.get('trend', 'N/A')}　　"
        f"**技术评分**: {r.get('tech_score', 0)}/10　　"
        f"**支撑位**: {r.get('support')}　　**压力位**: {r.get('resistance')}",
        "",
    ]

    tech_signals = r.get("tech_signals", [])
    if tech_signals:
        lines += ["**技术信号:**"]
        for sig in tech_signals:
            emoji = "🟢" if "多" in sig or "涨" in sig else ("🔴" if "空" in sig or "跌" in sig else "🔵")
            lines.append(f"- {emoji} {sig}")
        lines.append("")

    if r.get("kline"):
        k = r["kline"]
        if k:
            lines += [
                "**近7日K线:**",
                "",
                _tb(
                    ["日期", "前复权收盘", "开盘", "最高", "最低", "成交量"],
                    [
                        (kk.get("日期", kk.get("day","?")),
                         f"{float(kk.get('前复权收盘',0)):.2f}",
                         f"{float(kk.get('开盘',0)):.2f}",
                         f"{float(kk.get('最高',0)):.2f}",
                         f"{float(kk.get('最低',0)):.2f}",
                         f"{int(float(kk.get('成交量',0))):,}手")
                        for kk in k[-7:]
                    ]
                ),
                "",
            ]

    lines += [
        "---",
        "",
        "## 四、基本面要点",
        "",
    ]

    fund_signals = r.get("fundamental_signals", [])
    if fund_signals:
        for sig in fund_signals:
            lines.append(f"- {sig}")
    else:
        lines.append("  暂无基本面数据")

    lines += [
        "",
        f"**基本面评分**: {r.get('fund_score', 0):+d}/±5",
        "",
        "---",
        "",
        "## 五、综合评级",
        "",
        f"### {_score_bar(score)}",
        "",
        f"**操作建议**: **{r.get('action', 'N/A')}**",
        "",
        f"> {r.get('action_detail', '')}",
        "",
        f"**风险等级**: {'🔴 高风险' if r.get('risk_level')=='高' else ('🟡 中风险' if r.get('risk_level')=='中' else '🟢 低风险')}",
        "",
        "---",
        "",
    ]

    # 新闻舆情
    news = r.get("news", [])
    if news:
        lines += [
            "## 六、最新新闻舆情",
            "",
        ]
        for i, item in enumerate(news[:10], 1):
            snippet = item.get('snippet', item.get('content', ''))
            lines.append(
                f"{i}. **{item.get('title', 'N/A')}**\n"
                f"   {snippet[:150]}{'...' if len(snippet)>150 else ''}\n"
                f"   来源: {item.get('engine', item.get('source', '未知'))}　"
                f"[查看原文]({item.get('url', '#')})"
            )
        lines.append("")
    else:
        lines += [
            "## 六、新闻舆情",
            "",
            "> 当前无可用新闻数据，请确保 SearXNG 服务正常运行。",
            "",
        ]

    lines += [
        "---",
        "",
        f"_报告生成时间: {ts} _",
    ]
    return "\n".join(lines)


# ── 主入口 ──────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════
# 4. 机会发现报告
# ═══════════════════════════════════════════════════════════════════

def _fmt_chg(v):
    try:
        # 防御：避免 Series/DataFrame 传入
        if hasattr(v, "shape") and v.shape != ():
            v = v.iloc[0] if hasattr(v, "iloc") else str(v)
        v = float(v)
        if v != v: return "N/A"
        return f"+{v:.2f}%" if v > 0 else f"{v:.2f}%"
    except:
        return str(v) if v is not None else "N/A"


def gen_opportunity_report(result: dict, ts: str) -> str:
    opp = result.get("opportunities", {})
    summary = result.get("summary", "暂无数据")

    lines = [
        "# 个股机会发现报告",
        "",
        f"**生成时间**: {ts}",
        "",
        "---",
        "",
        "## 一、机会总览",
        "",
        summary,
        "",
        "---",
        "",
        "## 二、各类机会详情",
        "",
    ]

    # 优先级展示
    priority = [
        ("涨停股池", "🔴", "当日最强，动能充沛"),
        ("强势股池", "🟡", "近期持续强势，人气高"),
        ("昨日涨停", "🟡", "昨日涨停延续，人气基础好"),
        ("量价齐升", "🟢", "量价配合，上涨健康"),
        ("持续放量", "🟢", "资金持续介入"),
        ("连续上涨", "🟢", "动量延续"),
        ("创新高",   "🟢", "突破历史新高"),
        ("向上突破", "🟢", "技术面突破"),
        ("次新股池", "🔵", "新股溢价效应"),
        ("炸板股池", "⚪", "炸板后关注"),
        ("跌停股池", "🔵", "恐慌情绪参考"),
        ("创新低",   "⚪", "逆向机会参考"),
    ]

    for cat, emoji, tip in priority:
        if cat in opp and not opp[cat].empty:
            df = opp[cat]
            lines.append(f"### {emoji} **{cat}** ({len(df)}只) — {tip}")
            lines.append("")

            # 选择展示列
            show_cols = ["名称", "涨跌幅(%)", "最新价"]
            if "换手率(%)" in df.columns:
                show_cols.append("换手率(%)")
            if "量价齐升天数" in df.columns:
                show_cols.append("量价齐升天数")
            if "连涨天数" in df.columns:
                show_cols.append("连涨天数")
            if "总市值(亿)" in df.columns:
                show_cols.append("总市值(亿)")

            # 取前10（先去重列避免歧义）
            disp = df[[c for c in show_cols if c in df.columns]].head(10)
            # 强制去重：同名列只保留第一个，返回整数位置
            seen, keep_pos = set(), []
            for pos, col in enumerate(disp.columns):
                if col not in seen:
                    seen.add(col)
                    keep_pos.append(pos)
            disp = disp.iloc[:, keep_pos]
            disp_chg = disp.copy()
            if "涨跌幅(%)" in disp_chg.columns:
                # 安全获取列（iloc 避免重复列名歧义）
                chg_idx = disp_chg.columns.get_loc("涨跌幅(%)")
                chg_idx = chg_idx[0] if isinstance(chg_idx, (list, np.ndarray)) else chg_idx
                col = disp_chg.iloc[:, chg_idx]
                disp_chg["涨跌幅(%)"] = col.apply(_fmt_chg)

            col_names = list(disp_chg.columns)
            rows_data = disp_chg.values.tolist()
            # 格式化数值
            for i, row in enumerate(rows_data):
                rows_data[i] = list(row)

            lines.append(_tb(col_names, rows_data))
            lines.append("")
            lines.append("---")
            lines.append("")

    lines.append(f"_报告生成时间: {ts} _")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 5. 科创板报告
# ═══════════════════════════════════════════════════════════════════

def gen_kcb_report(result: dict, ts: str) -> str:
    if "error" in result:
        return f"# 科创板分析报告\n\n❌ {result['error']}"

    lines = [
        "# 科创板全景分析报告",
        "",
        f"**生成时间**: {ts}",
        "",
        "---",
        "",
        "## 一、科创板概况",
        "",
        _tb(
            ["维度", "数值", "说明"],
            [
                ("成分股总数", str(result["total"]), ""),
                ("上涨", f"**{result['up']} 只**", ""),
                ("下跌", f"{result['down']} 只", ""),
                ("平盘", str(result.get("flat", 0)), ""),
                ("平均涨跌幅", f"**{fmt_pct(result['avg_chg'])}**", result["sentiment"]),
                ("中位数涨跌幅", fmt_pct(result["median_chg"]), ""),
                ("涨停", f"**{result['zt_count']} 只**", ""),
                ("跌停", f"{result['dt_count']} 只", ""),
                ("平均市盈率(TTM)", f"{result['pe_avg']}" if result.get("pe_avg") else "N/A", ""),
                ("市盈率中位数", f"{result['pe_med']}" if result.get("pe_med") else "N/A", ""),
                ("PE范围", f"{result.get('pe_low','?')} ~ {result.get('pe_high','?')}", "最低~最高"),
                ("平均市值", f"{result.get('mkt_avg','N/A')}亿", ""),
            ]
        ),
        "",
        "---",
        "",
        "## 二、涨跌幅分布",
        "",
    ]

    dist = result.get("chg_dist", {})
    if dist:
        total = result["total"]
        lines.append(_tb(
            ["区间", "数量", "占比", "可视化"],
            [
                (label, str(count), f"{count/total*100:.1f}%", _bar(count, total, 25))
                for label, count in dist.items()
            ]
        ))
    lines.append("")

    lines += [
        "---",
        "",
        "## 三、强势个股 Top10",
        "",
        _tb(
            ["名称", "代码", "涨跌幅", "最新价", "换手率", "总市值"],
            [
                (s["名称"], s["代码"], fmt_pct(s["涨跌幅(%)"]),
                 f"{s['最新价']}", f"{s['换手率(%)']}%" if s.get("换手率(%)") else "N/A",
                 fmt_mkt(s.get("总市值(亿)")))
                for s in result.get("gainers", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 四、弱势个股 Top10",
        "",
        _tb(
            ["名称", "代码", "涨跌幅", "最新价", "换手率", "总市值"],
            [
                (s["名称"], s["代码"], fmt_pct(s["涨跌幅(%)"]),
                 f"{s['最新价']}", f"{s['换手率(%)']}%" if s.get("换手率(%)") else "N/A",
                 fmt_mkt(s.get("总市值(亿)")))
                for s in result.get("losers", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 五、成交额 Top10",
        "",
        _tb(
            ["名称", "涨跌幅", "成交额", "最新价", "换手率"],
            [
                (s["名称"], fmt_pct(s["涨跌幅(%)"]),
                 f"**{fmt_amt(s['成交额'])}**", f"{s['最新价']}",
                 f"{s['换手率(%)']}%" if s.get("换手率(%)") else "N/A")
                for s in result.get("amt_top", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 六、换手率 Top10（最活跃）",
        "",
        _tb(
            ["名称", "涨跌幅", "换手率", "最新价", "成交额"],
            [
                (s["名称"], fmt_pct(s["涨跌幅(%)"]),
                 f"**{s['换手率(%)']:.2f}%**", f"{s['最新价']}",
                 fmt_amt(s.get("成交额")))
                for s in result.get("turn_top", [])[:10]
            ]
        ),
        "",
        f"_报告生成时间: {ts} _",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 6. 龙虎榜报告
# ═══════════════════════════════════════════════════════════════════

def gen_lhb_report(result: dict, ts: str) -> str:
    if "error" in result:
        return f"# 龙虎榜分析报告\n\n❌ {result['error']}"

    lines = [
        "# 龙虎榜分析报告",
        "",
        f"**生成时间**: {ts}",
        "",
        "---",
        "",
        "## 一、龙虎榜概览",
        "",
        f"> 近 {result['period_days']} 个交易日，共 **{result['total_entries']} 条** 上榜记录，涉及 **{result['total_stocks']} 只** 股票",
        "",
    ]

    # 解读关键词
    kw = result.get("interpretation_kw", {})
    if kw:
        kw_items = " ".join(f"{k}: {v}次" for k, v in kw.items())
        lines += [f"**市场解读词频**: {kw_items}", ""]

    lines += ["---", ""]

    # 机构净买
    inst_top = result.get("inst_buy_top", [])
    if inst_top:
        lines += [
            "## 二、机构溢价效应 Top10",
            "",
            _tb(
                ["代码", "名称", "收盘价", "涨跌幅", "机构数", "解读"],
                [
                    (s["代码"], s["名称"], str(s.get("收盘价", "N/A")),
                     fmt_pct(s.get("涨跌幅(%)", 0)),
                     str(s.get("机构数", "N/A")), s.get("解读", ""))
                    for s in inst_top[:10]
                ]
            ),
            "",
            "---",
            "",
        ]

    # 高频上榜
    hot = result.get("hot_stocks", [])
    if hot:
        lines += [
            "## 三、游资高频上榜 Top10",
            "",
            _tb(
                ["代码", "名称", "上榜次数", "最近上榜", "收盘价", "涨跌幅"],
                [
                    (s["代码"], s["名称"], f"**{s['上榜次数']}**次",
                     str(s.get("最近上榜", "N/A")),
                     str(s.get("收盘价", "N/A")),
                     fmt_pct(s.get("涨跌幅(%)", 0)))
                    for s in hot[:10]
                ]
            ),
            "",
            "---",
            "",
        ]

    # 近期明细
    recent = result.get("recent_entries", [])
    if recent:
        lines += [
            "## 四、近期上榜明细（前30条）",
            "",
            _tb(
                ["代码", "名称", "上榜日期", "涨跌幅", "收盘价", "解读"],
                [
                    (str(r.get("代码", "")).replace("sh", "").replace("sz", ""),
                     r.get("名称", ""),
                     str(r.get("上榜日期", ""))[:10] if r.get("上榜日期") else "N/A",
                     fmt_pct(r.get("涨跌幅(%)", 0)),
                     str(r.get("收盘价", "N/A")),
                     str(r.get("解读", ""))[:30])
                    for r in recent[:30]
                ]
            ),
            "",
        ]

    lines.append(f"_报告生成时间: {ts} _")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 7. 资金流报告
# ═══════════════════════════════════════════════════════════════════

def gen_fund_flow_report(result: dict, ts: str) -> str:
    lines = [
        "# 资金流分析报告",
        "",
        f"**生成时间**: {ts}",
        "",
        "---",
        "",
        "## 一、资金流总览",
        "",
        _tb(
            ["维度", "数值"],
            [
                ("总流入", result.get("total_inflow", "N/A")),
                ("总流出", result.get("total_outflow", "N/A")),
                ("净流入", f"**{result.get('net_flow', 'N/A')}**"),
                ("净流入行业数", f"{result.get('inflow_inds', 0)} 个"),
                ("净流出行业数", f"{result.get('outflow_inds', 0)} 个"),
                ("资金情绪", f"**{result.get('flow_sentiment', 'N/A')}**"),
            ]
        ),
        "",
        "---",
        "",
        "## 二、行业净流入 Top10",
        "",
        _tb(
            ["行业", "涨跌幅", "净流入", "流入资金"],
            [
                (s["行业"], s["涨跌幅"], f"🟢 {s['净流入']}", s.get("流入资金", "N/A"))
                for s in result.get("ind_top10", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 三、行业净流出 Top10",
        "",
        _tb(
            ["行业", "涨跌幅", "净流入", "流出资金"],
            [
                (s["行业"], s["涨跌幅"], f"🔴 {s['净流入']}", s.get("流出资金", "N/A"))
                for s in result.get("ind_bottom10", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 四、概念净流入 Top10",
        "",
        _tb(
            ["概念", "涨跌幅", "净流入"],
            [
                (s["概念"], s["涨跌幅"], f"🟢 {s['净流入']}")
                for s in result.get("con_top10", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        "## 五、北向持股 Top10（沪深股通）",
        "",
        _tb(
            ["代码", "名称", "收盘价", "涨跌幅", "持股比例"],
            [
                (s["代码"], s["名称"], str(s.get("收盘价", "N/A")),
                 s["涨跌幅"], str(s.get("持股比例", "N/A")))
                for s in result.get("hsgt_top10", [])[:10]
            ]
        ),
        "",
        "---",
        "",
        f"_报告生成时间: {ts} _",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 8. 涨停板专项报告
# ═══════════════════════════════════════════════════════════════════

def gen_limit_up_report(result: dict, ts: str) -> str:
    summary = result.get("summary", "暂无数据")
    zt_df = result.get("zt_df")

    lines = [
        "# 涨停板专项分析报告",
        "",
        f"**生成时间**: {ts}",
        "",
        "---",
        "",
        "## 一、涨停板总览",
        "",
        f"> {summary}",
        "",
        "---",
        "",
        "## 二、连板个股",
        "",
    ]

    lianban = result.get("lianban_stocks", [])
    if lianban:
        lines.append(_tb(
            ["代码", "名称", "连板数", "涨跌幅"],
            [
                (s["代码"], s["名称"], f"**{s['连板数']}连板**", _fmt_chg(s.get("涨跌幅", 0)))
                for s in lianban
            ]
        ))
    else:
        lines.append("> 暂无连板数据")

    lines += ["", "---", "", "## 三、涨停股池明细", ""]

    if zt_df is not None and not zt_df.empty:
        cols = [c for c in ["名称", "代码", "涨跌幅(%)", "换手率(%)", "总市值(亿)"] if c in zt_df.columns]
        if not cols:
            cols = list(zt_df.columns[:6])
        disp = zt_df[cols].head(30).copy()
        if "涨跌幅(%)" in disp.columns:
            disp["涨跌幅(%)"] = disp["涨跌幅(%)"].apply(lambda x: f"+{float(x):.2f}%" if float(x) > 0 else f"{float(x):.2f}%")
        lines.append(_tb(cols, disp.values.tolist()))
    else:
        lines.append("> 暂无涨停数据")

    lines += ["", f"_报告生成时间: {ts} _"]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 9. 热门股票报告
# ═══════════════════════════════════════════════════════════════════

def gen_hot_report(result: dict, ts: str) -> str:
    stocks = result.get("hot_stocks", [])
    lines = [
        "# 当日热门股票报告",
        "",
        f"**生成时间**: {ts}",
        f"**股票数量**: {len(stocks)} 只",
        "",
        "---",
        "",
    ]

    if not stocks:
        return "\n".join(lines) + "\n\n> 暂无热门股票数据"

    # 按来源分类
    cats = {}
    for s in stocks:
        cat = s.get("_来源分类", "其他")
        if cat not in cats:
            cats[cat] = []
        cats[cat].append(s)

    for cat, items in cats.items():
        lines.append(f"### {cat} ({len(items)}只)")
        lines.append("")
        cols = [c for c in ["名称", "代码", "涨跌幅(%)", "换手率(%)", "最新价", "总市值(亿)"] if c in items[0]]
        if not cols:
            cols = list(items[0].keys())[:6]
        disp = []
        for item in items[:15]:
            row = []
            for c in cols:
                v = item.get(c, "N/A")
                try:
                    if "涨跌幅" in c and v != "N/A":
                        v = f"+{float(v):.2f}%"
                except:
                    pass
                row.append(str(v))
            disp.append(row)
        lines.append(_tb(cols, disp))
        lines.append("")

    lines.append(f"_报告生成时间: {ts} _")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 星标股票报告
# ═══════════════════════════════════════════════════════════════════

def gen_star_stocks_report(data: dict, ts: str) -> str:
    """
    星标股票综合评级报告
    data keys: stars, stars_df, count, strong, focus, watch, board_dist, summary
    """
    stars = data.get("stars", [])
    strong = data.get("strong", [])
    focus  = data.get("focus",  [])
    watch  = data.get("watch",  [])
    board_dist = data.get("board_dist", [])

    lines = [
        f"# ⭐ 星标股票综合评级",
        f"",
        f"**生成时间**: {ts}",
        f"",
        f"## 📊 综合摘要",
        f"",
        f"{data.get('summary', '暂无数据')}",
        f"",
        f"---",
        f"",
    ]

    # 评分体系说明
    lines += [
        "## 📐 评分体系（5维度，10分制）",
        "",
        "| 维度 | 权重 | 数据来源 | 说明 |",
        "|------|------|----------|------|",
        "| 动量分 | 30% | 涨停/强势/技术选股 | 同时出现在多个正向分类得分更高 |",
        "| 资金分 | 25% | 北向持股/行业资金流/个股资金流 | 资金持续净流入加分 |",
        "| 技术分 | 20% | 创新高/放量/量价齐升 | 技术面强势信号 |",
        "| 市值分 | 15% | 流通市值/换手率/非ST | 50-500亿弹性佳 |",
        "| 板块分 | 15% | 个股所属行业/概念在热门榜排名 | 涨跌幅+资金流综合 |",
        "",
        "---",
        "",
    ]

    # 强烈推荐（综合评分 >= 14）
    if strong:
        lines += [
            "## 🔴 强烈推荐（评分 ≥ 14.0）",
            "",
            "| 代码 | 名称 | 综合评分 | 动量 | 资金 | 技术 | 市值 | 龙虎 | 板块 | 最新价 | 涨跌幅 | 机会标签 |",
            "|------|------|----------|------|------|------|------|------|------|--------|--------|-----------|",
        ]
        for s in strong:
            lines.append(
                f"| {s.get('代码','')} | **{s.get('名称','')}** | "
                f"**{s.get('综合评分',0):.1f}** | "
                f"{s.get('动量分',0):.1f} | {s.get('资金分',0):.1f} | "
                f"{s.get('技术分',0):.1f} | {s.get('市值分',0):.1f} | "
                f"{s.get('龙虎分',0):.1f} | {s.get('板块分',0):.1f} | "
                f"{s.get('最新价','')} | {s.get('涨跌幅','')} | "
                f"{s.get('机会标签','')} |"
            )
        lines.append("")

    # 建议关注（综合评分 9-14）
    if focus:
        lines += [
            "## 🟡 建议关注（评分 9.0 - 14.0）",
            "",
            "| 代码 | 名称 | 综合评分 | 动量 | 资金 | 技术 | 市值 | 龙虎 | 板块 | 最新价 | 涨跌幅 | 机会标签 |",
            "|------|------|----------|------|------|------|------|------|------|--------|--------|-----------|",
        ]
        for s in focus:
            lines.append(
                f"| {s.get('代码','')} | **{s.get('名称','')}** | "
                f"{s.get('综合评分',0):.1f} | "
                f"{s.get('动量分',0):.1f} | {s.get('资金分',0):.1f} | "
                f"{s.get('技术分',0):.1f} | {s.get('市值分',0):.1f} | "
                f"{s.get('龙虎分',0):.1f} | {s.get('板块分',0):.1f} | "
                f"{s.get('最新价','')} | {s.get('涨跌幅','')} | "
                f"{s.get('机会标签','')} |"
            )
        lines.append("")

    # 可观察（综合评分 4-9）
    if watch:
        lines += [
            "## ⚪ 可观察（评分 4.0 - 9.0）",
            "",
            "| 代码 | 名称 | 综合评分 | 动量 | 资金 | 技术 | 市值 | 龙虎 | 板块 | 涨跌幅 | 机会标签 |",
            "|------|------|----------|------|------|------|------|------|------|--------|-----------|",
        ]
        for s in watch[:20]:
            lines.append(
                f"| {s.get('代码','')} | {s.get('名称','')} | "
                f"{s.get('综合评分',0):.1f} | "
                f"{s.get('动量分',0):.1f} | {s.get('资金分',0):.1f} | "
                f"{s.get('技术分',0):.1f} | {s.get('市值分',0):.1f} | "
                f"{s.get('龙虎分',0):.1f} | {s.get('板块分',0):.1f} | "
                f"{s.get('涨跌幅','')} | {s.get('机会标签','')} |"
            )
        lines.append("")

    # 板块分布
    if board_dist:
        lines += [
            "## 🗺️ 机会板块分布",
            "",
            "| 分类 | 出现次数 |",
            "|------|----------|",
        ]
        for tag, cnt in board_dist[:10]:
            lines.append(f"| {tag} | {cnt} |")
        lines.append("")

    # 完整候选池
    if stars:
        lines += [
            "---",
            "",
            "## 📋 完整候选池",
            "",
            f"共 **{len(stars)} 只**，按综合评分降序排列",
            "",
            "| 代码 | 名称 | 评分 | 动量 | 资金 | 技术 | 市值 | 龙虎 | 板块 | 涨跌幅 | 换手率 | 流通市值 |",
            "|------|------|------|------|------|------|------|------|------|--------|--------|----------|",
        ]
        for s in stars:
            lines.append(
                f"| {s.get('代码','')} | {s.get('名称','')} | "
                f"**{s.get('综合评分',0):.1f}** | "
                f"{s.get('动量分',0):.1f} | {s.get('资金分',0):.1f} | "
                f"{s.get('技术分',0):.1f} | {s.get('市值分',0):.1f} | "
                f"{s.get('龙虎分',0):.1f} | {s.get('板块分',0):.1f} | "
                f"{s.get('涨跌幅','')} | {s.get('换手率','')} | "
                f"{s.get('流通市值','')} |"
            )

    lines += [
        "",
        "---",
        "",
        "*本报告综合动量/资金/技术/市值/龙虎榜/板块热度六个维度自动评分，仅供参考，不构成投资建议。*",
    ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 多股深度对比报告
# ═══════════════════════════════════════════════════════════════════

def gen_stock_compare_report(data: dict, ts: str) -> str:
    """
    多股深度对比报告
    data: {
        "_type": "stock_compare",
        "stocks": [{名称, 代码, 最新价, 涨跌幅, 总市值, 流通市值, tech: {...}}, ...],
        "best_for": {"指标名": stock_index, ...},
        "generated_at": "..."
    }
    """
    stocks = data.get("stocks", [])
    best_for = data.get("best_for", {})

    lines = [
        "# 多股深度对比分析",
        "",
        f"**生成时间**: {data.get('generated_at', ts)}",
        f"**对比股票**: {len(stocks)} 只",
        "",
        "---",
        "",
        "## 一、基础行情",
        "",
        "| 名称 | 代码 | 最新价 | 涨跌幅 | 总市值 | 流通市值 |",
        "|------|------|--------|--------|--------|---------|",
    ]

    for s in stocks:
        lines.append(
            f"| {s['名称']} | {s['代码']} | "
            f"{s.get('最新价', 'N/A')} | {s.get('涨跌幅', 'N/A')} | "
            f"{s.get('总市值', 'N/A')} | {s.get('流通市值', 'N/A')} |"
        )

    lines += ["", "---", "", "## 二、技术指标对比", ""]

    # 指标对照表
    tech_header = "| 指标 |" + "".join(f" {s['名称']} |" for s in stocks)
    lines.append(tech_header)
    lines.append("|" + "|".join(["------"] * (len(stocks) + 1)) + "|")

    tech_rows = [
        ("最新价",    "最新价",    "{:.2f}"),
        ("今日涨跌",  "今日涨跌",  "{:+.2f}%"),
        ("MA5",       "MA5",       "{:.2f}"),
        ("MA10",      "MA10",      "{:.2f}"),
        ("MA20",      "MA20",      "{:.2f}"),
        ("MA60",      "MA60",      "{:.2f}"),
        ("RSI(14)",   "RSI",       "{:.1f}"),
        ("KDJ-K",     "KDJ_K",     "{:.1f}"),
        ("KDJ-D",     "KDJ_D",     "{:.1f}"),
        ("KDJ-J",     "KDJ_J",     "{:.1f}"),
        ("MACD",      "MACD",      "{:.2f}"),
        ("DEA",       "DEA",       "{:.2f}"),
        ("MACD柱",    "MACD柱",    "{:.2f}"),
        ("布林上轨",  "BOLL_UPPER","{:.2f}"),
        ("布林中轨",  "BOLL_MID",  "{:.2f}"),
        ("布林下轨",  "BOLL_LOWER","{:.2f}"),
        ("布林位置",  "BOLL_PCT",  "{:.1f}%"),
        ("量比(5/20)", "VOL_RATIO", "{:.2f}"),
        ("近5日涨跌", "近5日",     "{:+.2f}%"),
        ("近10日涨跌","近10日",    "{:+.2f}%"),
        ("近20日涨跌","近20日",    "{:+.2f}%"),
        ("综合评分",  "综合评分",  "{:.0f}"),
    ]

    def fmt_val(s, key, fmt):
        val = s.get("tech", {}).get(key)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return "N/A"
        return fmt.format(val)

    for label, key, fmt in tech_rows:
        row = f"| {label} |"
        best_idx = best_for.get(key)
        for i, s in enumerate(stocks):
            v = fmt_val(s, key, fmt)
            mark = " **▲**" if i == best_idx else ""
            row += f" {v}{mark} |"
        lines.append(row)

    lines += ["", "  > **▲** 标记表示该指标最优", "", "---", "", "## 三、综合评分与优势"]

    # 按评分排序
    sorted_stocks = sorted(
        enumerate(stocks),
        key=lambda x: x[1].get("tech", {}).get("综合评分", 0),
        reverse=True
    )

    for rank, (idx, s) in enumerate(sorted_stocks, 1):
        tech = s.get("tech", {})
        tags = tech.get("优势标签", [])
        score = tech.get("综合评分", 0)
        rec = "🥇 强烈推荐" if rank == 1 else "🥈 建议关注" if rank == 2 else "⚪ 观察"
        lines += [
            f"### {rank}. {s['名称']} ({s['代码']}) — {rec}",
            "",
            f"- 综合评分: **{score}分**",
            f"- 优势标签: {' / '.join(tags) if tags else '无明显优势'}",
            f"- 今日: {s.get('涨跌幅','N/A')}  总市值: {s.get('总市值','N/A')}",
            f"- RSI: {tech.get('RSI','N/A')} | MACD柱: {tech.get('MACD柱','N/A')} | "
            f"KDJ-J: {tech.get('KDJ_J','N/A')} | 布林位置: {tech.get('BOLL_PCT','N/A')}%",
            "",
        ]

    lines += [
        "---",
        "",
        "*本报告基于历史技术指标分析，仅供参考，不构成投资建议。*",
    ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 主入口（扩展支持新类型）
# ═══════════════════════════════════════════════════════════════════

def gen_report(analysis_result: dict, report_type: str, output_path: str = None) -> str:
    """
    生成并保存 Markdown 报告
    report_type: 'market' | 'sector' | 'stock' | 'opportunity' | 'kcb' | 'lhb' | 'fund_flow' | 'limit_up' | 'hot' | 'stock_compare'
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    generators = {
        "market":          gen_market_report,
        "sector":         gen_sector_report,
        "stock":           gen_stock_report,
        "opportunity":     gen_opportunity_report,
        "kcb":             gen_kcb_report,
        "lhb":             gen_lhb_report,
        "fund_flow":       gen_fund_flow_report,
        "limit_up":        gen_limit_up_report,
        "hot":             gen_hot_report,
        "star_stocks":     gen_star_stocks_report,
        "stock_compare":   gen_stock_compare_report,
    }

    generator = generators.get(report_type)
    if generator:
        content = generator(analysis_result, ts)
    else:
        content = f"# 分析报告\n\n{analysis_result}"

    if output_path:
        DESKTOP.mkdir(parents=True, exist_ok=True)
        path = DESKTOP / output_path if "/" not in output_path else Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    return content