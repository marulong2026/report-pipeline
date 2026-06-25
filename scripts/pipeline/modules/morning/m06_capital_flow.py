"""
m06_capital_flow.py — 资金流向采集模块

数据源：
  - stock.db north_moneyflow → 北向资金
  - web_search 搜索当日板块资金流向
"""

import logging
import re
from datetime import date, datetime

import requests

from modules.base_module import BaseModule
from modules.lib.web_utils import collect_north_moneyflow

logger = logging.getLogger("pipeline.m06_capital_flow")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class CapitalFlowModule(BaseModule):
    """资金流向采集模块"""

    module_name = "capital_flow"

    def run(self, trade_date):
        conn = self.get_db_conn()

        try:
            latest_date = self.get_latest_trade_date(conn)
            if latest_date is None:
                return self._empty_result("无数据")

            # 北向资金（本地）
            north_money = collect_north_moneyflow(conn, latest_date.isoformat())

            # 板块资金流向搜索
            sector_flow = self._search_sector_flow(latest_date)

            # 主力资金流向搜索
            main_force = self._search_main_force_flow(latest_date)

        except Exception as e:
            return {
                "north_money": None,
                "sector_flow": [],
                "main_force_flow": [],
                "data_quality": {"status": "all_failed", "error": str(e)},
            }
        finally:
            conn.close()

        notes = []
        if north_money is None:
            notes.append("北向资金获取失败")
        if not sector_flow:
            notes.append("板块资金流向搜索无结果")

        status = "complete"
        if notes:
            status = "partial"
        if north_money is None and not sector_flow and not main_force:
            status = "all_failed"

        return {
            "trade_date": latest_date.isoformat(),
            "north_money": north_money,
            "north_money_str": f"{north_money / 1e8:.2f}亿" if north_money else None,
            "sector_flow": sector_flow,
            "main_force_flow": main_force,
            "data_quality": {
                "status": status,
                "north_money_source": "local_db",
                "sector_flow_count": len(sector_flow),
                "notes": notes,
            },
        }

    def _search_sector_flow(self, trade_date) -> list:
        """搜索当日板块资金流向。"""
        query = f"{trade_date.strftime('%m月%d日')} 板块资金流向 主力资金流入 流出"
        return self._duckduckgo_search(query, limit=6)

    def _search_main_force_flow(self, trade_date) -> list:
        """搜索当日主力资金流向。"""
        query = f"{trade_date.strftime('%m月%d日')} 主力资金流向 净流入 TOP板块"
        return self._duckduckgo_search(query, limit=6)

    def _duckduckgo_search(self, query: str, limit: int = 6) -> list:
        """DuckDuckGo 搜索通用实现。"""
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": UA},
                timeout=5,
            )
            resp.raise_for_status()
        except Exception:
            return []

        results = []
        # 提取 result__a 标签
        pattern_a = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            resp.text,
            re.DOTALL,
        )
        pattern_snip = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            resp.text,
            re.DOTALL,
        )
        for i, (url, title) in enumerate(pattern_a):
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            snippet = ""
            if i < len(pattern_snip):
                snippet = re.sub(r'<[^>]+>', '', pattern_snip[i]).strip()
            results.append({
                "title": title_clean,
                "summary": snippet[:200],
                "url": url,
                "source": "duckduckgo",
            })
            if len(results) >= limit:
                break

        return results

    def _empty_result(self, reason):
        return {
            "north_money": None,
            "sector_flow": [],
            "main_force_flow": [],
            "data_quality": {"status": "all_failed", "error": reason},
        }


# 模块单例导出
module = CapitalFlowModule()
