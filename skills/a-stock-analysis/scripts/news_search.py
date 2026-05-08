"""
news_search.py - A股新闻舆情搜索
主数据源：akshare stock_news_em（实时财经新闻）
备数据源：SearXNG（需配置好搜索引擎）
"""
import time
import warnings
import requests
import re

warnings.filterwarnings("ignore")

# ── 配置 ────────────────────────────────────────────────────────────────────
SEARXNG_BASE = "http://localhost:8080"
TIMEOUT = 8
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

_ak = None

def _akshare():
    global _ak
    if _ak is None:
        import akshare as a
        _ak = a
    return _ak

# ── akshare 主数据源 ───────────────────────────────────────────────────────
def _fetch_news_em(n: int = 50) -> list[dict]:
    """抓取东财实时财经新闻"""
    try:
        ak = _akshare()
        df = ak.stock_news_em()
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            title = str(row.get("新闻标题", "")).strip()
            content = str(row.get("新闻内容", ""))[:200]
            time_str = str(row.get("发布时间", ""))
            source = str(row.get("文章来源", ""))
            keyword = str(row.get("关键词", ""))
            if title and title not in ("nan", "None"):
                results.append({
                    "title": title,
                    "snippet": re.sub(r"\s+", " ", content).strip()[:150],
                    "url": "",
                    "source": source,
                    "time": time_str[:16] if time_str else "",
                    "_kw": keyword,
                })
        return results
    except Exception:
        return []


# ── SearXNG 备数据源 ─────────────────────────────────────────────────────────
def _search_searxng(query: str, n: int = 5) -> list[dict]:
    """通过 SearXNG 搜索（需启用搜索引擎）"""
    params = {
        "q": query,
        "format": "json",
        "safesearch": "1",
        "language": "zh",
        "time_range": "month",
    }
    try:
        r = requests.get(
            f"{SEARXNG_BASE}/search",
            params=params,
            timeout=TIMEOUT,
            proxies={"http": None, "https": None},
            headers={"User-Agent": UA},
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    results = []
    seen = set()
    for item in data.get("results", []):
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", ""))
        if not title or title.lower() in seen:
            continue
        if _is_junk(title, str(item.get("content", "")), url):
            continue
        seen.add(title.lower())
        results.append({
            "title": title,
            "snippet": re.sub(r"\s+", " ", str(item.get("content", ""))[:150]).strip(),
            "url": url,
            "source": item.get("engine", ""),
            "time": item.get("publishedDate", "") or "",
            "_kw": "",
        })
        if len(results) >= n:
            break
    return results


def _is_junk(title: str, snippet: str, url: str) -> bool:
    url_l = url.lower()
    for d in ["baidu.com", "360.cn", "sogou.com", "mopui", "91fuli", "t66y", "1024"]:
        if d in url_l:
            return True
    return False


# ── 智能过滤 ────────────────────────────────────────────────────────────────
def _filter_by_keywords(news: list[dict], keywords: list[str],
                         score_boost_keywords: list[str] = None) -> list[dict]:
    """
    按关键词过滤并排序。
    keywords: 必须全部匹配的词（交集）
    score_boost_keywords: 匹配则加分
    """
    sb = score_boost_keywords or []
    scored = []
    for n in news:
        t = n["title"].replace("板块", "").replace("概念", "").replace("行业", "")
        # 计算匹配度
        score = 0
        for kw in keywords:
            kw = kw.replace("板块", "").replace("概念", "").replace("行业", "").strip()
            if not kw:
                continue
            if kw in t:
                score += 3
            if kw in n.get("snippet", ""):
                score += 1
        for kw in sb:
            if kw in n["title"]:
                score += 2
        if any(kw in n["title"] for kw in ["研报", "机构评级", "深度报告"]):
            score -= 1
        if score > 0:
            scored.append((score, n))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in scored]


# ── 公开 API ────────────────────────────────────────────────────────────────
def check_searxng() -> bool:
    """检查 SearXNG 是否可用（通过实际搜索验证）"""
    try:
        r = requests.get(
            f"{SEARXNG_BASE}/search",
            params={"q": "test", "format": "json", "safesearch": "1"},
            timeout=6,
            proxies={"http": None, "https": None},
            headers={"User-Agent": UA},
        )
        if r.status_code == 200:
            data = r.json()
            return "results" in data
        return False
    except Exception:
        return False


def search_stock_news(name: str, code: str, n: int = 8) -> list[dict]:
    """搜索个股新闻舆情"""
    code6 = code.replace("sh", "").replace("sz", "").zfill(6) if code else ""
    news = _fetch_news_em(n=80)
    if not news:
        return []

    # 东财新闻按关键词匹配
    results = _filter_by_keywords(news, [name, code6] if code6 else [name],
                                   score_boost_keywords=["业绩", "涨停", "机构", "利好"])
    return results[:n]


def search_sector_news(board_name: str, n: int = 8) -> list[dict]:
    """搜索板块新闻舆情"""
    board_base = board_name.replace("板块", "").replace("概念", "").replace("行业", "").strip()
    news = _fetch_news_em(n=80)
    if not news:
        return []

    # 板块关键词：板块名、去板块后缀名
    keywords = [board_name, board_base]
    # 东财新闻通常标题含板块名
    results = _filter_by_keywords(
        news, keywords,
        score_boost_keywords=["政策", "利好", "业绩", "涨价", "订单", "扩产", "突破"]
    )
    return results[:n]


def search_market_news(n: int = 8) -> list[dict]:
    """搜索市场/大盘新闻舆情（直接用东财，无需过滤）"""
    news = _fetch_news_em(n=80)
    if not news:
        return []
    # 全市场：按来源和时效性排序
    scored = []
    for item in news:
        score = 0
        t = item["title"]
        # 大盘/市场类关键词优先
        if any(kw in t for kw in ["大盘", "A股", "市场", "指数", "央行", "政策", "统计局"]):
            score += 3
        if any(kw in t for kw in ["今日", "最新", "盘面"]):
            score += 1
        if "研报" in t or "评级" in t:
            score -= 1
        scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in scored[:n]]


if __name__ == "__main__":
    print("SearXNG:", "✅" if check_searxng() else "❌")
    print()
    print("板块新闻 测试: 人工智能")
    news = search_sector_news("人工智能", n=5)
    print(f"  找到 {len(news)} 条")
    for n in news:
        print(f"  [{n['source']}] {n['title']}")
        print(f"    {n['snippet'][:60]}")
    print()
    print("市场新闻 测试:")
    news = search_market_news(n=5)
    print(f"  找到 {len(news)} 条")
    for n in news:
        print(f"  [{n['source']}] {n['title']}")
