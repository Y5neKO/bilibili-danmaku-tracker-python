# -*- coding: utf-8 -*-
"""
主窗口类
整合所有UI组件
"""

import customtkinter as ctk
from typing import Optional

from .models.app_state import AppState, TaskStatus
from .controllers.tracker_controller import TrackerController
from .views.search_view import SearchView
from .views.results_view import ResultsView
from .views.settings_view import SettingsView
from .views.cache_view import CacheView
from .components.progress_panel import ProgressPanel
from .components.log_viewer import LogViewer


class MainWindow(ctk.CTk):
    """主窗口"""

    def __init__(self):
        super().__init__()

        # 初始化状态和控制器
        self.app_state = AppState()
        self.controller = TrackerController(self.app_state)

        # 设置控制器回调
        self.controller.on_progress_update = self._on_progress_update
        self.controller.on_log_update = self._on_log_update
        self.controller.on_status_change = self._on_status_change
        self.controller.on_result_ready = self._on_result_ready
        self.controller.on_error = self._on_error

        # 配置窗口
        self.title("B站弹幕发送者查询工具")
        self.geometry("1100x750")
        self.minsize(900, 600)

        # 设置主题
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # 创建UI
        self._create_ui()

        # 启动回调处理定时器
        self._start_callback_timer()

        # 关闭窗口时的处理
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _create_ui(self):
        """创建UI组件"""
        # 主容器
        self.main_container = ctk.CTkFrame(self)
        self.main_container.pack(fill="both", expand=True, padx=10, pady=10)

        # 底部面板（先pack到底部，使用side="bottom"）
        self.bottom_frame = ctk.CTkFrame(self.main_container)
        self.bottom_frame.pack(side="bottom", fill="x", pady=(10, 0))

        # 进度面板
        self.progress_panel = ProgressPanel(self.bottom_frame, self.app_state)
        self.progress_panel.pack(fill="x", padx=5, pady=5)

        # 日志面板（可折叠）
        self.log_toggle = ctk.CTkButton(
            self.bottom_frame,
            text="显示日志 ▼",
            width=100,
            command=self._toggle_log
        )
        self.log_toggle.pack(pady=5)

        self.log_viewer = LogViewer(self.bottom_frame, self.app_state)
        # 初始隐藏日志

        # Tab视图（在底部面板之后pack，占据剩余空间）
        self.tabview = ctk.CTkTabview(self.main_container)
        self.tabview.pack(fill="both", expand=True)

        # 添加标签页
        self.tab_search = self.tabview.add("查询")
        self.tab_results = self.tabview.add("结果")
        self.tab_settings = self.tabview.add("设置")
        self.tab_cache = self.tabview.add("缓存")

        # 创建各个视图
        self.search_view = SearchView(
            self.tab_search,
            self.app_state,
            on_search=self._start_search,
            on_stop=self._stop_search
        )
        self.search_view.pack(fill="both", expand=True, padx=5, pady=5)

        self.results_view = ResultsView(
            self.tab_results,
            self.app_state,
            on_export=self._export_html
        )
        self.results_view.pack(fill="both", expand=True, padx=5, pady=5)

        self.settings_view = SettingsView(
            self.tab_settings,
            self.app_state
        )
        self.settings_view.pack(fill="both", expand=True, padx=5, pady=5)
        # 加载已保存的设置到UI
        self.settings_view.load_settings()

        self.cache_view = CacheView(
            self.tab_cache,
            self.app_state
        )
        self.cache_view.pack(fill="both", expand=True, padx=5, pady=5)

    def _toggle_log(self):
        """切换日志显示"""
        if self.log_viewer.winfo_ismapped():
            self.log_viewer.pack_forget()
            self.log_toggle.configure(text="显示日志 ▼")
        else:
            self.log_viewer.pack(fill="x", padx=5, pady=5)
            self.log_toggle.configure(text="隐藏日志 ▲")

    def _start_callback_timer(self):
        """启动回调处理定时器"""
        self._process_callbacks()
        self._timer_id = self.after(50, self._start_callback_timer)

    def _process_callbacks(self):
        """处理待处理的回调"""
        self.controller.process_callbacks()

    def _start_search(self, bvid: str, content: str, time_seconds: Optional[int],
                      use_regex: bool):
        """开始查询"""
        cookie = self.app_state.cookie
        threads = self.app_state.threads

        self.controller.start_search(
            bvid=bvid,
            content=content,
            time_seconds=time_seconds,
            use_regex=use_regex,
            cookie=cookie,
            threads=threads
        )

        # 禁用搜索按钮
        self.search_view.set_searching(True)

    def _stop_search(self):
        """停止查询"""
        self.controller.stop_search()
        self.search_view.set_searching(False)

    def _export_html(self) -> str:
        """导出HTML报告"""
        from tkinter import filedialog

        file_path = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("HTML文件", "*.html"), ("所有文件", "*.*")],
            initialfile=f"report_{self.app_state.video_info.bvid if self.app_state.video_info else 'unknown'}.html"
        )

        if file_path:
            if self.controller.export_html(file_path):
                return file_path
        return ""

    def _on_progress_update(self, current: int, total: int, message: str):
        """进度更新回调"""
        self.progress_panel.update_progress(current, total, message)

    def _on_log_update(self, message: str):
        """日志更新回调"""
        self.log_viewer.add_log(message)

    def _on_status_change(self, status: TaskStatus):
        """状态变化回调"""
        self.progress_panel.update_status(status)

        if status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.ERROR):
            self.search_view.set_searching(False)

            if status == TaskStatus.COMPLETED and self.app_state.search_result:
                # 切换到结果标签页
                self.tabview.set("结果")
                self.results_view.display_results(self.app_state.search_result, self.app_state.video_info)

    def _on_result_ready(self, result):
        """结果就绪回调"""
        self.results_view.display_results(result, self.app_state.video_info)

    def _on_error(self, error_message: str):
        """错误回调"""
        self.progress_panel.show_error(error_message)

    def _on_closing(self):
        """关闭窗口"""
        # 停止工作线程
        if self.controller.is_running():
            self.controller.stop_search()

        # 取消定时器
        if hasattr(self, '_timer_id'):
            self.after_cancel(self._timer_id)

        self.destroy()
