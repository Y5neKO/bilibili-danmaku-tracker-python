# -*- coding: utf-8 -*-
"""
进度面板组件
"""

import customtkinter as ctk
from typing import Optional

from ..models.app_state import AppState, TaskStatus


class ProgressPanel(ctk.CTkFrame):
    """进度面板"""

    STATUS_TEXTS = {
        TaskStatus.IDLE: "就绪",
        TaskStatus.LOADING: "加载弹幕中...",
        TaskStatus.CRACKING: "匹配哈希中...",
        TaskStatus.FETCHING: "获取用户信息中...",
        TaskStatus.COMPLETED: "完成",
        TaskStatus.CANCELLED: "已取消",
        TaskStatus.ERROR: "出错"
    }

    def __init__(self, parent, state: AppState):
        super().__init__(parent)
        self.state = state

        self._create_ui()

    def _create_ui(self):
        """创建UI"""
        # 状态标签
        self.status_label = ctk.CTkLabel(
            self,
            text="就绪",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.status_label.pack(side="left", padx=10)

        # 进度条
        self.progress_bar = ctk.CTkProgressBar(self, width=400)
        self.progress_bar.set(0)
        self.progress_bar.pack(side="left", padx=10, fill="x", expand=True)

        # 进度文本
        self.progress_label = ctk.CTkLabel(self, text="0/0", width=80)
        self.progress_label.pack(side="left", padx=10)

        # 详细信息
        self.detail_label = ctk.CTkLabel(self, text="", width=200)
        self.detail_label.pack(side="left", padx=10)

    def update_progress(self, current: int, total: int, message: str = ""):
        """更新进度"""
        if total > 0:
            progress = current / total
            self.progress_bar.set(progress)
            self.progress_label.configure(text=f"{current}/{total}")
        else:
            self.progress_bar.set(0)
            self.progress_label.configure(text="0/0")

        if message:
            self.detail_label.configure(text=message)

    def update_status(self, status: TaskStatus):
        """更新状态"""
        status_text = self.STATUS_TEXTS.get(status, str(status))
        self.status_label.configure(text=status_text)

        # 根据状态改变颜色
        if status == TaskStatus.COMPLETED:
            self.status_label.configure(text_color="green")
        elif status == TaskStatus.ERROR:
            self.status_label.configure(text_color="red")
        elif status == TaskStatus.CANCELLED:
            self.status_label.configure(text_color="orange")
        else:
            self.status_label.configure(text_color="white")

        if status == TaskStatus.COMPLETED:
            self.progress_bar.set(1.0)
        elif status in (TaskStatus.IDLE, TaskStatus.CANCELLED, TaskStatus.ERROR):
            if status == TaskStatus.IDLE:
                self.progress_bar.set(0)

    def show_error(self, error_message: str):
        """显示错误"""
        self.status_label.configure(text=f"错误: {error_message}", text_color="red")
