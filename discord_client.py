import asyncio
import logging
import discord
import config

logger = logging.getLogger(__name__)


class GiveawayView(discord.ui.View):
    """Persistente Button-View fuer Giveaway-Teilnahme (kein Timeout)."""

    def __init__(self, giveaway_id: int, emoji: str, manager):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self._manager    = manager
        btn = discord.ui.Button(
            label=f"{emoji}  Teilnehmen",
            style=discord.ButtonStyle.primary,
            custom_id=f"giveaway_join:{giveaway_id}",
        )
        btn.callback = self._on_click
        self.add_item(btn)

    async def _on_click(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_id    = str(interaction.user.id)
        user_login = interaction.user.name
        result = await self._manager.handle_discord_button_click(
            self.giveaway_id, user_id, user_login
        )
        if result == "joined":
            text = "✅ Du nimmst jetzt am Giveaway teil!"
        elif result == "left":
            text = "👋 Du nimmst nicht mehr am Giveaway teil."
        else:
            text = "❌ Fehler. Bitte versuche es später nochmal."
        await interaction.followup.send(text, ephemeral=True)


class DiscordBot(discord.Client):
    """Discord-Bot fuer RedeemShip Giveaways (Button-basiert)."""

    def __init__(self, manager):
        intents = discord.Intents(guilds=True, guild_messages=True)
        super().__init__(intents=intents)
        self._manager = manager
        # message_id (str) -> giveaway dict
        self.active_messages: dict = {}

    async def on_ready(self):
        logger.info("Discord bot ready: %s (%s)", self.user, self.user.id)

    async def post_giveaway_message(self, channel_id: str, giveaway: dict) -> str | None:
        """Postet das Giveaway-Embed mit Teilnahme-Button, gibt message_id zurueck."""
        try:
            channel = await self.fetch_channel(int(channel_id))
            embed   = self._build_embed(giveaway)
            gid     = int(giveaway.get("id", 0))
            emoji   = giveaway.get("emoji", "🎉")
            view    = GiveawayView(gid, emoji, self._manager)
            msg     = await channel.send(embed=embed, view=view)
            return str(msg.id)
        except Exception as exc:
            logger.error("post_giveaway_message: %s", exc)
            return None

    async def end_giveaway_message(
        self, channel_id: str, message_id: str,
        winner_login: str | None, giveaway_id: int | None = None
    ):
        """Editiert das Embed auf 'beendet' und deaktiviert den Button."""
        try:
            channel = await self.fetch_channel(int(channel_id))
            msg     = await channel.fetch_message(int(message_id))
            if not msg.embeds:
                return
            embed = msg.embeds[0].copy()
            embed.colour = discord.Color.green()
            if winner_login:
                embed.add_field(name="Gewinner", value=f"@{winner_login}", inline=False)
            embed.set_footer(text="Giveaway beendet - RedeemShip")

            disabled_view = discord.ui.View()
            disabled_btn  = discord.ui.Button(
                label="Giveaway beendet",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id=f"giveaway_join:{giveaway_id}" if giveaway_id else "giveaway_ended",
            )
            disabled_view.add_item(disabled_btn)

            await msg.edit(embed=embed, view=disabled_view)
        except Exception as exc:
            logger.error("end_giveaway_message: %s", exc)

    async def send_dm(self, user_id: str, message: str) -> bool:
        """Sendet eine DM. Gibt False zurueck wenn DMs gesperrt sind."""
        try:
            user = await self.fetch_user(int(user_id))
            await user.send(message)
            return True
        except discord.Forbidden:
            logger.warning("DM blocked by user %s", user_id)
            return False
        except Exception as exc:
            logger.error("send_dm: %s", exc)
            return False

    async def get_guild_channels(self, guild_id: str) -> list:
        """Gibt Textkanale eines Servers sortiert nach Position zurueck."""
        try:
            guild = await self.fetch_guild(int(guild_id))
            channels = await guild.fetch_channels()
            return [
                {"id": str(c.id), "name": c.name, "position": c.position}
                for c in sorted(channels, key=lambda c: c.position)
                if isinstance(c, discord.TextChannel)
            ]
        except Exception as exc:
            logger.error("get_guild_channels: %s", exc)
            return []

    async def get_guilds(self) -> list:
        """Gibt alle Server zurueck, in denen der Bot Mitglied ist."""
        return [{"id": str(g.id), "name": g.name} for g in self.guilds]

    async def run_bot(self):
        try:
            await self.start(config.DISCORD_BOT_TOKEN)
        except Exception as exc:
            logger.error("Discord bot run error: %s", exc)

    def register_message(self, message_id: str, giveaway: dict):
        self.active_messages[message_id] = giveaway
        # View fuer persistente Button-Interaktionen registrieren (auch nach Bot-Restart)
        gid   = int(giveaway.get("id", 0))
        emoji = giveaway.get("emoji", "🎉")
        view  = GiveawayView(gid, emoji, self._manager)
        self.add_view(view, message_id=int(message_id))

    def unregister_message(self, message_id: str):
        self.active_messages.pop(message_id, None)

    def _build_embed(self, giveaway: dict) -> discord.Embed:
        emoji = giveaway.get("emoji", "🎉")
        embed = discord.Embed(
            title=f"{emoji} Giveaway: {giveaway.get('title', 'Giveaway')}",
            description=giveaway.get("description") or f"Reagiere mit {emoji} um teilzunehmen!",
            color=0x9146FF,
        )
        if giveaway.get("product_name"):
            embed.add_field(name="Preis", value=giveaway["product_name"], inline=True)
        wc = int(giveaway.get("winner_count") or 1)
        if wc > 1:
            embed.add_field(name="Gewinner", value=str(wc), inline=True)
        if giveaway.get("scheduled_end"):
            try:
                import datetime
                ts = int(datetime.datetime.fromisoformat(giveaway["scheduled_end"]).timestamp())
                embed.add_field(name="Endet", value=f"<t:{ts}:R>", inline=True)
            except Exception:
                pass
        if giveaway.get("product_image_url"):
            embed.set_image(url=giveaway["product_image_url"])
        embed.set_footer(text="RedeemShip Giveaway")
        return embed
