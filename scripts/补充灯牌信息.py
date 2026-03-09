#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量补充 userinfo 目录下缺少灯牌信息的用户
"""

import os
import json
import time
import requests

USERINFO_DIR = "cache/userinfo"
COOKIE = ""

# 读取 Cookie
if os.path.exists("cookie.txt"):
    with open("cookie.txt", "r") as f:
        COOKIE = f.read().strip()


def get_medal_wall(uid: int) -> dict:
    """获取用户粉丝灯牌信息"""
    url = f"https://api.live.bilibili.com/xlive/web-ucenter/user/MedalWall?target_id={uid}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://space.bilibili.com/{uid}/",
    }
    if COOKIE:
        headers["Cookie"] = COOKIE

    resp = requests.get(url, headers=headers, timeout=10)
    data = resp.json()

    if data.get("code") == 0 and data.get("data"):
        return data["data"]
    return None


def parse_medal_info(medal_data: dict) -> list:
    """解析灯牌信息"""
    medals = []
    for item in medal_data.get("list", []):
        medal_info = item.get("medal_info", {})
        medals.append({
            "medal_name": medal_info.get("medal_name", ""),
            "target_name": item.get("target_name", ""),
            "level": medal_info.get("level", 0),
            "target_id": medal_info.get("target_id", 0),
            "wearing_status": medal_info.get("wearing_status", 0)
        })
    return medals


def main():
    if not os.path.exists(USERINFO_DIR):
        print("userinfo 目录不存在")
        return

    # 获取所有用户 JSON 文件
    files = [f for f in os.listdir(USERINFO_DIR) if f.endswith(".json") and "-" not in f]

    # 筛选需要补充的文件
    need_update = []
    for filename in files:
        filepath = os.path.join(USERINFO_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "medals" not in data:
                uid = int(filename.replace(".json", ""))
                need_update.append((uid, filepath, data))
        except Exception as e:
            print(f"读取 {filename} 失败: {e}")

    total = len(need_update)
    if total == 0:
        print("所有用户都已有灯牌信息，无需补充")
        return

    print(f"找到 {total} 个用户需要补充灯牌信息")
    print("-" * 50)

    success = 0
    for i, (uid, filepath, data) in enumerate(need_update, 1):
        print(f"[{i}/{total}] 正在获取 UID {uid} ({data.get('name', '未知')})...", end=" ")

        try:
            medal_data = get_medal_wall(uid)
            if medal_data:
                medals = parse_medal_info(medal_data)
                data["medals"] = medals

                # 保存用户信息
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                # 保存完整灯牌数据
                medal_filepath = os.path.join(USERINFO_DIR, f"{uid}-MedalWall.json")
                with open(medal_filepath, "w", encoding="utf-8") as f:
                    json.dump(medal_data, f, ensure_ascii=False, indent=2)

                print(f"成功 ({len(medals)} 个灯牌)")
                success += 1
            else:
                data["medals"] = []
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print("无灯牌")

            time.sleep(0.5)  # 避免请求过快

        except Exception as e:
            print(f"失败: {e}")

    print("-" * 50)
    print(f"完成！成功补充 {success}/{total} 个用户")


if __name__ == "__main__":
    main()
