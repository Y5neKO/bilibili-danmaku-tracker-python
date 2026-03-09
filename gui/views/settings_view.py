# -*- coding: utf-8 -*-
"""
设置页面视图
"""

import customtkinter as ctk
from typing import Optional

from ..models.app_state import AppState


class SettingsView(ctk.CTkScrollableFrame):
    """设置视图（带滚动条）"""

    def __init__(self, parent, state: AppState):
        super().__init__(parent)
        self.state = state

        self._create_ui()

    def _create_ui(self):
        """创建UI"""
        # 标题
        self.title_label = ctk.CTkLabel(
            self,
            text="设置",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.title_label.pack(pady=(10, 20))

        # Cookie设置
        self.cookie_frame = ctk.CTkFrame(self)
        self.cookie_frame.pack(fill="x", padx=20, pady=10)

        self.cookie_title = ctk.CTkLabel(
            self.cookie_frame,
            text="B站Cookie",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.cookie_title.pack(anchor="w", padx=10, pady=(10, 5))

        self.cookie_desc = ctk.CTkLabel(
            self.cookie_frame,
            text="用于获取用户灯牌信息（可选，不填也能使用基本功能）",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.cookie_desc.pack(anchor="w", padx=10, pady=(0, 5))

        self.cookie_entry = ctk.CTkTextbox(
            self.cookie_frame,
            height=80,
            font=ctk.CTkFont(size=11)
        )
        self.cookie_entry.pack(fill="x", padx=10, pady=5)

        self.cookie_btn_frame = ctk.CTkFrame(self.cookie_frame, fg_color="transparent")
        self.cookie_btn_frame.pack(fill="x", padx=10, pady=(5, 10))

        self.save_cookie_btn = ctk.CTkButton(
            self.cookie_btn_frame,
            text="保存Cookie",
            command=self._save_cookie
        )
        self.save_cookie_btn.pack(side="left")

        self.clear_cookie_btn = ctk.CTkButton(
            self.cookie_btn_frame,
            text="清空",
            command=self._clear_cookie,
            fg_color="gray",
            hover_color="darkgray"
        )
        self.clear_cookie_btn.pack(side="left", padx=10)

        # 线程设置
        self.threads_frame = ctk.CTkFrame(self)
        self.threads_frame.pack(fill="x", padx=20, pady=10)

        self.threads_title = ctk.CTkLabel(
            self.threads_frame,
            text="哈希匹配线程数",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.threads_title.pack(anchor="w", padx=10, pady=(10, 5))

        self.threads_desc = ctk.CTkLabel(
            self.threads_frame,
            text="更多线程可加快哈希匹配速度（纯计算，不请求网络）",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.threads_desc.pack(anchor="w", padx=10, pady=(0, 5))

        self.threads_slider = ctk.CTkSlider(
            self.threads_frame,
            from_=1,
            to=20,
            number_of_steps=19,
            command=self._on_threads_change
        )
        self.threads_slider.set(self.state.threads)
        self.threads_slider.pack(fill="x", padx=10, pady=5)

        self.threads_label = ctk.CTkLabel(
            self.threads_frame,
            text=f"当前: {self.state.threads} 线程"
        )
        self.threads_label.pack(anchor="w", padx=10, pady=(0, 10))

        # 外观设置
        self.appearance_frame = ctk.CTkFrame(self)
        self.appearance_frame.pack(fill="x", padx=20, pady=10)

        self.appearance_title = ctk.CTkLabel(
            self.appearance_frame,
            text="外观",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.appearance_title.pack(anchor="w", padx=10, pady=(10, 5))

        self.theme_label = ctk.CTkLabel(
            self.appearance_frame,
            text="主题模式:"
        )
        self.theme_label.pack(anchor="w", padx=10)

        self.theme_menu = ctk.CTkOptionMenu(
            self.appearance_frame,
            values=["dark", "light", "system"],
            command=self._on_theme_change
        )
        self.theme_menu.pack(anchor="w", padx=10, pady=(0, 10))

        # 关于
        self.about_frame = ctk.CTkFrame(self)
        self.about_frame.pack(fill="x", padx=20, pady=10)

        self.about_title = ctk.CTkLabel(
            self.about_frame,
            text="关于",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.about_title.pack(anchor="w", padx=10, pady=(10, 5))

        self.about_text = ctk.CTkLabel(
            self.about_frame,
            text="B站弹幕发送者查询工具 v2.0\n用于根据弹幕内容查询发送者信息",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            justify="left"
        )
        self.about_text.pack(anchor="w", padx=10, pady=(0, 10))

    def _save_cookie(self):
        """保存Cookie"""
        cookie = self.cookie_entry.get("1.0", "end-1c").strip()
        self.state.cookie = cookie
        self._show_message("Cookie已保存")

    def _clear_cookie(self):
        """清空Cookie"""
        self.cookie_entry.delete("1.0", "end")
        self.state.cookie = ""

    def _on_threads_change(self, value):
        """线程数变化"""
        threads = int(value)
        self.state.threads = threads
        self.threads_label.configure(text=f"当前: {threads} 线程")

    def _on_theme_change(self, theme: str):
        """主题变化"""
        ctk.set_appearance_mode(theme)
        self.state.theme = theme

    def _show_message(self, message: str):
        """显示消息"""
        # 显示临时消息
        if not hasattr(self, 'message_label'):
            self.message_label = ctk.CTkLabel(
                self.cookie_frame,
                text="",
                text_color="green"
            )
            self.message_label.pack(anchor="w", padx=10, pady=(0, 5))

        self.message_label.configure(text=message)
        # 2秒后清除消息
        self.after(2000, lambda: self.message_label.configure(text=""))

    def load_settings(self):
        """加载设置到UI"""
        if self.state.cookie:
            self.cookie_entry.delete("1.0", "end")
            self.cookie_entry.insert("1.0", self.state.cookie)

        self.threads_slider.set(self.state.threads)
        self.threads_label.configure(text=f"当前: {self.state.threads} 线程")

        # 加载并应用主题
        theme = self.state.theme
        if theme in ["dark", "light", "system"]:
            ctk.set_appearance_mode(theme)
            self.theme_menu.set(theme)
        else:
            # 无效主题值，使用默认
            ctk.set_appearance_mode("dark")
            self.theme_menu.set("dark")
            self.state.theme = "dark"
