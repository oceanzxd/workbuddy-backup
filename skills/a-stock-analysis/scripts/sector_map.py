"""
sector_map.py - 行业板块映射模块
支持：板块列表查询、板块成分股过滤、股票→行业双向查询
新浪行业板块节点代码通过解析接口动态获取，无需硬编码
"""

import pandas as pd
import re
import requests
from typing import Optional

SINA_BD_URL = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
SINA_CMP_URL = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
                "/Market_Center.getHQNodeData")
HEADERS_SINA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}


# ── 动态获取新浪节点代码映射 ────────────────────────────────
def _build_node_map() -> dict:
    """从新浪行业接口解析: {板块名: node_code}"""
    try:
        r = requests.get(SINA_BD_URL, headers=HEADERS_SINA, timeout=10)
        r.encoding = "gbk"
        mapping = {}
        for m in re.finditer(r'"(new_\w+)":"([^"]+)"', r.text):
            fields = m.group(2).split(",")
            if len(fields) < 5: continue
            board_name = fields[1]
            node_code  = m.group(1)
            mapping[board_name] = node_code
        return mapping
    except Exception:
        return {}


# 惰性初始化的节点映射
_NODE_MAP_CACHE = None


def _get_node_map() -> dict:
    global _NODE_MAP_CACHE
    if _NODE_MAP_CACHE is None:
        _NODE_MAP_CACHE = _build_node_map()
    return _NODE_MAP_CACHE


# ── 兼容旧硬编码映射（节点代码已知）────────────────────────
# 从接口解析验证后的完整映射
_SINA_BOARD_CODE_MAP = {
    "玻璃行业":    "new_blhy",
    "船舶制造":    "new_cbzz",
    "传媒娱乐":    "new_cmyl",
    "电力行业":    "new_dlhy",
    "电器行业":    "new_dqhy",
    "电子器件":    "new_dzqj",
    "电子信息":    "new_dzxx",
    "房地产":      "new_fdc",
    "发电设备":    "new_fdsb",
    "飞机制造":    "new_fjzz",
    "纺织行业":    "new_fzhy",
    "纺织机械":    "new_fzjx",
    "服装鞋类":    "new_fzxl",
    "公路桥梁":    "new_glql",
    "供水供气":    "newgsgq",
    "钢铁行业":    "new_gthy",
    "环保行业":    "new_hbhy",
    "化工行业":    "new_hghy",
    "化纤行业":    "new_hxhy",
    "家电行业":    "new_jdhy",
    "酒店旅游":    "new_jdlv",
    "家具行业":    "new_jjhy",
    "金融行业":    "new_jrhy",
    "交通运输":    "new_jtys",
    "机械行业":    "new_jxhy",
    "建筑建材":    "new_jzjc",
    "开发区":      "new_kfq",
    "酿酒行业":    "new_njhy",
    "摩托车":      "new_mtc",
    "煤炭行业":    "new_mthy",
    "农林牧渔":    "new_nlmy",
    "农药化肥":    "new_nyhf",
    "汽车制造":    "new_qczz",
    "其它行业":    "new_qtgy",
    "塑料制品":    "new_slzp",
    "水泥行业":    "new_snhy",
    "食品行业":    "new_sphy",
    "次新股":      "new_cxgp",
    "生物制药":    "new_swzy",
    "商业百货":    "new_symh",
    "石油行业":    "new_syhy",
    "陶瓷行业":    "new_tchy",
    "物资外贸":    "new_wzwm",
    "医疗器械":    "new_ylsx",
    "仪器仪表":    "new_yqyb",
    "印刷包装":    "new_yzbz",
    "有色金属":    "new_ysjs",
    "综合行业":    "new_zhhy",
    "造纸行业":    "new_zzhy",
}


def get_sina_board_stocks(board_name: str, realtime_df: pd.DataFrame = None,
                          max_pages=10) -> list[str]:
    """
    根据新浪行业名称获取该板块所有成分股代码
    realtime_df: 可选，若传入则只返回已在实时行情中的股票
    返回: [symbol, ...]
    """
    node = _SINA_BOARD_CODE_MAP.get(board_name)
    if not node:
        # 动态查找
        dyn_map = _get_node_map()
        node = dyn_map.get(board_name)
        if not node:
            # 模糊匹配
            for key, val in dyn_map.items():
                if board_name in key or key in board_name:
                    node = val
                    break
        if not node:
            return []

    all_symbols = []
    for page in range(1, max_pages + 1):
        params = {"node": node, "sort": "changepercent", "asc": 0, "page": page, "num": 50}
        try:
            r = requests.get(SINA_CMP_URL, params=params, headers=HEADERS_SINA, timeout=10)
            data = r.json()
            if not data:
                break
            for item in data:
                sym = item.get("symbol", "")
                if sym:
                    all_symbols.append(sym)
            if len(data) < 50:
                break
        except Exception:
            break

    # 过滤：只保留在实时行情中有的股票（可选优化）
    if realtime_df is not None and all_symbols:
        valid_syms = set(realtime_df["代码"].tolist())
        all_symbols = [s for s in all_symbols if s in valid_syms]

    return all_symbols


def filter_by_board(board_name: str, realtime_df: pd.DataFrame,
                     stock_industry_map: dict = None) -> pd.DataFrame:
    """
    根据板块名称过滤实时行情 DataFrame
    优先用新浪行业成分接口，再用 baostock 行业映射兜底
    """
    if realtime_df is None or realtime_df.empty:
        return pd.DataFrame()

    # 方法1: 新浪行业成分接口
    sina_symbols = get_sina_board_stocks(board_name, realtime_df=realtime_df)
    if sina_symbols:
        mask = realtime_df["代码"].isin(sina_symbols)
        if mask.any():
            return realtime_df[mask].copy()

    # 方法2: baostock 行业名匹配（模糊匹配）
    if stock_industry_map:
        board_lower = board_name.lower()
        matched_symbols = [
            sym for sym, ind in stock_industry_map.items()
            if board_lower in ind.lower() or ind.lower() in board_lower
        ]
        if matched_symbols:
            mask = realtime_df["代码"].isin(matched_symbols)
            if mask.any():
                return realtime_df[mask].copy()

    return pd.DataFrame()


def get_stock_industry(sym: str, stock_industry_map: dict = None) -> Optional[str]:
    """查询单只股票所属行业"""
    if not stock_industry_map:
        return None
    if sym in stock_industry_map:
        return stock_industry_map[sym]
    code = sym[2:] if sym.startswith(("sh", "sz")) else sym
    return stock_industry_map.get(code)


def list_boards(board_df: pd.DataFrame = None) -> list[str]:
    """返回所有可用板块名称列表"""
    if board_df is not None and not board_df.empty:
        return board_df["板块名称"].tolist()
    # 从硬编码映射返回
    return sorted(_SINA_BOARD_CODE_MAP.keys())
