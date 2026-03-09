# -*- coding: utf-8 -*-
"""
配置管理模块
"""

import json
import os
from dataclasses import asdict
from typing import Optional, Dict, Any


class ConfigManager:
    """配置管理器 - 处理配置的持久化"""

    DEFAULT_CONFIG = {
        "cookie": "",
        "threads": 10,
        "theme": "dark"
    }

    def __init__(self, config_file: str = None):
        # 如果未指定配置文件路径，使用应用程序所在目录
        if config_file is None:
            # 获取应用程序根目录（gui的父目录）
            app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.config_file = os.path.join(app_dir, "config.json")
        else:
            self.config_file = config_file
        self._config: Dict[str, Any] = {}
        self._load()

    def _load(self):
        """从文件加载配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._config = self.DEFAULT_CONFIG.copy()
        else:
            self._config = self.DEFAULT_CONFIG.copy()

    def save(self):
        """保存配置到文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"保存配置失败: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项"""
        return self._config.get(key, default)

    def set(self, key: str, value: Any, auto_save: bool = True):
        """设置配置项"""
        self._config[key] = value
        if auto_save:
            self.save()

    def get_all(self) -> Dict[str, Any]:
        """获取所有配置"""
        return self._config.copy()

    @property
    def cookie(self) -> str:
        """获取Cookie"""
        return self._config.get("cookie", "")

    @cookie.setter
    def cookie(self, value: str):
        """设置Cookie"""
        self._config["cookie"] = value
        self.save()

    @property
    def threads(self) -> int:
        """获取线程数"""
        return self._config.get("threads", 10)

    @threads.setter
    def threads(self, value: int):
        """设置线程数"""
        self._config["threads"] = value
        self.save()

    @property
    def theme(self) -> str:
        """获取主题"""
        return self._config.get("theme", "dark-blue")

    @theme.setter
    def theme(self, value: str):
        """设置主题"""
        self._config["theme"] = value
        self.save()
