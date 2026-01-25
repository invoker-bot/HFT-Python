"""
ClickHouse 数据库连接模块

使用 clickhouse-connect 官方驱动，支持异步操作。
通过 HTTP 协议连接 ClickHouse，兼容性好，支持负载均衡。

使用示例:
    db = ClickHouseDatabase(host='localhost', port=8123, user='default', password='', database='hft')
    await db.init()
    await db.insert('table_name', data, column_names=['col1', 'col2'])
    result = await db.query('SELECT * FROM table_name')
"""
from abc import ABC, abstractmethod
from typing import Type, TypeVar, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import DatabaseConfig
    from .controllers.base import DataBaseController

T = TypeVar('T', bound="DataBaseController")


class DatabaseClient(ABC):
    """数据库客户端抽象基类"""

    def __init__(self, config: "DatabaseConfig"):
        self.config = config
        # self.connector: Any = None

    @property
    def dsn(self):
        """获取数据库连接字符串"""
        return self.config.dsn

    @property
    def host(self):
        """获取数据库主机地址"""
        return self.dsn.host if self.dsn else None

    @property
    def port(self):
        """获取数据库连接端口"""
        return self.dsn.port if self.dsn else None

    @property
    def user(self):
        """获取数据库用户名"""
        return self.dsn.username if self.dsn else None

    @property
    def password(self):
        """获取数据库密码"""
        return self.dsn.password if self.dsn else None

    @property
    def database(self):
        """获取数据库名称"""
        if self.dsn is None:
            return None
        path = self.dsn.path
        if path is None:
            return "default"
        return path.lstrip('/')

    @abstractmethod
    async def init(self):
        """初始化数据库连接"""

    async def __aenter__(self):
        """异步上下文管理器进入时初始化连接"""
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """异步上下文管理器退出时关闭连接"""
        await self.close()

    @abstractmethod
    async def close(self):
        """关闭数据库连接"""

    def has_connection(self):
        return self.dsn is not None

    controllers = {

    }

    clients = {

    }

    def get_controller(self, controller_class: Type[T]) -> T:
        """获取控制器类型"""
        return self.controllers.get(controller_class, controller_class)(self)

    @classmethod
    def get_client(cls, config: "DatabaseConfig") -> Optional["DatabaseClient"]:
        """根据配置获取数据库客户端实例"""
        if config.dsn is None:
            return None
        return cls.clients[config.dsn.scheme](config)
