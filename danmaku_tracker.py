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
from datetime import datetime
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import asyncio

# bilibili_api 用于获取用户信息
from bilibili_api import user, sync as bilibili_sync

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


# ============== CRC32 破解算法 ==============

class CRC32Cracker:
    """CRC32哈希破解器 - 用于将midHash还原为UID"""

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
        破解UID哈希

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
        """通过BV号获取视频信息"""
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        try:
            resp = self.session.get(url, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                return {
                    "cid": data["data"]["cid"],
                    "title": data["data"]["title"],
                    "url": f"https://www.bilibili.com/video/{bvid}"
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

def get_user_info_by_api(uid: int) -> Optional[Dict]:
    """
    使用 bilibili_api 库获取用户信息
    线程安全，每次调用创建新的事件循环
    """
    try:
        async def _get_info():
            u = user.User(uid=uid)
            return await u.get_user_info()

        # 在新线程中运行异步函数
        info = bilibili_sync(_get_info())

        if info:
            return {
                "uid": uid,
                "name": info.get("name", ""),
                "avatar": info.get("face", ""),
                "sign": info.get("sign", ""),
                "space_url": f"https://space.bilibili.com/{uid}"
            }
    except Exception as e:
        pass  # 静默失败，返回None

    return None


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

    def __init__(self, cookie: str = "", threads: int = 10):
        self.client = BilibiliClient(cookie)
        self.cracker = CRC32Cracker()
        self.threads = threads
        self.danmaku_list: List[DanmakuItem] = []  # 保存所有弹幕
        self.danmaku_map: Dict[str, List[str]] = {}  # key: "内容|秒数", value: [midHash列表]
        self._cracked_cache: Dict[str, List[int]] = {}  # 哈希破解缓存

    def load_danmaku(self, cid: int):
        """加载弹幕数据"""
        print(f"正在加载弹幕数据 (CID: {cid})...")
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
        破解哈希值集合为UID集合（带缓存）

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
                          use_regex: bool = False, threads: int = 1) -> List[Dict]:
        """
        根据弹幕内容查询发送者

        Args:
            content: 弹幕内容（或正则表达式）
            time_seconds: 弹幕出现时间(秒)，可选，用于精确匹配
            use_regex: 是否使用正则表达式匹配
            threads: 线程数

        Returns:
            发送者信息列表
        """
        results = []
        matched_hashes = self._get_matched_hashes(content, time_seconds, use_regex)

        if not matched_hashes:
            print(f"未找到匹配的弹幕: {content}")
            return []

        matched_hashes = list(matched_hashes)
        total = len(matched_hashes)
        print(f"找到 {total} 个哈希值，正在破解...")

        # 使用线程锁保护共享数据
        lock = threading.Lock()
        progress = [0]  # 使用列表以便在闭包中修改

        def process_hash(mid_hash):
            nonlocal progress
            local_results = []

            with lock:
                progress[0] += 1
                print(f"[{progress[0]}/{total}] 破解哈希: {mid_hash}")

            uids = self._cracked_cache.get(mid_hash)
            if uids is None:
                uids = self.cracker.crack(mid_hash)
                with lock:
                    self._cracked_cache[mid_hash] = uids

            if not uids:
                with lock:
                    print(f"  无法破解 (可能是超过8位UID或已删号)")
                return local_results

            for uid in uids:
                user_info = get_user_info_by_api(uid)
                if user_info:
                    local_results.append(user_info)
                    with lock:
                        print(f"  -> {user_info['name']} (UID: {uid})")

            return local_results

        if threads > 1:
            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = {executor.submit(process_hash, h): h for h in matched_hashes}
                for future in as_completed(futures):
                    results.extend(future.result())
        else:
            for mid_hash in matched_hashes:
                results.extend(process_hash(mid_hash))

        return results

    def export_html_report(self, pattern: str, time_seconds: Optional[int] = None,
                           use_regex: bool = False, output_file: str = "report.html",
                           video_title: str = "", video_url: str = "",
                           threads: int = 1) -> str:
        """
        导出HTML报告

        Args:
            pattern: 匹配模式
            time_seconds: 弹幕出现时间(秒)，可选
            use_regex: 是否使用正则表达式
            output_file: 输出文件路径
            video_title: 视频标题
            video_url: 视频URL
            threads: 线程数

        Returns:
            生成的HTML内容
        """
        # 1. 收集匹配的弹幕，按哈希分组
        hash_to_danmaku: Dict[str, List[str]] = {}
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
                # 去重添加弹幕内容
                if dm.content not in hash_to_danmaku[dm.mid_hash]:
                    hash_to_danmaku[dm.mid_hash].append(dm.content)

        print(f"匹配弹幕: {matched_danmaku_count} 条")
        print(f"唯一哈希: {len(hash_to_danmaku)} 个")

        # 2. 多线程破解哈希并获取用户信息
        user_data: Dict[int, Dict] = {}  # uid -> {info, danmaku_list}
        uncracked_hashes = []
        lock = threading.Lock()
        progress = [0]
        total = len(hash_to_danmaku)

        print(f"\n正在破解哈希并获取用户信息 (线程数: {threads})...")

        def process_hash_item(item):
            mid_hash, danmaku_list = item
            local_results = []

            with lock:
                progress[0] += 1
                print(f"[{progress[0]}/{total}] 破解哈希: {mid_hash}")

            uids = self._cracked_cache.get(mid_hash)
            if uids is None:
                uids = self.cracker.crack(mid_hash)
                with lock:
                    self._cracked_cache[mid_hash] = uids

            if not uids:
                with lock:
                    uncracked_hashes.append((mid_hash, danmaku_list))
                return local_results

            for uid in uids:
                user_info = get_user_info_by_api(uid)
                if user_info:
                    local_results.append({
                        'uid': uid,
                        'info': user_info,
                        'danmaku_list': danmaku_list.copy()
                    })
                    with lock:
                        print(f"  -> {user_info['name']} (UID: {uid})")
                else:
                    local_results.append({
                        'uid': uid,
                        'info': {
                            'uid': uid,
                            'name': f'(用户{uid})',
                            'avatar': '',
                            'sign': '',
                            'space_url': f'https://space.bilibili.com/{uid}'
                        },
                        'danmaku_list': danmaku_list.copy()
                    })
                    with lock:
                        print(f"  -> 无法获取用户信息 (UID: {uid})")

            return local_results

        if threads > 1:
            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = {executor.submit(process_hash_item, item): item
                          for item in hash_to_danmaku.items()}
                for future in as_completed(futures):
                    for result in future.result():
                        uid = result['uid']
                        with lock:
                            if uid in user_data:
                                # 合并弹幕
                                for dm in result['danmaku_list']:
                                    if dm not in user_data[uid]['danmaku_list']:
                                        user_data[uid]['danmaku_list'].append(dm)
                            else:
                                user_data[uid] = result
        else:
            for item in hash_to_danmaku.items():
                for result in process_hash_item(item):
                    uid = result['uid']
                    if uid in user_data:
                        for dm in result['danmaku_list']:
                            if dm not in user_data[uid]['danmaku_list']:
                                user_data[uid]['danmaku_list'].append(dm)
                    else:
                        user_data[uid] = result

        # 3. 生成HTML
        html = self._generate_html(
            user_data=user_data,
            uncracked_hashes=uncracked_hashes,
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
                       video_title: str, video_url: str) -> str:
        """生成HTML报告"""

        # 统计
        total_users = len(user_data)
        total_uncracked = len(uncracked_hashes)

        # 按弹幕数量排序用户
        sorted_users = sorted(user_data.items(),
                             key=lambda x: len(x[1]['danmaku_list']),
                             reverse=True)

        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>弹幕发送者报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: #f5f5f5; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
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
        .danmaku-list {{ max-width: 500px; }}
        .danmaku-item {{ display: inline-block; background: #f0f0f0; padding: 4px 8px; margin: 2px; border-radius: 4px; font-size: 13px; word-break: break-all; }}
        .danmaku-count {{ background: #00a1d6; color: #fff; padding: 2px 8px; border-radius: 10px; font-size: 12px; }}
        .uncracked {{ background: #fff3cd; padding: 15px; border-radius: 8px; margin-top: 20px; }}
        .uncracked-title {{ color: #856404; font-weight: bold; margin-bottom: 10px; }}
        .uncracked-item {{ margin: 5px 0; font-size: 13px; color: #666; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 20px; }}
        .video-info {{ background: #fff; padding: 15px 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .video-info a {{ color: #00a1d6; text-decoration: none; font-size: 16px; }}
        .video-info a:hover {{ text-decoration: underline; }}
        .sign {{ color: #999; font-size: 12px; margin-top: 4px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
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
                <div class="summary-label">无法破解</div>
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
                    <th>匹配弹幕内容</th>
                    <th style="width:80px">弹幕数</th>
                </tr>
            </thead>
            <tbody>
'''

        for uid, data in sorted_users:
            info = data['info']
            danmaku_list = data['danmaku_list']
            danmaku_count = len(danmaku_list)

            avatar_html = f'<img class="avatar" src="{html_escape.escape(info["avatar"])}" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'"><div class="no-avatar" style="display:none">无头像</div>' if info.get('avatar') else '<div class="no-avatar">无头像</div>'

            danmaku_html = ''.join(f'<span class="danmaku-item">{html_escape.escape(dm)}</span>' for dm in danmaku_list)

            html += f'''                <tr>
                    <td>{avatar_html}</td>
                    <td>
                        <a class="user-name" href="{html_escape.escape(info["space_url"])}" target="_blank">{html_escape.escape(info["name"])}</a>
                        <div class="uid">UID: {uid}</div>
                    </td>
                    <td class="sign" title="{html_escape.escape(info.get("sign", ""))}">{html_escape.escape(info.get("sign", "暂无签名"))}</td>
                    <td class="danmaku-list">{danmaku_html}</td>
                    <td><span class="danmaku-count">{danmaku_count}</span></td>
                </tr>
'''

        html += '''            </tbody>
        </table>
'''

        # 无法破解的哈希
        if uncracked_hashes:
            html += f'''        <div class="uncracked">
            <div class="uncracked-title">无法破解的哈希 ({total_uncracked}个)</div>
            <div style="color:#666;font-size:13px;margin-bottom:10px;">可能是超过8位UID或已注销账号</div>
'''
            for mid_hash, danmaku_list in uncracked_hashes[:20]:  # 只显示前20个
                danmaku_preview = ', '.join(danmaku_list[:3])
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
                        help="多线程数 (默认: 10，用于加速破解和获取用户信息)")

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
    tracker = DanmakuTracker(cookie, threads=args.threads)

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
        # 只统计数量（不破解哈希，速度快）
        stats = tracker.count_users(pattern, time_seconds, use_regex)

        # 输出统计结果
        print("\n" + "=" * 50)
        print("统计结果:")
        print("=" * 50)
        print(f"匹配弹幕数量: {stats['matched_danmaku_count']} 条")
        print(f"唯一用户数量: {stats['unique_user_count']} 人")
    else:
        # 查询详细信息
        results = tracker.search_by_content(pattern, time_seconds, use_regex, threads=args.threads)

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

            # 自动导出HTML报告到 report/{bvid}.html
            if args.bvid:
                report_dir = "report"
                os.makedirs(report_dir, exist_ok=True)
                report_file = os.path.join(report_dir, f"{args.bvid}.html")
                tracker.export_html_report(
                    pattern=pattern,
                    time_seconds=time_seconds,
                    use_regex=use_regex,
                    output_file=report_file,
                    video_title=video_title,
                    video_url=video_url,
                    threads=args.threads
                )
                print(f"报告已导出: {report_file}")


if __name__ == "__main__":
    main()
