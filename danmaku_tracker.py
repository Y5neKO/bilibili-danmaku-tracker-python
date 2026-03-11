#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bilibili弹幕发送者查询工具
根据弹幕内容和时间查询发送者信息

使用方法:
    python danmaku_tracker.py --bvid BV1xx --content "弹幕内容" [--time 00:30]
    python danmaku_tracker.py --cid 123456 --content "弹幕内容" [--time 00:30]
    python danmaku_tracker.py --bvid BV1xx --regex "哈+" --count-only  # 模糊匹配+统计数量
"""

import argparse
import os
import requests
import re
import struct
import html as html_escape
import json
import signal
import sys
from datetime import datetime
from typing import List, Dict, Optional, Set, Tuple, Callable
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
import threading
import asyncio

# bilibili_api 用于获取用户信息
from bilibili_api import user, sync as bilibili_sync


# ============== 退出检测 ==============

class ExitHandler:
    """全局退出处理器，用于优雅地终止程序"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._exit_flag = threading.Event()
                    cls._instance._external_stop = None  # 外部停止事件
                    cls._instance._setup_signal_handlers()
        return cls._instance

    def _setup_signal_handlers(self):
        """设置信号处理器（仅在主线程中）"""
        try:
            # 只在主线程中设置信号处理器
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGINT, self._signal_handler)
                signal.signal(signal.SIGTERM, self._signal_handler)
        except (ValueError, OSError):
            # 在某些环境下（如GUI线程）可能无法设置信号处理器
            pass

    def _signal_handler(self, signum, frame):
        """信号处理函数"""
        sig_name = signal.Signals(signum).name
        print(f"\n\n收到退出信号 ({sig_name})，正在优雅退出...")
        self._exit_flag.set()

    def set_external_stop(self, stop_event: threading.Event):
        """设置外部停止事件（GUI模式使用）"""
        self._external_stop = stop_event

    def clear_external_stop(self):
        """清除外部停止事件"""
        self._external_stop = None

    def should_exit(self) -> bool:
        """检查是否应该退出"""
        if self._exit_flag.is_set():
            return True
        if self._external_stop is not None and self._external_stop.is_set():
            return True
        return False

    def wait_for_exit(self, timeout: float = None) -> bool:
        """等待退出信号"""
        return self._exit_flag.wait(timeout)

    def reset(self):
        """重置退出标志"""
        self._exit_flag.clear()
        self._external_stop = None


# 全局退出处理器实例
exit_handler = ExitHandler()


# ============== Protobuf 弹幕解析 ==============

# 弹幕数据结构定义 (根据原始JS脚本的proto定义)
@dataclass
class DanmakuItem:
    id: int = 0
    progress: int = 0  # 毫秒
    mode: int = 0
    fontsize: int = 0
    color: int = 0
    mid_hash: str = ""  # 用户ID的CRC32哈希
    content: str = ""
    ctime: int = 0
    weight: int = 0
    action: str = ""
    pool: int = 0
    id_str: str = ""


def decode_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """解码Protobuf varint"""
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            return result, pos
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def decode_signed_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """解码有符号varint (zigzag编码)"""
    value, pos = decode_varint(data, pos)
    return (value >> 1) ^ -(value & 1), pos


def decode_string(data: bytes, pos: int, length: int) -> str:
    """解码字符串"""
    try:
        return data[pos:pos+length].decode('utf-8')
    except:
        return data[pos:pos+length].decode('utf-8', errors='ignore')


def decode_dm_item(data: bytes) -> DanmakuItem:
    """解码单条弹幕数据"""
    item = DanmakuItem()
    pos = 0

    while pos < len(data):
        # 读取field tag
        tag, pos = decode_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # Varint
            value, pos = decode_varint(data, pos)
            if field_num == 1:
                item.id = value
            elif field_num == 2:
                item.progress = value
            elif field_num == 3:
                item.mode = value
            elif field_num == 4:
                item.fontsize = value
            elif field_num == 5:
                item.color = value
            elif field_num == 8:
                item.ctime = value
            elif field_num == 9:
                item.weight = value
            elif field_num == 11:
                item.pool = value
        elif wire_type == 2:  # Length-delimited
            length, pos = decode_varint(data, pos)
            value_bytes = data[pos:pos+length]
            pos += length
            if field_num == 6:
                item.mid_hash = decode_string(value_bytes, 0, len(value_bytes))
            elif field_num == 7:
                item.content = decode_string(value_bytes, 0, len(value_bytes))
            elif field_num == 10:
                item.action = decode_string(value_bytes, 0, len(value_bytes))
            elif field_num == 12:
                item.id_str = decode_string(value_bytes, 0, len(value_bytes))
        elif wire_type == 5:  # 32-bit (fixed32/float)
            # 对于 color 字段使用 fixed32
            if field_num == 5:
                item.color = struct.unpack('<I', data[pos:pos+4])[0]
            pos += 4
        else:
            # 跳过未知类型
            if wire_type == 1:  # 64-bit
                pos += 8
            elif wire_type == 5:  # 32-bit
                pos += 4

    return item


def decode_dm_list(data: bytes) -> List[DanmakuItem]:
    """解码弹幕列表 (Protobuf repeated字段)"""
    items = []
    pos = 0

    while pos < len(data):
        tag, pos = decode_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07

        if field_num == 1 and wire_type == 2:  # repeated dmItem
            length, pos = decode_varint(data, pos)
            item_data = data[pos:pos+length]
            pos += length
            items.append(decode_dm_item(item_data))
        else:
            # 跳过其他字段
            if wire_type == 0:
                _, pos = decode_varint(data, pos)
            elif wire_type == 2:
                length, pos = decode_varint(data, pos)
                pos += length
            elif wire_type == 1:
                pos += 8
            elif wire_type == 5:
                pos += 4

    return items


# ============== CRC32 匹配算法 ==============

class CRC32Cracker:
    """CRC32哈希匹配器 - 用于将midHash还原为UID"""

    CRCPOLYNOMIAL = 0xEDB88320

    def __init__(self):
        self.crctable = self._create_table()

    def _create_table(self) -> List[int]:
        """生成CRC32查找表"""
        table = [0] * 256
        for i in range(256):
            crcreg = i
            for _ in range(8):
                if (crcreg & 1) != 0:
                    crcreg = self.CRCPOLYNOMIAL ^ (crcreg >> 1)
                else:
                    crcreg = crcreg >> 1
            table[i] = crcreg
        return table

    def _crc32(self, string: str) -> int:
        """计算字符串的CRC32"""
        crcstart = 0xFFFFFFFF
        for char in str(string):
            index = (crcstart ^ ord(char)) & 255
            crcstart = (crcstart >> 8) ^ self.crctable[index]
        return crcstart

    def _crc32_last_index(self, string: str) -> int:
        """计算CRC32并返回最后一个索引"""
        crcstart = 0xFFFFFFFF
        index = 0
        for char in str(string):
            index = (crcstart ^ ord(char)) & 255
            crcstart = (crcstart >> 8) ^ self.crctable[index]
        return index

    def _get_crc_index(self, t: int) -> int:
        """查找CRC表中高8位等于t的索引"""
        for i in range(256):
            if self.crctable[i] >> 24 == t:
                return i
        return -1

    def _deep_check(self, i: int, index: List[int]) -> List:
        """深度检查并还原后3位数字"""
        string = ""
        hashcode = self._crc32(i)
        tc = hashcode & 0xff ^ index[2]
        if not (48 <= tc <= 57):
            return [0]
        string += str(tc - 48)

        hashcode = self.crctable[index[2]] ^ (hashcode >> 8)
        tc = hashcode & 0xff ^ index[1]
        if not (48 <= tc <= 57):
            return [0]
        string += str(tc - 48)

        hashcode = self.crctable[index[1]] ^ (hashcode >> 8)
        tc = hashcode & 0xff ^ index[0]
        if not (48 <= tc <= 57):
            return [0]
        string += str(tc - 48)

        return [1, string]

    def crack(self, uid_hash: str, max_digit: int = 10) -> List[int]:
        """
        匹配UID哈希

        Args:
            uid_hash: 用户ID的CRC32哈希值(十六进制字符串)
            max_digit: UID最大位数(默认10位，此实现支持8位)

        Returns:
            可能的UID列表
        """
        try:
            ht = int(f"0x{uid_hash}", 16) ^ 0xffffffff
        except ValueError:
            return []

        index = [0] * 4
        for i in range(3, -1, -1):
            index[3-i] = self._get_crc_index(ht >> (i * 8))
            if index[3-i] == -1:
                return []
            snum = self.crctable[index[3-i]]
            ht ^= snum >> ((3-i) * 8)

        # 遍历前5位数字(0-99999999)
        for i in range(100000000):
            lastindex = self._crc32_last_index(i)
            if lastindex == index[3]:
                deep_check_data = self._deep_check(i, index)
                if deep_check_data[0]:
                    uid = int(f"{i}{deep_check_data[1]}")
                    return [uid]

        return []


# ============== 哈希-UID映射缓存 ==============

class HashCache:
    """哈希到UID的映射缓存管理器"""

    def __init__(self, cache_file: str = "hash_cache.json"):
        self.cache_file = cache_file
        self.cache: Dict[str, List[int]] = {}  # hash -> [uid1, uid2, ...]
        self._lock = threading.Lock()  # 内部锁，保护缓存操作
        self._load()

    def _load(self):
        """从文件加载缓存"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.cache = {}

    def save(self):
        """保存缓存到文件（原子写入）"""
        with self._lock:
            try:
                # 先写入临时文件
                temp_file = self.cache_file + '.tmp'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.cache, f, ensure_ascii=False, indent=2)
                # 原子重命名（POSIX保证原子性）
                os.replace(temp_file, self.cache_file)
            except IOError as e:
                print(f"保存缓存失败: {e}")

    def get(self, mid_hash: str) -> Optional[List[int]]:
        """获取缓存的UID列表"""
        with self._lock:
            return self.cache.get(mid_hash)

    def set(self, mid_hash: str, uids: List[int]):
        """设置缓存"""
        with self._lock:
            self.cache[mid_hash] = uids

    def set_and_save(self, mid_hash: str, uids: List[int]):
        """设置缓存并立即保存（原子操作）"""
        with self._lock:
            self.cache[mid_hash] = uids
            try:
                # 先写入临时文件
                temp_file = self.cache_file + '.tmp'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.cache, f, ensure_ascii=False, indent=2)
                # 原子重命名
                os.replace(temp_file, self.cache_file)
            except IOError as e:
                print(f"保存缓存失败: {e}")

    def set_empty(self, mid_hash: str):
        """标记为无法匹配（空列表也缓存，避免重复匹配）"""
        self.cache[mid_hash] = []

    def clear(self):
        """清空缓存"""
        self.cache = {}
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)

    def __contains__(self, mid_hash: str) -> bool:
        return mid_hash in self.cache

    def __len__(self) -> int:
        return len(self.cache)


# ============== 用户信息缓存 ==============

class UserInfoCache:
    """用户信息缓存管理器（一个UID一个文件，包含用户信息和灯牌信息）"""

    def __init__(self, cache_dir: str = "userinfo"):
        self.cache_dir = cache_dir
        self._lock = threading.Lock()
        self._ensure_dir()

    def _ensure_dir(self):
        """确保缓存目录存在"""
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)

    def _get_cache_path(self, uid: int) -> str:
        """获取用户信息缓存文件路径"""
        return os.path.join(self.cache_dir, f"{uid}.json")

    def _get_medal_cache_path(self, uid: int) -> str:
        """获取灯牌信息缓存文件路径"""
        return os.path.join(self.cache_dir, f"{uid}-MedalWall.json")

    def get(self, uid: int) -> Optional[Dict]:
        """获取缓存的用户信息"""
        cache_path = self._get_cache_path(uid)
        with self._lock:
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except (json.JSONDecodeError, IOError):
                    return None
        return None

    def get_medal(self, uid: int) -> Optional[Dict]:
        """获取缓存的灯牌信息"""
        cache_path = self._get_medal_cache_path(uid)
        with self._lock:
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except (json.JSONDecodeError, IOError):
                    return None
        return None

    def set(self, uid: int, user_info: Dict):
        """保存用户信息到缓存"""
        cache_path = self._get_cache_path(uid)
        with self._lock:
            try:
                # 先写入临时文件
                temp_file = cache_path + '.tmp'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(user_info, f, ensure_ascii=False, indent=2)
                # 原子重命名
                os.replace(temp_file, cache_path)
            except IOError as e:
                print(f"保存用户缓存失败 (UID {uid}): {e}")

    def set_medal(self, uid: int, medal_info: Dict):
        """保存灯牌信息到缓存"""
        cache_path = self._get_medal_cache_path(uid)
        with self._lock:
            try:
                temp_file = cache_path + '.tmp'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(medal_info, f, ensure_ascii=False, indent=2)
                os.replace(temp_file, cache_path)
            except IOError as e:
                print(f"保存灯牌缓存失败 (UID {uid}): {e}")

    def exists(self, uid: int) -> bool:
        """检查用户信息缓存是否存在"""
        return os.path.exists(self._get_cache_path(uid))

    def medal_exists(self, uid: int) -> bool:
        """检查灯牌信息缓存是否存在"""
        return os.path.exists(self._get_medal_cache_path(uid))

    def delete(self, uid: int):
        """删除单个用户的所有缓存"""
        with self._lock:
            for path in [self._get_cache_path(uid), self._get_medal_cache_path(uid)]:
                if os.path.exists(path):
                    os.remove(path)

    def clear(self):
        """清空所有用户缓存（包括用户信息和灯牌信息）"""
        with self._lock:
            if os.path.exists(self.cache_dir):
                for filename in os.listdir(self.cache_dir):
                    if filename.endswith('.json'):
                        filepath = os.path.join(self.cache_dir, filename)
                        os.remove(filepath)


# ============== Bilibili API 客户端 ==============

class BilibiliClient:
    """Bilibili API客户端"""

    def __init__(self, cookie: str = ""):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        })
        if cookie:
            self.session.headers["Cookie"] = cookie

    def get_cid_by_bvid(self, bvid: str) -> Optional[int]:
        """通过BV号获取视频CID"""
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        try:
            resp = self.session.get(url, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                # 返回第一个分P的cid
                return data["data"]["cid"]
            else:
                print(f"获取CID失败: {data.get('message')}")
                return None
        except Exception as e:
            print(f"请求失败: {e}")
            return None

    def get_video_info(self, bvid: str) -> Optional[Dict]:
        """通过BV号获取视频信息

        Returns:
            包含视频信息的字典，None表示请求失败
        """
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        try:
            resp = self.session.get(url, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                video_data = data["data"]
                return {
                    "cid": video_data.get("cid"),
                    "title": video_data.get("title"),
                    "cover": video_data.get("pic"),  # 封面
                    "owner_mid": video_data.get("owner", {}).get("mid"),
                    "owner_name": video_data.get("owner", {}).get("name"),
                    "pages": video_data.get("pages", [])
                }
            else:
                print(f"获取视频信息失败: {data.get('message')}")
                return None
        except Exception as e:
            print(f"请求失败: {e}")
            return None


    def get_danmaku(self, cid: int, segment: int = 1) -> List[DanmakuItem]:
        """获取弹幕数据"""
        url = f"https://api.bilibili.com/x/v2/dm/web/seg.so?type=1&oid={cid}&segment_index={segment}"

        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 0:
                return decode_dm_list(resp.content)
        except Exception as e:
            print(f"获取弹幕失败: {e}")
        return []

    def get_all_danmaku(self, cid: int, max_pages: int = 30) -> List[DanmakuItem]:
        """获取所有弹幕"""
        all_danmaku = []
        for page in range(1, max_pages + 1):
            print(f"正在获取第 {page} 页弹幕...")
            danmaku_list = self.get_danmaku(cid, page)
            if not danmaku_list:
                print(f"第 {page} 页无数据，停止获取")
                break
            all_danmaku.extend(danmaku_list)
            print(f"  获取到 {len(danmaku_list)} 条弹幕")
        return all_danmaku


# ============== 用户信息获取 (使用 bilibili_api) ==============

# 全局用户信息缓存实例（在 DanmakuTracker 初始化时设置）
_user_info_cache: Optional[UserInfoCache] = None
_refresh_user_cache = False
_bilibili_cookie: str = ""  # 用于灯牌API请求


def get_medal_wall(uid: int) -> Optional[Dict]:
    """
    获取用户粉丝灯牌信息

    Args:
        uid: 用户ID

    Returns:
        灯牌信息字典，失败返回None
    """
    global _user_info_cache, _refresh_user_cache, _bilibili_cookie

    # 尝试从缓存读取
    if _user_info_cache and not _refresh_user_cache:
        cached = _user_info_cache.get_medal(uid)
        if cached:
            return cached

    url = f"https://api.live.bilibili.com/xlive/web-ucenter/user/MedalWall?target_id={uid}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://space.bilibili.com/{uid}/",
    }
    if _bilibili_cookie:
        headers["Cookie"] = _bilibili_cookie

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()

        if data.get("code") == 0 and data.get("data"):
            medal_data = data["data"]
            # 保存到缓存
            if _user_info_cache:
                _user_info_cache.set_medal(uid, medal_data)
            return medal_data
    except Exception as e:
        print(f"  [警告] 获取灯牌信息失败 (UID {uid}): {e}")

    return None


def get_user_info_by_api(uid: int, max_retries: int = 3) -> Optional[Dict]:
    """
    使用 bilibili_api 库获取用户信息
    线程安全，每次调用创建新的事件循环
    支持本地缓存（userinfo/{uid}.json）

    Args:
        uid: 用户ID
        max_retries: 最大重试次数（默认3次，风控校验失败时会无限重试）

    Returns:
        用户信息字典，失败返回None
    """
    global _user_info_cache, _refresh_user_cache

    # 尝试从缓存读取
    if _user_info_cache and not _refresh_user_cache:
        cached = _user_info_cache.get(uid)
        if cached:
            # 检查是否需要补充灯牌信息
            if "medals" not in cached:
                # 尝试读取灯牌缓存
                medal_cached = _user_info_cache.get_medal(uid)
                if medal_cached:
                    medals = _parse_medal_info(medal_cached)
                    cached["medals"] = medals
                else:
                    # 灯牌缓存也不存在，去获取
                    medal_data = get_medal_wall(uid)
                    if medal_data:
                        cached["medals"] = _parse_medal_info(medal_data)
                    else:
                        cached["medals"] = []
                    # 更新用户缓存
                    _user_info_cache.set(uid, cached)
            # 缓存命中，显示标记
            print(f"  [缓存] -> {cached.get('name', '未知')}")
            return cached

    async def _get_info():
        u = user.User(uid=uid)
        return await u.get_user_info()

    attempt = 0
    while True:
        is_risk_control_error = False
        try:
            # 在新线程中运行异步函数
            info = bilibili_sync(_get_info())

            if info:
                result = {
                    "uid": uid,
                    "name": info.get("name", ""),
                    "avatar": info.get("face", ""),
                    "sign": info.get("sign", ""),
                    "space_url": f"https://space.bilibili.com/{uid}"
                }

                # 获取灯牌信息
                medal_data = get_medal_wall(uid)
                if medal_data:
                    medals = _parse_medal_info(medal_data)
                    result["medals"] = medals

                # 保存到缓存
                if _user_info_cache:
                    _user_info_cache.set(uid, result)
                return result
            else:
                # info 为 None 或空 dict
                if attempt < max_retries - 1:
                    attempt += 1
                    continue
                else:
                    print(f"  [警告] API返回空数据 (attempt {attempt+1}/{max_retries})")
                    return None
        except Exception as e:
            error_msg = str(e)
            # 检测风控校验失败，无限重试
            if "风控校验失败" in error_msg:
                is_risk_control_error = True
                if attempt == 0 or attempt % 5 == 0:  # 每5次打印一次提示，避免刷屏
                    print(f"  [风控] 遇到风控校验，正在重试... (UID {uid}, 第{attempt+1}次)")
                attempt += 1
                continue
            else:
                # 其他错误按正常重试逻辑处理
                if attempt < max_retries - 1:
                    attempt += 1
                    continue
                # 最后一次失败，输出详细错误
                print(f"  [错误] 获取失败: {error_msg}")
                return None

        # 非风控错误且超过重试次数，退出循环
        if not is_risk_control_error and attempt >= max_retries:
            break

    return None


def _parse_medal_info(medal_data: Dict) -> List[Dict]:
    """
    解析灯牌信息，提取关键字段

    Args:
        medal_data: API返回的灯牌数据

    Returns:
        简化的灯牌列表 [{"medal_name": "xxx", "target_name": "xxx", "level": 20}, ...]
    """
    medals = []
    for item in medal_data.get("list", []):
        medal_info = item.get("medal_info", {})
        medals.append({
            "medal_name": medal_info.get("medal_name", ""),
            "target_name": item.get("target_name", ""),
            "level": medal_info.get("level", 0),
            "target_id": medal_info.get("target_id", 0),
            "wearing_status": medal_info.get("wearing_status", 0)  # 1=正在佩戴
        })
    return medals


# 保留旧方法作为备用（不需要bilibili_api依赖）
def get_user_info_by_html(session: requests.Session, uid: int) -> Optional[Dict]:
    """通过HTML解析获取用户信息（备用方法）"""
    url = f"https://m.bilibili.com/space/{uid}"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1",
        "Cookie": session.headers.get("Cookie", "")
    }

    try:
        resp = session.get(url, headers=headers, timeout=10)
        html = resp.text

        name_match = re.search(r'<title>(.*?)的个人空间', html)
        name = name_match.group(1) if name_match else None

        avatar_match = re.search(r'<link rel="apple-touch-icon" href="//([^"]+)"', html)
        avatar = "https://" + avatar_match.group(1) if avatar_match else None

        sign = ""
        desc_match = re.search(r'<meta name="description" content="[^"]*第一时间了解UP主动态[。，]?([^"]*)"', html)
        if desc_match:
            sign = desc_match.group(1).strip()

        if name and name != "false":
            return {
                "uid": uid,
                "name": name,
                "avatar": avatar,
                "sign": sign,
                "space_url": f"https://space.bilibili.com/{uid}"
            }
    except Exception as e:
        pass

    return None


# ============== 弹幕查询器 ==============

class DanmakuTracker:
    """弹幕发送者查询器"""

    def __init__(self, cookie: str = "", threads: int = 10,
                 cache_file: str = "cache/hash_cache.json", refresh_cache: bool = False,
                 user_cache_dir: str = "cache/userinfo", refresh_user_cache: bool = False,
                 max_retries: int = 3,
                 on_progress: Callable[[int, int, str], None] = None,
                 on_log: Callable[[str], None] = None,
                 on_stage: Callable[[str], None] = None):
        """
        Args:
            cookie: B站Cookie
            threads: 线程数
            cache_file: 哈希缓存文件
            refresh_cache: 是否刷新哈希缓存
            user_cache_dir: 用户信息缓存目录
            refresh_user_cache: 是否刷新用户缓存
            max_retries: 最大重试次数
            on_progress: 进度回调 (current, total, message)
            on_log: 日志回调 (message)
            on_stage: 阶段变化回调 (stage_name)
        """
        global _user_info_cache, _refresh_user_cache, _bilibili_cookie

        # 设置 Cookie 用于灯牌 API
        _bilibili_cookie = cookie

        self.client = BilibiliClient(cookie)
        self.cracker = CRC32Cracker()
        self.threads = threads
        self.max_retries = max_retries
        self.danmaku_list: List[DanmakuItem] = []  # 保存所有弹幕
        self.danmaku_map: Dict[str, List[str]] = {}  # key: "内容|秒数", value: [midHash列表]

        # GUI回调
        self._on_progress = on_progress
        self._on_log = on_log
        self._on_stage = on_stage

        # 哈希-UID映射缓存（独立管理）
        self.hash_cache = HashCache(cache_file)
        if refresh_cache:
            self._log("已清空哈希缓存")
            self.hash_cache.clear()

        # 用户信息缓存（独立管理）
        _user_info_cache = UserInfoCache(user_cache_dir)
        _refresh_user_cache = refresh_user_cache
        if refresh_user_cache:
            self._log("已清空用户信息缓存")
            _user_info_cache.clear()

    def _log(self, message: str):
        """输出日志"""
        print(message)
        if self._on_log:
            self._on_log(message)

    def _progress(self, current: int, total: int, message: str = ""):
        """报告进度"""
        if self._on_progress:
            self._on_progress(current, total, message)

    def _stage(self, stage_name: str):
        """报告阶段变化"""
        if self._on_stage:
            self._on_stage(stage_name)

    def load_danmaku(self, cid: int):
        """加载弹幕数据"""
        self._stage("loading")
        self._log(f"正在加载弹幕数据 (CID: {cid})...")
        self.danmaku_list = self.client.get_all_danmaku(cid)

        # 构建索引
        for dm in self.danmaku_list:
            key = f"{dm.content}|{dm.progress // 1000}"
            if key not in self.danmaku_map:
                self.danmaku_map[key] = []
            if dm.mid_hash not in self.danmaku_map[key]:
                self.danmaku_map[key].append(dm.mid_hash)

        print(f"加载完成，共 {len(self.danmaku_list)} 条弹幕")

    def _match_content(self, dm_content: str, pattern: str, use_regex: bool = False) -> bool:
        """
        匹配弹幕内容

        Args:
            dm_content: 弹幕内容
            pattern: 匹配模式
            use_regex: 是否使用正则表达式

        Returns:
            是否匹配
        """
        if use_regex:
            try:
                return bool(re.search(pattern, dm_content))
            except re.error:
                print(f"正则表达式错误: {pattern}")
                return False
        else:
            return pattern in dm_content

    def _get_matched_hashes(self, pattern: str, time_seconds: Optional[int] = None,
                            use_regex: bool = False) -> Set[str]:
        """
        获取匹配的哈希值集合

        Args:
            pattern: 匹配模式（字符串或正则）
            time_seconds: 弹幕出现时间(秒)，可选
            use_regex: 是否使用正则表达式

        Returns:
            匹配的哈希值集合
        """
        matched_hashes = set()

        for key, hashes in self.danmaku_map.items():
            key_content, key_time = key.rsplit("|", 1)
            key_time = int(key_time)

            # 时间过滤
            if time_seconds is not None:
                if abs(key_time - time_seconds) > 1:  # 允许1秒误差
                    continue

            # 内容匹配
            if self._match_content(key_content, pattern, use_regex):
                for h in hashes:
                    matched_hashes.add(h)

        return matched_hashes

    def _crack_hashes_to_uids(self, hashes: Set[str]) -> Set[int]:
        """
        匹配哈希值集合为UID集合（带缓存）

        Args:
            hashes: 哈希值集合

        Returns:
            UID集合
        """
        uids = set()

        for mid_hash in hashes:
            # 检查缓存
            if mid_hash in self._cracked_cache:
                uids.update(self._cracked_cache[mid_hash])
                continue

            cracked_uids = self.cracker.crack(mid_hash)
            self._cracked_cache[mid_hash] = cracked_uids
            uids.update(cracked_uids)

        return uids

    def count_users(self, pattern: str, time_seconds: Optional[int] = None,
                    use_regex: bool = False) -> Dict:
        """
        统计匹配的用户数量（去重，基于哈希值）

        Args:
            pattern: 匹配模式
            time_seconds: 弹幕出现时间(秒)，可选
            use_regex: 是否使用正则表达式

        Returns:
            统计结果字典
        """
        matched_hashes = set()
        matched_danmaku_count = 0

        # 遍历所有弹幕进行统计
        for dm in self.danmaku_list:
            # 时间过滤
            if time_seconds is not None:
                dm_time = dm.progress // 1000
                if abs(dm_time - time_seconds) > 1:
                    continue

            # 内容匹配
            if self._match_content(dm.content, pattern, use_regex):
                matched_danmaku_count += 1
                matched_hashes.add(dm.mid_hash)

        return {
            "matched_danmaku_count": matched_danmaku_count,
            "unique_user_count": len(matched_hashes),
            "hashes": matched_hashes
        }

    def search_by_content(self, content: str, time_seconds: Optional[int] = None,
                          use_regex: bool = False, threads: int = 1) -> Dict:
        """
        根据弹幕内容查询发送者

        Args:
            content: 弹幕内容（或正则表达式）
            time_seconds: 弹幕出现时间(秒)，可选，用于精确匹配
            use_regex: 是否使用正则表达式匹配
            threads: 线程数（仅用于哈希匹配阶段）

        Returns:
            包含用户信息和报告数据的字典:
            {
                'users': [用户信息列表],
                'user_data': {uid: {info, danmaku_list}} 用于报告,
                'uncracked_hashes': 未能匹配的哈希列表,
                'matched_danmaku_count': 匹配的弹幕数量,
                'cancelled': 是否被取消
            }
        """
        matched_hashes = self._get_matched_hashes(content, time_seconds, use_regex)

        # 收集匹配的弹幕（用于报告）- 存储完整弹幕信息（内容+时间）
        hash_to_danmaku: Dict[str, List[Dict]] = {}
        matched_danmaku_count = 0
        for dm in self.danmaku_list:
            if time_seconds is not None:
                dm_time = dm.progress // 1000
                if abs(dm_time - time_seconds) > 1:
                    continue
            if self._match_content(dm.content, content, use_regex):
                matched_danmaku_count += 1
                if dm.mid_hash not in hash_to_danmaku:
                    hash_to_danmaku[dm.mid_hash] = []
                # 保留所有弹幕（即使内容相同但时间不同也有价值）
                hash_to_danmaku[dm.mid_hash].append({
                    'content': dm.content,
                    'progress': dm.progress,  # 视频时间（毫秒）
                    'ctime': dm.ctime  # 发送时间戳
                })

        if not matched_hashes:
            self._log(f"未找到匹配的弹幕: {content}")
            return {'users': [], 'user_data': {}, 'uncracked_hashes': [], 'matched_danmaku_count': 0, 'cancelled': False}

        matched_hashes = list(matched_hashes)
        total = len(matched_hashes)
        self._log(f"找到 {total} 个哈希值")

        # 检查缓存命中情况
        cached_count = sum(1 for h in matched_hashes if h in self.hash_cache)
        if cached_count > 0:
            self._log(f"缓存命中: {cached_count}/{total} 个哈希值")

        # ============ 第一阶段：匹配哈希（可多线程） ============
        self._stage("cracking")
        self._log("[阶段1] 正在匹配哈希值...")
        hash_to_uids: Dict[str, List[int]] = {}
        lock = threading.Lock()
        progress_counter = [0]
        cache_write_lock = threading.Lock()  # 缓存写入锁，避免并发写入

        def crack_hash(mid_hash):
            with lock:
                progress_counter[0] += 1
                p = progress_counter[0]
            self._log(f"[{p}/{total}] 匹配哈希: {mid_hash}")
            self._progress(p, total, f"匹配哈希: {mid_hash}")

            # 优先从缓存获取
            cached = self.hash_cache.get(mid_hash)
            if cached is not None:
                if cached:
                    self._log(f"  [缓存] -> UID: {cached}")
                else:
                    self._log(f"  [缓存] 无法匹配")
                return mid_hash, cached, True  # 第三个参数表示来自缓存

            # 检查退出信号
            if exit_handler.should_exit():
                return mid_hash, None, True

            # 缓存未命中，执行匹配
            uids = self.cracker.crack(mid_hash)

            if uids:
                self._log(f"  [匹配] -> UID: {uids}")
            else:
                self._log(f"  无法匹配 (可能是超过8位UID或已删号)")

            # 立即写入缓存文件（原子操作）
            with cache_write_lock:
                self.hash_cache.set_and_save(mid_hash, uids if uids else [])

            return mid_hash, uids, False

        # 哈希匹配可使用多线程（纯计算，不请求网络）
        if threads > 1:
            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = {executor.submit(crack_hash, h): h for h in matched_hashes}
                for future in as_completed(futures):
                    # 检查退出信号
                    if exit_handler.should_exit():
                        self._log("检测到退出信号，取消剩余任务...")
                        for f in futures:
                            f.cancel()
                        break
                    try:
                        mid_hash, uids, from_cache = future.result(timeout=1)
                        if uids:
                            hash_to_uids[mid_hash] = uids
                    except Exception:
                        pass
        else:
            for mid_hash in matched_hashes:
                # 检查退出信号
                if exit_handler.should_exit():
                    self._log("检测到退出信号，停止匹配...")
                    break
                _, uids, from_cache = crack_hash(mid_hash)
                if uids:
                    hash_to_uids[mid_hash] = uids

        # 检查是否被中断
        if exit_handler.should_exit():
            return {'users': [], 'user_data': {}, 'uncracked_hashes': [], 'matched_danmaku_count': 0, 'cancelled': True}

        # 收集所有UID
        all_uids = set()
        for uids in hash_to_uids.values():
            all_uids.update(uids)

        self._log(f"哈希匹配完成，共找到 {len(all_uids)} 个唯一UID")

        # ============ 第二阶段：串行获取用户信息（避免风控） ============
        self._stage("fetching")
        self._log("[阶段2] 正在获取用户信息（串行执行，避免触发风控）...")
        uid_to_info: Dict[int, Dict] = {}

        for i, uid in enumerate(all_uids, 1):
            # 检查退出信号
            if exit_handler.should_exit():
                self._log("检测到退出信号，停止获取用户信息...")
                break

            self._log(f"[{i}/{len(all_uids)}] 获取用户信息: UID {uid}")
            self._progress(i, len(all_uids), f"获取用户信息: UID {uid}")
            user_info = get_user_info_by_api(uid, self.max_retries)
            if user_info:
                uid_to_info[uid] = user_info
                self._log(f"  -> {user_info['name']}")
            else:
                self._log(f"  -> 无法获取用户信息（将标记为未匹配）")
                # 不添加默认信息， 获取失败的UID不加入 uid_to_info

        # 构建用户数据（包含弹幕列表，用于报告）
        user_data: Dict[int, Dict] = {}  # uid -> {info, danmaku_list}
        error_hashes = []  # 匹配到UID但无法获取信息的哈希
        uncracked_hashes = []  # 完全无法匹配的哈希
        for mid_hash in matched_hashes:
            danmaku_list = hash_to_danmaku.get(mid_hash, [])
            uids = hash_to_uids.get(mid_hash)
            if not uids:
                # 没有匹配到任何UID
                uncracked_hashes.append((mid_hash, danmaku_list))
                continue

            # 检查是否有成功获取用户信息的UID
            has_valid_uid = False
            for uid in uids:
                if uid in uid_to_info:
                    has_valid_uid = True
                    if uid in user_data:
                        # 合并弹幕（保留所有弹幕，即使内容相同但时间不同）
                        for dm in danmaku_list:
                            user_data[uid]['danmaku_list'].append(dm)
                    else:
                        user_data[uid] = {
                            'uid': uid,
                            'info': uid_to_info[uid],
                            'danmaku_list': danmaku_list.copy()
                        }

            # 如果所有UID都无法获取用户信息，创建错误标记的用户数据
            if not has_valid_uid:
                # 使用第一个UID作为标识，创建错误标记的用户信息
                error_uid = uids[0]
                error_info = {
                    'mid_hash': mid_hash,
                    'name': f'UID:{error_uid}',
                    'face': '',
                    'sign': '[警告] 用户信息获取失败（已注销/封禁/超过8位UID/网络错误）',
                    'is_error': True,
                    'error_type': 'fetch_failed'
                }
                user_data[f"error_{mid_hash}"] = {
                    'uid': error_uid,
                    'info': error_info,
                    'danmaku_list': danmaku_list.copy()
                }
                error_hashes.append((mid_hash, danmaku_list))

        return {
            'users': list(uid_to_info.values()),
            'user_data': user_data,
            'uncracked_hashes': uncracked_hashes,
            'error_hashes': error_hashes,
            'matched_danmaku_count': matched_danmaku_count,
            'cancelled': exit_handler.should_exit()
        }

    def export_html_report(self, pattern: str, time_seconds: Optional[int] = None,
                           use_regex: bool = False, output_file: str = "report.html",
                           video_title: str = "", video_url: str = "",
                           threads: int = 1, cached_data: Optional[Dict] = None) -> str:
        """
        导出HTML报告

        Args:
            pattern: 匹配模式
            time_seconds: 弹幕出现时间(秒)，可选
            use_regex: 是否使用正则表达式
            output_file: 输出文件路径
            video_title: 视频标题
            video_url: 视频URL
            threads: 线程数（仅用于哈希匹配阶段）
            cached_data: 已有的查询结果（由search_by_content返回），提供时直接使用，避免重复查询

        Returns:
            生成的HTML内容
        """
        # 如果提供了缓存数据，直接使用
        if cached_data:
            user_data = cached_data.get('user_data', {})
            uncracked_hashes = cached_data.get('uncracked_hashes', [])
            error_hashes = cached_data.get('error_hashes', [])
            matched_danmaku_count = cached_data.get('matched_danmaku_count', 0)

            # 生成HTML
            html = self._generate_html(
                user_data=user_data,
                uncracked_hashes=uncracked_hashes,
                error_hashes=error_hashes,
                pattern=pattern,
                use_regex=use_regex,
                matched_danmaku_count=matched_danmaku_count,
                video_title=video_title,
                video_url=video_url
            )

            # 保存文件
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(html)

            print(f"\n报告已保存到: {output_file}")
            return html

        # 以下为原有逻辑（无缓存数据时执行）
        # 1. 收集匹配的弹幕，按哈希分组 - 存储完整弹幕信息（内容+时间）
        hash_to_danmaku: Dict[str, List[Dict]] = {}
        matched_danmaku_count = 0

        print("正在收集匹配的弹幕...")
        for dm in self.danmaku_list:
            # 时间过滤
            if time_seconds is not None:
                dm_time = dm.progress // 1000
                if abs(dm_time - time_seconds) > 1:
                    continue

            # 内容匹配
            if self._match_content(dm.content, pattern, use_regex):
                matched_danmaku_count += 1
                if dm.mid_hash not in hash_to_danmaku:
                    hash_to_danmaku[dm.mid_hash] = []
                # 保留所有弹幕（即使内容相同但时间不同也有价值）
                hash_to_danmaku[dm.mid_hash].append({
                    'content': dm.content,
                    'progress': dm.progress,  # 视频时间（毫秒）
                    'ctime': dm.ctime  # 发送时间戳
                })

        print(f"匹配弹幕: {matched_danmaku_count} 条")
        print(f"唯一哈希: {len(hash_to_danmaku)} 个")

        # 检查缓存命中情况
        cached_count = sum(1 for h in hash_to_danmaku.keys() if h in self.hash_cache)
        if cached_count > 0:
            print(f"缓存命中: {cached_count}/{len(hash_to_danmaku)} 个哈希值")

        # ============ 第一阶段：匹配哈希（可多线程） ============
        print(f"\n[阶段1] 正在匹配哈希值 (线程数: {threads})...")
        uncracked_hashes = []
        lock = threading.Lock()
        progress = [0]
        total = len(hash_to_danmaku)
        cache_write_lock = threading.Lock()  # 缓存写入锁，避免并发写入

        def crack_hash_item(item):
            mid_hash, danmaku_list = item
            with lock:
                progress[0] += 1
                print(f"[{progress[0]}/{total}] 匹配哈希: {mid_hash}")

            # 优先从缓存获取
            cached = self.hash_cache.get(mid_hash)
            if cached is not None:
                with lock:
                    if cached:
                        print(f"  [缓存] -> UID: {cached}")
                    else:
                        print(f"  [缓存] 无法匹配")
                        uncracked_hashes.append((mid_hash, danmaku_list))
                return mid_hash, cached if cached else None, danmaku_list, True

            # 检查退出信号
            if exit_handler.should_exit():
                return mid_hash, None, danmaku_list, True

            # 缓存未命中，执行匹配
            uids = self.cracker.crack(mid_hash)

            with lock:
                if uids:
                    print(f"  [匹配] -> UID: {uids}")
                else:
                    uncracked_hashes.append((mid_hash, danmaku_list))
                    print(f"  无法匹配 (可能是超过8位UID或已删号)")

            # 立即写入缓存文件（原子操作）
            with cache_write_lock:
                self.hash_cache.set_and_save(mid_hash, uids if uids else [])

            return mid_hash, uids if uids else None, danmaku_list, False

        # 哈希匹配结果：hash -> (uids, danmaku_list)
        hash_match_results: Dict[str, Tuple[List[int], List[str]]] = {}

        if threads > 1:
            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = {executor.submit(crack_hash_item, item): item
                          for item in hash_to_danmaku.items()}
                for future in as_completed(futures):
                    # 检查退出信号
                    if exit_handler.should_exit():
                        print("\n检测到退出信号，取消剩余任务...")
                        for f in futures:
                            f.cancel()
                        break
                    try:
                        mid_hash, uids, danmaku_list, from_cache = future.result(timeout=1)
                        if uids:
                            hash_match_results[mid_hash] = (uids, danmaku_list)
                    except Exception:
                        pass
        else:
            for item in hash_to_danmaku.items():
                # 检查退出信号
                if exit_handler.should_exit():
                    print("\n检测到退出信号，停止匹配...")
                    break
                mid_hash, uids, danmaku_list, from_cache = crack_hash_item(item)
                if uids:
                    hash_match_results[mid_hash] = (uids, danmaku_list)

        # 检查是否被中断
        if exit_handler.should_exit():
            print("哈希匹配被中断")
            print("程序已退出")
            sys.exit(0)

        # 收集所有UID
        all_uids = set()
        for uids, _ in hash_match_results.values():
            all_uids.update(uids)

        print(f"\n哈希匹配完成，共找到 {len(all_uids)} 个唯一UID")

        # ============ 第三阶段：串行获取用户信息（避免风控） ============
        print("\n[阶段2] 正在获取用户信息（串行执行，避免触发风控）...")
        uid_to_info: Dict[int, Dict] = {}

        for i, uid in enumerate(all_uids, 1):
            # 检查退出信号
            if exit_handler.should_exit():
                print("\n检测到退出信号，停止获取用户信息...")
                break

            print(f"[{i}/{len(all_uids)}] 获取用户信息: UID {uid}")
            user_info = get_user_info_by_api(uid, self.max_retries)
            if user_info:
                uid_to_info[uid] = user_info
                print(f"  -> {user_info['name']}")
            else:
                print(f"  -> 无法获取用户信息（将标记为未匹配）")
                # 不添加默认信息，获取失败的UID不加入 uid_to_info

        # 构建用户数据（合并弹幕）
        user_data: Dict[int, Dict] = {}  # uid -> {info, danmaku_list}
        error_hashes = []  # 匹配到UID但无法获取信息的哈希
        for mid_hash, (uids, danmaku_list) in hash_match_results.items():
            if not uids:
                # 没有匹配到任何UID
                uncracked_hashes.append((mid_hash, danmaku_list))
                continue

            # 检查是否有成功获取用户信息的UID
            has_valid_uid = False
            for uid in uids:
                if uid in uid_to_info:
                    has_valid_uid = True
                    if uid in user_data:
                        # 合并弹幕（保留所有弹幕，即使内容相同但时间不同）
                        for dm in danmaku_list:
                            user_data[uid]['danmaku_list'].append(dm)
                    else:
                        user_data[uid] = {
                            'uid': uid,
                            'info': uid_to_info[uid],
                            'danmaku_list': danmaku_list.copy()
                        }

            # 如果所有UID都无法获取用户信息，创建错误标记的用户数据
            if not has_valid_uid:
                error_uid = uids[0]
                error_info = {
                    'mid_hash': mid_hash,
                    'name': f'UID:{error_uid}',
                    'face': '',
                    'sign': '[警告] 用户信息获取失败（已注销/封禁/超过8位UID/网络错误）',
                    'is_error': True,
                    'error_type': 'fetch_failed'
                }
                user_data[f"error_{mid_hash}"] = {
                    'uid': error_uid,
                    'info': error_info,
                    'danmaku_list': danmaku_list.copy()
                }
                error_hashes.append((mid_hash, danmaku_list))

        # 3. 生成HTML
        html = self._generate_html(
            user_data=user_data,
            uncracked_hashes=uncracked_hashes,
            error_hashes=error_hashes,
            pattern=pattern,
            use_regex=use_regex,
            matched_danmaku_count=matched_danmaku_count,
            video_title=video_title,
            video_url=video_url
        )

        # 4. 保存文件
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"\n报告已保存到: {output_file}")
        return html

    def _generate_html(self, user_data: Dict[int, Dict], uncracked_hashes: List,
                       pattern: str, use_regex: bool, matched_danmaku_count: int,
                       video_title: str, video_url: str, error_hashes: List = None) -> str:
        """生成HTML报告"""

        if error_hashes is None:
            error_hashes = []

        # 统计
        total_users = len([u for u in user_data.values() if not u.get('info', {}).get('is_error')])
        total_errors = len([u for u in user_data.values() if u.get('info', {}).get('is_error')])
        total_uncracked = len(uncracked_hashes)

        # 按弹幕数量排序用户，正常用户在前，错误用户在后
        sorted_users = sorted(user_data.items(),
                             key=lambda x: (x[1].get('info', {}).get('is_error', False), -len(x[1]['danmaku_list'])))

        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>弹幕发送者报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f5f5f5; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ color: #333; margin-bottom: 10px; }}
        .summary {{ background: #fff; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .summary-item {{ display: inline-block; margin-right: 30px; }}
        .summary-label {{ color: #666; font-size: 14px; }}
        .summary-value {{ font-size: 24px; font-weight: bold; color: #00a1d6; }}
        .pattern-info {{ color: #666; font-size: 14px; margin-top: 10px; }}
        table {{ width: 100%; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); border-collapse: collapse; }}
        th {{ background: #00a1d6; color: #fff; padding: 12px 15px; text-align: left; font-weight: 500; }}
        td {{ padding: 12px 15px; border-bottom: 1px solid #eee; vertical-align: top; }}
        tr:hover {{ background: #f9f9f9; }}
        .avatar {{ width: 50px; height: 50px; border-radius: 50%; object-fit: cover; }}
        .no-avatar {{ width: 50px; height: 50px; border-radius: 50%; background: #e0e0e0; display: flex; align-items: center; justify-content: center; color: #999; font-size: 12px; }}
        .user-name {{ color: #333; font-weight: 500; text-decoration: none; }}
        .user-name:hover {{ color: #00a1d6; }}
        .uid {{ color: #999; font-size: 12px; }}
        .danmaku-list {{ max-width: 400px; }}
        .danmaku-item {{ display: inline-block; background: #f0f0f0; padding: 4px 8px; margin: 2px; border-radius: 4px; font-size: 13px; word-break: break-all; }}
        .danmaku-time {{ display: block; font-size: 10px; color: #999; margin-top: 2px; }}
        .danmaku-count {{ background: #00a1d6; color: #fff; padding: 2px 8px; border-radius: 10px; font-size: 12px; }}
        .uncracked {{ background: #fff3cd; padding: 15px; border-radius: 8px; margin-top: 20px; }}
        .uncracked-title {{ color: #856404; font-weight: bold; margin-bottom: 10px; }}
        .uncracked-item {{ margin: 5px 0; font-size: 13px; color: #666; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 20px; }}
        .video-info {{ background: #fff; padding: 15px 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .video-info a {{ color: #00a1d6; text-decoration: none; font-size: 16px; }}
        .video-info a:hover {{ text-decoration: underline; }}
        .sign {{ color: #999; font-size: 12px; margin-top: 4px; max-width: 200px; word-wrap: break-word; white-space: pre-wrap; }}
        .medal-list {{ max-width: 250px; }}
        .medal-item {{ display: inline-block; background: linear-gradient(90deg, #667eea 0%, #764ba2 100%); color: #fff; padding: 2px 8px; margin: 2px; border-radius: 4px; font-size: 12px; }}
        .medal-level {{ background: rgba(255,255,255,0.3); padding: 1px 4px; border-radius: 3px; margin-left: 4px; font-size: 11px; }}
        .medal-wearing {{ border: 2px solid #ffd700; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>弹幕发送者报告</h1>

        <div class="video-info">
            {'<a href="' + html_escape.escape(video_url) + '" target="_blank">' + html_escape.escape(video_title) + '</a>' if video_url else ''}
        </div>

        <div class="summary">
            <div class="summary-item">
                <div class="summary-label">匹配弹幕</div>
                <div class="summary-value">{matched_danmaku_count}</div>
            </div>
            <div class="summary-item">
                <div class="summary-label">匹配用户</div>
                <div class="summary-value">{total_users}</div>
            </div>
            <div class="summary-item">
                <div class="summary-label">获取失败</div>
                <div class="summary-value" style="color:#ff6b6b;">{total_errors}</div>
            </div>
            <div class="summary-item">
                <div class="summary-label">无法匹配</div>
                <div class="summary-value">{total_uncracked}</div>
            </div>
            <div class="pattern-info">
                匹配模式: <code>{html_escape.escape(pattern)}</code> {'(正则表达式)' if use_regex else '(子字符串)'}
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th style="width:60px">头像</th>
                    <th style="width:150px">用户信息</th>
                    <th>签名</th>
                    <th style="width:250px">粉丝灯牌</th>
                    <th>匹配弹幕内容（时间信息）</th>
                    <th style="width:80px">弹幕数</th>
                </tr>
            </thead>
            <tbody>
'''

        for uid, data in sorted_users:
            info = data['info']
            danmaku_list = data['danmaku_list']
            danmaku_count = len(danmaku_list)
            medals = info.get('medals', [])
            is_error = info.get('is_error', False)

            # 错误用户使用特殊样式
            if is_error:
                row_style = 'background:#fff3cd;'
                avatar_html = '<div class="no-avatar" style="background:#ffe0b2;">[!]</div>'
                user_name_html = f'<span class="user-name" style="color:#856404;">{html_escape.escape(info["name"])} <span style="font-size:11px;color:#ff6b6b;">[获取失败]</span></span>'
                uid_html = f'<div class="uid">UID: {data["uid"]} | Hash: {html_escape.escape(info.get("mid_hash", ""))}</div>'
                sign_html = f'<span style="color:#ff6b6b;">{html_escape.escape(info.get("sign", "用户信息获取失败"))}</span>'
                medal_html = '<span style="color:#999;font-size:12px;">-</span>'
            else:
                row_style = ''
                avatar_html = f'<img class="avatar" src="{html_escape.escape(info["avatar"])}" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'"><div class="no-avatar" style="display:none">无头像</div>' if info.get('avatar') else '<div class="no-avatar">无头像</div>'
                user_name_html = f'<a class="user-name" href="{html_escape.escape(info["space_url"])}" target="_blank">{html_escape.escape(info["name"])}</a>'
                uid_html = f'<div class="uid">UID: {uid}</div>'
                sign_html = f'<span title="{html_escape.escape(info.get("sign", ""))}">{html_escape.escape(info.get("sign", "暂无签名"))}</span>'

                # 生成灯牌 HTML
                medal_html = ''
                if medals:
                    for m in medals[:10]:  # 最多显示10个
                        wearing_class = 'medal-wearing' if m.get('wearing_status') == 1 else ''
                        medal_html += f'<span class="medal-item {wearing_class}" title="主播: {html_escape.escape(m["target_name"])} (Lv.{m["level"]})">{html_escape.escape(m["medal_name"])}({html_escape.escape(m["target_name"])})<span class="medal-level">{m["level"]}</span></span>'
                    if len(medals) > 10:
                        medal_html += f'<span style="color:#999;font-size:12px;"> +{len(medals)-10}</span>'
                else:
                    medal_html = '<span style="color:#999;font-size:12px;">暂无灯牌</span>'

            # 生成弹幕HTML（带时间信息）
            danmaku_html_parts = []
            for dm in danmaku_list:
                if isinstance(dm, dict):
                    # 新格式：字典，包含内容和时间
                    content = dm.get('content', '')
                    progress = dm.get('progress', 0)
                    ctime = dm.get('ctime', 0)

                    # 格式化视频时间
                    video_time = f"{progress // 60000:02d}:{(progress // 1000) % 60:02d}"

                    # 格式化发送时间
                    if ctime > 0:
                        send_time = datetime.fromtimestamp(ctime).strftime('%m-%d %H:%M:%S')
                        time_info = f"视频: {video_time} | 发送: {send_time}"
                    else:
                        time_info = f"视频: {video_time}"

                    danmaku_html_parts.append(
                        f'<span class="danmaku-item" title="{html_escape.escape(time_info)}">{html_escape.escape(content)}<span class="danmaku-time">{time_info}</span></span>'
                    )
                else:
                    # 旧格式：纯字符串
                    danmaku_html_parts.append(f'<span class="danmaku-item">{html_escape.escape(str(dm))}</span>')

            danmaku_html = ''.join(danmaku_html_parts)

            html += f'''                <tr style="{row_style}">
                    <td>{avatar_html}</td>
                    <td>
                        {user_name_html}
                        {uid_html}
                    </td>
                    <td class="sign">{sign_html}</td>
                    <td class="medal-list">{medal_html}</td>
                    <td class="danmaku-list">{danmaku_html}</td>
                    <td><span class="danmaku-count">{danmaku_count}</span></td>
                </tr>
'''

        html += '''            </tbody>
        </table>
'''

        # 无法匹配的哈希
        if uncracked_hashes:
            html += f'''        <div class="uncracked">
            <div class="uncracked-title">无法匹配的哈希 ({total_uncracked}个)</div>
            <div style="color:#666;font-size:13px;margin-bottom:10px;">可能是超过8位UID或已注销账号</div>
'''
            for mid_hash, danmaku_list in uncracked_hashes[:20]:  # 只显示前20个
                # 支持新旧两种格式的弹幕列表
                if danmaku_list and isinstance(danmaku_list[0], dict):
                    contents = [dm.get('content', '') for dm in danmaku_list[:3]]
                else:
                    contents = [str(dm) for dm in danmaku_list[:3]]
                danmaku_preview = ', '.join(contents)
                if len(danmaku_list) > 3:
                    danmaku_preview += f' ... 等共{len(danmaku_list)}条'
                html += f'            <div class="uncracked-item"><code>{mid_hash}</code>: {html_escape.escape(danmaku_preview)}</div>\n'
            if len(uncracked_hashes) > 20:
                html += f'            <div class="uncracked-item">... 还有 {len(uncracked_hashes) - 20} 个</div>\n'
            html += '        </div>\n'

        html += f'''        <div class="footer">
            生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | Bilibili弹幕发送者查询工具
        </div>
    </div>
</body>
</html>'''

        return html


def time_to_seconds(time_str: str) -> int:
    """将时间字符串转换为秒数"""
    parts = time_str.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0])


def main():
    parser = argparse.ArgumentParser(
        description="Bilibili弹幕发送者查询工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 通过BV号查询
  python danmaku_tracker.py --bvid BV1xx --content "哈哈哈哈"

  # 通过CID查询
  python danmaku_tracker.py --cid 123456 --content "666666"

  # 指定时间精确查询
  python danmaku_tracker.py --bvid BV1xx --content "哈哈哈哈" --time 01:30

  # 模糊匹配（正则表达式）
  python danmaku_tracker.py --bvid BV1xx --regex "哈+" --count-only

  # 只统计用户数量（不获取详细信息，速度更快）
  python danmaku_tracker.py --bvid BV1xx --content "哈哈哈哈" --count-only

  # 导出HTML报告
  python danmaku_tracker.py --bvid BV1xx --content "哈哈哈哈" --export-html report.html

  # 使用Cookie (推荐登录后使用)
  python danmaku_tracker.py --bvid BV1xx --content "测试" --cookie "SESSDATA=xxx"

  # 强制刷新哈希缓存（重新匹配所有哈希）
  python danmaku_tracker.py --bvid BV1xx --content "测试" --refresh-cache

  # 强制刷新用户信息缓存（重新获取所有用户信息）
  python danmaku_tracker.py --bvid BV1xx --content "测试" --refresh-user-cache

  # 指定缓存文件路径
  python danmaku_tracker.py --bvid BV1xx --content "测试" --cache-file my_cache.json
        """
    )

    parser.add_argument("--bvid", help="视频BV号")
    parser.add_argument("--cid", type=int, help="视频CID (与bvid二选一)")

    # 匹配模式（二选一）
    content_group = parser.add_mutually_exclusive_group(required=True)
    content_group.add_argument("--content", help="弹幕内容 (子字符串匹配)")
    content_group.add_argument("--regex", help="弹幕内容 (正则表达式匹配)")

    parser.add_argument("--time", help="弹幕时间 (格式: MM:SS 或 HH:MM:SS)")
    parser.add_argument("--cookie", default="", help="B站Cookie (可选，但建议提供)")
    parser.add_argument("--cookie-file", help="从文件读取Cookie")
    parser.add_argument("--count-only", action="store_true",
                        help="只统计匹配的用户数量（不获取用户详细信息，速度更快）")
    parser.add_argument("--threads", type=int, default=10,
                        help="多线程数 (默认: 10，用于加速哈希匹配)")
    parser.add_argument("--cache-file", default="cache/hash_cache.json",
                        help="哈希-UID映射缓存文件路径 (默认: hash_cache.json)")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="强制刷新哈希缓存，清除已有的哈希-UID映射")
    parser.add_argument("--user-cache-dir", default="cache/userinfo",
                        help="用户信息缓存目录 (默认: userinfo)")
    parser.add_argument("--refresh-user-cache", action="store_true",
                        help="强制刷新用户信息缓存，重新获取所有用户信息")
    parser.add_argument("--retries", type=int, default=3,
                        help="获取用户信息失败时的重试次数 (默认: 3)")

    args = parser.parse_args()

    # 获取Cookie
    cookie = args.cookie
    if args.cookie_file:
        try:
            with open(args.cookie_file, "r") as f:
                cookie = f.read().strip()
        except FileNotFoundError:
            print(f"Cookie文件不存在: {args.cookie_file}")
            return

    # 初始化查询器
    tracker = DanmakuTracker(
        cookie,
        threads=args.threads,
        cache_file=args.cache_file,
        refresh_cache=args.refresh_cache,
        user_cache_dir=args.user_cache_dir,
        refresh_user_cache=args.refresh_user_cache,
        max_retries=args.retries
    )

    # 获取CID和视频信息
    cid = args.cid
    video_title = ""
    video_url = ""

    if args.bvid:
        print(f"正在获取视频信息 (BV: {args.bvid})...")
        video_info = tracker.client.get_video_info(args.bvid)
        if not video_info:
            print("获取视频信息失败")
            return
        cid = video_info["cid"]
        video_title = video_info["title"]
        video_url = video_info["url"]
        print(f"标题: {video_title}")
        print(f"CID: {cid}")

    if not cid:
        print("请提供 --bvid 或 --cid")
        return

    # 加载弹幕
    tracker.load_danmaku(cid)

    # 解析时间
    time_seconds = None
    if args.time:
        time_seconds = time_to_seconds(args.time)

    # 确定匹配模式和内容
    if args.regex:
        pattern = args.regex
        use_regex = True
        mode_str = "正则匹配"
    else:
        pattern = args.content
        use_regex = False
        mode_str = "子字符串匹配"

    # 查询
    print(f"\n查询模式: {mode_str}")
    print(f"匹配模式: {pattern}")
    if time_seconds is not None:
        print(f"时间: {args.time} ({time_seconds}秒)")

    if args.count_only:
        # 只统计数量（不匹配哈希，速度快）
        stats = tracker.count_users(pattern, time_seconds, use_regex)

        # 输出统计结果
        print("\n" + "=" * 50)
        print("统计结果:")
        print("=" * 50)
        print(f"匹配弹幕数量: {stats['matched_danmaku_count']} 条")
        print(f"唯一用户数量: {stats['unique_user_count']} 人")
    else:
        # 查询详细信息
        result_data = tracker.search_by_content(pattern, time_seconds, use_regex, threads=args.threads)
        results = result_data.get('users', [])

        # 输出结果
        print("\n" + "=" * 50)
        print("查询结果:")
        print("=" * 50)

        if not results:
            print("未找到发送者")
        else:
            # 去重显示
            seen_uids = set()
            unique_users = []
            for user in results:
                if user['uid'] not in seen_uids:
                    seen_uids.add(user['uid'])
                    unique_users.append(user)

            print(f"共找到 {len(unique_users)} 位唯一用户\n")
            for i, user in enumerate(unique_users, 1):
                print(f"[{i}] {user['name']}")
                print(f"    UID: {user['uid']}")
                print(f"    空间: {user['space_url']}")
                if user['sign']:
                    print(f"    签名: {user['sign']}")
                print()

            # 自动导出HTML报告到 report/{bvid}_{pattern}.html
            if args.bvid:
                report_dir = "report"
                os.makedirs(report_dir, exist_ok=True)

                # 清理 pattern 中的特殊字符，用于文件名
                safe_pattern = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', pattern)
                # 限制长度，避免文件名过长
                if len(safe_pattern) > 30:
                    safe_pattern = safe_pattern[:30] + "..."

                report_file = os.path.join(report_dir, f"{args.bvid}_{safe_pattern}.html")
                tracker.export_html_report(
                    pattern=pattern,
                    time_seconds=time_seconds,
                    use_regex=use_regex,
                    output_file=report_file,
                    video_title=video_title,
                    video_url=video_url,
                    threads=args.threads,
                    cached_data=result_data  # 传递已有数据，避免重复查询
                )
                print(f"报告已导出: {report_file}")


if __name__ == "__main__":
    main()
