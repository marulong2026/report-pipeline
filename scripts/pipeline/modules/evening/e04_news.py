"""
e04_news.py — 晚报·新闻联播要点模块

用 web_search 搜索「新闻联播 今晚 要点」等硬编码搜索词。
"""

import logging
import re
from datetime import date, datetime

import requests

from modules.base_module import BaseModule

logger = logging.getLogger("pipeline.e04_news")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 硬编码搜索词
SEARCH_QUERIES = [
    "新闻联播 今晚 主要内容 要点",
    "新闻联播 今日 要闻 速览",
    "今日 国内要闻 财经要闻 汇总",
]


class EveningNewsModule(BaseModule):
    """晚报·新闻联播要点模块"""

    module_name = "eve_news"

    def run(self, trade_date):
        all_news = []
        sources_used = []

        day_str = trade_date.strftime("%m月%d日")

        for query_template in SEARCH_QUERIES:
            full_query = f"{day_str} {query_template}"
            try:
                items = self._search(full_query)
                if items:
                    all_news.extend(items)
                    sources_used.append(query_template[:20])
            except Exception as e:
                logger.debug(f"搜索失败: {e}")

        # 去重
        seen = set()
        unique_news = []
        for item in all_news:
            key = item.get("title", "")
            if key and key not in seen:
                seen.add(key)
                unique_news.append(item)

        status = "complete"
        notes = []
        if not unique_news:
            status = "all_failed"
            notes.append("全部搜索无结果")

        return {
            "trade_date": trade_date.isoformat(),
            "news": unique_news[:15],
            "total_items": len(unique_news),
            "data_quality": {
                "status": status,
                "searches_used": len(sources_used),
                "notes": notes,
            },
        }

    def _search(self, query: str, limit: int = 8) -> list:
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
        pattern_a = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            resp.text, re.DOTALL,
        )
        pattern_snip = re.findall(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            resp.text, re.DOTALL,
        )
        for i, (url, title) in enumerate(pattern_a):
            title_clean = re.sub(r'<[^>]+>', '', title).strip()
            snippet = ""
            if i < len(pattern_snip):
                snippet = re.sub(r'<[^>]+>', '', pattern_snip[i]).strip()
            results.append({
                "title": title_clean,
                "summary": snippet[:300],
                "url": url,
                "source": "duckduckgo",
            })
            if len(results) >= limit:
                break
        return results


module = EveningNewsModule()
