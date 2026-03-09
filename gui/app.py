#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUI应用入口模块
可以直接运行此模块启动GUI
"""

from .main_window import MainWindow
import customtkinter as ctk


def create_app() -> MainWindow:
    """创建应用实例"""
    return MainWindow()


def run():
    """运行应用"""
    app = create_app()
    app.mainloop()


if __name__ == "__main__":
    run()
