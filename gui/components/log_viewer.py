# -*- coding: utf-8 -*-
"""
日志查看器组件
"""

import customtkinter as ctk
from typing import Optional

from ..models.app_state import AppState


class LogViewer(ctk.CTkFrame):
    """日志查看器"""

    MAX_LINES = 500

    def __init__(self, parent, state: AppState):
        super().__init__(parent)
        self.state = state

        self._create_ui()

    def _create_ui(self):
        """创建UI"""
        # 标题栏（透明背景）
        self.title_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.title_frame.pack(fill="x")

        self.title_label = ctk.CTkLabel(
            self.title_frame,
            text="执行日志",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.title_label.pack(side="left", padx=5)

        self.clear_btn = ctk.CTkButton(
            self.title_frame,
            text="清空",
            width=50,
            height=24,
            command=self._clear_log
        )
        self.clear_btn.pack(side="right", padx=5)

        # 日志文本框
        self.log_text = ctk.CTkTextbox(
            self,
            height=150,
            font=ctk.CTkFont(family="Consolas", size=11)
        )
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    def add_log(self, message: str):
        """添加日志"""
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")

        # 限制行数
        self._trim_log()

        self.log_text.configure(state="disabled")

    def _trim_log(self):
        """裁剪日志，保持最大行数"""
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > self.MAX_LINES:
            self.log_text.delete("1.0", f"{lines - self.MAX_LINES}.0")

    def _clear_log(self):
        """清空日志"""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.state.logs = []

    def load_logs(self, logs: list):
        """加载日志列表"""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for log in logs:
            self.log_text.insert("end", log + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
