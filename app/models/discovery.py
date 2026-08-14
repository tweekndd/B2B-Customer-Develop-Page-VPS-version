"""发现与搜索模型：SearchTask / SearchCache（V5.3 拆分）"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime

from app.core.database import Base


class SearchTask(Base):
    """搜索任务（V2.0 新增）"""
    __tablename__ = "search_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    country = Column(String(100), nullable=False, comment="搜索国家")
    keyword = Column(String(200), nullable=False, comment="原始关键词")
    expanded_keywords = Column(Text, nullable=True, comment="AI扩展的关键词列表（JSON数组）")
    search_depth = Column(Integer, default=50, comment="每个关键词期望搜索数量")
    # Round 3 新增：创建任务的用户（后台任务按此解析用户自己的 API Key）
    user_id = Column(Integer, nullable=True, index=True, comment="创建任务的用户ID")
    status = Column(String(20), default="Pending", comment="任务状态: Pending/Running/Completed/Failed/Paused")
    found_websites = Column(Integer, default=0, comment="发现的网站数量")
    analyzed_companies = Column(Integer, default=0, comment="已分析的公司数量")
    new_companies = Column(Integer, default=0, comment="新增公司数量")
    current_keyword_index = Column(Integer, default=0, comment="当前处理到第几个扩展关键词（断点续跑）")
    error_message = Column(Text, nullable=True, comment="失败时的错误信息")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="创建时间")
    finished_at = Column(DateTime, nullable=True, comment="完成时间")
    task_log = Column(Text, nullable=True, comment="任务运行日志")


class SearchCache(Base):
    """搜索结果缓存（V2.0 新增，避免重复搜索相同关键词）"""
    __tablename__ = "search_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    keyword = Column(String(200), nullable=False, index=True, comment="搜索关键词")
    country = Column(String(100), nullable=False, index=True, comment="搜索国家")
    website = Column(String(500), nullable=False, comment="发现的企业官网")
    title = Column(String(500), nullable=True, comment="搜索结果标题")
    snippet = Column(Text, nullable=True, comment="搜索结果摘要")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, comment="缓存时间")
