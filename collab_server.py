#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
协作服务器管理工具 - 完善版
🎯 新增功能：
- 飘窗提示占用释放内容
- 右上角×缩小到托盘，托盘退出
- 服务器支持多人占用
- 完善的公告删除功能
- 丰富的提示消息系统
"""

import asyncio
import json
import sys
import threading
import time
import argparse
import socket
import uuid
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional, Set
from datetime import datetime
import logging

#ubuntu上设置中文export QT_IM_MODULE=ibus
import os
os.environ["QT_IM_MODULE"] = "ibus"

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

def get_local_ip() -> str:
    """获取本机IP地址"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())

# ========================= 数据模型 =========================
@dataclass
class UserActivity:
    """用户活动信息"""
    username: str
    activity: str
    since: int
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserActivity':
        return cls(**data)

@dataclass
class ServerInfo:
    """服务器信息模型 - 支持多用户占用"""
    ip: str
    description: str = ""
    group: str = "默认组"
    users: List[UserActivity] = field(default_factory=list)  # 当前占用的用户列表
    
    @property
    def status(self) -> str:
        """服务器状态"""
        return "occupied" if self.users else "idle"
    
    @property
    def user_count(self) -> int:
        """占用用户数量"""
        return len(self.users)
    
    @property
    def users_text(self) -> str:
        """用户列表文本"""
        if not self.users:
            return "-"
        return ", ".join([f"{u.username}({u.activity})" for u in self.users])
    
    def add_user(self, username: str, activity: str):
        """添加用户"""
        # 先移除已存在的用户（如果有）
        self.remove_user(username)
        self.users.append(UserActivity(username, activity, int(time.time())))
    
    def remove_user(self, username: str):
        """移除用户"""
        self.users = [u for u in self.users if u.username != username]
    
    def get_user(self, username: str) -> Optional[UserActivity]:
        """获取用户活动"""
        for user in self.users:
            if user.username == username:
                return user
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "description": self.description,
            "group": self.group,
            "users": [u.to_dict() for u in self.users]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ServerInfo':
        users_data = data.pop("users", [])
        server = cls(**data)
        server.users = [UserActivity.from_dict(u) for u in users_data]
        return server

@dataclass
class UserInfo:
    """用户信息模型"""
    username: str
    last_seen: int
    status: str = "online"  # online, away, offline
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserInfo':
        return cls(**data)

@dataclass
class Announcement:
    """公告信息模型"""
    id: str
    content: str
    author: str
    timestamp: int
    can_delete: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Announcement':
        return cls(**data)

# ========================= 服务端 =========================
class CollabServer:
    """协作服务器"""
    
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.servers: Dict[str, ServerInfo] = {}
        self.announcements: List[Announcement] = []
        self.users: Dict[str, UserInfo] = {}  # 在线用户
        self.clients: Dict[str, Any] = {}  # websocket连接映射
        self._lock = asyncio.Lock()
        
        # 初始化演示数据
        self._init_demo_data()
    
    def _init_demo_data(self):
        """初始化演示数据"""
        demo_servers = [
            ("172.23.57.24", "英伟达4.0开发环境", "开发"),
            ("172.23.57.241", "算能4.0开发环境", "开发"),
            ("172.21.57.1", "华为4.0开发环境", "开发"),
            ("172.23.58.51", "训练服务器", "训练")
        ]
        
        for ip, desc, group in demo_servers:
            self.servers[ip] = ServerInfo(ip=ip, description=desc, group=group)
        
        # 添加欢迎公告
        welcome = Announcement(
            id="welcome",
            content="🎉 欢迎使用协作服务器管理系统！",
            author="系统",
            timestamp=int(time.time())
        )
        self.announcements.append(welcome)
    
    async def start(self):
        """启动服务器"""
        try:
            import websockets
            
            logger.info(f"🚀 服务器启动中...")
            logger.info(f"📡 监听地址: ws://{self.host}:{self.port}")
            logger.info(f"🌐 本机IP: {get_local_ip()}")
            
            async with websockets.serve(
                self.handle_client,
                self.host,
                self.port,
                ping_interval=30,
                ping_timeout=30
            ):
                logger.info("✅ 服务器启动成功！")
                await asyncio.Future()  # 永远运行
                
        except Exception as e:
            logger.error(f"❌ 服务器启动失败: {e}")
            raise
    
    async def handle_client(self, websocket, path=None):
        """处理客户端连接"""
        client_addr = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        client_id = str(uuid.uuid4())
        logger.info(f"🔗 客户端连接: {client_addr} ({client_id})")
        
        async with self._lock:
            self.clients[client_id] = websocket
        
        try:
            # 发送初始状态
            await self._send_full_state(websocket)
            
            # 处理消息
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._handle_message(data, websocket, client_id)
                except json.JSONDecodeError:
                    logger.warning(f"❌ JSON解析错误: {message}")
                except Exception as e:
                    logger.error(f"❌ 处理消息错误: {e}")
                    
        except Exception as e:
            logger.warning(f"🔌 客户端断开 {client_addr}: {e}")
        finally:
            async with self._lock:
                self.clients.pop(client_id, None)
                # 更新用户状态为离线
                for user in self.users.values():
                    if user.last_seen < time.time() - 60:  # 60秒未活动视为离线
                        user.status = "offline"
            logger.info(f"👋 客户端断开: {client_addr}")
    
    async def _send_full_state(self, websocket):
        """发送完整状态"""
        try:
            message = {
                "type": "full_state",
                "servers": {ip: server.to_dict() for ip, server in self.servers.items()},
                "announcements": [ann.to_dict() for ann in self.announcements[-50:]],
                "users": {name: user.to_dict() for name, user in self.users.items()}
            }
            await websocket.send(json.dumps(message))
            logger.info(f"📤 发送完整状态: {len(self.servers)}台服务器")
        except Exception as e:
            logger.error(f"❌ 发送状态失败: {e}")
    
    async def _handle_message(self, data: Dict[str, Any], websocket, client_id: str):
        """处理收到的消息"""
        action = data.get("action")
        username = data.get("username", "匿名")
        logger.info(f"📨 收到操作: {action} from {username}")
        
        # 更新用户状态
        async with self._lock:
            if username != "匿名":
                self.users[username] = UserInfo(
                    username=username,
                    last_seen=int(time.time()),
                    status="online"
                )
        
        async with self._lock:
            if action == "add_server":
                await self._add_server(data)
            elif action == "update_server":
                await self._update_server(data)
            elif action == "occupy_server":
                await self._occupy_server(data)
            elif action == "release_server":
                await self._release_server(data)
            elif action == "add_announcement":
                await self._add_announcement(data)
            elif action == "delete_announcement":
                await self._delete_announcement(data)
            elif action == "heartbeat":
                pass  # 心跳包，只更新用户状态
    
    async def _add_server(self, data: Dict[str, Any]):
        """添加服务器"""
        ip = data.get("ip", "").strip()
        description = data.get("description", "").strip()
        group = data.get("group", "默认组").strip()
        
        if ip and ip not in self.servers:
            self.servers[ip] = ServerInfo(ip=ip, description=description, group=group)
            await self._broadcast_servers()
            await self._broadcast_popup_notification("server_added", {
                "ip": ip,
                "description": description,
                "group": group
            })
            logger.info(f"✅ 添加服务器: {ip}")
    
    async def _update_server(self, data: Dict[str, Any]):
        """更新服务器"""
        ip = data.get("ip")
        description = data.get("description", "")
        group = data.get("group", "默认组")
        
        if ip in self.servers:
            self.servers[ip].description = description.strip()
            self.servers[ip].group = group.strip()
            await self._broadcast_servers()
            logger.info(f"✅ 更新服务器: {ip}")
    
    async def _occupy_server(self, data: Dict[str, Any]):
        """占用服务器"""
        ip = data.get("ip")
        who = data.get("who", "匿名")
        activity = data.get("activity", "")
        
        if ip in self.servers:
            server = self.servers[ip]
            server.add_user(who, activity.strip())
            await self._broadcast_servers()
            
            # 发送飘窗通知
            await self._broadcast_popup_notification("server_occupied", {
                "ip": ip,
                "description": server.description,
                "username": who,
                "activity": activity.strip(),
                "user_count": server.user_count
            })
            logger.info(f"🔒 {who} 占用服务器 {ip}: {activity}")
    
    async def _release_server(self, data: Dict[str, Any]):
        """释放服务器"""
        ip = data.get("ip")
        username = data.get("username", "匿名")
        
        if ip in self.servers:
            server = self.servers[ip]
            user_activity = server.get_user(username)
            
            if user_activity:
                server.remove_user(username)
                await self._broadcast_servers()
                
                # 发送飘窗通知
                await self._broadcast_popup_notification("server_released", {
                    "ip": ip,
                    "description": server.description,
                    "username": username,
                    "activity": user_activity.activity,
                    "user_count": server.user_count
                })
                logger.info(f"🔓 {username} 释放服务器 {ip}")
    
    async def _add_announcement(self, data: Dict[str, Any]):
        """添加公告"""
        content = data.get("content", "").strip()
        author = data.get("author", "匿名")
        
        if content:
            announcement = Announcement(
                id=str(uuid.uuid4()),
                content=content,
                author=author,
                timestamp=int(time.time()),
                can_delete=True
            )
            self.announcements.append(announcement)
            
            # 只保留最近100条
            if len(self.announcements) > 100:
                self.announcements = self.announcements[-100:]
            
            await self._broadcast_announcements()
            await self._broadcast_popup_notification("announcement_added", {
                "author": author,
                "content": content
            })
            logger.info(f"📢 {author} 发布公告: {content[:50]}...")
    
    async def _delete_announcement(self, data: Dict[str, Any]):
        """删除公告"""
        ann_id = data.get("announcement_id")
        username = data.get("username", "匿名")
        
        for i, ann in enumerate(self.announcements):
            if ann.id == ann_id:
                # 只有作者或系统管理员可以删除
                if ann.author == username or username == "admin":
                    content = ann.content
                    del self.announcements[i]
                    await self._broadcast_announcements()
                    await self._broadcast_popup_notification("announcement_deleted", {
                        "username": username,
                        "content": content[:30] + "..." if len(content) > 30 else content
                    })
                    logger.info(f"🗑️ {username} 删除公告: {ann.content[:30]}...")
                    break
    
    async def _broadcast_popup_notification(self, notification_type: str, data: Dict[str, Any]):
        """广播飘窗通知"""
        await self._broadcast({
            "type": "popup_notification",
            "notification_type": notification_type,
            "data": data,
            "timestamp": int(time.time())
        })
    
    async def _broadcast_servers(self):
        """广播服务器状态"""
        await self._broadcast({
            "type": "servers_update",
            "servers": {ip: server.to_dict() for ip, server in self.servers.items()}
        })
    
    async def _broadcast_announcements(self):
        """广播公告更新"""
        await self._broadcast({
            "type": "announcements_update",
            "announcements": [ann.to_dict() for ann in self.announcements[-50:]]
        })
    
    async def _broadcast(self, message: Dict[str, Any]):
        """广播消息"""
        if not self.clients:
            return
        
        data = json.dumps(message)
        dead_clients = []
        
        for client_id, client in list(self.clients.items()):
            try:
                await client.send(data)
            except Exception:
                dead_clients.append(client_id)
        
        # 清理断开的客户端
        for client_id in dead_clients:
            self.clients.pop(client_id, None)

# ========================= 主程序 =========================
def check_dependencies():
    """检查依赖"""
    try:
        import websockets
        logger.info("✅ websockets 已安装")
    except ImportError:
        logger.error("❌ 缺少 websockets，请运行: pip install websockets")
        return False
    
    return True

def main():
    """主函数"""
    if not check_dependencies():
        sys.exit(1)
    
    parser = argparse.ArgumentParser(description="🚀 协作服务器管理工具")
    parser.add_argument("--server", action="store_true", help="启动服务端")
    parser.add_argument("--host", default=DEFAULT_HOST, help="服务器地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="端口")
    args = parser.parse_args()
    
    if args.server:
        # 服务端模式
        logger.info("🚀 启动服务端...")
        try:
            server = CollabServer(args.host, args.port)
            asyncio.run(server.start())
        except KeyboardInterrupt:
            logger.info("👋 服务端停止")
        except Exception as e:
            logger.error(f"❌ 服务端错误: {e}")

if __name__ == "__main__":
    main()
