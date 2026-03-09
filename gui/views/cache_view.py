# -*- coding: utf-8 -*-
"""
缓存管理页面视图
"""

import customtkinter as ctk
import os
import json
from typing import Optional

from ..models.app_state import AppState


class CacheView(ctk.CTkFrame):
    """缓存管理视图"""

    def __init__(self, parent, state: AppState):
        super().__init__(parent)
        self.state = state

        self._cache_dir = "cache/userinfo"
        self._hash_cache_file = "cache/hash_cache.json"

        self._create_ui()
        self._refresh_stats()

    def _create_ui(self):
        """创建UI"""
        # 标题
        self.title_label = ctk.CTkLabel(
            self,
            text="缓存管理",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.title_label.pack(pady=(10, 20))

        # 哈希缓存
        self.hash_cache_frame = ctk.CTkFrame(self)
        self.hash_cache_frame.pack(fill="x", padx=20, pady=10)

        self.hash_cache_title = ctk.CTkLabel(
            self.hash_cache_frame,
            text="哈希-UID映射缓存",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.hash_cache_title.pack(anchor="w", padx=10, pady=(10, 5))

        self.hash_cache_desc = ctk.CTkLabel(
            self.hash_cache_frame,
            text="缓存已匹配的哈希值，避免重复计算",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.hash_cache_desc.pack(anchor="w", padx=10, pady=(0, 5))

        self.hash_cache_stats = ctk.CTkLabel(
            self.hash_cache_frame,
            text="加载中..."
        )
        self.hash_cache_stats.pack(anchor="w", padx=10, pady=5)

        self.hash_cache_btn_frame = ctk.CTkFrame(self.hash_cache_frame, fg_color="transparent")
        self.hash_cache_btn_frame.pack(fill="x", padx=10, pady=(5, 10))

        self.clear_hash_cache_btn = ctk.CTkButton(
            self.hash_cache_btn_frame,
            text="清空哈希缓存",
            command=self._clear_hash_cache,
            fg_color="red",
            hover_color="darkred"
        )
        self.clear_hash_cache_btn.pack(side="left")

        # 用户信息缓存
        self.user_cache_frame = ctk.CTkFrame(self)
        self.user_cache_frame.pack(fill="x", padx=20, pady=10)

        self.user_cache_title = ctk.CTkLabel(
            self.user_cache_frame,
            text="用户信息缓存",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.user_cache_title.pack(anchor="w", padx=10, pady=(10, 5))

        self.user_cache_desc = ctk.CTkLabel(
            self.user_cache_frame,
            text="缓存用户信息，减少API请求",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.user_cache_desc.pack(anchor="w", padx=10, pady=(0, 5))

        self.user_cache_stats = ctk.CTkLabel(
            self.user_cache_frame,
            text="加载中..."
        )
        self.user_cache_stats.pack(anchor="w", padx=10, pady=5)

        self.user_cache_btn_frame = ctk.CTkFrame(self.user_cache_frame, fg_color="transparent")
        self.user_cache_btn_frame.pack(fill="x", padx=10, pady=(5, 10))

        self.clear_user_cache_btn = ctk.CTkButton(
            self.user_cache_btn_frame,
            text="清空用户缓存",
            command=self._clear_user_cache,
            fg_color="red",
            hover_color="darkred"
        )
        self.clear_user_cache_btn.pack(side="left")

        # 全部清空
        self.all_cache_frame = ctk.CTkFrame(self)
        self.all_cache_frame.pack(fill="x", padx=20, pady=10)

        self.clear_all_btn = ctk.CTkButton(
            self.all_cache_frame,
            text="清空所有缓存",
            command=self._clear_all_cache,
            fg_color="darkred",
            hover_color="#550000",
            height=40
        )
        self.clear_all_btn.pack(padx=10, pady=10)

    def _refresh_stats(self):
        """刷新缓存统计"""
        # 哈希缓存统计
        hash_count = 0
        hash_size = 0
        if os.path.exists(self._hash_cache_file):
            try:
                with open(self._hash_cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    hash_count = len(data)
                hash_size = os.path.getsize(self._hash_cache_file)
            except:
                pass

        hash_size_str = self._format_size(hash_size)
        self.hash_cache_stats.configure(
            text=f"缓存条目: {hash_count} | 文件大小: {hash_size_str}"
        )

        # 用户缓存统计
        user_count = 0
        user_size = 0
        if os.path.exists(self._cache_dir):
            try:
                files = [f for f in os.listdir(self._cache_dir) if f.endswith('.json')]
                user_count = len(files)
                for f in files:
                    user_size += os.path.getsize(os.path.join(self._cache_dir, f))
            except:
                pass

        user_size_str = self._format_size(user_size)
        self.user_cache_stats.configure(
            text=f"缓存条目: {user_count} | 目录大小: {user_size_str}"
        )

    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / 1024 / 1024:.1f} MB"

    def _clear_hash_cache(self):
        """清空哈希缓存"""
        if os.path.exists(self._hash_cache_file):
            try:
                os.remove(self._hash_cache_file)
                self._show_message("哈希缓存已清空")
            except Exception as e:
                self._show_error(f"清空失败: {e}")
        self._refresh_stats()

    def _clear_user_cache(self):
        """清空用户缓存"""
        if os.path.exists(self._cache_dir):
            try:
                import shutil
                shutil.rmtree(self._cache_dir)
                os.makedirs(self._cache_dir)
                self._show_message("用户缓存已清空")
            except Exception as e:
                self._show_error(f"清空失败: {e}")
        self._refresh_stats()

    def _clear_all_cache(self):
        """清空所有缓存"""
        self._clear_hash_cache()
        self._clear_user_cache()
        self._show_message("所有缓存已清空")

    def _show_message(self, message: str):
        """显示消息"""
        self.hash_cache_stats.configure(text=message, text_color="green")
        self.after(2000, lambda: self.hash_cache_stats.configure(text_color="white"))
        self._refresh_stats()

    def _show_error(self, message: str):
        """显示错误"""
        self.hash_cache_stats.configure(text=message, text_color="red")
        self.after(3000, lambda: self.hash_cache_stats.configure(text_color="white"))
