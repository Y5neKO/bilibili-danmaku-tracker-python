# -*- coding: utf-8 -*-
"""
结果展示页面视图
"""

import customtkinter as ctk
from typing import Optional, Callable, Dict, Any, List

from ..models.app_state import AppState, VideoInfo
from ..components.user_card import UserCardList


class ResultsView(ctk.CTkFrame):
    """结果展示视图"""

    def __init__(self, parent, state: AppState, on_export: Callable = None):
        super().__init__(parent)
        self.state = state
        self._on_export = on_export

        self._create_ui()

    def _create_ui(self):
        """创建UI"""
        # 顶部：视频信息和操作（透明背景）
        self.top_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.top_frame.pack(fill="x", padx=10, pady=10)

        # 视频信息（透明背景）
        self.video_info_frame = ctk.CTkFrame(self.top_frame, fg_color="transparent")
        self.video_info_frame.pack(side="left", fill="both", expand=True)

        self.video_title_label = ctk.CTkLabel(
            self.video_info_frame,
            text="等待查询...",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.video_title_label.pack(anchor="w")

        self.video_url_label = ctk.CTkLabel(
            self.video_info_frame,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.video_url_label.pack(anchor="w")

        # 操作按钮（透明背景）
        self.action_frame = ctk.CTkFrame(self.top_frame, fg_color="transparent")
        self.action_frame.pack(side="right")

        self.export_btn = ctk.CTkButton(
            self.action_frame,
            text="导出HTML报告",
            command=self._on_export_click,
            state="disabled"
        )
        self.export_btn.pack(side="right", padx=5)

        # 统计信息（透明背景）
        self.stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_frame.pack(fill="x", padx=10, pady=5)

        self.stats_label = ctk.CTkLabel(
            self.stats_frame,
            text="统计信息: 等待查询结果",
            font=ctk.CTkFont(size=12)
        )
        self.stats_label.pack(anchor="w", padx=10, pady=5)

        # 用户列表
        self.user_list = UserCardList(self)
        self.user_list.pack(fill="both", expand=True, padx=10, pady=10)

        # 未匹配哈希区域（初始隐藏，透明背景）
        self.unmatched_frame = ctk.CTkFrame(self, fg_color="transparent")
        # 不在这里 pack，只有有未匹配时才显示
        self.unmatched_label = ctk.CTkLabel(
            self.unmatched_frame,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="orange"
        )
        self.unmatched_label.pack(anchor="w", padx=10, pady=5)

    def display_results(self, result: Dict[str, Any], video_info: Optional[VideoInfo]):
        """显示查询结果"""
        # 更新视频信息
        if video_info:
            self.video_title_label.configure(text=f"视频: {video_info.title}")
            self.video_url_label.configure(text=video_info.url)

        # 更新统计信息
        users = result.get('users', [])
        user_data = result.get('user_data', {})
        matched_count = result.get('matched_danmaku_count', 0)
        uncracked = result.get('uncracked_hashes', [])
        error_hashes = result.get('error_hashes', [])

        # 统计正常用户和错误用户
        normal_users = [u for u in user_data.values() if not u.get('info', {}).get('is_error')]
        error_users = [u for u in user_data.values() if u.get('info', {}).get('is_error')]

        stats_text = f"匹配弹幕: {matched_count} 条 | 正常用户: {len(normal_users)} 人"
        if error_users:
            stats_text += f" | 获取失败: {len(error_users)} 人"
        self.stats_label.configure(text=stats_text)

        # 显示用户列表（包含正常用户和错误用户）
        users_list = list(user_data.values())
        self.user_list.set_users(users_list, on_user_click=self._on_user_click)

        # 显示未匹配哈希
        if uncracked:
            uncracked_count = len(uncracked)
            self.unmatched_label.configure(
                text=f"⚠️ 有 {uncracked_count} 个哈希无法匹配（可能是超过8位UID或已删号）"
            )
            # 显示未匹配区域
            if not self.unmatched_frame.winfo_ismapped():
                self.unmatched_frame.pack(fill="x", padx=10, pady=5)
        else:
            # 隐藏未匹配区域
            if self.unmatched_frame.winfo_ismapped():
                self.unmatched_frame.pack_forget()

        # 启用导出按钮
        self.export_btn.configure(state="normal")

    def _on_export_click(self):
        """导出按钮点击"""
        if self._on_export:
            file_path = self._on_export()
            if file_path:
                self._show_success(f"报告已保存: {file_path}")

    def _on_user_click(self, user_data: Dict[str, Any]):
        """用户卡片点击"""
        # 可以扩展为显示详细信息弹窗
        pass

    def _show_success(self, message: str):
        """显示成功消息"""
        # 简单实现，可以后续改进
        self.stats_label.configure(text=message, text_color="green")
        self.after(3000, lambda: self.stats_label.configure(text_color="white"))

    def clear(self):
        """清空结果"""
        self.video_title_label.configure(text="等待查询...")
        self.video_url_label.configure(text="")
        self.stats_label.configure(text="统计信息: 等待查询结果")
        self.unmatched_label.configure(text="")
        # 隐藏未匹配区域
        if self.unmatched_frame.winfo_ismapped():
            self.unmatched_frame.pack_forget()
        self.user_list.clear()
        self.export_btn.configure(state="disabled")
