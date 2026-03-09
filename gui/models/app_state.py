# -*- coding: utf-8 -*-
"""
应用状态模型
管理整个应用的状态
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import Enum

from ..utils.config_manager import ConfigManager


class TaskStatus(Enum):
    """任务状态枚举"""
    IDLE = "idle"              # 空闲
    LOADING = "loading"        # 加载弹幕中
    CRACKING = "cracking"      # 哈希匹配中
    FETCHING = "fetching"      # 获取用户信息中
    COMPLETED = "completed"    # 完成
    CANCELLED = "cancelled"    # 已取消
    ERROR = "error"           # 错误


@dataclass
class VideoInfo:
    """视频信息"""
    bvid: str = ""
    cid: int = 0
    title: str = ""
    url: str = ""
    cover: str = ""
    owner_mid: int = 0
    owner_name: str = ""


@dataclass
class SearchParams:
    """查询参数"""
    content: str = ""
    time_seconds: Optional[int] = None
    use_regex: bool = False
    match_exact: bool = False  # 精确匹配时间（默认允许±1秒误差）


@dataclass
class ProgressInfo:
    """进度信息"""
    current: int = 0
    total: int = 0
    stage: str = ""  # 阶段描述
    detail: str = ""  # 详细信息


class AppState:
    """应用状态"""

    def __init__(self):
        """初始化状态"""
        # 配置管理器
        self._config = ConfigManager()

        # 任务状态
        self.status = TaskStatus.IDLE
        self.progress = ProgressInfo()

        # 视频信息
        self.video_info: Optional[VideoInfo] = None

        # 查询参数
        self.search_params = SearchParams()

        # 查询结果
        self.search_result: Optional[Dict[str, Any]] = None

        # 错误信息
        self.error_message: str = ""

        # 日志
        self.logs: list = []

    @property
    def cookie(self) -> str:
        """获取Cookie"""
        return self._config.cookie

    @cookie.setter
    def cookie(self, value: str):
        """设置Cookie"""
        self._config.cookie = value

    @property
    def threads(self) -> int:
        """获取线程数"""
        return self._config.threads

    @threads.setter
    def threads(self, value: int):
        """设置线程数"""
        self._config.threads = value

    @property
    def theme(self) -> str:
        """获取主题"""
        return self._config.theme

    @theme.setter
    def theme(self, value: str):
        """设置主题"""
        self._config.theme = value

    def reset(self):
        """重置状态（保留用户配置）"""
        self.status = TaskStatus.IDLE
        self.progress = ProgressInfo()
        self.video_info = None
        self.search_params = SearchParams()
        self.search_result = None
        self.error_message = ""
        self.logs = []
        # 注意：不清空 cookie, threads, theme 等用户配置

    def add_log(self, message: str):
        """添加日志"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        # 保留最近500条日志
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]

    def get_progress_percent(self) -> float:
        """获取进度百分比"""
        if self.progress.total == 0:
            return 0.0
        return (self.progress.current / self.progress.total) * 100
