# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from .models import Base


class ClickHouseDatabase:
    """ClickHouse 数据库连接管理类"""

    def __init__(self, url: str):
        self.engine = create_async_engine(url)
        self.Session = async_sessionmaker(bind=self.engine)

    def get_session(self):
        """获取一个新的数据库会话"""
        return self.Session()

    def init(self):
        """初始化数据库，创建所有表"""
        Base.metadata.create_all(self.engine)
