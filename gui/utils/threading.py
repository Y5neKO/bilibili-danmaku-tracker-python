# -*- coding: utf-8 -*-
"""
线程工具
提供线程安全的回调机制
"""

import threading
import queue
from typing import Callable, Any, Optional
from dataclasses import dataclass
from enum import Enum


class CallbackType(Enum):
    """回调类型"""
    PROGRESS = "progress"      # 进度更新
    LOG = "log"               # 日志消息
    STATUS = "status"         # 状态变化
    RESULT = "result"         # 结果返回
    ERROR = "error"           # 错误


@dataclass
class CallbackMessage:
    """回调消息"""
    type: CallbackType
    data: Any


class ThreadSafeCallback:
    """线程安全回调管理器

    通过队列实现后台线程到主线程的安全通信
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._handlers: dict[CallbackType, Callable] = {}
        self._stop_event = threading.Event()

    def register_handler(self, callback_type: CallbackType, handler: Callable):
        """注册回调处理器"""
        self._handlers[callback_type] = handler

    def unregister_handler(self, callback_type: CallbackType):
        """注销回调处理器"""
        if callback_type in self._handlers:
            del self._handlers[callback_type]

    def emit(self, callback_type: CallbackType, data: Any = None):
        """发送回调消息（线程安全）"""
        self._queue.put(CallbackMessage(callback_type, data))

    def process_pending(self):
        """处理待处理的回调（在主线程调用）"""
        processed = 0
        while not self._queue.empty() and processed < 100:  # 限制每次处理数量
            try:
                msg = self._queue.get_nowait()
                handler = self._handlers.get(msg.type)
                if handler:
                    try:
                        handler(msg.data)
                    except Exception as e:
                        print(f"回调处理错误: {e}")
                processed += 1
            except queue.Empty:
                break

    def stop(self):
        """停止回调处理"""
        self._stop_event.set()

    def is_stopped(self) -> bool:
        """检查是否已停止"""
        return self._stop_event.is_set()

    def clear(self):
        """清空队列"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


class StoppableThread(threading.Thread):
    """可停止的线程"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def stop(self):
        """请求停止线程"""
        self._stop_event.set()

    def is_stopped(self) -> bool:
        """检查是否请求停止"""
        return self._stop_event.is_set()

    def check_stopped(self):
        """检查停止状态，如果已停止则抛出异常"""
        if self._stop_event.is_set():
            raise ThreadStoppedException("线程被请求停止")


class ThreadStoppedException(Exception):
    """线程停止异常"""
    pass


class WorkerThread:
    """工作线程管理器

    封装线程创建、启动、停止的便捷工具
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self, target: Callable, args: tuple = (), kwargs: dict = None):
        """启动工作线程"""
        if self.is_running():
            raise RuntimeError("工作线程已在运行")

        self._stop_event.clear()
        kwargs = kwargs or {}

        def wrapped_target():
            try:
                target(self._stop_event, *args, **kwargs)
            except Exception as e:
                print(f"工作线程异常: {e}")

        self._thread = threading.Thread(target=wrapped_target, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """停止工作线程"""
        if not self.is_running():
            return True

        self._stop_event.set()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def is_running(self) -> bool:
        """检查线程是否运行中"""
        return self._thread is not None and self._thread.is_alive()

    def should_stop(self) -> bool:
        """检查是否应该停止"""
        return self._stop_event.is_set()
