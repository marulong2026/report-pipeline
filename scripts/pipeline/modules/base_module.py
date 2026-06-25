"""
base_module.py — 数据采集模块基类

所有采集模块必须继承本基类，实现：
  - module_name 属性（模块标识）
  - run(trade_date) 方法（返回 JSON-serializable dict，含 data_quality 字段）

数据库操作统一使用 read_only 连接，防止意外写入。
"""

from abc import ABC, abstractmethod
from datetime import date, datetime
import duckdb

DB_PATH = '/mnt/d/stock-data/stock.db'


class BaseModule(ABC):
    """所有数据采集模块的抽象基类"""

    @property
    @abstractmethod
    def module_name(self) -> str:
        """模块标识，如 'overseas', 'portfolio'"""
        ...

    @abstractmethod
    def run(self, trade_date: date) -> dict:
        """
        执行采集并返回 JSON-serializable dict。

        返回值必须包含顶层 'data_quality' 字段，格式：
          {"status": "complete"|"partial"|"all_failed", ...}

        Args:
            trade_date: 交易日（datetime.date 对象）

        Returns:
            dict: 采集结果，可 json.dumps 序列化
        """
        ...

    @staticmethod
    def get_db_conn():
        """
        获取 DuckDB read_only 数据库连接。

        Returns:
            duckdb.DuckDBPyConnection: read_only 连接
        """
        return duckdb.connect(DB_PATH, read_only=True)

    @staticmethod
    def get_prev_trade_date(conn, ref_date: date) -> date | None:
        """
        获取指定日期之前的最近交易日。

        Args:
            conn: DuckDB 数据库连接
            ref_date: 参考日期

        Returns:
            date or None: 最近交易日，不存在则返回 None
        """
        row = conn.execute(
            "SELECT MAX(trade_date) FROM daily_quotes WHERE trade_date < ?",
            [ref_date]
        ).fetchone()
        return row[0] if row and row[0] else None

    @staticmethod
    def get_latest_trade_date(conn) -> date | None:
        """获取 daily_quotes 表中最新交易日。"""
        row = conn.execute("SELECT MAX(trade_date) FROM daily_quotes").fetchone()
        return row[0] if row and row[0] else None

    def __str__(self) -> str:
        return f"<Module:{self.module_name}>"
