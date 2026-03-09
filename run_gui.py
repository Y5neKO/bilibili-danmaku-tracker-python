#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站弹幕发送者查询工具 - GUI启动入口
"""

import sys
import os

# 确保当前目录在路径中
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    """主函数"""
    try:
        import customtkinter as ctk
    except ImportError:
        print("错误: 缺少 customtkinter 库")
        print("请运行: pip install customtkinter")
        sys.exit(1)

    try:
        from gui.main_window import MainWindow
    except ImportError as e:
        print(f"错误: 导入GUI模块失败: {e}")
        sys.exit(1)

    # 创建并运行主窗口
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
