# -*- coding: utf-8 -*-
"""
查询页面视图
"""

import customtkinter as ctk
from typing import Optional, Callable

from ..models.app_state import AppState


class SearchView(ctk.CTkFrame):
    """查询视图"""

    def __init__(self, parent, state: AppState,
                 on_search: Callable = None, on_stop: Callable = None):
        super().__init__(parent)
        self.state = state
        self._on_search = on_search
        self._on_stop = on_stop

        self._create_ui()

    def _create_ui(self):
        """创建UI"""
        # 左侧：输入区域（透明背景）
        self.left_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # 标题
        self.title_label = ctk.CTkLabel(
            self.left_frame,
            text="弹幕发送者查询",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.title_label.pack(pady=(10, 20))

        # BV号输入（透明背景）
        self.bvid_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        self.bvid_frame.pack(fill="x", padx=20, pady=5)

        self.bvid_label = ctk.CTkLabel(self.bvid_frame, text="BV号:", width=80)
        self.bvid_label.pack(side="left")

        self.bvid_entry = ctk.CTkEntry(
            self.bvid_frame,
            placeholder_text="例如: BV1xx411c7mD",
            width=300
        )
        self.bvid_entry.pack(side="left", padx=10, fill="x", expand=True)

        # 弹幕内容输入（透明背景）
        self.content_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        self.content_frame.pack(fill="x", padx=20, pady=5)

        self.content_label = ctk.CTkLabel(self.content_frame, text="弹幕内容:", width=80)
        self.content_label.pack(side="left")

        self.content_entry = ctk.CTkEntry(
            self.content_frame,
            placeholder_text="输入要搜索的弹幕内容",
            width=300
        )
        self.content_entry.pack(side="left", padx=10, fill="x", expand=True)

        # 匹配选项（透明背景）
        self.options_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        self.options_frame.pack(fill="x", padx=20, pady=10)

        self.regex_var = ctk.BooleanVar(value=False)
        self.regex_check = ctk.CTkCheckBox(
            self.options_frame,
            text="使用正则表达式",
            variable=self.regex_var
        )
        self.regex_check.pack(side="left", padx=10)

        # 按钮区域（透明背景）
        self.button_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        self.button_frame.pack(fill="x", padx=20, pady=20)

        self.search_btn = ctk.CTkButton(
            self.button_frame,
            text="开始查询",
            command=self._on_search_click,
            width=120,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.search_btn.pack(side="left", padx=10)

        self.stop_btn = ctk.CTkButton(
            self.button_frame,
            text="取消",
            command=self._on_stop_click,
            width=80,
            height=40,
            fg_color="gray",
            hover_color="darkgray",
            state="disabled"
        )
        self.stop_btn.pack(side="left", padx=10)

        # 右侧：使用说明（透明背景）
        self.right_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.right_frame.pack(side="right", fill="both", expand=True)

        self.help_title = ctk.CTkLabel(
            self.right_frame,
            text="使用说明",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.help_title.pack(pady=(10, 10))

        help_text = """
1. 输入视频的BV号
   - 可以从视频URL中获取
   - 例如: https://www.bilibili.com/video/BV1xx...

2. 输入要查询的弹幕内容
   - 支持精确匹配或正则表达式
   - 正则示例: "哈+" 匹配 "哈哈"、"哈哈哈"等

3. 点击"开始查询"
   - 系统会加载弹幕并匹配用户
   - 结果将显示在"结果"标签页

注意事项:
- 首次查询需要加载全部弹幕
- 哈希匹配可能需要较长时间
- 部分UID可能无法匹配（超过8位）
- 获取用户信息会串行执行（避免风控）
        """

        self.help_textbox = ctk.CTkTextbox(
            self.right_frame,
            width=300,
            height=400
        )
        self.help_textbox.pack(fill="both", expand=True, padx=10, pady=10)
        self.help_textbox.insert("1.0", help_text)
        self.help_textbox.configure(state="disabled")

    def _on_search_click(self):
        """搜索按钮点击"""
        bvid = self.bvid_entry.get().strip()
        content = self.content_entry.get().strip()
        use_regex = self.regex_var.get()

        # 验证输入
        if not bvid:
            self._show_error("请输入BV号")
            return

        if not bvid.startswith("BV"):
            self._show_error("BV号格式错误，应以BV开头")
            return

        if not content:
            self._show_error("请输入弹幕内容")
            return

        if self._on_search:
            self._on_search(bvid, content, None, use_regex)

    def _on_stop_click(self):
        """停止按钮点击"""
        if self._on_stop:
            self._on_stop()

    def _show_error(self, message: str):
        """显示错误提示"""
        # 简单的错误提示，可以后续改进为弹窗
        self.bvid_entry.configure(border_color="red")
        self.after(2000, lambda: self.bvid_entry.configure(border_color="gray"))

    def set_searching(self, searching: bool):
        """设置搜索状态"""
        if searching:
            self.search_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.bvid_entry.configure(state="disabled")
            self.content_entry.configure(state="disabled")
            self.regex_check.configure(state="disabled")
        else:
            self.search_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.bvid_entry.configure(state="normal")
            self.content_entry.configure(state="normal")
            self.regex_check.configure(state="normal")

    def get_search_params(self) -> dict:
        """获取搜索参数"""
        return {
            "bvid": self.bvid_entry.get().strip(),
            "content": self.content_entry.get().strip(),
            "use_regex": self.regex_var.get()
        }
