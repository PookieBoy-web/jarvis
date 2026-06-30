import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import datetime
import json
import logging
import os
import re
import time
import random
import math
from collections import defaultdict

# ──────────────────────────────────────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Data directory helpers
# ──────────────────────────────────────────────────────────────────────────────

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR     = os.path.join(_SCRIPT_DIR, 'data')
_AUTOMOD_FILE = os.path.join(_DATA_DIR, 'automod.json')

INVITE_RE = re.compile(r'discord(?:\.gg|\.com/invite)/\S+', re.IGNORECASE)
LINK_RE   = re.compile(r'https?://\S+', re.IGNORECASE)


def _load_json(filepath: str, default=None):
    os.makedirs(_DATA_DIR, exist_ok=True)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default if default is not None else {}


def _save_json(filepath: str, data):
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)


def _automod_load() -> dict:
    return _load_json(_AUTOMOD_FILE)


def _automod_save(data: dict):
    _save_json(_AUTOMOD_FILE, data)


def _automod_default() -> dict:
    return {
        "enabled": False,
        "banned_words": [],
        "max_mentions": 5,
        "max_caps_percent": 70,
        "block_links": False,
        "block_invites": True,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Shared logging helper
# ──────────────────────────────────────────────────────────────────────────────

async def get_jarvis_channel(guild: discord.Guild):
    return discord.utils.get(guild.text_channels, name="jarvis")


def _get_access_level(guild: discord.Guild, actor) -> str:
    if actor is None:
        return "System"
    if actor.id == guild.owner_id:
        return "Owner"
    if isinstance(actor, discord.Member):
        if actor.guild_permissions.administrator:
            return "Admin"
        if actor.bot:
            return "Bot"
        return "Member"
    return "Member"


async def log_action(
    guild: discord.Guild,
    action_title: str,
    actor,
    target,
    description: str,
    color: discord.Color = None,
):
    if color is None:
        color = discord.Color.blurple()

    channel = await get_jarvis_channel(guild)
    if channel is None:
        return

    bot_me = guild.me
    access = _get_access_level(guild, actor)

    actor_username = str(actor) if actor else "System"
    actor_mention  = actor.mention if actor else "**System**"

    if target is None:
        target_username = "—"
        target_mention  = "—"
    elif isinstance(target, (discord.User, discord.Member)):
        target_username = str(target)
        target_mention  = target.mention
    elif isinstance(target, discord.Role):
        target_username = target.name
        target_mention  = target.mention
    else:
        target_username = str(target)
        target_mention  = f"**{target}**"

    embed = discord.Embed(color=color, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_author(
        name=f"\u200b> | AUTHORISED : {access.upper()} | 🦋",
        icon_url=bot_me.display_avatar.url if bot_me else None,
    )
    if actor and hasattr(actor, "display_avatar"):
        embed.set_thumbnail(url=actor.display_avatar.url)
    embed.add_field(name="\u200b", value=f"**{action_title}**", inline=False)
    embed.add_field(name="\u200b", value=f"`{actor_username}` → `{target_username}` : {action_title} : Authorised", inline=False)
    embed.add_field(name="\u200b", value=f"Trigger Count : ∞ | Access : {access}", inline=False)
    embed.add_field(name="\u200b", value=f"🦅 {actor_mention} → 🦅 {target_mention} : {description}", inline=False)
    embed.set_footer(text="Secure Unbypassable Security | 🦋")

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.warning(f"Could not log to jarvis channel in {guild.name}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# COG 1 — Logging
# ══════════════════════════════════════════════════════════════════════════════

class Logging(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await log_action(guild, "Member Banned", None, user, f"Banned User: {user}", discord.Color.red())

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await log_action(guild, "Member Unbanned", None, user, f"Unbanned User: {user}", discord.Color.green())

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        await log_action(member.guild, "Member Left / Removed", None, member, f"Left or removed: {member}", discord.Color.orange())

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not message.content or len(message.content.strip()) < 2:
            return
        jarvis_ch = await get_jarvis_channel(message.guild)
        if jarvis_ch and message.channel.id == jarvis_ch.id:
            return
        await log_action(message.guild, "Message Deleted", message.author, None, f"Deleted in {message.channel.mention}: {message.content[:250]}", discord.Color.light_gray())

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.guild is None:
            return
        if before.content == after.content:
            return
        await log_action(before.guild, "Message Edited", before.author, None, f"In {before.channel.mention}\n**Before:** {before.content[:150]}\n**After:** {after.content[:150]}", discord.Color.yellow())

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await log_action(role.guild, "Role Created", None, role, f"Created Role: {role.name}", discord.Color.blue())

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await log_action(role.guild, "Role Deleted", None, role, f"Deleted Role: {role.name}", discord.Color.red())

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            added   = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            if added:
                await log_action(after.guild, "Role Added", None, after, f"Received Role(s): {', '.join(r.mention for r in added)}", discord.Color.green())
            if removed:
                await log_action(after.guild, "Role Removed", None, after, f"Lost Role(s): {', '.join(r.name for r in removed)}", discord.Color.orange())


# ══════════════════════════════════════════════════════════════════════════════
# COG 2 — Security  (bot-join authorization + instant anti-raid)
# ══════════════════════════════════════════════════════════════════════════════

RAID_ACTIONS = {
    discord.AuditLogAction.channel_delete,
    discord.AuditLogAction.channel_create,
    discord.AuditLogAction.guild_update,
    discord.AuditLogAction.role_delete,
    discord.AuditLogAction.webhook_create,
}

MAX_CACHED_MESSAGES = 50


def _describe_audit_entry(entry: discord.AuditLogEntry) -> str:
    target_name = ""
    if entry.target:
        target_name = getattr(entry.target, "name", None) or getattr(entry.target, "id", "unknown")
    if entry.action == discord.AuditLogAction.channel_delete:
        return f"deleted channel **#{target_name}**"
    if entry.action == discord.AuditLogAction.channel_create:
        return f"created channel **#{target_name}**"
    if entry.action == discord.AuditLogAction.role_delete:
        return f"deleted role **{target_name}**"
    if entry.action == discord.AuditLogAction.webhook_create:
        return f"created webhook **{target_name}**"
    if entry.action == discord.AuditLogAction.guild_update:
        changes = []
        if entry.changes:
            bef = entry.changes.before
            aft = entry.changes.after
            if getattr(bef, "name", None) != getattr(aft, "name", None):
                changes.append(f"server renamed **{getattr(bef, 'name', '?')}** → **{getattr(aft, 'name', '?')}**")
        return ", ".join(changes) if changes else "modified server settings"
    return f"performed action: {entry.action}"


class Security(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._punishing: set = set()
        self._channel_cache: dict = {}
        self._msg_cache: dict = defaultdict(lambda: defaultdict(list))
        self._guild_name_cache: dict = {}

    def _cache_channel(self, channel: discord.TextChannel):
        if not isinstance(channel, discord.TextChannel):
            return
        guild_id = channel.guild.id
        if guild_id not in self._channel_cache:
            self._channel_cache[guild_id] = {}
        overwrites_data: dict = {}
        for target, ow in channel.overwrites.items():
            allow, deny = ow.pair()
            overwrites_data[target.id] = (
                "role" if isinstance(target, discord.Role) else "member",
                allow.value,
                deny.value,
            )
        self._channel_cache[guild_id][channel.id] = {
            "name": channel.name, "topic": channel.topic, "position": channel.position,
            "nsfw": channel.nsfw, "slowmode_delay": channel.slowmode_delay,
            "category_id": channel.category_id, "overwrites": overwrites_data,
        }

    def _cache_guild(self, guild: discord.Guild):
        self._guild_name_cache[guild.id] = guild.name
        for ch in guild.text_channels:
            self._cache_channel(ch)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            self._cache_guild(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self._cache_guild(guild)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        self._cache_channel(channel)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after: discord.abc.GuildChannel):
        self._cache_channel(after)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or not message.content:
            return
        jarvis_ch = discord.utils.get(message.guild.text_channels, name="jarvis")
        if jarvis_ch and message.channel.id == jarvis_ch.id:
            return
        bucket = self._msg_cache[message.guild.id][message.channel.id]
        bucket.append({
            "author_name":   message.author.display_name,
            "author_avatar": str(message.author.display_avatar.url),
            "content":       message.content[:2000],
            "attachments":   [a.url for a in message.attachments],
            "timestamp":     message.created_at,
        })
        if len(bucket) > MAX_CACHED_MESSAGES:
            bucket.pop(0)

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if entry.action not in RAID_ACTIONS:
            return
        perp = entry.user
        if not perp or not perp.bot or perp.id == self.bot.user.id:
            return
        guild  = entry.guild
        member = guild.get_member(perp.id)
        if member is None:
            return

        action_desc = _describe_audit_entry(entry)
        restore_channel_id = None
        restore_guild_name = None

        if entry.action == discord.AuditLogAction.channel_delete and entry.target:
            restore_channel_id = entry.target.id
        elif entry.action == discord.AuditLogAction.guild_update and entry.changes:
            old_name = getattr(entry.changes.before, "name", None)
            if old_name:
                restore_guild_name = old_name

        asyncio.create_task(
            self._punish_bad_actor(guild, member, action_desc, restore_channel_id, restore_guild_name)
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not member.bot:
            return
        guild = member.guild
        owner = guild.owner
        if owner is None:
            try:
                await member.kick(reason="Jarvis: Bot joined — owner not found, auto-kicked")
            except discord.Forbidden:
                pass
            await log_action(guild, "Bot Auto-Kicked", None, member, f"Kicked Bot: {member.name} (owner not found)", discord.Color.red())
            return

        embed = discord.Embed(
            title="⚠️ New Bot Joining Your Server",
            description=(
                f"**Bot:** {member.name} (`{member.id}`)\n**Server:** {guild.name}\n\n"
                f"React ✅ to **approve** or ❌ to **deny**.\n⏰ You have **10 seconds** to respond."
            ),
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        dm_msg = None
        try:
            dm_msg = await owner.send(embed=embed)
            await dm_msg.add_reaction("✅")
            await dm_msg.add_reaction("❌")
        except discord.Forbidden:
            try:
                await member.kick(reason="Jarvis: Bot joined — couldn't DM owner, auto-kicked")
            except discord.Forbidden:
                pass
            await log_action(guild, "Bot Auto-Kicked", None, member, f"Kicked Bot: {member.name} (owner DMs closed)", discord.Color.red())
            return

        approved = False
        def check(reaction, user):
            return user.id == owner.id and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == dm_msg.id

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=10.0, check=check)
            approved = str(reaction.emoji) == "✅"
        except asyncio.TimeoutError:
            approved = False

        if approved:
            try:
                await owner.send(embed=discord.Embed(title="✅ Bot Approved", description=f"**{member.name}** has been approved in **{guild.name}**.", color=discord.Color.green()))
            except discord.Forbidden:
                pass
            await log_action(guild, "Bot Approved", owner, member, f"Approved Bot: {member.name}", discord.Color.green())
        else:
            kicked = False
            try:
                await member.kick(reason="Jarvis: Bot not approved by server owner within 10 seconds")
                kicked = True
            except discord.Forbidden:
                pass
            try:
                await owner.send(embed=discord.Embed(
                    title="❌ Bot Denied / Timed Out",
                    description=f"**{member.name}** has been **{'kicked' if kicked else 'failed to kick — check permissions'}** from **{guild.name}**.",
                    color=discord.Color.red()
                ))
            except discord.Forbidden:
                pass
            await log_action(guild, "Bot Denied / Kicked", owner, member, f"{'Kicked' if kicked else 'Failed to kick'} Bot: {member.name}", discord.Color.red())

    async def _punish_bad_actor(self, guild, member, action_desc, restore_channel_id=None, restore_guild_name=None):
        key = (guild.id, member.id)
        already_punishing = key in self._punishing
        if not already_punishing:
            self._punishing.add(key)

        try:
            if not already_punishing:
                manageable_roles = [r for r in member.roles if r != guild.default_role and r.is_assignable()]
                ban_coro = member.ban(delete_message_days=0, reason=f"Jarvis Anti-Raid: {action_desc}")
                if manageable_roles:
                    strip_coro = member.remove_roles(*manageable_roles, reason="Jarvis Anti-Raid: immediate permission strip")
                    outcomes = await asyncio.gather(ban_coro, strip_coro, return_exceptions=True)
                else:
                    outcomes = await asyncio.gather(ban_coro, return_exceptions=True)

                ban_outcome = outcomes[0]
                if isinstance(ban_outcome, discord.Forbidden):
                    try:
                        await member.kick(reason=f"Jarvis Anti-Raid: {action_desc}")
                        result, color = "kicked", discord.Color.orange()
                    except discord.Forbidden:
                        result, color = "could not punish (Jarvis needs Administrator + top role)", discord.Color.dark_gray()
                elif isinstance(ban_outcome, Exception):
                    result, color = "could not punish (unexpected error)", discord.Color.dark_gray()
                else:
                    result, color = "banned", discord.Color.red()

                await log_action(guild, "Anti-Raid Action", member, None, f"{result.capitalize()} — {action_desc}", color)
                owner = guild.owner
                if owner:
                    alert = discord.Embed(title="🚨 Anti-Raid Action Taken", description=f"A nuke/raid bot was **{result}** in **{guild.name}**.", color=discord.Color.red())
                    alert.add_field(name="Bot", value=f"{member.name} (`{member.id}`)", inline=False)
                    alert.add_field(name="Detected action", value=action_desc, inline=False)
                    alert.add_field(name="Result", value=result.capitalize(), inline=False)
                    try:
                        await owner.send(embed=alert)
                    except discord.Forbidden:
                        pass

            restore_tasks = []
            if restore_channel_id is not None:
                restore_tasks.append(self._restore_channel(guild, restore_channel_id))
            if restore_guild_name is not None:
                restore_tasks.append(self._restore_guild_name(guild, restore_guild_name))
            if restore_tasks:
                asyncio.create_task(asyncio.gather(*restore_tasks, return_exceptions=True))
        finally:
            if not already_punishing:
                self._punishing.discard(key)

    async def _restore_channel(self, guild: discord.Guild, channel_id: int):
        info = self._channel_cache.get(guild.id, {}).get(channel_id)
        if info is None:
            return
        overwrites: dict = {}
        for target_id, (kind, allow_val, deny_val) in info["overwrites"].items():
            target = guild.get_role(target_id) if kind == "role" else guild.get_member(target_id)
            if target:
                overwrites[target] = discord.PermissionOverwrite.from_pair(discord.Permissions(allow_val), discord.Permissions(deny_val))
        category = guild.get_channel(info["category_id"]) if info["category_id"] else None
        try:
            new_ch = await guild.create_text_channel(
                name=info["name"], topic=info["topic"] or discord.utils.MISSING,
                nsfw=info["nsfw"], slowmode_delay=info["slowmode_delay"],
                category=category, overwrites=overwrites or discord.utils.MISSING,
                reason="Jarvis Anti-Raid: restoring deleted channel",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            await log_action(guild, "Channel Restore Failed", guild.me, None, f"Could not recreate #{info['name']} — {e}", discord.Color.red())
            return
        cached_msgs = list(self._msg_cache[guild.id].get(channel_id, []))
        if cached_msgs:
            try:
                wh = await new_ch.create_webhook(name="Jarvis Restore", reason="Jarvis: history replay")
                for msg in cached_msgs:
                    content = msg["content"] or ""
                    if not content and not msg["attachments"]:
                        continue
                    if not content and msg["attachments"]:
                        content = " ".join(msg["attachments"])
                    try:
                        await wh.send(content=content[:2000], username=msg["author_name"][:80], avatar_url=msg["author_avatar"])
                    except (discord.HTTPException, discord.Forbidden):
                        pass
                    await asyncio.sleep(0.4)
                try:
                    await wh.delete()
                except (discord.HTTPException, discord.Forbidden):
                    pass
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"Could not replay messages in #{info['name']}: {e}")
        self._msg_cache[guild.id][new_ch.id] = self._msg_cache[guild.id].pop(channel_id, [])
        self._cache_channel(new_ch)
        await log_action(guild, "Channel Restored", guild.me, None, f"Recreated #{info['name']} after nuke-bot deletion" + (f" — replayed {len(cached_msgs)} message(s)" if cached_msgs else ""), discord.Color.green())

    async def _restore_guild_name(self, guild: discord.Guild, old_name: str):
        try:
            await guild.edit(name=old_name, reason="Jarvis Anti-Raid: reverting nuke-bot server rename")
            self._guild_name_cache[guild.id] = old_name
            await log_action(guild, "Server Name Restored", guild.me, None, f"Reverted server name back to: **{old_name}**", discord.Color.green())
        except (discord.Forbidden, discord.HTTPException) as e:
            await log_action(guild, "Server Name Restore Failed", None, None, f"Could not revert server name — {e}", discord.Color.red())


# ══════════════════════════════════════════════════════════════════════════════
# COG 3 — Anti-Spam
# ══════════════════════════════════════════════════════════════════════════════

SPAM_THRESHOLD   = 5
SPAM_WINDOW_SECS = 5
TIMEOUT_1_SECS   = 5  * 60
TIMEOUT_2_SECS   = 60 * 60


class AntiSpam(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self.records: dict = defaultdict(lambda: defaultdict(lambda: {"timestamps": [], "offense": 0}))
        self.daily_reset_task.start()

    def cog_unload(self):
        self.daily_reset_task.cancel()

    @tasks.loop(hours=24)
    async def daily_reset_task(self):
        self.records.clear()
        logger.info("AntiSpam: Daily offense records reset.")

    @daily_reset_task.before_loop
    async def before_daily_reset(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.id == self.bot.user.id:
            return
        guild_id = message.guild.id
        now      = time.monotonic()

        app_triggered_by = None
        if message.author.bot and message.interaction is not None:
            human = message.guild.get_member(message.interaction.user.id)
            if human is not None:
                app_triggered_by = human

        tracked_id = app_triggered_by.id if app_triggered_by else message.author.id
        record     = self.records[guild_id][tracked_id]
        record["timestamps"].append(now)
        record["timestamps"] = [t for t in record["timestamps"] if now - t <= SPAM_WINDOW_SECS]

        if len(record["timestamps"]) >= SPAM_THRESHOLD:
            record["timestamps"] = []
            await self._handle_spam(message, record, app_triggered_by=app_triggered_by)

    async def _handle_spam(self, message: discord.Message, record: dict, app_triggered_by=None):
        guild         = message.guild
        msg_author    = message.author
        punish_target = app_triggered_by or message.guild.get_member(message.author.id) or message.author
        offense       = record["offense"]
        record["offense"] += 1

        try:
            await message.channel.purge(limit=30, check=lambda m: m.author.id == msg_author.id, reason="Jarvis: Anti-spam")
        except (discord.Forbidden, discord.HTTPException):
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

        via_app  = app_triggered_by is not None
        app_name = msg_author.name if via_app else None
        is_bot   = punish_target.bot

        if offense == 0:
            await self._apply_timeout(guild, punish_target, TIMEOUT_1_SECS, "1st offense", "1 hour timeout on next offense", discord.Color.orange(), is_bot, app_name)
        elif offense == 1:
            await self._apply_timeout(guild, punish_target, TIMEOUT_2_SECS, "2nd offense", "permanent ban on next offense", discord.Color.red(), is_bot, app_name)
        else:
            await self._apply_ban(guild, punish_target, is_bot)

    async def _apply_timeout(self, guild, member, duration_secs, offense_label, next_label, color, is_bot, app_name=None):
        label = f"{duration_secs // 60} minutes" if duration_secs < 3600 else "1 hour"
        try:
            await member.timeout(datetime.timedelta(seconds=duration_secs), reason=f"Jarvis Anti-Spam: spam ({offense_label})")
            desc = f"Timed out for **{label}** — spam ({offense_label})"
            if app_name:
                desc += f"\n⚠️ Punished for using app **{app_name}** to spam"
            desc += f"\nNext: {next_label}"
            await log_action(guild, "Spam Timeout", member, None, desc, color)
            if not is_bot:
                try:
                    await member.send(embed=discord.Embed(
                        title=f"⚠️ Spam Warning — {guild.name}",
                        description=f"You were timed out for **{label}** in **{guild.name}** for spamming.\n**Offense:** {offense_label}\n**Next:** {next_label}\n\nOffenses reset every 24 hours.",
                        color=color
                    ))
                except discord.Forbidden:
                    pass
        except discord.Forbidden:
            await log_action(guild, "Spam Timeout Failed", None, member, "Missing permissions to timeout member", discord.Color.dark_red())

    async def _apply_ban(self, guild, member, is_bot):
        try:
            await member.ban(delete_message_days=1, reason="Jarvis Anti-Spam: spam — 3rd offense (permanent ban)")
            await log_action(guild, "Spam Ban", member, None, f"Permanently banned — spam (3rd offense)", discord.Color.dark_red())
        except discord.Forbidden:
            await log_action(guild, "Spam Ban Failed", None, member, "Missing permissions to ban member", discord.Color.dark_red())


# ══════════════════════════════════════════════════════════════════════════════
# COG 4 — AutoMod
# ══════════════════════════════════════════════════════════════════════════════

class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self._data = _automod_load()

    def _guild_config(self, guild_id: int) -> dict:
        key = str(guild_id)
        if key not in self._data:
            self._data[key] = _automod_default()
        return self._data[key]

    def _persist(self):
        _automod_save(self._data)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        if isinstance(message.author, discord.Member) and message.author.guild_permissions.administrator:
            return
        cfg = self._guild_config(message.guild.id)
        if not cfg.get("enabled"):
            return
        content = message.content
        lower   = content.lower()

        for word in cfg.get("banned_words", []):
            if word.lower() in lower:
                await self._delete_and_log(message, f"banned word (`{word}`)"); return

        if len(message.mentions) + len(message.role_mentions) > cfg.get("max_mentions", 5):
            await self._delete_and_log(message, f"mass mentions ({len(message.mentions) + len(message.role_mentions)})"); return

        alpha = [c for c in content if c.isalpha()]
        if len(alpha) > 10:
            caps_pct = sum(1 for c in alpha if c.isupper()) / len(alpha) * 100
            if caps_pct > cfg.get("max_caps_percent", 70):
                await self._delete_and_log(message, f"excessive caps ({int(caps_pct)}%)"); return

        if cfg.get("block_invites") and INVITE_RE.search(content):
            await self._delete_and_log(message, "Discord invite link"); return

        if cfg.get("block_links") and LINK_RE.search(content):
            await self._delete_and_log(message, "link posting blocked"); return

    async def _delete_and_log(self, message: discord.Message, reason: str):
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        await log_action(message.guild, "AutoMod: Message Removed", message.author, None, f"Removed in {message.channel.mention} — {reason}", discord.Color.orange())
        try:
            await message.channel.send(f"{message.author.mention} Your message was removed: **{reason}**.", delete_after=6)
        except (discord.Forbidden, discord.HTTPException):
            pass

    automod_group = app_commands.Group(name="automod", description="Configure Jarvis AutoMod", default_permissions=discord.Permissions(administrator=True))

    @automod_group.command(name="enable", description="Enable AutoMod for this server")
    async def automod_enable(self, interaction: discord.Interaction):
        cfg = self._guild_config(interaction.guild.id)
        cfg["enabled"] = True
        self._persist()
        await log_action(interaction.guild, "AutoMod Enabled", interaction.user, None, "AutoMod is now active", discord.Color.green())
        await interaction.response.send_message(embed=discord.Embed(title="✅ AutoMod Enabled", description="AutoMod is now **active**.", color=discord.Color.green()), ephemeral=True)

    @automod_group.command(name="disable", description="Disable AutoMod (server owner only)")
    async def automod_disable(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("❌ Only the **server owner** can disable AutoMod.", ephemeral=True); return
        cfg = self._guild_config(interaction.guild.id)
        cfg["enabled"] = False
        self._persist()
        await log_action(interaction.guild, "AutoMod Disabled", interaction.user, None, "AutoMod has been disabled", discord.Color.red())
        await interaction.response.send_message(embed=discord.Embed(title="⛔ AutoMod Disabled", description="AutoMod is now off.", color=discord.Color.red()), ephemeral=True)

    @automod_group.command(name="addword", description="Add a banned word/phrase to AutoMod")
    @app_commands.describe(word="The word or phrase to ban")
    async def automod_addword(self, interaction: discord.Interaction, word: str):
        cfg = self._guild_config(interaction.guild.id)
        cleaned = word.strip().lower()
        if cleaned not in cfg["banned_words"]:
            cfg["banned_words"].append(cleaned)
            self._persist()
        await interaction.response.send_message(f"✅ Added `{cleaned}` to the banned words list.", ephemeral=True)

    @automod_group.command(name="removeword", description="Remove a banned word from AutoMod")
    @app_commands.describe(word="The word or phrase to unban")
    async def automod_removeword(self, interaction: discord.Interaction, word: str):
        cfg = self._guild_config(interaction.guild.id)
        cfg["banned_words"] = [w for w in cfg["banned_words"] if w != word.strip().lower()]
        self._persist()
        await interaction.response.send_message(f"✅ Removed `{word.strip().lower()}` from the banned words list.", ephemeral=True)

    @automod_group.command(name="setmaxmentions", description="Set the max mentions allowed per message")
    @app_commands.describe(count="Maximum number of mentions before deletion (default 5)")
    async def automod_maxmentions(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 50]):
        cfg = self._guild_config(interaction.guild.id)
        cfg["max_mentions"] = count
        self._persist()
        await interaction.response.send_message(f"✅ Max mentions set to **{count}**.", ephemeral=True)

    @automod_group.command(name="setmaxcaps", description="Set the maximum caps % allowed (default 70%)")
    @app_commands.describe(percent="Percentage of caps before deletion (10–100)")
    async def automod_maxcaps(self, interaction: discord.Interaction, percent: app_commands.Range[int, 10, 100]):
        cfg = self._guild_config(interaction.guild.id)
        cfg["max_caps_percent"] = percent
        self._persist()
        await interaction.response.send_message(f"✅ Max caps percentage set to **{percent}%**.", ephemeral=True)

    @automod_group.command(name="blockinvites", description="Toggle blocking Discord invite links")
    @app_commands.describe(enabled="True to block invites, False to allow")
    async def automod_blockinvites(self, interaction: discord.Interaction, enabled: bool):
        cfg = self._guild_config(interaction.guild.id)
        cfg["block_invites"] = enabled
        self._persist()
        await interaction.response.send_message(f"✅ Invite blocking {'**enabled**' if enabled else '**disabled**'}.", ephemeral=True)

    @automod_group.command(name="blocklinks", description="Toggle blocking all external links")
    @app_commands.describe(enabled="True to block all links, False to allow")
    async def automod_blocklinks(self, interaction: discord.Interaction, enabled: bool):
        cfg = self._guild_config(interaction.guild.id)
        cfg["block_links"] = enabled
        self._persist()
        await interaction.response.send_message(f"✅ Link blocking {'**enabled**' if enabled else '**disabled**'}.", ephemeral=True)

    @automod_group.command(name="status", description="View current AutoMod configuration")
    async def automod_status(self, interaction: discord.Interaction):
        cfg   = self._guild_config(interaction.guild.id)
        words = cfg.get("banned_words", [])
        embed = discord.Embed(title="🛡️ AutoMod Configuration", color=discord.Color.blue())
        embed.add_field(name="Status",        value="✅ Enabled" if cfg.get("enabled") else "❌ Disabled", inline=True)
        embed.add_field(name="Max Mentions",  value=str(cfg.get("max_mentions", 5)),                       inline=True)
        embed.add_field(name="Max Caps",      value=f"{cfg.get('max_caps_percent', 70)}%",                 inline=True)
        embed.add_field(name="Block Invites", value="✅" if cfg.get("block_invites") else "❌",             inline=True)
        embed.add_field(name="Block Links",   value="✅" if cfg.get("block_links") else "❌",               inline=True)
        embed.add_field(name=f"Banned Words ({len(words)})", value=(", ".join(f"`{w}`" for w in words[:15]) if words else "None"), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 5 — Moderation  (/help, /setup, /purge, /kick, /ban, /unban, /timeout, /warn)
# ══════════════════════════════════════════════════════════════════════════════

MODULE_DATA: dict = {
    "security":     {"title": "🛡️  SECURITY SYSTEM",  "color": 0x5865F2, "gif": "https://media1.tenor.com/m/xnDaTMFs4f0AAAAd/security-shield.gif", "description": "Automatically protects your server from unauthorized bots.", "fields": [("How it works", "When a bot joins, Jarvis DMs the **server owner** with ✅ / ❌ reactions.\n• Owner reacts ✅ → bot is **approved**\n• Owner reacts ❌ → bot is **kicked**\n• No response in **10 seconds** → bot is **auto-kicked**"), ("Note", "Make sure Jarvis has **Administrator** and is at the **top of the role list**.")]},
    "antinuke":     {"title": "⚔️  ANTI NUKE SYSTEM",  "color": 0xED4245, "gif": "https://media.tenor.com/Lu02HVXXwegAAAAM/firewall-security.gif", "description": "Instant, real-time detection and ban of any bot that performs destructive actions.", "fields": [("Watched Actions", "• Bot **deletes a channel** → instant ban\n• Bot **creates a channel** → instant ban\n• Bot **deletes a role** → instant ban\n• Bot **creates a webhook** → instant ban\n• Bot **renames the server** → instant ban"), ("Note", "Jarvis must have **Administrator** and be at the **top of the role list**.")]},
    "antispam":     {"title": "🚫  ANTI SPAM SYSTEM",  "color": 0xFEE75C, "gif": "https://media.giphy.com/media/077i6AULCXc0FKTj9s/giphy.gif", "description": "Detects spam from both regular users and bot-triggered users.", "fields": [("Trigger", "5 or more messages sent within 5 seconds"), ("Punishment ladder", "• **1st offense** — 5 minute timeout\n• **2nd offense** — 1 hour timeout\n• **3rd offense** — Permanent ban")]},
    "automod":      {"title": "🤖  AUTOMOD SYSTEM",    "color": 0x57F287, "gif": "https://media.giphy.com/media/RDZo7znAdn2u7sAcWH/giphy.gif", "description": "Fully customisable automated message moderation.", "fields": [("Commands", "`/automod enable/disable/addword/removeword/setmaxmentions/setmaxcaps/blockinvites/blocklinks/status`"), ("What it filters", "• Banned words\n• Mass mentions\n• Excessive CAPS\n• Discord invite links\n• All external links")]},
    "moderation":   {"title": "🔨  MODERATION SYSTEM", "color": 0xEB459E, "gif": "https://media1.tenor.com/m/DgyN84eIP8MAAAAd/hacker-hackerman.gif", "description": "Standard moderation commands.", "fields": [("Commands", "`/kick` `/ban` `/unban` `/timeout` `/warn` `/remove` `/purge`\n`/lock` `/unlock` `/slowmode` `/nick` `/dehoist`")]},
    "welcome":      {"title": "👋  WELCOME SYSTEM",    "color": 0x57F287, "gif": None, "description": "Send custom welcome and goodbye messages when members join or leave.", "fields": [("Commands", "`/welcome setchannel` — Set the welcome channel\n`/welcome setmessage` — Set custom message (use {user} {server} {count})\n`/welcome setgoodbye` — Set goodbye message\n`/welcome disable` — Disable welcome messages\n`/welcome test` — Preview your welcome message")]},
    "autorole":     {"title": "🎭  AUTO-ROLE SYSTEM",  "color": 0x9B59B6, "gif": None, "description": "Automatically assign roles when members join.", "fields": [("Commands", "`/autorole set <role>` — Set role to give on join\n`/autorole remove` — Remove auto-role\n`/autorole status` — View current auto-role")]},
    "reactionroles":{"title": "🎨  REACTION ROLES",    "color": 0xFEA500, "gif": None, "description": "Create button-based role menus members can click to self-assign roles.", "fields": [("Commands", "`/reactionrole create <title>` — Create a new role menu\n`/reactionrole add <message_id> <role> <label>` — Add a role button\n`/reactionrole remove <message_id> <role>` — Remove a role button")]},
    "leveling":     {"title": "⭐  LEVELING SYSTEM",   "color": 0xFFD700, "gif": None, "description": "XP-based leveling system. Members earn XP for chatting.", "fields": [("Commands", "`/rank [user]` — Check your or someone's rank\n`/leaderboard` — View top 10 members\n`/setlevelchannel` — Set where level-up messages appear\n`/setlevelrole <level> <role>` — Award a role at a specific level\n`/resetxp <user>` — Reset a member's XP (admin)")]},
    "tickets":      {"title": "🎫  TICKET SYSTEM",     "color": 0x5865F2, "gif": None, "description": "Support ticket system with categories and transcripts.", "fields": [("Commands", "`/ticket setup <category> [support_role]` — Set up tickets\n`/ticket close` — Close current ticket\n`/ticket add <user>` — Add user to ticket\n`/ticket remove <user>` — Remove user from ticket")]},
    "giveaway":     {"title": "🎉  GIVEAWAY SYSTEM",   "color": 0xFF6B9D, "gif": None, "description": "Create and manage giveaways with automatic winner selection.", "fields": [("Commands", "`/giveaway start <duration> <winners> <prize>` — Start a giveaway\n`/giveaway end <message_id>` — End giveaway early\n`/giveaway reroll <message_id>` — Reroll winner")]},
    "polls":        {"title": "📊  POLL SYSTEM",        "color": 0x3498DB, "gif": None, "description": "Create polls with up to 5 options.", "fields": [("Commands", "`/poll <question> <option1> <option2> [option3] [option4] [option5]` — Create a poll\n`/quickpoll <question>` — Create a yes/no poll")]},
    "starboard":    {"title": "⭐  STARBOARD",           "color": 0xFFD700, "gif": None, "description": "Pin popular messages to a starboard channel when they receive enough reactions.", "fields": [("Commands", "`/starboard setchannel <channel>` — Set starboard channel\n`/starboard setthreshold <n>` — Set star count needed (default 3)\n`/starboard disable` — Disable starboard")]},
    "suggestions":  {"title": "💡  SUGGESTIONS",         "color": 0x1ABC9C, "gif": None, "description": "Members can submit suggestions that get voted on by the community.", "fields": [("Commands", "`/suggest <idea>` — Submit a suggestion\n`/suggestion approve <id> [reason]` — Approve a suggestion\n`/suggestion deny <id> [reason]` — Deny a suggestion")]},
    "fun":          {"title": "🎮  FUN COMMANDS",         "color": 0xE91E63, "gif": None, "description": "Fun and entertainment commands for your community.", "fields": [("Commands", "`/8ball <question>` — Ask the magic 8 ball\n`/coinflip` — Flip a coin\n`/dice [sides]` — Roll a dice\n`/rps <choice>` — Rock paper scissors\n`/meme` — Get a random meme format\n`/roast <user>` — Roast a friend\n`/compliment <user>` — Compliment someone")]},
    "info":         {"title": "ℹ️  INFO COMMANDS",        "color": 0x95A5A6, "gif": None, "description": "Lookup information about users, roles, and the server.", "fields": [("Commands", "`/userinfo [user]` — View user details\n`/serverinfo` — View server details\n`/avatar [user]` — Get user avatar\n`/roleinfo <role>` — View role details\n`/snipe` — Show last deleted message\n`/botinfo` — Bot information")]},
    "utility":      {"title": "🔧  UTILITY",             "color": 0x7289DA, "gif": None, "description": "Utility and server management commands.", "fields": [("Commands", "`/slowmode <seconds>` — Set channel slowmode\n`/lock [channel]` — Lock a channel\n`/unlock [channel]` — Unlock a channel\n`/nick <user> <name>` — Change a member's nickname\n`/dehoist` — Remove hoisting characters from nicknames\n`/afk [reason]` — Set AFK status\n`/remind <time> <message>` — Set a reminder")]},
    "jtc":          {"title": "🔊  JOIN TO CREATE",      "color": 0x5865F2, "gif": None, "description": "Astro-style Join-to-Create voice channels. Members join one trigger channel and instantly get their own private temp VC.", "fields": [("Admin Setup", "`/jtc setup <channel> [category] [template]` — Set the trigger channel\n`/jtc settemplate <template>` — Change name template (use `{user}`)\n`/jtc setlimit <n>` — Default user limit\n`/jtc setbitrate <kbps>` — Default bitrate\n`/jtc disable` — Turn off JTC\n`/jtc status` — View config"), ("Member Controls (in your VC)", "`/vc name <name>` — Rename your channel\n`/vc limit <n>` — Set user limit (0 = unlimited)\n`/vc lock` / `/vc unlock` — Lock or unlock your channel\n`/vc hide` / `/vc show` — Hide or show your channel\n`/vc ghost` / `/vc unghost` — Ghost your channel\n`/vc permit <user>` — Let a specific user in\n`/vc reject <user>` — Deny and kick a specific user\n`/vc kick <user>` — Kick a user from your VC\n`/vc invite <user>` — Send a private invite link\n`/vc claim` — Claim an abandoned channel\n`/vc transfer <user>` — Give ownership to someone\n`/vc bitrate <kbps>` — Change audio quality\n`/vc region <region>` — Set voice region\n`/vc info` — View channel details\n`/vc reset` — Clear your saved defaults")]},
    "logging":      {"title": "📋  LOGGING SYSTEM",      "color": 0x9B59B6, "gif": "https://media.tenor.com/D30cxBStgjQAAAAM/channelbot-discord.gif", "description": "Every action Jarvis takes is logged to #jarvis.", "fields": [("What gets logged", "• Bot join/kick/approve\n• Anti-raid bans\n• Anti-spam timeouts & bans\n• AutoMod deletions\n• Member kick/ban/unban\n• Timeout, warn, purge\n• Message edits & deletions\n• Role and member changes")]},
    "setup":        {"title": "⚙️  SETUP SYSTEM",        "color": 0x1ABC9C, "gif": "https://media.tenor.com/Fn9Zb7_CDR0AAAAM/discord-hello.gif", "description": "One command to get Jarvis fully initialised.", "fields": [("Command", "`/setup` — Creates the #jarvis log channel"), ("Recommended after setup", "• Give Jarvis **Administrator**\n• Move Jarvis role to the **top**\n• Run `/automod enable`")]},
}


def _build_module_embed(module_key: str) -> discord.Embed:
    data  = MODULE_DATA[module_key]
    embed = discord.Embed(title=f"__{data['title']}__", description=data["description"], color=data["color"])
    for name, value in data["fields"]:
        embed.add_field(name=name, value=value, inline=False)
    if data.get("gif"):
        embed.set_image(url=data["gif"])
    embed.set_footer(text="Jarvis • Unbypassable Security  •  Jarvis is created by Pookie Boy")
    return embed


class ModuleSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Security System",    emoji="🛡️", value="security",      description="Bot authorization & raid protection"),
            discord.SelectOption(label="Anti Nuke System",   emoji="⚔️", value="antinuke",      description="Instant ban on channel/role/webhook attacks"),
            discord.SelectOption(label="Anti Spam System",   emoji="🚫", value="antispam",      description="Progressive punishment — 5 msg / 5 sec"),
            discord.SelectOption(label="AutoMod System",     emoji="🤖", value="automod",       description="Word filter, caps, links & invites"),
            discord.SelectOption(label="Moderation",         emoji="🔨", value="moderation",    description="Kick, ban, unban, timeout & warn"),
            discord.SelectOption(label="Welcome System",     emoji="👋", value="welcome",       description="Custom welcome & goodbye messages"),
            discord.SelectOption(label="Auto-Role",          emoji="🎭", value="autorole",      description="Auto-assign roles on join"),
            discord.SelectOption(label="Reaction Roles",     emoji="🎨", value="reactionroles", description="Button-based self-assignable role menus"),
            discord.SelectOption(label="Leveling System",    emoji="⭐", value="leveling",      description="XP tracking, ranks & leaderboards"),
            discord.SelectOption(label="Ticket System",      emoji="🎫", value="tickets",       description="Support tickets with transcripts"),
            discord.SelectOption(label="Giveaway System",    emoji="🎉", value="giveaway",      description="Create & roll giveaways"),
            discord.SelectOption(label="Poll System",        emoji="📊", value="polls",         description="Create polls with up to 5 options"),
            discord.SelectOption(label="Starboard",          emoji="⭐", value="starboard",     description="Pin popular messages"),
            discord.SelectOption(label="Suggestions",        emoji="💡", value="suggestions",   description="Community suggestion voting"),
            discord.SelectOption(label="Fun Commands",       emoji="🎮", value="fun",           description="8ball, coinflip, dice, roast & more"),
            discord.SelectOption(label="Info Commands",      emoji="ℹ️", value="info",          description="Userinfo, serverinfo, avatar & more"),
            discord.SelectOption(label="Utility",            emoji="🔧", value="utility",       description="Slowmode, lock, nick, AFK, remind"),
            discord.SelectOption(label="Join to Create",     emoji="🔊", value="jtc",           description="Astro JTC — auto temp voice channels"),
            discord.SelectOption(label="Logging System",     emoji="📋", value="logging",       description="All actions logged to #jarvis"),
            discord.SelectOption(label="Setup System",       emoji="⚙️", value="setup",        description="One-command server initialization"),
        ]
        super().__init__(placeholder="Click to view modules . . .", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=_build_module_embed(self.values[0]), ephemeral=True)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(ModuleSelect())

    async def on_timeout(self):
        pass


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Set up Jarvis in your server (creates the log channel)")
    @app_commands.default_permissions(administrator=True)
    async def setup_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild    = interaction.guild
        existing = discord.utils.get(guild.text_channels, name="jarvis")
        if existing:
            jarvis_ch, created = existing, False
        else:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False, read_messages=True, add_reactions=False),
                guild.me:           discord.PermissionOverwrite(send_messages=True, read_messages=True, embed_links=True, manage_messages=True),
            }
            try:
                jarvis_ch = await guild.create_text_channel("jarvis", overwrites=overwrites, topic="🔒 Jarvis Security Bot — Action Log (read-only)", reason="Jarvis: /setup")
                created = True
            except discord.Forbidden:
                await interaction.followup.send("❌ I don't have permission to create channels.", ephemeral=True); return

        embed = discord.Embed(title="✅ Jarvis Setup Complete", color=discord.Color.green())
        embed.add_field(name="Log Channel", value=f"{jarvis_ch.mention} {'(newly created)' if created else '(already existed)'}", inline=False)
        embed.add_field(name="🔧 Recommended Next Steps", value=(
            "1. Give Jarvis the **Administrator** permission\n"
            "2. Move the **Jarvis** role to the **top** of the role list\n"
            "3. Run `/automod enable` to turn on AutoMod\n"
            "4. Run `/welcome setchannel` to set up welcome messages\n"
            "5. Run `/help` to see all available commands"
        ), inline=False)
        embed.set_footer(text="Jarvis is created by Pookie Boy")
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_action(guild, "Jarvis Setup", interaction.user, None, "Setup completed — #jarvis channel configured", discord.Color.green())

    @app_commands.command(name="help", description="Show all Jarvis commands and features")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            description=(
                f"# Hey !!! , I am 🛡️ {self.bot.user.mention}\n"
                ">>> Welcome to **Jarvis** — built for **unbypassable security** and complete "
                "community management. Made by 👑 **Pookie Boy**.\n"
                "Use the dropdown below to view any module in detail:"
            ),
            color=0x2B2D31,
        )

        # Row 1 — Protection
        embed.add_field(name="🛡️  Security",       value="Bot authorization\n& raid protection",       inline=True)
        embed.add_field(name="⚔️  Anti Nuke",      value="Instant ban on\nchannel/role attacks",       inline=True)
        embed.add_field(name="🚫  Anti Spam",      value="Progressive punishment\n5 msg / 5 sec",       inline=True)

        # Row 2 — Moderation
        embed.add_field(name="🤖  Auto Mod",       value="Word filter, caps,\nlinks & invites",         inline=True)
        embed.add_field(name="🔨  Moderation",     value="Kick, ban, unban\ntimeout & warn",            inline=True)
        embed.add_field(name="📋  Logging",        value="All actions logged\nto #jarvis channel",      inline=True)

        # Row 3 — Members
        embed.add_field(name="👋  Welcome",        value="Custom welcome\n& goodbye messages",          inline=True)
        embed.add_field(name="🎭  Auto-Role",      value="Auto-assign roles\non member join",           inline=True)
        embed.add_field(name="🎨  Reaction Roles", value="Button-based\nself-role menus",               inline=True)

        # Row 4 — Engagement
        embed.add_field(name="⭐  Leveling",       value="XP, ranks &\nleaderboards",                   inline=True)
        embed.add_field(name="🎫  Tickets",        value="Support tickets\nwith transcripts",           inline=True)
        embed.add_field(name="🎉  Giveaways",      value="Create & roll\ngiveaways automatically",      inline=True)

        # Row 5 — Community
        embed.add_field(name="📊  Polls",          value="Quick & multi-option\nvoting polls",          inline=True)
        embed.add_field(name="🌟  Starboard",      value="Pin popular\nmessages automatically",         inline=True)
        embed.add_field(name="💡  Suggestions",    value="Community idea\nsubmission & voting",         inline=True)

        # Row 6 — Voice
        embed.add_field(name="🔊  Join to Create", value="Astro JTC — auto\ntemp voice channels",      inline=True)
        embed.add_field(name="🎮  Fun",            value="8ball, dice, roast\ncoinflip & more",         inline=True)
        embed.add_field(name="ℹ️  Info",           value="Userinfo, serverinfo\navatar & roleinfo",      inline=True)

        # Row 7 — Utility / Setup (3 fields to complete last row)
        embed.add_field(name="🔧  Utility",        value="Slowmode, lock, nick\nAFK, remind, embed",   inline=True)
        embed.add_field(name="💤  AFK & Snipe",    value="Set AFK status\n& snipe deleted messages",   inline=True)
        embed.add_field(name="⚙️  Setup",          value="Run `/setup` to\ninitialise Jarvis",         inline=True)

        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_image(url="https://media1.tenor.com/m/xnDaTMFs4f0AAAAd/security-shield.gif")
        embed.set_footer(
            text=f"Jarvis • 20 Modules • Unbypassable Security  •  Created by Pookie Boy",
            icon_url=self.bot.user.display_avatar.url,
        )
        await interaction.response.send_message(embed=embed, view=HelpView())

    @app_commands.command(name="purge", description="Delete messages from this channel (max 500)")
    @app_commands.describe(amount="Number of messages to delete (1–500)")
    @app_commands.default_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500]):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount, reason=f"Jarvis /purge — {interaction.user}")
            await log_action(interaction.guild, "Messages Purged", interaction.user, None, f"Purged {len(deleted)} messages in {interaction.channel.mention}", discord.Color.orange())
            await interaction.followup.send(f"✅ Deleted **{len(deleted)}** messages.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to delete messages here.", ephemeral=True)

    @app_commands.command(name="kick", description="Kick a member from the server")
    @app_commands.describe(member="The member to kick", reason="Reason for the kick")
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't kick yourself.", ephemeral=True); return
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I can't kick this member — their role is too high.", ephemeral=True); return
        try:
            await member.kick(reason=f"Kicked by {interaction.user}: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to kick this member.", ephemeral=True); return
        await log_action(interaction.guild, "Member Kicked", interaction.user, member, f"Kicked: {member} — {reason}", discord.Color.orange())
        await interaction.response.send_message(embed=discord.Embed(title="👢 Member Kicked", description=f"**{member}** has been kicked.\n**Reason:** {reason}", color=discord.Color.orange()))

    @app_commands.command(name="ban", description="Ban a member from the server")
    @app_commands.describe(member="The member to ban", reason="Reason for the ban")
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if member.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't ban yourself.", ephemeral=True); return
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I can't ban this member — their role is too high.", ephemeral=True); return
        try:
            await member.ban(delete_message_days=1, reason=f"Banned by {interaction.user}: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to ban this member.", ephemeral=True); return
        await log_action(interaction.guild, "Member Banned", interaction.user, member, f"Banned: {member} — {reason}", discord.Color.red())
        await interaction.response.send_message(embed=discord.Embed(title="🔨 Member Banned", description=f"**{member}** has been banned.\n**Reason:** {reason}", color=discord.Color.red()))

    @app_commands.command(name="unban", description="Unban a user by their Discord ID")
    @app_commands.describe(user_id="The user's Discord ID", reason="Reason for the unban")
    @app_commands.default_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
        try:
            uid = int(user_id.strip())
        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID — must be a number.", ephemeral=True); return
        try:
            user = await self.bot.fetch_user(uid)
            await interaction.guild.unban(user, reason=f"Unbanned by {interaction.user}: {reason}")
        except discord.NotFound:
            await interaction.response.send_message("❌ User not found or not banned.", ephemeral=True); return
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to unban users.", ephemeral=True); return
        await log_action(interaction.guild, "Member Unbanned", interaction.user, user, f"Unbanned User: {user} — {reason}", discord.Color.green())
        await interaction.response.send_message(embed=discord.Embed(title="✅ User Unbanned", description=f"**{user}** has been unbanned.\n**Reason:** {reason}", color=discord.Color.green()))

    @app_commands.command(name="timeout", description="Timeout a member (mute them for a duration)")
    @app_commands.describe(member="Member to timeout", minutes="Duration in minutes (1–40320)", reason="Reason")
    @app_commands.default_permissions(moderate_members=True)
    async def timeout_cmd(self, interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason provided"):
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("❌ I can't timeout this member — their role is too high.", ephemeral=True); return
        try:
            await member.timeout(datetime.timedelta(minutes=minutes), reason=f"Timeout by {interaction.user}: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to timeout this member.", ephemeral=True); return
        await log_action(interaction.guild, "Member Timed Out", interaction.user, member, f"Timed out {member} for {minutes}m — {reason}", discord.Color.orange())
        await interaction.response.send_message(embed=discord.Embed(title="⏱️ Member Timed Out", description=f"**{member}** has been timed out for **{minutes} minute(s)**.\n**Reason:** {reason}", color=discord.Color.orange()))

    @app_commands.command(name="warn", description="Send a warning to a member via DM")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    @app_commands.default_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        dm_sent = False
        try:
            await member.send(embed=discord.Embed(title=f"⚠️ Warning from {interaction.guild.name}", description=f"You have been warned by **{interaction.user}**.\n**Reason:** {reason}", color=discord.Color.yellow()))
            dm_sent = True
        except discord.Forbidden:
            pass
        await log_action(interaction.guild, "Member Warned", interaction.user, member, f"Warned: {member} — {reason}", discord.Color.yellow())
        await interaction.response.send_message(embed=discord.Embed(
            title="⚠️ Member Warned",
            description=f"**{member}** has been warned.\n**Reason:** {reason}\n{'✅ Warning DM sent' if dm_sent else '❌ Could not send DM (user has DMs disabled)'}",
            color=discord.Color.yellow()
        ))

    @app_commands.command(name="remove", description="Remove all warnings (spam & AutoMod) for a member")
    @app_commands.describe(member="The member whose warnings should be cleared")
    @app_commands.default_permissions(moderate_members=True)
    async def remove_warnings(self, interaction: discord.Interaction, member: discord.Member):
        cleared = False
        antispam_cog = self.bot.cogs.get("AntiSpam")
        if antispam_cog is not None:
            guild_records = antispam_cog.records.get(interaction.guild.id)
            if guild_records and member.id in guild_records:
                guild_records[member.id]["offense"]    = 0
                guild_records[member.id]["timestamps"] = []
                cleared = True
        await log_action(interaction.guild, "Warnings Removed", interaction.user, member, f"All warnings cleared for {member}" + (" (spam offenses reset)" if cleared else " (no active records found)"), discord.Color.green())
        try:
            await member.send(embed=discord.Embed(title=f"✅ Warnings Cleared — {interaction.guild.name}", description=f"All your warnings in **{interaction.guild.name}** have been cleared by **{interaction.user}**.", color=discord.Color.green()))
        except discord.Forbidden:
            pass
        await interaction.response.send_message(embed=discord.Embed(title="✅ Warnings Removed", description=f"All warnings for **{member}** have been cleared.\n{'✅ Spam offense counter reset' if cleared else 'ℹ️ No active spam records found'}", color=discord.Color.green()), ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 6 — Welcome / Goodbye  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_WELCOME_FILE = os.path.join(_DATA_DIR, 'welcome.json')


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self.data = _load_json(_WELCOME_FILE)

    def _save(self):
        _save_json(_WELCOME_FILE, self.data)

    def _cfg(self, guild_id: int) -> dict:
        key = str(guild_id)
        if key not in self.data:
            self.data[key] = {"channel_id": None, "message": "Welcome {user} to **{server}**! You are member #{count} 🎉", "goodbye": "**{user}** has left {server}. Goodbye! 👋", "enabled": False}
        return self.data[key]

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = self._cfg(member.guild.id)
        if not cfg.get("enabled") or not cfg.get("channel_id"):
            return
        ch = member.guild.get_channel(cfg["channel_id"])
        if ch is None:
            return
        msg = cfg["message"].replace("{user}", member.mention).replace("{server}", member.guild.name).replace("{count}", str(member.guild.member_count)).replace("{username}", str(member))
        embed = discord.Embed(description=msg, color=discord.Color.green(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_author(name=f"Welcome to {member.guild.name}!", icon_url=member.guild.icon.url if member.guild.icon else None)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        cfg = self._cfg(member.guild.id)
        if not cfg.get("enabled") or not cfg.get("channel_id"):
            return
        ch = member.guild.get_channel(cfg["channel_id"])
        if ch is None:
            return
        msg = cfg["goodbye"].replace("{user}", str(member)).replace("{server}", member.guild.name).replace("{username}", str(member))
        embed = discord.Embed(description=msg, color=discord.Color.red(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    welcome_group = app_commands.Group(name="welcome", description="Configure welcome/goodbye messages", default_permissions=discord.Permissions(administrator=True))

    @welcome_group.command(name="setchannel", description="Set the welcome/goodbye channel")
    @app_commands.describe(channel="The channel to send welcome messages in")
    async def welcome_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        cfg = self._cfg(interaction.guild.id)
        cfg["channel_id"] = channel.id
        cfg["enabled"]    = True
        self._save()
        await interaction.response.send_message(f"✅ Welcome channel set to {channel.mention}.", ephemeral=True)

    @welcome_group.command(name="setmessage", description="Set the welcome message (use {user} {server} {count} {username})")
    @app_commands.describe(message="The welcome message template")
    async def welcome_setmessage(self, interaction: discord.Interaction, message: str):
        cfg = self._cfg(interaction.guild.id)
        cfg["message"] = message
        self._save()
        await interaction.response.send_message(f"✅ Welcome message updated.\n**Preview:** {message.replace('{user}', interaction.user.mention).replace('{server}', interaction.guild.name).replace('{count}', str(interaction.guild.member_count)).replace('{username}', str(interaction.user))}", ephemeral=True)

    @welcome_group.command(name="setgoodbye", description="Set the goodbye message (use {user} {server} {username})")
    @app_commands.describe(message="The goodbye message template")
    async def welcome_setgoodbye(self, interaction: discord.Interaction, message: str):
        cfg = self._cfg(interaction.guild.id)
        cfg["goodbye"] = message
        self._save()
        await interaction.response.send_message(f"✅ Goodbye message updated.", ephemeral=True)

    @welcome_group.command(name="disable", description="Disable welcome/goodbye messages")
    async def welcome_disable(self, interaction: discord.Interaction):
        cfg = self._cfg(interaction.guild.id)
        cfg["enabled"] = False
        self._save()
        await interaction.response.send_message("✅ Welcome/goodbye messages disabled.", ephemeral=True)

    @welcome_group.command(name="test", description="Send a test welcome message")
    async def welcome_test(self, interaction: discord.Interaction):
        cfg = self._cfg(interaction.guild.id)
        if not cfg.get("channel_id"):
            await interaction.response.send_message("❌ No welcome channel set. Use `/welcome setchannel` first.", ephemeral=True); return
        ch = interaction.guild.get_channel(cfg["channel_id"])
        if ch is None:
            await interaction.response.send_message("❌ Welcome channel not found.", ephemeral=True); return
        msg = cfg["message"].replace("{user}", interaction.user.mention).replace("{server}", interaction.guild.name).replace("{count}", str(interaction.guild.member_count)).replace("{username}", str(interaction.user))
        embed = discord.Embed(description=msg, color=discord.Color.green(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_author(name=f"Welcome to {interaction.guild.name}!", icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text="This is a test message")
        await ch.send(embed=embed)
        await interaction.response.send_message(f"✅ Test welcome message sent to {ch.mention}.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 7 — Auto-Role  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_AUTOROLE_FILE = os.path.join(_DATA_DIR, 'autorole.json')


class AutoRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self.data = _load_json(_AUTOROLE_FILE)

    def _save(self):
        _save_json(_AUTOROLE_FILE, self.data)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        role_id = self.data.get(str(member.guild.id))
        if not role_id:
            return
        role = member.guild.get_role(role_id)
        if role and role.is_assignable():
            try:
                await member.add_roles(role, reason="Jarvis Auto-Role")
            except (discord.Forbidden, discord.HTTPException):
                pass

    autorole_group = app_commands.Group(name="autorole", description="Configure auto-role on join", default_permissions=discord.Permissions(administrator=True))

    @autorole_group.command(name="set", description="Set the role to give to new members")
    @app_commands.describe(role="The role to auto-assign on join")
    async def autorole_set(self, interaction: discord.Interaction, role: discord.Role):
        if not role.is_assignable():
            await interaction.response.send_message("❌ I can't assign that role (it may be higher than my role).", ephemeral=True); return
        self.data[str(interaction.guild.id)] = role.id
        self._save()
        await interaction.response.send_message(f"✅ Auto-role set to {role.mention}. New members will receive this role on join.", ephemeral=True)

    @autorole_group.command(name="remove", description="Remove the auto-role")
    async def autorole_remove(self, interaction: discord.Interaction):
        self.data.pop(str(interaction.guild.id), None)
        self._save()
        await interaction.response.send_message("✅ Auto-role removed.", ephemeral=True)

    @autorole_group.command(name="status", description="View current auto-role setting")
    async def autorole_status(self, interaction: discord.Interaction):
        role_id = self.data.get(str(interaction.guild.id))
        if not role_id:
            await interaction.response.send_message("ℹ️ No auto-role is set. Use `/autorole set <role>` to configure one.", ephemeral=True); return
        role = interaction.guild.get_role(role_id)
        if role:
            await interaction.response.send_message(f"✅ Auto-role is set to {role.mention}.", ephemeral=True)
        else:
            self.data.pop(str(interaction.guild.id), None)
            self._save()
            await interaction.response.send_message("⚠️ The configured role no longer exists. Auto-role has been cleared.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 8 — Reaction Roles (Button-based)  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_RROLES_FILE = os.path.join(_DATA_DIR, 'reaction_roles.json')


class RoleButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, emoji: str = None):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, emoji=emoji, custom_id=f"rr_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message("❌ This role no longer exists.", ephemeral=True); return
        if role in interaction.user.roles:
            try:
                await interaction.user.remove_roles(role, reason="Jarvis Reaction Role")
                await interaction.response.send_message(f"✅ Removed role **{role.name}**.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to remove that role.", ephemeral=True)
        else:
            try:
                await interaction.user.add_roles(role, reason="Jarvis Reaction Role")
                await interaction.response.send_message(f"✅ Added role **{role.name}**.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to add that role.", ephemeral=True)


class PersistentRoleView(discord.ui.View):
    def __init__(self, buttons: list):
        super().__init__(timeout=None)
        for btn in buttons:
            self.add_item(RoleButton(btn["role_id"], btn["label"], btn.get("emoji")))


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self.data = _load_json(_RROLES_FILE)

    def _save(self):
        _save_json(_RROLES_FILE, self.data)

    async def cog_load(self):
        for guild_id, messages in self.data.items():
            for msg_id, info in messages.items():
                if info.get("buttons"):
                    view = PersistentRoleView(info["buttons"])
                    self.bot.add_view(view, message_id=int(msg_id))

    rr_group = app_commands.Group(name="reactionrole", description="Manage reaction role menus", default_permissions=discord.Permissions(administrator=True))

    @rr_group.command(name="create", description="Create a new role menu message")
    @app_commands.describe(channel="Channel to post the menu in", title="Title for the role menu")
    async def rr_create(self, interaction: discord.Interaction, channel: discord.TextChannel, title: str):
        embed = discord.Embed(title=f"🎨 {title}", description="Click the buttons below to assign or remove roles.", color=discord.Color.blurple())
        embed.set_footer(text="Click a button to toggle the role • Jarvis by Pookie Boy")
        msg = await channel.send(embed=embed)
        gid = str(interaction.guild.id)
        if gid not in self.data:
            self.data[gid] = {}
        self.data[gid][str(msg.id)] = {"title": title, "channel_id": channel.id, "buttons": []}
        self._save()
        await interaction.response.send_message(f"✅ Role menu created in {channel.mention}.\nMessage ID: `{msg.id}`\nNow use `/reactionrole add {msg.id} <role> <label>` to add roles.", ephemeral=True)

    @rr_group.command(name="add", description="Add a role button to an existing role menu")
    @app_commands.describe(message_id="The ID of the role menu message", role="The role to add", label="Button label")
    async def rr_add(self, interaction: discord.Interaction, message_id: str, role: discord.Role, label: str):
        gid = str(interaction.guild.id)
        if gid not in self.data or message_id not in self.data[gid]:
            await interaction.response.send_message("❌ Role menu not found. Create one first with `/reactionrole create`.", ephemeral=True); return
        info = self.data[gid][message_id]
        if len(info["buttons"]) >= 25:
            await interaction.response.send_message("❌ Maximum of 25 buttons per role menu.", ephemeral=True); return
        info["buttons"].append({"role_id": role.id, "label": label})
        self._save()
        ch = interaction.guild.get_channel(info["channel_id"])
        if ch:
            try:
                msg = await ch.fetch_message(int(message_id))
                view = PersistentRoleView(info["buttons"])
                self.bot.add_view(view, message_id=int(message_id))
                await msg.edit(view=view)
            except (discord.NotFound, discord.Forbidden):
                pass
        await interaction.response.send_message(f"✅ Added **{role.name}** button to the role menu.", ephemeral=True)

    @rr_group.command(name="remove", description="Remove a role button from a role menu")
    @app_commands.describe(message_id="The ID of the role menu message", role="The role to remove")
    async def rr_remove(self, interaction: discord.Interaction, message_id: str, role: discord.Role):
        gid = str(interaction.guild.id)
        if gid not in self.data or message_id not in self.data[gid]:
            await interaction.response.send_message("❌ Role menu not found.", ephemeral=True); return
        info = self.data[gid][message_id]
        info["buttons"] = [b for b in info["buttons"] if b["role_id"] != role.id]
        self._save()
        ch = interaction.guild.get_channel(info["channel_id"])
        if ch:
            try:
                msg = await ch.fetch_message(int(message_id))
                if info["buttons"]:
                    view = PersistentRoleView(info["buttons"])
                    self.bot.add_view(view, message_id=int(message_id))
                    await msg.edit(view=view)
                else:
                    await msg.edit(view=None)
            except (discord.NotFound, discord.Forbidden):
                pass
        await interaction.response.send_message(f"✅ Removed **{role.name}** button from the role menu.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 9 — Leveling / XP System  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_XP_FILE         = os.path.join(_DATA_DIR, 'xp.json')
_XP_CONFIG_FILE  = os.path.join(_DATA_DIR, 'xp_config.json')

XP_PER_MESSAGE_MIN = 15
XP_PER_MESSAGE_MAX = 25
XP_COOLDOWN_SECS   = 60


def _xp_for_level(level: int) -> int:
    return 5 * (level ** 2) + 50 * level + 100


def _level_from_xp(xp: int) -> int:
    level = 0
    while xp >= _xp_for_level(level):
        xp -= _xp_for_level(level)
        level += 1
    return level


class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot       = bot
        self.xp_data   = _load_json(_XP_FILE)
        self.xp_config = _load_json(_XP_CONFIG_FILE)
        self._cooldowns: dict = {}

    def _save_xp(self):
        _save_json(_XP_FILE, self.xp_data)

    def _save_config(self):
        _save_json(_XP_CONFIG_FILE, self.xp_config)

    def _get_user(self, guild_id: int, user_id: int) -> dict:
        gid = str(guild_id)
        uid = str(user_id)
        if gid not in self.xp_data:
            self.xp_data[gid] = {}
        if uid not in self.xp_data[gid]:
            self.xp_data[gid][uid] = {"xp": 0, "level": 0, "messages": 0}
        return self.xp_data[gid][uid]

    def _get_config(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if gid not in self.xp_config:
            self.xp_config[gid] = {"level_channel_id": None, "level_roles": {}}
        return self.xp_config[gid]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not message.content:
            return
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._cooldowns.get(key, 0) < XP_COOLDOWN_SECS:
            return
        self._cooldowns[key] = now

        user_data = self._get_user(message.guild.id, message.author.id)
        gained = random.randint(XP_PER_MESSAGE_MIN, XP_PER_MESSAGE_MAX)
        user_data["xp"] += gained
        user_data["messages"] = user_data.get("messages", 0) + 1

        old_level = user_data["level"]
        new_level = _level_from_xp(user_data["xp"])
        user_data["level"] = new_level
        self._save_xp()

        if new_level > old_level:
            await self._on_level_up(message, new_level)

    async def _on_level_up(self, message: discord.Message, new_level: int):
        cfg = self._get_config(message.guild.id)
        ch_id = cfg.get("level_channel_id")
        ch = message.guild.get_channel(ch_id) if ch_id else message.channel

        embed = discord.Embed(
            title="⭐ Level Up!",
            description=f"🎉 {message.author.mention} reached **Level {new_level}**!",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        level_roles = cfg.get("level_roles", {})
        role_id = level_roles.get(str(new_level))
        if role_id:
            role = message.guild.get_role(role_id)
            if role and role.is_assignable():
                try:
                    await message.author.add_roles(role, reason=f"Jarvis Leveling: reached level {new_level}")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @app_commands.command(name="rank", description="Check your or someone else's rank and XP")
    @app_commands.describe(user="The user to check (defaults to you)")
    async def rank(self, interaction: discord.Interaction, user: discord.Member = None):
        user = user or interaction.user
        user_data = self._get_user(interaction.guild.id, user.id)
        level = user_data["level"]
        total_xp = user_data["xp"]

        xp_progress = total_xp
        for lvl in range(level):
            xp_progress -= _xp_for_level(lvl)
        xp_needed = _xp_for_level(level)
        bar_filled = int((xp_progress / max(xp_needed, 1)) * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)

        all_users = self.xp_data.get(str(interaction.guild.id), {})
        sorted_users = sorted(all_users.items(), key=lambda x: x[1].get("xp", 0), reverse=True)
        rank_pos = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == str(user.id)), "?")

        embed = discord.Embed(color=discord.Color.gold(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_author(name=f"{user.display_name}'s Rank", icon_url=user.display_avatar.url)
        embed.add_field(name="🏆 Rank",     value=f"**#{rank_pos}**",       inline=True)
        embed.add_field(name="⭐ Level",    value=f"**{level}**",            inline=True)
        embed.add_field(name="✉️ Messages", value=f"**{user_data.get('messages', 0):,}**", inline=True)
        embed.add_field(name=f"XP Progress [{xp_progress:,} / {xp_needed:,}]", value=f"`{bar}`", inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Jarvis Leveling • by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="View the top 10 members by XP")
    async def leaderboard(self, interaction: discord.Interaction):
        all_users = self.xp_data.get(str(interaction.guild.id), {})
        if not all_users:
            await interaction.response.send_message("ℹ️ No XP data yet. Members earn XP by chatting!", ephemeral=True); return
        sorted_users = sorted(all_users.items(), key=lambda x: x[1].get("xp", 0), reverse=True)[:10]
        embed = discord.Embed(title="🏆 Server Leaderboard", color=discord.Color.gold(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        medals = ["🥇", "🥈", "🥉"]
        desc = ""
        for i, (uid, data) in enumerate(sorted_users):
            medal = medals[i] if i < 3 else f"`#{i+1}`"
            member = interaction.guild.get_member(int(uid))
            name = member.display_name if member else f"Unknown ({uid})"
            desc += f"{medal} **{name}** — Level {data.get('level', 0)} • {data.get('xp', 0):,} XP\n"
        embed.description = desc
        embed.set_footer(text="Jarvis Leveling • by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="setlevelchannel", description="Set where level-up announcements are sent")
    @app_commands.describe(channel="Channel for level-up messages")
    @app_commands.default_permissions(administrator=True)
    async def setlevelchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        cfg = self._get_config(interaction.guild.id)
        cfg["level_channel_id"] = channel.id
        self._save_config()
        await interaction.response.send_message(f"✅ Level-up announcements will be sent to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="setlevelrole", description="Award a role when a member reaches a specific level")
    @app_commands.describe(level="The level that triggers the role award", role="The role to award")
    @app_commands.default_permissions(administrator=True)
    async def setlevelrole(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 500], role: discord.Role):
        cfg = self._get_config(interaction.guild.id)
        cfg["level_roles"][str(level)] = role.id
        self._save_config()
        await interaction.response.send_message(f"✅ {role.mention} will be awarded when members reach **Level {level}**.", ephemeral=True)

    @app_commands.command(name="resetxp", description="Reset a member's XP and level (admin only)")
    @app_commands.describe(user="The member whose XP should be reset")
    @app_commands.default_permissions(administrator=True)
    async def resetxp(self, interaction: discord.Interaction, user: discord.Member):
        gid = str(interaction.guild.id)
        uid = str(user.id)
        if gid in self.xp_data and uid in self.xp_data[gid]:
            self.xp_data[gid][uid] = {"xp": 0, "level": 0, "messages": 0}
            self._save_xp()
        await log_action(interaction.guild, "XP Reset", interaction.user, user, f"Reset XP for {user}", discord.Color.orange())
        await interaction.response.send_message(f"✅ Reset XP and level for **{user.display_name}**.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 10 — Ticket System  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_TICKET_FILE = os.path.join(_DATA_DIR, 'tickets.json')


class TicketCloseButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        if not (interaction.user.guild_permissions.manage_channels or interaction.channel.name.startswith("ticket-")):
            await interaction.response.send_message("❌ You don't have permission to close this ticket.", ephemeral=True); return

        content = []
        async for msg in interaction.channel.history(limit=200, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M")
            content.append(f"[{ts}] {msg.author.display_name}: {msg.content or '[embed/attachment]'}")
        transcript = "\n".join(content)

        transcript_file = discord.File(
            fp=__import__('io').StringIO(transcript),
            filename=f"transcript-{interaction.channel.name}.txt"
        )

        tickets_cog = interaction.client.cogs.get("Tickets")
        log_ch = None
        if tickets_cog:
            cfg = tickets_cog.data.get(str(interaction.guild.id), {})
            log_ch_id = cfg.get("log_channel_id")
            if log_ch_id:
                log_ch = interaction.guild.get_channel(log_ch_id)

        embed = discord.Embed(title="🎫 Ticket Closed", description=f"Ticket **{interaction.channel.name}** was closed by {interaction.user.mention}.", color=discord.Color.red(), timestamp=datetime.datetime.now(datetime.timezone.utc))

        if log_ch:
            try:
                await log_ch.send(embed=embed, file=transcript_file)
            except (discord.Forbidden, discord.HTTPException):
                pass

        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...", ephemeral=False)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            pass


class TicketOpenButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Open a Ticket", style=discord.ButtonStyle.success, custom_id="ticket_open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets_cog = interaction.client.cogs.get("Tickets")
        if not tickets_cog:
            await interaction.response.send_message("❌ Ticket system unavailable.", ephemeral=True); return

        cfg = tickets_cog.data.get(str(interaction.guild.id), {})
        category_id = cfg.get("category_id")
        support_role_id = cfg.get("support_role_id")

        existing = discord.utils.get(interaction.guild.text_channels, name=f"ticket-{interaction.user.name.lower()[:15]}")
        if existing:
            await interaction.response.send_message(f"❌ You already have an open ticket: {existing.mention}", ephemeral=True); return

        category = interaction.guild.get_channel(category_id) if category_id else None
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }
        if support_role_id:
            support_role = interaction.guild.get_role(support_role_id)
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        try:
            ch = await interaction.guild.create_text_channel(
                name=f"ticket-{interaction.user.name.lower()[:15]}",
                category=category,
                overwrites=overwrites,
                topic=f"Ticket by {interaction.user} ({interaction.user.id})",
                reason="Jarvis Ticket System"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not create ticket channel: {e}", ephemeral=True); return

        embed = discord.Embed(
            title="🎫 Support Ticket",
            description=f"Hello {interaction.user.mention}! A staff member will be with you shortly.\n\nPlease describe your issue in detail.",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text="Click the button below to close your ticket when done.")
        await ch.send(embed=embed, view=TicketCloseButton())

        if support_role_id:
            support_role = interaction.guild.get_role(support_role_id)
            if support_role:
                await ch.send(f"{support_role.mention}", delete_after=3)

        await interaction.response.send_message(f"✅ Ticket created: {ch.mention}", ephemeral=True)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self.data = _load_json(_TICKET_FILE)

    def _save(self):
        _save_json(_TICKET_FILE, self.data)

    async def cog_load(self):
        self.bot.add_view(TicketOpenButton())
        self.bot.add_view(TicketCloseButton())

    ticket_group = app_commands.Group(name="ticket", description="Manage the ticket system", default_permissions=discord.Permissions(administrator=True))

    @ticket_group.command(name="setup", description="Set up the ticket system with a panel message")
    @app_commands.describe(channel="Channel to post the ticket panel in", category="Category for ticket channels", support_role="Role that can see all tickets")
    async def ticket_setup(self, interaction: discord.Interaction, channel: discord.TextChannel, category: discord.CategoryChannel = None, support_role: discord.Role = None):
        gid = str(interaction.guild.id)
        self.data[gid] = {
            "category_id": category.id if category else None,
            "support_role_id": support_role.id if support_role else None,
            "log_channel_id": None,
            "panel_channel_id": channel.id
        }
        self._save()

        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Need help? Click the button below to open a private support ticket.\nA staff member will assist you as soon as possible.",
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Jarvis Ticket System • by Pookie Boy")
        await channel.send(embed=embed, view=TicketOpenButton())
        await interaction.response.send_message(f"✅ Ticket panel created in {channel.mention}.", ephemeral=True)

    @ticket_group.command(name="close", description="Close the current ticket channel")
    async def ticket_close(self, interaction: discord.Interaction):
        if not interaction.channel.name.startswith("ticket-"):
            await interaction.response.send_message("❌ This command can only be used inside a ticket channel.", ephemeral=True); return
        await interaction.response.send_message("🔒 Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @ticket_group.command(name="add", description="Add a user to the current ticket")
    @app_commands.describe(user="User to add to the ticket")
    @app_commands.default_permissions(manage_channels=True)
    async def ticket_add(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.channel.name.startswith("ticket-"):
            await interaction.response.send_message("❌ Use this inside a ticket channel.", ephemeral=True); return
        await interaction.channel.set_permissions(user, read_messages=True, send_messages=True)
        await interaction.response.send_message(f"✅ Added {user.mention} to the ticket.")

    @ticket_group.command(name="remove", description="Remove a user from the current ticket")
    @app_commands.describe(user="User to remove from the ticket")
    @app_commands.default_permissions(manage_channels=True)
    async def ticket_remove(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.channel.name.startswith("ticket-"):
            await interaction.response.send_message("❌ Use this inside a ticket channel.", ephemeral=True); return
        await interaction.channel.set_permissions(user, overwrite=None)
        await interaction.response.send_message(f"✅ Removed {user.mention} from the ticket.")

    @ticket_group.command(name="setlogchannel", description="Set the channel where closed ticket transcripts are sent")
    @app_commands.describe(channel="Channel for ticket transcripts")
    async def ticket_setlog(self, interaction: discord.Interaction, channel: discord.TextChannel):
        gid = str(interaction.guild.id)
        if gid not in self.data:
            self.data[gid] = {}
        self.data[gid]["log_channel_id"] = channel.id
        self._save()
        await interaction.response.send_message(f"✅ Ticket transcripts will be sent to {channel.mention}.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 11 — Giveaway System  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_GIVEAWAY_FILE = os.path.join(_DATA_DIR, 'giveaways.json')
GIVEAWAY_EMOJI = "🎉"


def _parse_duration(duration_str: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    match = re.fullmatch(r"(\d+)([smhdw])", duration_str.strip().lower())
    if not match:
        return 0
    return int(match.group(1)) * units[match.group(2)]


class Giveaway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self.data = _load_json(_GIVEAWAY_FILE)
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    def _save(self):
        _save_json(_GIVEAWAY_FILE, self.data)

    @tasks.loop(seconds=15)
    async def check_giveaways(self):
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        to_end = [(mid, g) for mid, g in self.data.items() if not g.get("ended") and g.get("ends_at", 0) <= now]
        for mid, g in to_end:
            await self._end_giveaway(int(mid), g)

    @check_giveaways.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def _end_giveaway(self, message_id: int, g: dict):
        self.data[str(message_id)]["ended"] = True
        self._save()
        ch = self.bot.get_channel(g["channel_id"])
        if ch is None:
            return
        try:
            msg = await ch.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        try:
            reaction = discord.utils.get(msg.reactions, emoji=GIVEAWAY_EMOJI)
            if reaction:
                entrants = [u async for u in reaction.users() if not u.bot]
            else:
                entrants = []
        except Exception:
            entrants = []

        winners_count = g.get("winners", 1)
        if entrants:
            winners = random.sample(entrants, min(winners_count, len(entrants)))
            winner_mentions = ", ".join(w.mention for w in winners)
        else:
            winner_mentions = "No one entered 😢"
            winners = []

        embed = discord.Embed(title=f"🎉 GIVEAWAY ENDED — {g['prize']}", color=discord.Color.green(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="Winner(s)", value=winner_mentions, inline=False)
        embed.add_field(name="Hosted by", value=f"<@{g['host_id']}>", inline=True)
        embed.add_field(name="Entries",   value=str(len(entrants)), inline=True)
        embed.set_footer(text="Giveaway ended • Jarvis by Pookie Boy")
        try:
            await msg.edit(embed=embed)
            if winners:
                await ch.send(f"🎊 Congratulations {winner_mentions}! You won **{g['prize']}**!")
        except (discord.Forbidden, discord.HTTPException):
            pass

    giveaway_group = app_commands.Group(name="giveaway", description="Manage giveaways", default_permissions=discord.Permissions(manage_guild=True))

    @giveaway_group.command(name="start", description="Start a giveaway")
    @app_commands.describe(duration="Duration (e.g. 1h, 30m, 2d)", winners="Number of winners", prize="What you're giving away", channel="Channel to post in")
    async def giveaway_start(self, interaction: discord.Interaction, duration: str, winners: app_commands.Range[int, 1, 20], prize: str, channel: discord.TextChannel = None):
        secs = _parse_duration(duration)
        if secs <= 0:
            await interaction.response.send_message("❌ Invalid duration. Use formats like `30m`, `1h`, `2d`.", ephemeral=True); return
        ch = channel or interaction.channel
        ends_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=secs)
        embed = discord.Embed(
            title=f"🎉 GIVEAWAY — {prize}",
            description=f"React with {GIVEAWAY_EMOJI} to enter!\n\n**Winners:** {winners}\n**Ends:** <t:{int(ends_at.timestamp())}:R>\n**Hosted by:** {interaction.user.mention}",
            color=discord.Color.gold(),
            timestamp=ends_at
        )
        embed.set_footer(text=f"Ends at • {winners} winner(s)")
        msg = await ch.send(embed=embed)
        await msg.add_reaction(GIVEAWAY_EMOJI)
        self.data[str(msg.id)] = {
            "channel_id": ch.id, "guild_id": interaction.guild.id,
            "prize": prize, "winners": winners, "host_id": interaction.user.id,
            "ends_at": ends_at.timestamp(), "ended": False
        }
        self._save()
        await interaction.response.send_message(f"✅ Giveaway started in {ch.mention}! Message ID: `{msg.id}`", ephemeral=True)

    @giveaway_group.command(name="end", description="End a giveaway early")
    @app_commands.describe(message_id="The giveaway message ID")
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str):
        g = self.data.get(message_id)
        if not g or g.get("ended"):
            await interaction.response.send_message("❌ Giveaway not found or already ended.", ephemeral=True); return
        if g.get("guild_id") != interaction.guild.id:
            await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True); return
        g["ends_at"] = 0
        self._save()
        await self._end_giveaway(int(message_id), g)
        await interaction.response.send_message("✅ Giveaway ended!", ephemeral=True)

    @giveaway_group.command(name="reroll", description="Reroll a giveaway winner")
    @app_commands.describe(message_id="The giveaway message ID")
    async def giveaway_reroll(self, interaction: discord.Interaction, message_id: str):
        g = self.data.get(message_id)
        if not g or not g.get("ended"):
            await interaction.response.send_message("❌ Giveaway not found or hasn't ended yet.", ephemeral=True); return
        ch = self.bot.get_channel(g["channel_id"])
        if ch is None:
            await interaction.response.send_message("❌ Giveaway channel not found.", ephemeral=True); return
        try:
            msg = await ch.fetch_message(int(message_id))
            reaction = discord.utils.get(msg.reactions, emoji=GIVEAWAY_EMOJI)
            entrants = [u async for u in reaction.users() if not u.bot] if reaction else []
        except Exception:
            await interaction.response.send_message("❌ Could not fetch giveaway.", ephemeral=True); return
        if not entrants:
            await interaction.response.send_message("❌ No entries to reroll.", ephemeral=True); return
        winner = random.choice(entrants)
        await ch.send(f"🎊 New winner by reroll: {winner.mention}! Congratulations on winning **{g['prize']}**!")
        await interaction.response.send_message("✅ Rerolled!", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 12 — Poll System  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="poll", description="Create a poll with up to 5 options")
    @app_commands.describe(question="The poll question", option1="First option", option2="Second option", option3="Third option (optional)", option4="Fourth option (optional)", option5="Fifth option (optional)")
    async def poll(self, interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None, option4: str = None, option5: str = None):
        options = [o for o in [option1, option2, option3, option4, option5] if o]
        embed = discord.Embed(title=f"📊 {question}", color=discord.Color.blurple(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        desc = ""
        for i, opt in enumerate(options):
            desc += f"{POLL_EMOJIS[i]} {opt}\n"
        embed.description = desc
        embed.set_footer(text=f"Poll by {interaction.user.display_name} • React to vote!")
        await interaction.response.send_message("✅ Poll created!", ephemeral=True)
        msg = await interaction.channel.send(embed=embed)
        for i in range(len(options)):
            await msg.add_reaction(POLL_EMOJIS[i])

    @app_commands.command(name="quickpoll", description="Create a quick yes/no poll")
    @app_commands.describe(question="The yes/no question")
    async def quickpoll(self, interaction: discord.Interaction, question: str):
        embed = discord.Embed(title=f"📊 {question}", description="👍 Yes  |  👎 No", color=discord.Color.blurple(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_footer(text=f"Poll by {interaction.user.display_name} • React to vote!")
        await interaction.response.send_message("✅ Poll created!", ephemeral=True)
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")


# ══════════════════════════════════════════════════════════════════════════════
# COG 13 — Starboard  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_STARBOARD_FILE = os.path.join(_DATA_DIR, 'starboard.json')
STAR_EMOJI = "⭐"


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self.data = _load_json(_STARBOARD_FILE)
        self._posted: dict = {}

    def _save(self):
        _save_json(_STARBOARD_FILE, self.data)

    def _cfg(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if gid not in self.data:
            self.data[gid] = {"channel_id": None, "threshold": 3, "enabled": False, "posted": {}}
        return self.data[gid]

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        if user.bot or reaction.message.guild is None:
            return
        if str(reaction.emoji) != STAR_EMOJI:
            return
        cfg = self._cfg(reaction.message.guild.id)
        if not cfg.get("enabled") or not cfg.get("channel_id"):
            return
        if reaction.count < cfg.get("threshold", 3):
            return
        msg_id = str(reaction.message.id)
        if msg_id in cfg.get("posted", {}):
            return
        if reaction.message.channel.id == cfg["channel_id"]:
            return

        starboard_ch = reaction.message.guild.get_channel(cfg["channel_id"])
        if starboard_ch is None:
            return

        message = reaction.message
        embed = discord.Embed(description=message.content or "", color=discord.Color.gold(), timestamp=message.created_at)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="Original", value=f"[Jump to message]({message.jump_url})")
        embed.add_field(name="Channel", value=message.channel.mention)
        if message.attachments:
            embed.set_image(url=message.attachments[0].url)
        embed.set_footer(text=f"⭐ {reaction.count} stars")

        try:
            sb_msg = await starboard_ch.send(embed=embed)
            cfg.setdefault("posted", {})[msg_id] = sb_msg.id
            self._save()
        except (discord.Forbidden, discord.HTTPException):
            pass

    starboard_group = app_commands.Group(name="starboard", description="Configure the starboard", default_permissions=discord.Permissions(administrator=True))

    @starboard_group.command(name="setchannel", description="Set the starboard channel")
    @app_commands.describe(channel="Channel for the starboard")
    async def sb_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        cfg = self._cfg(interaction.guild.id)
        cfg["channel_id"] = channel.id
        cfg["enabled"]    = True
        self._save()
        await interaction.response.send_message(f"✅ Starboard channel set to {channel.mention}.", ephemeral=True)

    @starboard_group.command(name="setthreshold", description="Set how many ⭐ a message needs to get starred")
    @app_commands.describe(count="Number of stars required (default 3)")
    async def sb_setthreshold(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 50]):
        cfg = self._cfg(interaction.guild.id)
        cfg["threshold"] = count
        self._save()
        await interaction.response.send_message(f"✅ Starboard threshold set to **{count}** ⭐.", ephemeral=True)

    @starboard_group.command(name="disable", description="Disable the starboard")
    async def sb_disable(self, interaction: discord.Interaction):
        cfg = self._cfg(interaction.guild.id)
        cfg["enabled"] = False
        self._save()
        await interaction.response.send_message("✅ Starboard disabled.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 14 — Suggestions  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_SUGGEST_FILE = os.path.join(_DATA_DIR, 'suggestions.json')


class Suggestions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot  = bot
        self.data = _load_json(_SUGGEST_FILE)

    def _save(self):
        _save_json(_SUGGEST_FILE, self.data)

    def _cfg(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if gid not in self.data:
            self.data[gid] = {"channel_id": None, "suggestions": {}, "next_id": 1}
        return self.data[gid]

    @app_commands.command(name="suggestchannel", description="Set the suggestions channel")
    @app_commands.describe(channel="Channel for suggestions")
    @app_commands.default_permissions(administrator=True)
    async def suggestchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        cfg = self._cfg(interaction.guild.id)
        cfg["channel_id"] = channel.id
        self._save()
        await interaction.response.send_message(f"✅ Suggestions channel set to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="suggest", description="Submit a suggestion for the server")
    @app_commands.describe(idea="Your suggestion or idea")
    async def suggest(self, interaction: discord.Interaction, idea: str):
        cfg = self._cfg(interaction.guild.id)
        if not cfg.get("channel_id"):
            await interaction.response.send_message("❌ No suggestion channel set. Ask an admin to run `/suggestchannel`.", ephemeral=True); return
        ch = interaction.guild.get_channel(cfg["channel_id"])
        if ch is None:
            await interaction.response.send_message("❌ Suggestion channel not found.", ephemeral=True); return

        suggestion_id = cfg["next_id"]
        cfg["next_id"] += 1

        embed = discord.Embed(title=f"💡 Suggestion #{suggestion_id}", description=idea, color=0xFEE75C, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Status", value="⏳ Pending", inline=True)
        embed.set_footer(text=f"Suggestion ID: {suggestion_id}")

        msg = await ch.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")

        cfg["suggestions"][str(suggestion_id)] = {
            "message_id": msg.id, "author_id": interaction.user.id,
            "idea": idea, "status": "pending"
        }
        self._save()
        await interaction.response.send_message(f"✅ Suggestion #{suggestion_id} submitted to {ch.mention}!", ephemeral=True)

    suggestion_group = app_commands.Group(name="suggestion", description="Manage suggestions", default_permissions=discord.Permissions(manage_guild=True))

    @suggestion_group.command(name="approve", description="Approve a suggestion")
    @app_commands.describe(suggestion_id="The suggestion number", reason="Reason for approval")
    async def suggestion_approve(self, interaction: discord.Interaction, suggestion_id: int, reason: str = "No reason provided"):
        await self._update_suggestion(interaction, suggestion_id, "approved", reason, discord.Color.green(), "✅ Approved")

    @suggestion_group.command(name="deny", description="Deny a suggestion")
    @app_commands.describe(suggestion_id="The suggestion number", reason="Reason for denial")
    async def suggestion_deny(self, interaction: discord.Interaction, suggestion_id: int, reason: str = "No reason provided"):
        await self._update_suggestion(interaction, suggestion_id, "denied", reason, discord.Color.red(), "❌ Denied")

    async def _update_suggestion(self, interaction, suggestion_id, status, reason, color, status_label):
        cfg = self._cfg(interaction.guild.id)
        s = cfg["suggestions"].get(str(suggestion_id))
        if not s:
            await interaction.response.send_message(f"❌ Suggestion #{suggestion_id} not found.", ephemeral=True); return
        ch = interaction.guild.get_channel(cfg.get("channel_id"))
        if ch:
            try:
                msg = await ch.fetch_message(s["message_id"])
                embed = msg.embeds[0] if msg.embeds else discord.Embed()
                embed.color = color
                for i, field in enumerate(embed.fields):
                    if field.name == "Status":
                        embed.set_field_at(i, name="Status", value=status_label, inline=True)
                        break
                embed.add_field(name="Response", value=f"**By:** {interaction.user.mention}\n**Reason:** {reason}", inline=False)
                await msg.edit(embed=embed)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        s["status"] = status
        self._save()
        author = interaction.guild.get_member(s["author_id"])
        if author:
            try:
                await author.send(embed=discord.Embed(
                    title=f"💡 Your Suggestion was {status_label}",
                    description=f"Your suggestion in **{interaction.guild.name}**: _{s['idea']}_\n\n**Decision:** {status_label}\n**Reason:** {reason}",
                    color=color
                ))
            except discord.Forbidden:
                pass
        await interaction.response.send_message(f"✅ Suggestion #{suggestion_id} has been {status}.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# COG 15 — AFK + Snipe  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

class AFKSnipe(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot        = bot
        self._afk: dict = {}
        self._snipe: dict = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        gid = message.guild.id
        uid = message.author.id

        if uid in self._afk.get(gid, {}):
            del self._afk[gid][uid]
            try:
                await message.channel.send(f"👋 Welcome back {message.author.mention}! Your AFK status has been removed.", delete_after=10)
            except (discord.Forbidden, discord.HTTPException):
                pass

        afk_users = self._afk.get(gid, {})
        mentioned = list(message.mentions) + [m for role in message.role_mentions for m in role.members]
        for member in mentioned:
            if member.id in afk_users and member.id != uid:
                afk_info = afk_users[member.id]
                since = datetime.datetime.now(datetime.timezone.utc) - afk_info["since"]
                mins = int(since.total_seconds() // 60)
                try:
                    await message.channel.send(f"💤 **{member.display_name}** is AFK: *{afk_info['reason']}* — {mins}m ago", delete_after=15)
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None or message.author.bot or not message.content:
            return
        gid = message.guild.id
        cid = message.channel.id
        if gid not in self._snipe:
            self._snipe[gid] = {}
        self._snipe[gid][cid] = {
            "content":   message.content,
            "author":    str(message.author),
            "avatar":    str(message.author.display_avatar.url),
            "timestamp": datetime.datetime.now(datetime.timezone.utc),
        }

    @app_commands.command(name="afk", description="Set your AFK status")
    @app_commands.describe(reason="Reason for going AFK")
    async def afk(self, interaction: discord.Interaction, reason: str = "AFK"):
        gid = interaction.guild.id
        uid = interaction.user.id
        if gid not in self._afk:
            self._afk[gid] = {}
        self._afk[gid][uid] = {"reason": reason, "since": datetime.datetime.now(datetime.timezone.utc)}
        await interaction.response.send_message(f"💤 You are now AFK: *{reason}*", ephemeral=True)

    @app_commands.command(name="snipe", description="Show the last deleted message in this channel")
    async def snipe(self, interaction: discord.Interaction):
        data = self._snipe.get(interaction.guild.id, {}).get(interaction.channel.id)
        if not data:
            await interaction.response.send_message("❌ Nothing to snipe — no recently deleted messages.", ephemeral=True); return
        embed = discord.Embed(description=data["content"], color=discord.Color.red(), timestamp=data["timestamp"])
        embed.set_author(name=data["author"], icon_url=data["avatar"])
        embed.set_footer(text="Sniped by Jarvis • Deleted message")
        await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
# COG 16 — Reminders  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="remind", description="Set a reminder for yourself")
    @app_commands.describe(time="When to remind you (e.g. 30m, 2h, 1d)", message="What to remind you about")
    async def remind(self, interaction: discord.Interaction, time: str, message: str):
        secs = _parse_duration(time)
        if secs <= 0:
            await interaction.response.send_message("❌ Invalid time format. Use `30m`, `2h`, `1d`, etc.", ephemeral=True); return
        if secs > 86400 * 30:
            await interaction.response.send_message("❌ Maximum reminder time is 30 days.", ephemeral=True); return

        remind_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=secs)
        await interaction.response.send_message(f"⏰ Got it! I'll remind you <t:{int(remind_at.timestamp())}:R>: **{message}**", ephemeral=True)

        async def _fire():
            await asyncio.sleep(secs)
            embed = discord.Embed(
                title="⏰ Reminder!",
                description=message,
                color=discord.Color.blurple(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.set_footer(text="Jarvis Reminder • by Pookie Boy")
            try:
                await interaction.user.send(embed=embed)
            except discord.Forbidden:
                try:
                    await interaction.channel.send(f"⏰ {interaction.user.mention} — Reminder: **{message}**")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        asyncio.create_task(_fire())


# ══════════════════════════════════════════════════════════════════════════════
# COG 17 — Info Commands  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

class Info(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="userinfo", description="View detailed information about a user")
    @app_commands.describe(user="The user to look up (defaults to you)")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None):
        user = user or interaction.user
        roles = [r.mention for r in reversed(user.roles) if r != interaction.guild.default_role]
        joined_guild   = f"<t:{int(user.joined_at.timestamp())}:F>" if user.joined_at else "Unknown"
        joined_discord = f"<t:{int(user.created_at.timestamp())}:F>"

        flags = []
        if user.bot:          flags.append("🤖 Bot")
        if user.public_flags.staff:            flags.append("👨‍💼 Discord Staff")
        if user.public_flags.partner:          flags.append("🤝 Partner")
        if user.public_flags.hypesquad:        flags.append("🏠 HypeSquad Events")
        if user.public_flags.bug_hunter:       flags.append("🐛 Bug Hunter")
        if user.public_flags.early_supporter:  flags.append("⭐ Early Supporter")

        embed = discord.Embed(color=user.color if user.color != discord.Color.default() else discord.Color.blurple(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_author(name=f"{user} — User Info", icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="🆔 User ID",        value=f"`{user.id}`",             inline=True)
        embed.add_field(name="📛 Display Name",   value=user.display_name,           inline=True)
        embed.add_field(name="🏷️ Discriminator", value=str(user),                   inline=True)
        embed.add_field(name="📅 Joined Server",  value=joined_guild,                inline=True)
        embed.add_field(name="🗓️ Joined Discord", value=joined_discord,              inline=True)
        embed.add_field(name="🎨 Top Role",        value=user.top_role.mention,       inline=True)
        if flags:
            embed.add_field(name="🏅 Badges", value=" ".join(flags), inline=False)
        if roles:
            embed.add_field(name=f"🎭 Roles ({len(roles)})", value=" ".join(roles[:15]) + (" ..." if len(roles) > 15 else ""), inline=False)
        embed.set_footer(text="Jarvis Info • by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="View detailed information about this server")
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        text_ch  = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        cats     = len(guild.categories)
        bots     = sum(1 for m in guild.members if m.bot)

        embed = discord.Embed(title=guild.name, color=discord.Color.blurple(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        embed.add_field(name="🆔 Server ID",      value=f"`{guild.id}`",                           inline=True)
        embed.add_field(name="👑 Owner",          value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        embed.add_field(name="🗓️ Created",        value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=True)
        embed.add_field(name="👥 Members",        value=f"{guild.member_count:,} ({bots} bots)",   inline=True)
        embed.add_field(name="💬 Channels",       value=f"{text_ch} text • {voice_ch} voice • {cats} categories", inline=True)
        embed.add_field(name="🎭 Roles",          value=str(len(guild.roles)),                     inline=True)
        embed.add_field(name="🌍 Region",         value="Automatic",                               inline=True)
        embed.add_field(name="🔒 Verification",   value=str(guild.verification_level).title(),     inline=True)
        embed.add_field(name="🚀 Boosts",         value=f"Level {guild.premium_tier} ({guild.premium_subscription_count} boosts)", inline=True)
        if guild.emojis:
            embed.add_field(name=f"😀 Emojis ({len(guild.emojis)})", value=" ".join(str(e) for e in list(guild.emojis)[:15]) or "None", inline=False)
        embed.set_footer(text="Jarvis Info • by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Get a user's avatar")
    @app_commands.describe(user="The user whose avatar to get (defaults to you)")
    async def avatar(self, interaction: discord.Interaction, user: discord.Member = None):
        user = user or interaction.user
        embed = discord.Embed(title=f"{user.display_name}'s Avatar", color=discord.Color.blurple())
        embed.set_image(url=user.display_avatar.url)
        embed.add_field(name="Links", value=f"[PNG]({user.display_avatar.with_format('png').url}) | [JPG]({user.display_avatar.with_format('jpg').url}) | [WEBP]({user.display_avatar.with_format('webp').url})")
        embed.set_footer(text="Jarvis Info • by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="roleinfo", description="View information about a role")
    @app_commands.describe(role="The role to inspect")
    async def roleinfo(self, interaction: discord.Interaction, role: discord.Role):
        members_with_role = len(role.members)
        embed = discord.Embed(title=f"Role: {role.name}", color=role.color, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="🆔 ID",         value=f"`{role.id}`",                            inline=True)
        embed.add_field(name="🎨 Color",      value=str(role.color),                            inline=True)
        embed.add_field(name="📅 Created",    value=f"<t:{int(role.created_at.timestamp())}:F>", inline=True)
        embed.add_field(name="👥 Members",    value=str(members_with_role),                     inline=True)
        embed.add_field(name="📌 Position",   value=str(role.position),                         inline=True)
        embed.add_field(name="🔔 Mentionable",value="Yes" if role.mentionable else "No",        inline=True)
        embed.add_field(name="📢 Hoisted",    value="Yes" if role.hoist else "No",              inline=True)
        embed.add_field(name="🤖 Managed",    value="Yes" if role.managed else "No",            inline=True)
        embed.set_footer(text="Jarvis Info • by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="botinfo", description="View information about Jarvis")
    async def botinfo(self, interaction: discord.Interaction):
        guilds   = len(self.bot.guilds)
        members  = sum(g.member_count for g in self.bot.guilds)
        embed = discord.Embed(title="🤖 Jarvis Bot Info", color=discord.Color.blurple(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="👑 Created by",  value="Pookie Boy",      inline=True)
        embed.add_field(name="🏠 Servers",     value=f"{guilds:,}",     inline=True)
        embed.add_field(name="👥 Users",       value=f"{members:,}",    inline=True)
        embed.add_field(name="🆔 Bot ID",      value=f"`{self.bot.user.id}`", inline=True)
        embed.add_field(name="📚 Library",     value="discord.py",      inline=True)
        embed.add_field(name="🐍 Language",    value="Python",          inline=True)
        embed.add_field(name="✨ Features",    value=(
            "🛡️ Security • ⚔️ Anti-Nuke • 🚫 Anti-Spam\n"
            "🤖 AutoMod • 🔨 Moderation • 👋 Welcome\n"
            "⭐ Leveling • 🎫 Tickets • 🎉 Giveaways\n"
            "📊 Polls • 🌟 Starboard • 💡 Suggestions\n"
            "🎮 Fun • ℹ️ Info • 🔧 Utility & More!"
        ), inline=False)
        embed.set_footer(text="Jarvis • Unbypassable Security • by Pookie Boy")
        await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
# COG 18 — Fun Commands  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

EIGHT_BALL_RESPONSES = [
    "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes, definitely!",
    "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
    "Yes!", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
    "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
    "Don't count on it.", "My reply is no.", "My sources say no.",
    "Outlook not so good.", "Very doubtful."
]

ROASTS = [
    "You're like a cloud — when you disappear, it's a beautiful day.",
    "I'd agree with you, but then we'd both be wrong.",
    "You bring everyone so much joy when you leave the room.",
    "If laughter is the best medicine, your face must be curing diseases.",
    "I'd explain it to you, but I left my crayons at home.",
    "You're proof that even evolution makes mistakes sometimes.",
    "I've met parking tickets with more appeal than you.",
    "You have your entire life to be an idiot. Why not take today off?",
    "Is your drama going to have an intermission soon? I need snacks.",
    "I'd call you a clown, but clowns at least make people smile."
]

COMPLIMENTS = [
    "You're literally the reason someone smiles today! 🌟",
    "You are more fun than bubble wrap. Seriously. 🫧",
    "You have the best taste in friends. They're clearly amazing. 😊",
    "If you were a vegetable, you'd be a cute-cumber. 🥒",
    "You light up every room you walk into — like a living lamp of joy. 💡",
    "You could make even a rainy day feel like sunshine. ☀️",
    "You are the human equivalent of a warm cup of hot chocolate. ☕",
    "Your vibe is immaculate. Keep going! 🚀",
    "You're so cool, even polar bears are jealous. 🐻‍❄️",
    "The world is genuinely a better place with you in it. 💙"
]

MEME_FORMATS = [
    "Drake pointing — when you {0} vs. when you {1}",
    "Two buttons — {0} OR {1}???",
    "This is fine dog 🔥 — me seeing {0}",
    "Distracted boyfriend — me, my responsibilities, {0}",
    "Expanding brain — Level 1: {0} | Level 100: {1}",
    "Change my mind — {0} is actually just {1}",
    "Surprised Pikachu face — when {0} leads to {1}",
]


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="8ball", description="Ask the magic 8 ball a question")
    @app_commands.describe(question="Your yes/no question")
    async def eightball(self, interaction: discord.Interaction, question: str):
        answer = random.choice(EIGHT_BALL_RESPONSES)
        embed = discord.Embed(color=discord.Color.dark_purple())
        embed.add_field(name="🎱 Question", value=question, inline=False)
        embed.add_field(name="🔮 Answer",   value=f"**{answer}**", inline=False)
        embed.set_footer(text="Jarvis Magic 8 Ball")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="coinflip", description="Flip a coin")
    async def coinflip(self, interaction: discord.Interaction):
        result = random.choice(["Heads 🪙", "Tails 🪙"])
        embed = discord.Embed(title="🪙 Coin Flip", description=f"The coin landed on... **{result}**!", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="dice", description="Roll a dice")
    @app_commands.describe(sides="Number of sides (default 6)")
    async def dice(self, interaction: discord.Interaction, sides: app_commands.Range[int, 2, 100] = 6):
        result = random.randint(1, sides)
        embed = discord.Embed(title="🎲 Dice Roll", description=f"You rolled a **{result}** on a {sides}-sided dice!", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="rps", description="Play Rock Paper Scissors against Jarvis")
    @app_commands.describe(choice="Your choice")
    @app_commands.choices(choice=[
        app_commands.Choice(name="Rock", value="rock"),
        app_commands.Choice(name="Paper", value="paper"),
        app_commands.Choice(name="Scissors", value="scissors"),
    ])
    async def rps(self, interaction: discord.Interaction, choice: str):
        emojis = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
        bot_choice = random.choice(["rock", "paper", "scissors"])
        wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

        if choice == bot_choice:
            result, color = "It's a **tie**! 🤝", discord.Color.yellow()
        elif wins[choice] == bot_choice:
            result, color = "You **win**! 🎉", discord.Color.green()
        else:
            result, color = "You **lose**! 😢", discord.Color.red()

        embed = discord.Embed(title="🎮 Rock Paper Scissors", color=color)
        embed.add_field(name="Your choice",   value=f"{emojis[choice]} {choice.title()}",    inline=True)
        embed.add_field(name="My choice",     value=f"{emojis[bot_choice]} {bot_choice.title()}", inline=True)
        embed.add_field(name="Result",        value=result, inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="roast", description="Roast a friend (all in good fun!)")
    @app_commands.describe(user="The person to roast")
    async def roast(self, interaction: discord.Interaction, user: discord.Member):
        if user.id == interaction.user.id:
            roast = "You're roasting yourself? Bold move. The roast is: you're brave enough to roast yourself. 👏"
        elif user.id == self.bot.user.id:
            roast = "Nice try, but I'm fireproof. 🔥"
        else:
            roast = random.choice(ROASTS)
        embed = discord.Embed(title=f"🔥 Roast — {user.display_name}", description=roast, color=discord.Color.orange())
        embed.set_footer(text="All in good fun! • Jarvis by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="compliment", description="Compliment someone!")
    @app_commands.describe(user="The person to compliment")
    async def compliment(self, interaction: discord.Interaction, user: discord.Member):
        comp = random.choice(COMPLIMENTS)
        embed = discord.Embed(title=f"💙 Compliment for {user.display_name}", description=comp, color=discord.Color.blurple())
        embed.set_footer(text="Spread kindness! • Jarvis by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="meme", description="Get a random meme template or prompt")
    async def meme(self, interaction: discord.Interaction):
        template = random.choice(MEME_FORMATS)
        words = ["sleep", "work", "gaming", "studying", "snacks", "napping", "coding", "vibing", "drama", "chaos"]
        filled = template.format(random.choice(words), random.choice(words))
        embed = discord.Embed(title="😂 Meme Format", description=f"**{filled}**", color=discord.Color.yellow())
        embed.set_footer(text="Jarvis Fun • by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="choose", description="Let Jarvis choose between multiple options for you")
    @app_commands.describe(options="Comma-separated options to choose from")
    async def choose(self, interaction: discord.Interaction, options: str):
        choices = [o.strip() for o in options.split(",") if o.strip()]
        if len(choices) < 2:
            await interaction.response.send_message("❌ Please provide at least 2 options separated by commas.", ephemeral=True); return
        chosen = random.choice(choices)
        embed = discord.Embed(title="🤔 Decision Made!", description=f"Out of {len(choices)} options, I choose:\n\n**{chosen}**", color=discord.Color.green())
        embed.set_footer(text="Jarvis Fun • by Pookie Boy")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="reverse", description="Reverse any text")
    @app_commands.describe(text="The text to reverse")
    async def reverse(self, interaction: discord.Interaction, text: str):
        await interaction.response.send_message(f"🔄 **{text[::-1]}**")

    @app_commands.command(name="rate", description="Rate anything out of 10")
    @app_commands.describe(thing="What to rate")
    async def rate(self, interaction: discord.Interaction, thing: str):
        rating = random.randint(0, 10)
        stars = "⭐" * rating + "☆" * (10 - rating)
        embed = discord.Embed(title="⭐ Rating", description=f"I rate **{thing}**:\n{stars}\n**{rating}/10**", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
# COG 19 — Utility Commands  (PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="slowmode", description="Set slowmode for the current channel")
    @app_commands.describe(seconds="Slowmode delay in seconds (0 to disable, max 21600)")
    @app_commands.default_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        try:
            await interaction.channel.edit(slowmode_delay=seconds)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not set slowmode: {e}", ephemeral=True); return
        if seconds == 0:
            await interaction.response.send_message("✅ Slowmode **disabled** for this channel.", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Slowmode set to **{seconds} seconds** for this channel.", ephemeral=True)
        await log_action(interaction.guild, "Slowmode Set", interaction.user, None, f"Set slowmode to {seconds}s in {interaction.channel.mention}", discord.Color.blue())

    @app_commands.command(name="lock", description="Lock a channel so members can't send messages")
    @app_commands.describe(channel="Channel to lock (defaults to current)")
    @app_commands.default_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        try:
            await ch.set_permissions(interaction.guild.default_role, send_messages=False)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not lock channel: {e}", ephemeral=True); return
        embed = discord.Embed(title="🔒 Channel Locked", description=f"{ch.mention} has been locked. Members can no longer send messages here.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "Channel Locked", interaction.user, None, f"Locked {ch.mention}", discord.Color.red())

    @app_commands.command(name="unlock", description="Unlock a channel so members can send messages again")
    @app_commands.describe(channel="Channel to unlock (defaults to current)")
    @app_commands.default_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        try:
            await ch.set_permissions(interaction.guild.default_role, send_messages=None)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not unlock channel: {e}", ephemeral=True); return
        embed = discord.Embed(title="🔓 Channel Unlocked", description=f"{ch.mention} has been unlocked. Members can send messages again.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "Channel Unlocked", interaction.user, None, f"Unlocked {ch.mention}", discord.Color.green())

    @app_commands.command(name="nick", description="Change a member's nickname")
    @app_commands.describe(member="The member to rename", nickname="New nickname (leave blank to reset)")
    @app_commands.default_permissions(manage_nicknames=True)
    async def nick(self, interaction: discord.Interaction, member: discord.Member, nickname: str = None):
        old_nick = member.display_name
        try:
            await member.edit(nick=nickname, reason=f"Nickname changed by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to change this member's nickname.", ephemeral=True); return
        if nickname:
            await interaction.response.send_message(f"✅ Changed **{old_nick}**'s nickname to **{nickname}**.", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Reset **{old_nick}**'s nickname.", ephemeral=True)
        await log_action(interaction.guild, "Nickname Changed", interaction.user, member, f"Renamed {old_nick} → {nickname or 'reset'}", discord.Color.blue())

    @app_commands.command(name="dehoist", description="Remove hoisting characters (!, #, etc.) from all nicknames")
    @app_commands.default_permissions(manage_nicknames=True)
    async def dehoist(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        hoisting_chars = "!\"#$%&'()*+,-./0123456789:;<=>?@"
        count = 0
        for member in interaction.guild.members:
            if member.bot or member == interaction.guild.me:
                continue
            name = member.display_name
            if name and name[0] in hoisting_chars:
                new_name = name.lstrip(hoisting_chars).strip() or f"Member {member.discriminator}"
                try:
                    await member.edit(nick=new_name, reason="Jarvis Dehoist")
                    count += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await interaction.followup.send(f"✅ Dehoisted **{count}** member(s).", ephemeral=True)
        await log_action(interaction.guild, "Dehoist", interaction.user, None, f"Dehoisted {count} members", discord.Color.blue())

    @app_commands.command(name="say", description="Make Jarvis say something (in an embed)")
    @app_commands.describe(message="What to say", channel="Channel to send in (defaults to current)")
    @app_commands.default_permissions(manage_messages=True)
    async def say(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        embed = discord.Embed(description=message, color=discord.Color.blurple())
        try:
            await ch.send(embed=embed)
            await interaction.response.send_message(f"✅ Message sent to {ch.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to send messages in that channel.", ephemeral=True)

    @app_commands.command(name="embed", description="Create a custom embed message")
    @app_commands.describe(title="Embed title", description="Embed description", color="Hex color (e.g. FF0000)", channel="Channel to send in")
    @app_commands.default_permissions(manage_messages=True)
    async def embed_cmd(self, interaction: discord.Interaction, title: str, description: str, color: str = "5865F2", channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        try:
            color_int = int(color.lstrip("#"), 16)
        except ValueError:
            color_int = 0x5865F2
        embed = discord.Embed(title=title, description=description, color=color_int, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_footer(text=f"Posted by {interaction.user.display_name}")
        try:
            await ch.send(embed=embed)
            await interaction.response.send_message(f"✅ Embed sent to {ch.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to send messages in that channel.", ephemeral=True)

    @app_commands.command(name="members", description="Show current member count")
    async def members(self, interaction: discord.Interaction):
        guild = interaction.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots   = sum(1 for m in guild.members if m.bot)
        online = sum(1 for m in guild.members if m.status != discord.Status.offline)
        embed = discord.Embed(title=f"👥 {guild.name} — Members", color=discord.Color.blurple())
        embed.add_field(name="Total",   value=f"**{guild.member_count:,}**", inline=True)
        embed.add_field(name="Humans",  value=f"**{humans:,}**",             inline=True)
        embed.add_field(name="Bots",    value=f"**{bots:,}**",               inline=True)
        embed.add_field(name="Online",  value=f"**{online:,}**",             inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="firstmessage", description="Get a link to the first message in a channel")
    @app_commands.describe(channel="Channel to check (defaults to current)")
    async def firstmessage(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        ch = channel or interaction.channel
        await interaction.response.defer()
        try:
            first = await ch.history(limit=1, oldest_first=True).__anext__()
            embed = discord.Embed(title="📜 First Message", description=f"[Jump to first message in {ch.mention}]({first.jump_url})", color=discord.Color.blurple())
            embed.add_field(name="Author",  value=first.author.mention,          inline=True)
            embed.add_field(name="Sent",    value=f"<t:{int(first.created_at.timestamp())}:F>", inline=True)
            embed.add_field(name="Content", value=first.content[:500] or "[embed/attachment]", inline=False)
            await interaction.followup.send(embed=embed)
        except StopAsyncIteration:
            await interaction.followup.send("❌ No messages found in that channel.")


# ══════════════════════════════════════════════════════════════════════════════
# COG 20 — Join to Create  (ASTRO JTC — PREMIUM FEATURE)
# ══════════════════════════════════════════════════════════════════════════════

_JTC_FILE = os.path.join(_DATA_DIR, 'jtc.json')


# ── JTC Control Panel — persistent button UI ─────────────────────────────────

class _LimitModal(discord.ui.Modal, title="Set User Limit"):
    limit_input = discord.ui.TextInput(
        label="User Limit (0 = unlimited, max 99)",
        placeholder="Enter a number e.g. 5",
        min_length=1,
        max_length=2,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.limit_input.value.strip())
            if value < 0 or value > 99:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Enter a number between 0 and 99.", ephemeral=True)
            return
        ch = interaction.user.voice.channel if interaction.user.voice else None
        if ch is None:
            await interaction.response.send_message("❌ You must be in a voice channel.", ephemeral=True)
            return
        jtc_cog = interaction.client.cogs.get("JoinToCreate")
        info = jtc_cog._temp.get(ch.id) if jtc_cog else None
        if not info or info["owner_id"] != interaction.user.id:
            await interaction.response.send_message("❌ You are not the owner of this channel.", ephemeral=True)
            return
        await ch.edit(user_limit=value, reason=f"JTC panel: limit set by {interaction.user}")
        label = f"**{value}**" if value else "**unlimited**"
        await interaction.response.send_message(f"✅ User limit set to {label}.", ephemeral=True)
        # Refresh the panel embed
        if jtc_cog:
            await jtc_cog._refresh_panel(ch, info)


class _RenameModal(discord.ui.Modal, title="Rename Your Channel"):
    name_input = discord.ui.TextInput(
        label="New Channel Name",
        placeholder="e.g. 🎮 Gaming Session",
        min_length=1,
        max_length=100,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        ch = interaction.user.voice.channel if interaction.user.voice else None
        if ch is None:
            await interaction.response.send_message("❌ You must be in a voice channel.", ephemeral=True)
            return
        jtc_cog = interaction.client.cogs.get("JoinToCreate")
        info = jtc_cog._temp.get(ch.id) if jtc_cog else None
        if not info or info["owner_id"] != interaction.user.id:
            await interaction.response.send_message("❌ You are not the owner of this channel.", ephemeral=True)
            return
        await ch.edit(name=name, reason=f"JTC panel: renamed by {interaction.user}")
        # Save as user default
        cfg = jtc_cog._guild_cfg(interaction.guild.id)
        cfg.setdefault("user_defaults", {}).setdefault(str(interaction.user.id), {})["name"] = name
        jtc_cog._save()
        await interaction.response.send_message(f"✅ Channel renamed to **{name}**.", ephemeral=True)
        if jtc_cog:
            await jtc_cog._refresh_panel(ch, info)


class VCControlPanel(discord.ui.View):
    """Persistent button panel posted inside every JTC temp voice channel."""

    def __init__(self):
        super().__init__(timeout=None)

    def _get_cog(self, interaction: discord.Interaction):
        return interaction.client.cogs.get("JoinToCreate")

    def _get_channel_and_info(self, interaction: discord.Interaction):
        ch = interaction.user.voice.channel if interaction.user.voice else None
        if ch is None:
            return None, None
        cog = self._get_cog(interaction)
        info = cog._temp.get(ch.id) if cog else None
        return ch, info

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        ch, info = self._get_channel_and_info(interaction)
        return info is not None and info["owner_id"] == interaction.user.id

    # ── Row 0 ────────────────────────────────────────────────────────────────

    @discord.ui.button(label="🔒 Lock", style=discord.ButtonStyle.danger, custom_id="jtc_panel_lock", row=0)
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Only the channel owner can do this.", ephemeral=True); return
        ch, info = self._get_channel_and_info(interaction)
        info["locked"] = True
        await ch.set_permissions(interaction.guild.default_role, connect=False)
        cog = self._get_cog(interaction)
        await interaction.response.send_message("🔒 Channel locked. Only permitted users can join.", ephemeral=True)
        if cog:
            await cog._refresh_panel(ch, info)

    @discord.ui.button(label="🔓 Unlock", style=discord.ButtonStyle.success, custom_id="jtc_panel_unlock", row=0)
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Only the channel owner can do this.", ephemeral=True); return
        ch, info = self._get_channel_and_info(interaction)
        info["locked"] = False
        await ch.set_permissions(interaction.guild.default_role, connect=True, view_channel=None if not info.get("hidden") else False)
        cog = self._get_cog(interaction)
        await interaction.response.send_message("🔓 Channel unlocked.", ephemeral=True)
        if cog:
            await cog._refresh_panel(ch, info)

    @discord.ui.button(label="👻 Ghost", style=discord.ButtonStyle.secondary, custom_id="jtc_panel_ghost", row=0)
    async def ghost_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Only the channel owner can do this.", ephemeral=True); return
        ch, info = self._get_channel_and_info(interaction)
        info["hidden"] = True
        await ch.set_permissions(interaction.guild.default_role, view_channel=False)
        for member in ch.members:
            await ch.set_permissions(member, view_channel=True, connect=True)
        cog = self._get_cog(interaction)
        await interaction.response.send_message("👻 Channel is now ghosted — invisible to outsiders.", ephemeral=True)
        if cog:
            await cog._refresh_panel(ch, info)

    @discord.ui.button(label="👁️ Show", style=discord.ButtonStyle.secondary, custom_id="jtc_panel_show", row=0)
    async def show_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Only the channel owner can do this.", ephemeral=True); return
        ch, info = self._get_channel_and_info(interaction)
        info["hidden"] = False
        connect_perm = False if info.get("locked") else True
        await ch.set_permissions(interaction.guild.default_role, view_channel=True, connect=connect_perm)
        cog = self._get_cog(interaction)
        await interaction.response.send_message("👁️ Channel is now visible to everyone.", ephemeral=True)
        if cog:
            await cog._refresh_panel(ch, info)

    @discord.ui.button(label="✏️ Rename", style=discord.ButtonStyle.primary, custom_id="jtc_panel_rename", row=0)
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Only the channel owner can do this.", ephemeral=True); return
        await interaction.response.send_modal(_RenameModal())

    # ── Row 1 ────────────────────────────────────────────────────────────────

    @discord.ui.button(label="🔢 Set Limit", style=discord.ButtonStyle.secondary, custom_id="jtc_panel_limit", row=1)
    async def limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Only the channel owner can do this.", ephemeral=True); return
        await interaction.response.send_modal(_LimitModal())

    @discord.ui.button(label="👑 Claim", style=discord.ButtonStyle.primary, custom_id="jtc_panel_claim", row=1)
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = self._get_cog(interaction)
        ch, info = self._get_channel_and_info(interaction)
        if ch is None or info is None:
            await interaction.response.send_message("❌ Join the voice channel first.", ephemeral=True); return
        if info["owner_id"] == interaction.user.id:
            await interaction.response.send_message("❌ You already own this channel.", ephemeral=True); return
        current_owner = interaction.guild.get_member(info["owner_id"])
        if current_owner and current_owner.voice and current_owner.voice.channel and current_owner.voice.channel.id == ch.id:
            await interaction.response.send_message("❌ The owner is still in the channel.", ephemeral=True); return
        old_id = info["owner_id"]
        info["owner_id"] = interaction.user.id
        try:
            await ch.set_permissions(interaction.user, connect=True, view_channel=True, manage_channels=True, move_members=True, mute_members=True, deafen_members=True)
            old_member = interaction.guild.get_member(old_id)
            if old_member:
                await ch.set_permissions(old_member, overwrite=None)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message("👑 You are now the channel owner.", ephemeral=True)
        if cog:
            await cog._refresh_panel(ch, info)

    @discord.ui.button(label="ℹ️ Info", style=discord.ButtonStyle.secondary, custom_id="jtc_panel_info", row=1)
    async def info_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = self._get_cog(interaction)
        ch, info = self._get_channel_and_info(interaction)
        if ch is None or info is None:
            await interaction.response.send_message("❌ Join the voice channel first.", ephemeral=True); return
        owner = interaction.guild.get_member(info["owner_id"])
        members_list = ", ".join(m.display_name for m in ch.members) or "Empty"
        embed = discord.Embed(title=f"🔊 {ch.name}", color=discord.Color.blurple(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="👑 Owner",    value=owner.mention if owner else f"<@{info['owner_id']}>", inline=True)
        embed.add_field(name="👥 Members",  value=f"{len(ch.members)} / {ch.user_limit or '∞'}",        inline=True)
        embed.add_field(name="🎙️ Bitrate", value=f"{ch.bitrate // 1000} kbps",                         inline=True)
        embed.add_field(name="🔒 Locked",   value="Yes" if info.get("locked") else "No",                inline=True)
        embed.add_field(name="👻 Ghosted",  value="Yes" if info.get("hidden") else "No",                inline=True)
        embed.add_field(name="🌍 Region",   value=str(ch.rtc_region or "Automatic").title(),            inline=True)
        embed.add_field(name="👤 In VC",    value=members_list,                                         inline=False)
        embed.set_footer(text="Jarvis JTC • by Pookie Boy")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.danger, custom_id="jtc_panel_delete", row=1)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Only the channel owner can delete this channel.", ephemeral=True); return
        ch, info = self._get_channel_and_info(interaction)
        await interaction.response.send_message("🗑️ Deleting channel...", ephemeral=True)
        cog = self._get_cog(interaction)
        if cog:
            cog._temp.pop(ch.id, None)
        try:
            await ch.delete(reason=f"JTC: manually deleted by owner {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            pass

# Voice regions available in Discord
VOICE_REGIONS = [
    app_commands.Choice(name="Automatic",       value=""),
    app_commands.Choice(name="Brazil",          value="brazil"),
    app_commands.Choice(name="Europe",          value="europe"),
    app_commands.Choice(name="Hong Kong",       value="hongkong"),
    app_commands.Choice(name="India",           value="india"),
    app_commands.Choice(name="Japan",           value="japan"),
    app_commands.Choice(name="Rotterdam",       value="rotterdam"),
    app_commands.Choice(name="Russia",          value="russia"),
    app_commands.Choice(name="Singapore",       value="singapore"),
    app_commands.Choice(name="South Africa",    value="southafrica"),
    app_commands.Choice(name="Sydney",          value="sydney"),
    app_commands.Choice(name="US Central",      value="us-central"),
    app_commands.Choice(name="US East",         value="us-east"),
    app_commands.Choice(name="US South",        value="us-south"),
    app_commands.Choice(name="US West",         value="us-west"),
]


class JoinToCreate(commands.Cog):
    """
    Full Astro Join-to-Create feature set.

    How it works:
    - Admin runs /jtc setup to designate a "trigger" voice channel.
    - When any member joins that trigger channel, Jarvis instantly creates a
      private temp voice channel for them and moves them into it.
    - The creator is the "owner" and can control it with /vc subcommands.
    - When the channel becomes empty it is automatically deleted.
    - Owners can transfer ownership, ghost/unghost, set limits, kick users, etc.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config: dict = _load_json(_JTC_FILE)
        # channel_id -> {owner_id, guild_id, permitted: set, rejected: set, locked, hidden, panel_msg_id}
        self._temp: dict[int, dict] = {}

    async def cog_load(self):
        self.bot.add_view(VCControlPanel())

    def _save(self):
        _save_json(_JTC_FILE, self.config)

    # ── Panel helpers ────────────────────────────────────────────────────────

    def _build_panel_embed(self, ch: discord.VoiceChannel, info: dict, guild: discord.Guild) -> discord.Embed:
        owner = guild.get_member(info["owner_id"])
        locked = info.get("locked", False)
        hidden = info.get("hidden", False)
        members_in = ", ".join(m.display_name for m in ch.members) or "—"

        embed = discord.Embed(
            title=f"🔊 {ch.name}  —  Control Panel",
            description=(
                "Use the buttons below to manage your voice channel.\n"
                "Only the **channel owner** can use Lock, Ghost, Rename, Limit and Delete.\n"
                "Anyone in the channel can use **Claim** or **Info**."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="👑 Owner",    value=owner.mention if owner else f"<@{info['owner_id']}>", inline=True)
        embed.add_field(name="👥 In VC",    value=f"{len(ch.members)} / {ch.user_limit or '∞'}",        inline=True)
        embed.add_field(name="🎙️ Bitrate", value=f"{ch.bitrate // 1000} kbps",                         inline=True)
        embed.add_field(name="🔒 Locked",   value="🔴 Yes" if locked else "🟢 No",                      inline=True)
        embed.add_field(name="👻 Ghosted",  value="🔴 Yes" if hidden else "🟢 No",                      inline=True)
        embed.add_field(name="🌍 Region",   value=str(ch.rtc_region or "Automatic").title(),            inline=True)
        embed.add_field(name="👤 Members",  value=members_in,                                           inline=False)
        embed.set_footer(text="Jarvis JTC • by Pookie Boy  |  Panel auto-refreshes on changes")
        return embed

    async def _refresh_panel(self, ch: discord.VoiceChannel, info: dict):
        """Edit the existing panel message with updated embed."""
        msg_id = info.get("panel_msg_id")
        if not msg_id:
            return
        try:
            msg = await ch.fetch_message(msg_id)
            guild = ch.guild
            await msg.edit(embed=self._build_panel_embed(ch, info, guild))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            info.pop("panel_msg_id", None)

    def _guild_cfg(self, guild_id: int) -> dict:
        gid = str(guild_id)
        if gid not in self.config:
            self.config[gid] = {
                "trigger_channel_id": None,
                "category_id": None,
                "template": "{user}'s Channel",
                "default_limit": 0,
                "default_bitrate": 64,
                "user_defaults": {},
            }
        return self.config[gid]

    def _is_owner(self, channel_id: int, user_id: int) -> bool:
        info = self._temp.get(channel_id)
        return info is not None and info["owner_id"] == user_id

    def _get_temp_info(self, channel_id: int) -> dict | None:
        return self._temp.get(channel_id)

    # ── Voice state listener ─────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        guild = member.guild
        cfg   = self._guild_cfg(guild.id)
        trigger_id = cfg.get("trigger_channel_id")

        # ── Someone joined the trigger channel ─────────────────────────────
        if after.channel and after.channel.id == trigger_id:
            await self._create_temp_channel(member, after.channel, cfg)

        # ── Someone left a temp channel ────────────────────────────────────
        if before.channel and before.channel.id in self._temp:
            ch_id  = before.channel.id
            info   = self._temp[ch_id]
            ch     = guild.get_channel(ch_id)
            if ch is None:
                self._temp.pop(ch_id, None)
                return

            # Auto-delete when empty
            if len(ch.members) == 0:
                self._temp.pop(ch_id, None)
                try:
                    await ch.delete(reason="Jarvis JTC: temp channel empty — auto-deleted")
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return

            # Auto-transfer ownership if owner left
            if before.channel and info["owner_id"] == member.id:
                remaining = [m for m in ch.members if not m.bot]
                if remaining:
                    new_owner = remaining[0]
                    info["owner_id"] = new_owner.id
                    try:
                        await ch.edit(name=f"{new_owner.display_name}'s Channel")
                        await ch.send(
                            embed=discord.Embed(
                                title="👑 Ownership Transferred",
                                description=f"The previous owner left. {new_owner.mention} is now the owner of this channel.",
                                color=discord.Color.gold(),
                            ),
                            delete_after=30,
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass

    async def _create_temp_channel(
        self,
        member: discord.Member,
        trigger: discord.VoiceChannel,
        cfg: dict,
    ):
        guild    = member.guild
        template = cfg.get("template", "{user}'s Channel")
        name     = template.replace("{user}", member.display_name).replace("{username}", str(member)).replace("{server}", guild.name)[:100]

        cat_id   = cfg.get("category_id")
        category = guild.get_channel(cat_id) if cat_id else trigger.category

        # Per-user saved defaults
        user_defaults = cfg.get("user_defaults", {}).get(str(member.id), {})
        limit   = user_defaults.get("limit",   cfg.get("default_limit",   0))
        bitrate = user_defaults.get("bitrate", cfg.get("default_bitrate", 64)) * 1000
        custom_name = user_defaults.get("name")
        if custom_name:
            name = custom_name[:100]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
            member:             discord.PermissionOverwrite(
                connect=True, view_channel=True, manage_channels=True,
                move_members=True, mute_members=True, deafen_members=True,
            ),
            guild.me:           discord.PermissionOverwrite(
                connect=True, view_channel=True, manage_channels=True, move_members=True,
            ),
        }

        try:
            new_ch = await guild.create_voice_channel(
                name=name,
                category=category,
                overwrites=overwrites,
                user_limit=limit,
                bitrate=min(bitrate, guild.bitrate_limit),
                reason=f"Jarvis JTC: created for {member}",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"JTC: could not create voice channel for {member} in {guild.name}: {e}")
            return

        info = {
            "owner_id":    member.id,
            "guild_id":    guild.id,
            "permitted":   set(),
            "rejected":    set(),
            "locked":      False,
            "hidden":      False,
            "panel_msg_id": None,
        }
        self._temp[new_ch.id] = info

        try:
            await member.move_to(new_ch, reason="Jarvis JTC: moving to temp channel")
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Post the control panel embed inside the new voice channel
        try:
            panel_embed = self._build_panel_embed(new_ch, info, guild)
            panel_msg   = await new_ch.send(embed=panel_embed, view=VCControlPanel())
            info["panel_msg_id"] = panel_msg.id
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── /jtc admin group ──────────────────────────────────────────────────────

    jtc_group = app_commands.Group(
        name="jtc",
        description="Configure the Join-to-Create system",
        default_permissions=discord.Permissions(administrator=True),
    )

    @jtc_group.command(name="setup", description="Set up the Join-to-Create trigger channel")
    @app_commands.describe(
        channel="The voice channel members join to get their own channel",
        category="Category where temp channels will be created",
        template="Name template — use {user} for member name (default: {user}'s Channel)",
    )
    async def jtc_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
        category: discord.CategoryChannel = None,
        template: str = "{user}'s Channel",
    ):
        cfg = self._guild_cfg(interaction.guild.id)
        cfg["trigger_channel_id"] = channel.id
        cfg["category_id"]        = category.id if category else None
        cfg["template"]           = template
        self._save()

        embed = discord.Embed(
            title="✅ Join-to-Create Setup Complete",
            color=discord.Color.green(),
        )
        embed.add_field(name="🔊 Trigger Channel", value=channel.mention,                                      inline=True)
        embed.add_field(name="📂 Category",        value=category.mention if category else "Same as trigger",  inline=True)
        embed.add_field(name="📝 Template",        value=f"`{template}`",                                      inline=True)
        embed.add_field(
            name="How it works",
            value=(
                "Members who join **" + channel.name + "** will instantly get their own "
                "private voice channel.\n"
                "They can control it with `/vc` commands.\n"
                "The channel auto-deletes when everyone leaves."
            ),
            inline=False,
        )
        embed.set_footer(text="Jarvis JTC • by Pookie Boy")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @jtc_group.command(name="settemplate", description="Change the default channel name template")
    @app_commands.describe(template="Use {user}, {username}, {server} as placeholders")
    async def jtc_settemplate(self, interaction: discord.Interaction, template: str):
        cfg = self._guild_cfg(interaction.guild.id)
        cfg["template"] = template
        self._save()
        await interaction.response.send_message(f"✅ Template set to `{template}`.", ephemeral=True)

    @jtc_group.command(name="setlimit", description="Set the default user limit for new temp channels (0 = unlimited)")
    @app_commands.describe(limit="Max users (0–99, 0 = unlimited)")
    async def jtc_setlimit(self, interaction: discord.Interaction, limit: app_commands.Range[int, 0, 99]):
        cfg = self._guild_cfg(interaction.guild.id)
        cfg["default_limit"] = limit
        self._save()
        await interaction.response.send_message(f"✅ Default user limit set to **{limit if limit else 'unlimited'}**.", ephemeral=True)

    @jtc_group.command(name="setbitrate", description="Set the default bitrate for new temp channels (kbps)")
    @app_commands.describe(bitrate="Bitrate in kbps (8–384)")
    async def jtc_setbitrate(self, interaction: discord.Interaction, bitrate: app_commands.Range[int, 8, 384]):
        cfg = self._guild_cfg(interaction.guild.id)
        cfg["default_bitrate"] = bitrate
        self._save()
        await interaction.response.send_message(f"✅ Default bitrate set to **{bitrate} kbps**.", ephemeral=True)

    @jtc_group.command(name="disable", description="Disable the Join-to-Create system")
    async def jtc_disable(self, interaction: discord.Interaction):
        cfg = self._guild_cfg(interaction.guild.id)
        cfg["trigger_channel_id"] = None
        self._save()
        await interaction.response.send_message("✅ Join-to-Create disabled.", ephemeral=True)

    @jtc_group.command(name="status", description="View current Join-to-Create configuration")
    async def jtc_status(self, interaction: discord.Interaction):
        cfg       = self._guild_cfg(interaction.guild.id)
        trig_id   = cfg.get("trigger_channel_id")
        cat_id    = cfg.get("category_id")
        trig_ch   = interaction.guild.get_channel(trig_id)
        cat_ch    = interaction.guild.get_channel(cat_id)
        active    = sum(1 for info in self._temp.values() if info["guild_id"] == interaction.guild.id)

        embed = discord.Embed(title="🔊 Join-to-Create Status", color=discord.Color.blurple())
        embed.add_field(name="Trigger Channel", value=trig_ch.mention if trig_ch else "❌ Not set",             inline=True)
        embed.add_field(name="Category",        value=cat_ch.mention if cat_ch else "Same as trigger",          inline=True)
        tmpl = cfg.get("template", "{user}'s Channel")
        embed.add_field(name="Template",        value=f"`{tmpl}`",                                                inline=True)
        embed.add_field(name="Default Limit",   value=str(cfg.get("default_limit", 0) or "Unlimited"),         inline=True)
        embed.add_field(name="Default Bitrate", value=f"{cfg.get('default_bitrate', 64)} kbps",                inline=True)
        embed.add_field(name="Active Channels", value=str(active),                                              inline=True)
        embed.set_footer(text="Jarvis JTC • by Pookie Boy")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /vc user control group ────────────────────────────────────────────────

    vc_group = app_commands.Group(
        name="vc",
        description="Control your Join-to-Create voice channel",
    )

    def _resolve_user_channel(self, interaction: discord.Interaction) -> tuple[discord.VoiceChannel | None, dict | None, str | None]:
        """Returns (channel, temp_info, error_message)."""
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            return None, None, "❌ You must be in a voice channel to use this command."
        ch = member.voice.channel
        info = self._temp.get(ch.id)
        if info is None:
            return None, None, "❌ This channel is not a Jarvis Join-to-Create channel."
        if info["owner_id"] != member.id:
            return None, None, "❌ You are not the owner of this channel. Use `/vc claim` if the owner has left."
        return ch, info, None

    @vc_group.command(name="name", description="Rename your voice channel")
    @app_commands.describe(name="New channel name (max 100 characters)")
    async def vc_name(self, interaction: discord.Interaction, name: str):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        name = name[:100]
        try:
            await ch.edit(name=name, reason=f"JTC: renamed by owner {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not rename: {e}", ephemeral=True); return
        # Save as user default
        cfg = self._guild_cfg(interaction.guild.id)
        cfg.setdefault("user_defaults", {}).setdefault(str(interaction.user.id), {})["name"] = name
        self._save()
        await interaction.response.send_message(f"✅ Channel renamed to **{name}**.", ephemeral=True)

    @vc_group.command(name="limit", description="Set the user limit for your channel (0 = unlimited)")
    @app_commands.describe(limit="Max users (0–99, 0 = unlimited)")
    async def vc_limit(self, interaction: discord.Interaction, limit: app_commands.Range[int, 0, 99]):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        try:
            await ch.edit(user_limit=limit, reason=f"JTC: limit changed by owner {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not set limit: {e}", ephemeral=True); return
        # Save as user default
        cfg = self._guild_cfg(interaction.guild.id)
        cfg.setdefault("user_defaults", {}).setdefault(str(interaction.user.id), {})["limit"] = limit
        self._save()
        label = f"**{limit}**" if limit else "**unlimited**"
        await interaction.response.send_message(f"✅ User limit set to {label}.", ephemeral=True)

    @vc_group.command(name="lock", description="Lock your channel — only permitted users can join")
    async def vc_lock(self, interaction: discord.Interaction):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        info["locked"] = True
        try:
            await ch.set_permissions(interaction.guild.default_role, connect=False)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not lock: {e}", ephemeral=True); return
        embed = discord.Embed(title="🔒 Channel Locked", description=f"{interaction.user.mention} locked **{ch.name}**.\nOnly permitted users can now join.", color=discord.Color.red())
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message("🔒 Channel locked.", ephemeral=True)

    @vc_group.command(name="unlock", description="Unlock your channel — everyone can join again")
    async def vc_unlock(self, interaction: discord.Interaction):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        info["locked"] = False
        try:
            await ch.set_permissions(interaction.guild.default_role, connect=True, view_channel=None if not info.get("hidden") else False)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not unlock: {e}", ephemeral=True); return
        embed = discord.Embed(title="🔓 Channel Unlocked", description=f"{interaction.user.mention} unlocked **{ch.name}**.\nEveryone can now join.", color=discord.Color.green())
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message("🔓 Channel unlocked.", ephemeral=True)

    @vc_group.command(name="hide", description="Hide your channel from everyone except current members")
    async def vc_hide(self, interaction: discord.Interaction):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        info["hidden"] = True
        try:
            await ch.set_permissions(interaction.guild.default_role, view_channel=False)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not hide: {e}", ephemeral=True); return
        await interaction.response.send_message("👻 Channel is now hidden from everyone.", ephemeral=True)

    @vc_group.command(name="show", description="Make your hidden channel visible again")
    async def vc_show(self, interaction: discord.Interaction):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        info["hidden"] = False
        connect_perm = False if info.get("locked") else True
        try:
            await ch.set_permissions(interaction.guild.default_role, view_channel=True, connect=connect_perm)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not show: {e}", ephemeral=True); return
        await interaction.response.send_message("👁️ Channel is now visible.", ephemeral=True)

    @vc_group.command(name="permit", description="Allow a specific user to join your locked channel")
    @app_commands.describe(user="The user to allow in")
    async def vc_permit(self, interaction: discord.Interaction, user: discord.Member):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't permit yourself.", ephemeral=True); return
        info["permitted"].add(user.id)
        info["rejected"].discard(user.id)
        try:
            await ch.set_permissions(user, connect=True, view_channel=True)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not permit: {e}", ephemeral=True); return
        try:
            await ch.send(embed=discord.Embed(description=f"✅ {user.mention} has been **permitted** to join this channel.", color=discord.Color.green()), delete_after=10)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message(f"✅ {user.mention} can now join your channel.", ephemeral=True)

    @vc_group.command(name="reject", description="Deny a user from joining your channel and kick them if inside")
    @app_commands.describe(user="The user to deny/remove")
    async def vc_reject(self, interaction: discord.Interaction, user: discord.Member):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't reject yourself.", ephemeral=True); return
        info["rejected"].add(user.id)
        info["permitted"].discard(user.id)
        try:
            await ch.set_permissions(user, connect=False, view_channel=False)
        except (discord.Forbidden, discord.HTTPException):
            pass
        if user.voice and user.voice.channel and user.voice.channel.id == ch.id:
            try:
                await user.move_to(None, reason=f"JTC: rejected by channel owner {interaction.user}")
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.response.send_message(f"✅ {user.mention} has been rejected from your channel.", ephemeral=True)

    @vc_group.command(name="kick", description="Kick a user out of your voice channel")
    @app_commands.describe(user="The user to kick from your channel")
    async def vc_kick(self, interaction: discord.Interaction, user: discord.Member):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't kick yourself.", ephemeral=True); return
        if not user.voice or user.voice.channel is None or user.voice.channel.id != ch.id:
            await interaction.response.send_message("❌ That user is not in your channel.", ephemeral=True); return
        try:
            await user.move_to(None, reason=f"JTC: kicked by channel owner {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not kick: {e}", ephemeral=True); return
        await interaction.response.send_message(f"✅ Kicked **{user.display_name}** from your channel.", ephemeral=True)

    @vc_group.command(name="claim", description="Claim ownership of a temp channel whose owner has left")
    async def vc_claim(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message("❌ You must be in a voice channel to claim.", ephemeral=True); return
        ch   = member.voice.channel
        info = self._temp.get(ch.id)
        if info is None:
            await interaction.response.send_message("❌ This is not a Jarvis JTC channel.", ephemeral=True); return
        if info["owner_id"] == member.id:
            await interaction.response.send_message("❌ You already own this channel.", ephemeral=True); return
        current_owner = interaction.guild.get_member(info["owner_id"])
        if current_owner and current_owner.voice and current_owner.voice.channel and current_owner.voice.channel.id == ch.id:
            await interaction.response.send_message("❌ The current owner is still in the channel. You can only claim an abandoned channel.", ephemeral=True); return
        old_owner_id = info["owner_id"]
        info["owner_id"] = member.id
        try:
            await ch.set_permissions(member, connect=True, view_channel=True, manage_channels=True, move_members=True, mute_members=True, deafen_members=True)
            old_owner = interaction.guild.get_member(old_owner_id)
            if old_owner:
                await ch.set_permissions(old_owner, overwrite=None)
        except (discord.Forbidden, discord.HTTPException):
            pass
        embed = discord.Embed(title="👑 Channel Claimed", description=f"{member.mention} has claimed ownership of **{ch.name}**.", color=discord.Color.gold())
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message("✅ You are now the owner of this channel.", ephemeral=True)

    @vc_group.command(name="transfer", description="Transfer channel ownership to another member in your VC")
    @app_commands.describe(user="The user to transfer ownership to")
    async def vc_transfer(self, interaction: discord.Interaction, user: discord.Member):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ You can't transfer to yourself.", ephemeral=True); return
        if user.bot:
            await interaction.response.send_message("❌ You can't transfer to a bot.", ephemeral=True); return
        if not user.voice or user.voice.channel is None or user.voice.channel.id != ch.id:
            await interaction.response.send_message("❌ That user must be in your channel to receive ownership.", ephemeral=True); return
        old_owner = interaction.user
        info["owner_id"] = user.id
        try:
            await ch.set_permissions(user, connect=True, view_channel=True, manage_channels=True, move_members=True, mute_members=True, deafen_members=True)
            await ch.set_permissions(old_owner, connect=True, view_channel=True, manage_channels=False, move_members=False)
        except (discord.Forbidden, discord.HTTPException):
            pass
        embed = discord.Embed(title="👑 Ownership Transferred", description=f"{old_owner.mention} transferred ownership to {user.mention}.", color=discord.Color.gold())
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.response.send_message(f"✅ Ownership transferred to {user.mention}.", ephemeral=True)

    @vc_group.command(name="bitrate", description="Set the bitrate of your voice channel")
    @app_commands.describe(kbps="Bitrate in kbps (8–384, server limit applies)")
    async def vc_bitrate(self, interaction: discord.Interaction, kbps: app_commands.Range[int, 8, 384]):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        actual = min(kbps * 1000, interaction.guild.bitrate_limit)
        try:
            await ch.edit(bitrate=actual, reason=f"JTC: bitrate changed by owner {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not change bitrate: {e}", ephemeral=True); return
        cfg = self._guild_cfg(interaction.guild.id)
        cfg.setdefault("user_defaults", {}).setdefault(str(interaction.user.id), {})["bitrate"] = kbps
        self._save()
        await interaction.response.send_message(f"✅ Bitrate set to **{actual // 1000} kbps**.", ephemeral=True)

    @vc_group.command(name="region", description="Set the voice region of your channel")
    @app_commands.describe(region="Voice region (leave blank for automatic)")
    @app_commands.choices(region=VOICE_REGIONS)
    async def vc_region(self, interaction: discord.Interaction, region: str):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        rtc_region = region if region else None
        try:
            await ch.edit(rtc_region=rtc_region, reason=f"JTC: region changed by owner {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not change region: {e}", ephemeral=True); return
        label = region if region else "Automatic"
        await interaction.response.send_message(f"✅ Voice region set to **{label}**.", ephemeral=True)

    @vc_group.command(name="invite", description="Send a voice channel invite link to a user")
    @app_commands.describe(user="The user to invite")
    async def vc_invite(self, interaction: discord.Interaction, user: discord.Member):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        if user.bot:
            await interaction.response.send_message("❌ You can't invite a bot.", ephemeral=True); return
        try:
            invite = await ch.create_invite(max_age=300, max_uses=1, reason=f"JTC: invite by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not create invite: {e}", ephemeral=True); return
        # Also permit them
        info["permitted"].add(user.id)
        try:
            await ch.set_permissions(user, connect=True, view_channel=True)
        except (discord.Forbidden, discord.HTTPException):
            pass
        try:
            await user.send(embed=discord.Embed(
                title="📨 Voice Channel Invite",
                description=f"{interaction.user.mention} invited you to join **{ch.name}** in **{interaction.guild.name}**!\n\n[Click here to join]({invite.url})",
                color=discord.Color.blurple(),
            ))
            await interaction.response.send_message(f"✅ Invite sent to {user.mention}. (Expires in 5 minutes, single use.)", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"✅ {user.mention} has been permitted, but their DMs are closed so the link couldn't be sent.\nInvite link: {invite.url}", ephemeral=True)

    @vc_group.command(name="ghost", description="Ghost your channel — hide it from non-members (keeps current members able to see)")
    async def vc_ghost(self, interaction: discord.Interaction):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        info["hidden"] = True
        try:
            await ch.set_permissions(interaction.guild.default_role, view_channel=False)
            for member in ch.members:
                await ch.set_permissions(member, view_channel=True, connect=True)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not ghost: {e}", ephemeral=True); return
        await interaction.response.send_message("👻 Channel is now ghosted — invisible to everyone outside.", ephemeral=True)

    @vc_group.command(name="unghost", description="Make your ghosted channel visible to everyone again")
    async def vc_unghost(self, interaction: discord.Interaction):
        ch, info, err = self._resolve_user_channel(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True); return
        info["hidden"] = False
        connect_perm = False if info.get("locked") else True
        try:
            await ch.set_permissions(interaction.guild.default_role, view_channel=True, connect=connect_perm)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not unghost: {e}", ephemeral=True); return
        await interaction.response.send_message("👁️ Channel is visible again.", ephemeral=True)

    @vc_group.command(name="info", description="Show information about your current voice channel")
    async def vc_info(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message("❌ You must be in a voice channel.", ephemeral=True); return
        ch   = member.voice.channel
        info = self._temp.get(ch.id)
        if info is None:
            await interaction.response.send_message("❌ This is not a Jarvis JTC channel.", ephemeral=True); return
        owner = interaction.guild.get_member(info["owner_id"])
        members_list = ", ".join(m.display_name for m in ch.members) or "Empty"
        embed = discord.Embed(title=f"🔊 {ch.name}", color=discord.Color.blurple(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="👑 Owner",       value=owner.mention if owner else f"<@{info['owner_id']}>", inline=True)
        embed.add_field(name="👥 Members",     value=f"{len(ch.members)} / {ch.user_limit or '∞'}",        inline=True)
        embed.add_field(name="🎙️ Bitrate",    value=f"{ch.bitrate // 1000} kbps",                         inline=True)
        embed.add_field(name="🔒 Locked",      value="Yes" if info.get("locked") else "No",                inline=True)
        embed.add_field(name="👻 Hidden",      value="Yes" if info.get("hidden") else "No",                inline=True)
        embed.add_field(name="🌍 Region",      value=str(ch.rtc_region or "Automatic").title(),            inline=True)
        embed.add_field(name="👤 In Channel",  value=members_list,                                         inline=False)
        if info.get("permitted"):
            perms = ", ".join(f"<@{uid}>" for uid in info["permitted"])
            embed.add_field(name="✅ Permitted", value=perms, inline=False)
        embed.set_footer(text="Jarvis JTC • by Pookie Boy")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @vc_group.command(name="reset", description="Reset your saved channel defaults (name, limit, bitrate)")
    async def vc_reset(self, interaction: discord.Interaction):
        cfg = self._guild_cfg(interaction.guild.id)
        cfg.get("user_defaults", {}).pop(str(interaction.user.id), None)
        self._save()
        await interaction.response.send_message("✅ Your saved channel defaults have been reset.", ephemeral=True)

    @vc_group.command(name="interface", description="(Re)post the control panel inside your temp voice channel")
    async def vc_interface(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message("❌ You must be in a voice channel.", ephemeral=True); return
        ch   = member.voice.channel
        info = self._temp.get(ch.id)
        if info is None:
            await interaction.response.send_message("❌ This is not a Jarvis JTC channel.", ephemeral=True); return
        if info["owner_id"] != member.id:
            await interaction.response.send_message("❌ Only the channel owner can post the control panel.", ephemeral=True); return

        # Delete old panel if it exists
        old_msg_id = info.get("panel_msg_id")
        if old_msg_id:
            try:
                old_msg = await ch.fetch_message(old_msg_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        # Post fresh panel
        try:
            panel_embed = self._build_panel_embed(ch, info, interaction.guild)
            panel_msg   = await ch.send(embed=panel_embed, view=VCControlPanel())
            info["panel_msg_id"] = panel_msg.id
            await interaction.response.send_message("✅ Control panel posted in your voice channel.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.response.send_message(f"❌ Could not post panel: {e}", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# Bot setup & entry point
# ══════════════════════════════════════════════════════════════════════════════

intents = discord.Intents.all()
bot     = commands.Bot(command_prefix='!jarvis ', intents=intents, help_command=None)

STATUS_MESSAGES = [
    discord.Activity(type=discord.ActivityType.watching, name="Type /help for bot cmds"),
    discord.Activity(type=discord.ActivityType.watching, name="secure | secured"),
    discord.Activity(type=discord.ActivityType.watching, name="powerful | unbypassable"),
    discord.Activity(type=discord.ActivityType.watching, name="safe for your server"),
    discord.Activity(type=discord.ActivityType.watching, name="🎉 Giveaways & Tickets"),
    discord.Activity(type=discord.ActivityType.watching, name="⭐ Leveling & XP"),
]
_status_index = 0


@tasks.loop(minutes=5)
async def rotate_status():
    global _status_index
    await bot.change_presence(status=discord.Status.online, activity=STATUS_MESSAGES[_status_index % len(STATUS_MESSAGES)])
    _status_index += 1


@bot.event
async def on_ready():
    logger.info(f'Jarvis is online as {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} slash commands globally')
    except Exception as e:
        logger.error(f'Error syncing slash commands: {e}')
    rotate_status.start()
    logger.info('Jarvis is fully operational.')


@bot.event
async def on_command_error(ctx, error):
    logger.warning(f'Command error: {error}')


async def main():
    async with bot:
        await bot.add_cog(Logging(bot))
        await bot.add_cog(Security(bot))
        await bot.add_cog(AntiSpam(bot))
        await bot.add_cog(AutoMod(bot))
        await bot.add_cog(Moderation(bot))
        # ── Premium features (Astro Bot parity) ──────────────────────────────
        await bot.add_cog(Welcome(bot))
        await bot.add_cog(AutoRole(bot))
        await bot.add_cog(ReactionRoles(bot))
        await bot.add_cog(Leveling(bot))
        await bot.add_cog(Tickets(bot))
        await bot.add_cog(Giveaway(bot))
        await bot.add_cog(Polls(bot))
        await bot.add_cog(Starboard(bot))
        await bot.add_cog(Suggestions(bot))
        await bot.add_cog(AFKSnipe(bot))
        await bot.add_cog(Reminders(bot))
        await bot.add_cog(Info(bot))
        await bot.add_cog(Fun(bot))
        await bot.add_cog(Utility(bot))
        await bot.add_cog(JoinToCreate(bot))
        logger.info('All cogs loaded — 20 modules active.')
        token = os.environ.get('DISCORD_TOKEN')
        if not token:
            logger.critical('DISCORD_TOKEN environment variable is not set!')
            return
        await bot.start(token)


if __name__ == '__main__':
    asyncio.run(main())
