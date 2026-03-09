# -*- coding: utf-8 -*-
"""
业务逻辑控制器
管理后台任务和状态更新
"""

import threading
import queue
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

from ..models.app_state import AppState, TaskStatus, VideoInfo, ProgressInfo
from ..utils.threading import ThreadSafeCallback, CallbackType, WorkerThread


class TrackerController:
    """弹幕查询控制器

    负责协调DanmakuTracker和GUI之间的交互
    """

    def __init__(self, state: AppState):
        self.state = state
        self._callback = ThreadSafeCallback()
        self._worker = WorkerThread()

        # 注册默认回调
        self._callback.register_handler(CallbackType.PROGRESS, self._handle_progress)
        self._callback.register_handler(CallbackType.LOG, self._handle_log)
        self._callback.register_handler(CallbackType.STATUS, self._handle_status)
        self._callback.register_handler(CallbackType.RESULT, self._handle_result)
        self._callback.register_handler(CallbackType.ERROR, self._handle_error)

        # 外部回调（GUI设置）
        self.on_progress_update: Optional[Callable] = None
        self.on_log_update: Optional[Callable] = None
        self.on_status_change: Optional[Callable] = None
        self.on_result_ready: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

        # Tracker实例
        self._tracker = None

    def process_callbacks(self):
        """处理待处理的回调（在主线程调用）"""
        self._callback.process_pending()

    def _handle_progress(self, data: tuple):
        """处理进度更新"""
        current, total, message = data
        self.state.progress.current = current
        self.state.progress.total = total
        self.state.progress.detail = message
        if self.on_progress_update:
            self.on_progress_update(current, total, message)

    def _handle_log(self, message: str):
        """处理日志更新"""
        self.state.add_log(message)
        if self.on_log_update:
            self.on_log_update(message)

    def _handle_status(self, status: TaskStatus):
        """处理状态变化"""
        self.state.status = status
        if self.on_status_change:
            self.on_status_change(status)

    def _handle_result(self, result: Dict[str, Any]):
        """处理结果返回"""
        self.state.search_result = result
        self.state.status = TaskStatus.COMPLETED
        # 触发状态变化回调
        if self.on_status_change:
            self.on_status_change(TaskStatus.COMPLETED)
        if self.on_result_ready:
            self.on_result_ready(result)

    def _handle_error(self, error_message: str):
        """处理错误"""
        self.state.error_message = error_message
        self.state.status = TaskStatus.ERROR
        if self.on_error:
            self.on_error(error_message)

    def start_search(self, bvid: str, content: str, time_seconds: Optional[int] = None,
                     use_regex: bool = False, cookie: str = "", threads: int = 10):
        """开始查询任务"""
        if self._worker.is_running():
            self._callback.emit(CallbackType.ERROR, "已有任务在运行中")
            return

        # 重置状态
        self.state.reset()
        self.state.search_params.content = content
        self.state.search_params.time_seconds = time_seconds
        self.state.search_params.use_regex = use_regex
        self.state.cookie = cookie
        self.state.threads = threads

        # 启动工作线程
        self._worker.start(self._search_worker, args=(bvid, content, time_seconds, use_regex, cookie, threads))

    def _search_worker(self, stop_event: threading.Event, bvid: str, content: str,
                       time_seconds: Optional[int], use_regex: bool, cookie: str, threads: int):
        """查询工作线程"""
        import sys
        import os

        # 添加父目录到路径以导入danmaku_tracker
        parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        from danmaku_tracker import DanmakuTracker, exit_handler, BilibiliClient

        try:
            # 设置外部停止事件
            exit_handler.set_external_stop(stop_event)
            exit_handler.reset()

            # 阶段1：获取视频信息
            self._callback.emit(CallbackType.STATUS, TaskStatus.LOADING)
            self._callback.emit(CallbackType.LOG, f"正在获取视频信息: {bvid}")

            client = BilibiliClient(cookie)
            video_info = client.get_video_info(bvid)

            if stop_event.is_set():
                self._callback.emit(CallbackType.STATUS, TaskStatus.CANCELLED)
                return

            if not video_info:
                self._callback.emit(CallbackType.ERROR, f"无法获取视频信息: {bvid}")
                return

            # 保存视频信息到状态
            self.state.video_info = VideoInfo(
                bvid=bvid,
                cid=video_info.get('cid', 0),
                title=video_info.get('title', ''),
                url=f"https://www.bilibili.com/video/{bvid}",
                cover=video_info.get('cover', ''),
                owner_mid=video_info.get('owner_mid', 0),
                owner_name=video_info.get('owner_name', '')
            )

            self._callback.emit(CallbackType.LOG, f"视频标题: {self.state.video_info.title}")

            # 阶段2：创建Tracker并加载弹幕
            def on_progress(current, total, message):
                if stop_event.is_set():
                    return
                self._callback.emit(CallbackType.PROGRESS, (current, total, message))

            def on_log(message):
                if stop_event.is_set():
                    return
                self._callback.emit(CallbackType.LOG, message)

            def on_stage(stage_name):
                if stop_event.is_set():
                    return
                if stage_name == "cracking":
                    self._callback.emit(CallbackType.STATUS, TaskStatus.CRACKING)
                elif stage_name == "fetching":
                    self._callback.emit(CallbackType.STATUS, TaskStatus.FETCHING)
                elif stage_name == "loading":
                    self._callback.emit(CallbackType.STATUS, TaskStatus.LOADING)

            self._tracker = DanmakuTracker(
                cookie=cookie,
                threads=threads,
                on_progress=on_progress,
                on_log=on_log,
                on_stage=on_stage
            )

            # 加载弹幕
            cid = video_info.get('cid')
            if not cid:
                # 尝试获取分P的cid
                pages = video_info.get('pages', [])
                if pages:
                    cid = pages[0].get('cid')

            if not cid:
                self._callback.emit(CallbackType.ERROR, "无法获取视频CID")
                return

            self._tracker.load_danmaku(cid)

            if stop_event.is_set():
                self._callback.emit(CallbackType.STATUS, TaskStatus.CANCELLED)
                return

            # 阶段3：执行查询
            result = self._tracker.search_by_content(
                content=content,
                time_seconds=time_seconds,
                use_regex=use_regex,
                threads=threads
            )

            if stop_event.is_set() or result.get('cancelled', False):
                self._callback.emit(CallbackType.STATUS, TaskStatus.CANCELLED)
                return

            # 完成
            self._callback.emit(CallbackType.RESULT, result)

        except Exception as e:
            import traceback
            error_msg = f"查询出错: {str(e)}"
            traceback.print_exc()
            self._callback.emit(CallbackType.ERROR, error_msg)
        finally:
            exit_handler.clear_external_stop()

    def stop_search(self):
        """停止查询任务"""
        if self._worker.is_running():
            self._worker.stop()
            self._callback.emit(CallbackType.STATUS, TaskStatus.CANCELLED)
            self._callback.emit(CallbackType.LOG, "任务已取消")

    def is_running(self) -> bool:
        """检查是否有任务在运行"""
        return self._worker.is_running()

    def export_html(self, output_path: str) -> bool:
        """导出HTML报告"""
        if not self._tracker or not self.state.search_result:
            return False

        try:
            video_info = self.state.video_info
            self._tracker.export_html_report(
                pattern=self.state.search_params.content,
                time_seconds=self.state.search_params.time_seconds,
                use_regex=self.state.search_params.use_regex,
                output_file=output_path,
                video_title=video_info.title if video_info else "",
                video_url=video_info.url if video_info else "",
                cached_data=self.state.search_result
            )
            return True
        except Exception as e:
            self._callback.emit(CallbackType.ERROR, f"导出失败: {str(e)}")
            return False
