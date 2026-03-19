import asyncio
import aiohttp
from datetime import datetime
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Plain
from astrbot.api.event import MessageChain
from astrbot.api import logger

@register("dna_missions", "你的名字", "DNA Builder 密函自动推送与查询插件", "1.0.0")
class DnaMissions(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.check_interval = 3600  # 每小时检查一次（秒）
        self.push_hour_offset = 1    # 每小时的第1分钟推送（可调整）
        self._task = None
        asyncio.create_task(self._delayed_start())

    async def _delayed_start(self):
        await asyncio.sleep(2)
        self._task = asyncio.create_task(self._push_scheduler())

    async def fetch_missions(self):
        """请求 GraphQL 获取 missions 数组"""
        url = "https://api.dna-builder.cn/graphql"
        query = """
        query {
          missionsIngame(server: "cn") {
            missions
          }
        }
        """
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json={"query": query}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("data", {}).get("missionsIngame", {}).get("missions", [])
                    else:
                        logger.error(f"请求失败: {resp.status}")
                        return []
            except Exception as e:
                logger.error(f"请求异常: {e}")
                return []

    def parse_mission(self, raw_mission):
        """解析密函： [人物, 武器, 魔之楔]"""
        return {
            "character": raw_mission[0] if len(raw_mission) > 0 else "",
            "weapon": raw_mission[1] if len(raw_mission) > 1 else "",
            "mod": raw_mission[2] if len(raw_mission) > 2 else "",
        }

    def format_missions_text(self, missions_raw):
        """格式化密函数据，按类别显示"""
        if not missions_raw or len(missions_raw) < 3:
            return "密函数据格式异常"
        
        # 按顺序取出三个数组：人物、武器、魔之楔
        characters = missions_raw[0] if len(missions_raw) > 0 else []
        weapons = missions_raw[1] if len(missions_raw) > 1 else []
        mods = missions_raw[2] if len(missions_raw) > 2 else []
        
        # 将列表转换为逗号分隔的字符串
        char_str = "、".join(characters) if characters else "无"
        weapon_str = "、".join(weapons) if weapons else "无"
        mod_str = "、".join(mods) if mods else "无"
        
        return f"👤 人物：{char_str}\n🔫 武器：{weapon_str}\n🌀 魔之楔：{mod_str}"

    async def _push_scheduler(self):
        """定时任务：每小时的第 push_hour_offset 分钟推送"""
        while True:
            try:
                now = datetime.now()
                # 计算距离下一次推送的秒数
                next_run = now.replace(minute=self.push_hour_offset, second=0, microsecond=0)
                if now >= next_run:
                    next_run = next_run.replace(hour=next_run.hour + 1)
                wait_seconds = (next_run - now).total_seconds()
                logger.info(f"距离下次推送还有 {wait_seconds:.0f} 秒")
                await asyncio.sleep(wait_seconds)

                # 获取密函数据
                missions_raw = await self.fetch_missions()
                if not missions_raw:
                    logger.warning("推送时获取密函失败，跳过本次推送")
                    continue

                text = self.format_missions_text(missions_raw)
                # 获取推送群列表
                push_groups = await self.get_kv_data("push_groups", [])
                if not push_groups:
                    logger.info("没有群开启推送，跳过")
                    continue

                # 向每个群发送
                for origin in push_groups:
                    try:
                        await self.context.send_message(origin, MessageChain([Plain(text)]))
                        logger.info(f"已向群 {origin} 推送密函")
                    except Exception as e:
                        logger.error(f"向群 {origin} 推送失败: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"推送任务异常: {e}")
                await asyncio.sleep(60)

    # ========== 指令部分 ==========
    @filter.command_group("dna")
    def dna(self):
        pass

    # 查询指令（别名“实时密函”）
    @dna.command("missions")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)  # 仅限群聊
    async def missions(self, event: AstrMessageEvent):
        """查询当前所有密函"""
        missions_raw = await self.fetch_missions()
        text = self.format_missions_text(missions_raw)
        yield event.plain_result(text)

    # 添加推送群（仅管理员）
    @dna.command("addgroup")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def add_group(self, event: AstrMessageEvent):
        origin = event.unified_msg_origin
        push_groups = await self.get_kv_data("push_groups", [])
        if origin not in push_groups:
            push_groups.append(origin)
            await self.put_kv_data("push_groups", push_groups)
            yield event.plain_result("✅ 本群已加入定时推送列表")
        else:
            yield event.plain_result("⚠️ 本群已在推送列表中")

    # 移除推送群（仅管理员）
    @dna.command("removegroup")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def remove_group(self, event: AstrMessageEvent):
        origin = event.unified_msg_origin
        push_groups = await self.get_kv_data("push_groups", [])
        if origin in push_groups:
            push_groups.remove(origin)
            await self.put_kv_data("push_groups", push_groups)
            yield event.plain_result("✅ 本群已从定时推送列表移除")
        else:
            yield event.plain_result("❌ 本群不在推送列表中")

    # 列出推送群（仅管理员，私聊显示避免刷屏）
    @dna.command("listgroups")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def list_groups(self, event: AstrMessageEvent):
        push_groups = await self.get_kv_data("push_groups", [])
        if not push_groups:
            yield event.plain_result("📭 当前没有群开启定时推送")
            return
        msg = "📋 已开启推送的群列表：\n" + "\n".join(push_groups)
        yield event.plain_result(msg)

    async def terminate(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
