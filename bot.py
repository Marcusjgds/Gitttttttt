import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import os
import re

# ─── Configuration ───────────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ Variable d'environnement DISCORD_TOKEN manquante !")

# ─── yt-dlp options ──────────────────────────────────────────────
YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# ─── Bot setup ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# État par serveur : { guild_id: { "source": url_ou_path, "loop": bool, "paused": bool } }
music_state: dict[int, dict] = {}


# ─── Helper : résoudre la source audio ───────────────────────────
def resolve_source(query: str) -> str:
    """Retourne l'URL du stream audio depuis un lien YouTube/autre ou un chemin local."""
    url_pattern = re.compile(r"https?://")
    if url_pattern.match(query):
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            formats = info.get("formats", [])
            audio_formats = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]
            if audio_formats:
                return audio_formats[-1]["url"]
            return info["url"]
    return query  # fichier local


# ─── Helper : lancer la lecture ──────────────────────────────────
def play_audio(vc: discord.VoiceClient, source_url: str, guild_id: int):
    """Lance FFmpegPCMAudio et reboucle automatiquement."""
    def after_play(error):
        if error:
            print(f"Erreur lecture : {error}")
        state = music_state.get(guild_id, {})
        if state.get("loop") and vc.is_connected():
            new_url = resolve_source(state["source"])
            audio = discord.FFmpegPCMAudio(new_url, **FFMPEG_OPTIONS)
            audio = discord.PCMVolumeTransformer(audio, volume=0.5)
            vc.play(audio, after=after_play)

    audio = discord.FFmpegPCMAudio(source_url, **FFMPEG_OPTIONS)
    audio = discord.PCMVolumeTransformer(audio, volume=0.5)
    vc.play(audio, after=after_play)


# ─── Events ──────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot connecté : {bot.user} | Commandes slash synchronisées.")


# ─── /connect ────────────────────────────────────────────────────
@bot.tree.command(name="connect", description="Connecte le bot à ton canal vocal")
async def connect(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            "❌ Tu dois être dans un canal vocal d'abord !", ephemeral=True
        )
        return

    channel = interaction.user.voice.channel
    guild = interaction.guild

    if guild.voice_client:
        await guild.voice_client.move_to(channel)
        await interaction.response.send_message(f"🔀 Déplacé vers **{channel.name}**.")
    else:
        await channel.connect()
        await interaction.response.send_message(f"🎵 Connecté à **{channel.name}** !")


# ─── /deconecte ──────────────────────────────────────────────────
@bot.tree.command(name="deconecte", description="Déconnecte le bot du canal vocal")
async def deconecte(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        vc.stop()
        await vc.disconnect()
        music_state.pop(interaction.guild_id, None)
        await interaction.response.send_message("👋 Bot déconnecté du canal vocal.")
    else:
        await interaction.response.send_message("❌ Le bot n'est pas dans un canal vocal.", ephemeral=True)


# ─── /play ───────────────────────────────────────────────────────
@bot.tree.command(name="play", description="Joue un lien YouTube ou un fichier MP3 (en boucle infinie)")
@app_commands.describe(
    lien="URL YouTube / SoundCloud ou laisse vide si tu envoies un fichier",
    fichier="Fichier audio MP3 à jouer"
)
async def play(
    interaction: discord.Interaction,
    lien: str = None,
    fichier: discord.Attachment = None,
):
    vc = interaction.guild.voice_client

    if not vc:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message(
                "❌ Rejoins un canal vocal d'abord !", ephemeral=True
            )
            return
        vc = await interaction.user.voice.channel.connect()

    if not lien and not fichier:
        await interaction.response.send_message(
            "❌ Fournis un lien ou un fichier audio.", ephemeral=True
        )
        return

    await interaction.response.defer()

    try:
        if fichier:
            file_path = f"/tmp/{fichier.filename}"
            await fichier.save(file_path)
            source_key = file_path
            source_url = file_path
            label = fichier.filename
        else:
            source_key = lien
            source_url = resolve_source(lien)
            label = lien

        if vc.is_playing() or vc.is_paused():
            vc.stop()

        music_state[interaction.guild_id] = {
            "source": source_key,
            "loop": True,
            "paused": False,
        }

        play_audio(vc, source_url, interaction.guild_id)
        await interaction.followup.send(
            f"▶️ **En cours** : `{label}`\n🔁 Lecture en boucle infinie activée."
        )

    except Exception as e:
        await interaction.followup.send(f"❌ Erreur : `{e}`")


# ─── /pause ──────────────────────────────────────────────────────
@bot.tree.command(name="pause", description="Met en pause ou reprend la lecture")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("❌ Le bot n'est pas dans un canal vocal.", ephemeral=True)
        return

    state = music_state.get(interaction.guild_id, {})

    if vc.is_playing():
        vc.pause()
        state["paused"] = True
        music_state[interaction.guild_id] = state
        await interaction.response.send_message("⏸️ Lecture mise en pause.")
    elif vc.is_paused():
        vc.resume()
        state["paused"] = False
        music_state[interaction.guild_id] = state
        await interaction.response.send_message("▶️ Lecture reprise.")
    else:
        await interaction.response.send_message("❌ Aucune musique en cours.", ephemeral=True)


# ─── /volume ─────────────────────────────────────────────────────
@bot.tree.command(name="volume", description="Règle le volume (0–100)")
@app_commands.describe(niveau="Volume entre 0 et 100")
async def volume(interaction: discord.Interaction, niveau: int):
    vc = interaction.guild.voice_client
    if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = max(0.0, min(1.0, niveau / 100))
        await interaction.response.send_message(f"🔊 Volume réglé à **{niveau}%**.")
    else:
        await interaction.response.send_message("❌ Aucune musique en cours.", ephemeral=True)


# ─── Lancement ───────────────────────────────────────────────────
bot.run(TOKEN)
