# -*- coding: utf-8 -*-
"""
用户卡片组件
"""

import customtkinter as ctk
from typing import Dict, Any, Optional
import tkinter as tk
import threading
import requests
from io import BytesIO
from PIL import Image, ImageDraw

# 头像缓存
_avatar_cache: Dict[str, Any] = {}


class UserCard(ctk.CTkFrame):
    """用户信息卡片"""

    def __init__(self, parent, user_data: Dict[str, Any], on_click: callable = None):
        super().__init__(parent)
        self.user_data = user_data
        self._on_click = on_click
        self._avatar_image = None

        self._create_ui()

        # 绑定点击事件
        if on_click:
            self.bind("<Button-1>", lambda e: on_click(user_data))
            for child in self.winfo_children():
                child.bind("<Button-1>", lambda e: on_click(user_data))

        # 异步加载头像
        avatar_url = self.user_data.get('info', {}).get('avatar', '')
        if avatar_url:
            self._load_avatar_async(avatar_url)

    def _load_avatar_async(self, url: str):
        """异步加载头像"""
        # 检查缓存
        global _avatar_cache
        if url in _avatar_cache:
            self._set_avatar(_avatar_cache[url])
            return

        def load_task():
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://space.bilibili.com/"
                }
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200 and len(resp.content) > 0:
                    img = Image.open(BytesIO(resp.content))
                    # 缓存图片
                    _avatar_cache[url] = img.copy()
                    # 在主线程更新UI
                    self.after(0, lambda: self._set_avatar(img))
            except Exception as e:
                print(f"加载头像失败: {e}")

        thread = threading.Thread(target=load_task, daemon=True)
        thread.start()

    def _set_avatar(self, img):
        """设置头像图片"""
        try:
            # 检查 widget 是否仍然存在
            if not self.winfo_exists():
                return
            if not hasattr(self, 'avatar_label') or not self.avatar_label.winfo_exists():
                return

            # 调整图片大小
            img = img.resize((50, 50), Image.Resampling.LANCZOS)
            # 确保图片是 RGBA 模式
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            # 创建圆形遮罩
            mask = Image.new('L', (50, 50), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, 50, 50), fill=255)

            # 创建圆形头像
            circular_img = Image.new('RGBA', (50, 50), (0, 0, 0, 0))
            circular_img.paste(img, (0, 0))
            circular_img.putalpha(mask)

            # 转换为 CTkImage
            self._avatar_image = ctk.CTkImage(light_image=circular_img, dark_image=circular_img, size=(50, 50))
            self.avatar_label.configure(image=self._avatar_image, text="")
        except tk.TclError:
            # Widget 已被销毁，忽略
            pass
        except Exception as e:
            print(f"设置头像失败: {e}")

    def _create_ui(self):
        """创建UI"""
        is_error = self.user_data.get('info', {}).get('is_error', False)

        # 错误用户使用特殊边框颜色
        if is_error:
            self.configure(border_width=2, border_color="#ff6b6b")
        else:
            self.configure(border_width=1, border_color="gray")

        # 主容器
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="x", padx=10, pady=10)

        # 左侧：头像
        self.avatar_frame = ctk.CTkFrame(self.container, width=60, height=60)
        self.avatar_frame.pack(side="left", padx=(0, 15))
        self.avatar_frame.pack_propagate(False)

        # 头像占位符（加载中显示）
        if is_error:
            self.avatar_label = ctk.CTkLabel(
                self.avatar_frame,
                text="⚠️",
                font=ctk.CTkFont(size=30)
            )
        else:
            self.avatar_label = ctk.CTkLabel(
                self.avatar_frame,
                text="👤",
                font=ctk.CTkFont(size=30)
            )
        self.avatar_label.pack(expand=True)

        # 中间：用户信息
        self.info_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.info_frame.pack(side="left", fill="both", expand=True)

        # 用户名和UID
        self.name_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        self.name_frame.pack(fill="x")

        name = self.user_data.get('info', {}).get('name', '未知用户')
        uid = self.user_data.get('uid', 'N/A')

        # 错误用户显示特殊样式
        if is_error:
            self.name_label = ctk.CTkLabel(
                self.name_frame,
                text=f"{name} [获取失败]",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color="#ff6b6b"
            )
            self.name_label.pack(side="left")

            mid_hash = self.user_data.get('info', {}).get('mid_hash', '')
            self.uid_label = ctk.CTkLabel(
                self.name_frame,
                text=f"  UID: {uid} | Hash: {mid_hash[:8]}...",
                font=ctk.CTkFont(size=12),
                text_color="gray"
            )
            self.uid_label.pack(side="left")
        else:
            self.name_label = ctk.CTkLabel(
                self.name_frame,
                text=name,
                font=ctk.CTkFont(size=14, weight="bold")
            )
            self.name_label.pack(side="left")

            self.uid_label = ctk.CTkLabel(
                self.name_frame,
                text=f"  UID: {uid}",
                font=ctk.CTkFont(size=12),
                text_color="gray"
            )
            self.uid_label.pack(side="left")

        # 签名
        sign = self.user_data.get('info', {}).get('sign', '')
        if sign:
            sign_color = "#ff6b6b" if is_error else "gray"
            self.sign_label = ctk.CTkLabel(
                self.info_frame,
                text=f"{'⚠️ ' if is_error else '签名: '}{sign}",
                font=ctk.CTkFont(size=11),
                text_color=sign_color,
                wraplength=400,
                justify="left"
            )
            self.sign_label.pack(fill="x", anchor="w")

        # 灯牌信息（错误用户不显示）
        if not is_error:
            medals = self.user_data.get('info', {}).get('medals', [])
            if medals:
                self.medal_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
                self.medal_frame.pack(fill="x", pady=(5, 0))

                # 找到正在佩戴的灯牌，否则显示第一个
                wearing_medal = None
                for m in medals:
                    if m.get('wearing_status') == 1:
                        wearing_medal = m
                        break

                display_medal = wearing_medal or medals[0]
                medal_name = display_medal.get('medal_name', '')
                target_name = display_medal.get('target_name', '')
                medal_level = display_medal.get('level', 0)

                # 灯牌颜色渐变（根据等级）
                medal_color = self._get_medal_color(medal_level)

                medal_text = f"🏅 {medal_name}({target_name}) Lv.{medal_level}"
                if len(medals) > 1:
                    medal_text += f" (+{len(medals)-1})"

                self.medal_label = ctk.CTkLabel(
                    self.medal_frame,
                    text=medal_text,
                    font=ctk.CTkFont(size=11),
                    text_color=medal_color
                )
                self.medal_label.pack(side="left")

        # 弹幕列表
        danmaku_list = self.user_data.get('danmaku_list', [])
        if danmaku_list:
            self.danmaku_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
            self.danmaku_frame.pack(fill="x", pady=(5, 0))

            self.danmaku_label = ctk.CTkLabel(
                self.danmaku_frame,
                text=f"发送弹幕: {', '.join(danmaku_list[:5])}{'...' if len(danmaku_list) > 5 else ''}",
                font=ctk.CTkFont(size=11),
                wraplength=400,
                justify="left"
            )
            self.danmaku_label.pack(side="left")

        # 右侧：操作按钮（错误用户不显示访问空间按钮）
        self.action_frame = ctk.CTkFrame(self.container, fg_color="transparent")
        self.action_frame.pack(side="right", padx=(10, 0))

        if not is_error:
            space_url = self.user_data.get('info', {}).get('space_url', '')
            if space_url:
                self.space_btn = ctk.CTkButton(
                    self.action_frame,
                    text="访问空间",
                    width=80,
                    command=lambda: self._open_url(space_url)
                )
                self.space_btn.pack()

    def _open_url(self, url: str):
        """打开URL"""
        import webbrowser
        webbrowser.open(url)

    def _get_medal_color(self, level: int) -> str:
        """根据灯牌等级返回颜色"""
        # B站灯牌颜色等级
        if level >= 30:
            return "#FF6B6B"  # 红色 - 高等级
        elif level >= 20:
            return "#FFB347"  # 橙色
        elif level >= 10:
            return "#87CEEB"  # 蓝色
        else:
            return "#98FB98"  # 浅绿色 - 低等级


class UserCardList(ctk.CTkScrollableFrame):
    """用户卡片列表"""

    def __init__(self, parent):
        super().__init__(parent)
        self._cards = []

    def set_users(self, users_data: list, on_user_click: callable = None):
        """设置用户列表"""
        # 清空现有卡片
        self.clear()

        # 创建新卡片
        for user_data in users_data:
            card = UserCard(self, user_data, on_user_click)
            card.pack(fill="x", pady=5)
            self._cards.append(card)

    def clear(self):
        """清空卡片"""
        for card in self._cards:
            card.destroy()
        self._cards = []
