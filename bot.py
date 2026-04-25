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
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "age_limit": None,
    "extractor_retries": 3,
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
}

FFMPEG_STREAM_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

FFMPEG_FILE_OPTIONS = {
    "options": "-vn",
}

# ─── Bot setup ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

music_state: dict[int, dict] = {}


def is_url(s: str) -> bool:
    return bool(re.match(r"https?://", s))


def resolve_source(query: str) -> str:
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        formats = info.get("formats", [])
        # Priorité : audio seul sans vidéo
        audio_only = [
            f for f in formats
            if f.get("acodec") != "none" and f.get("vcodec") == "none"
        ]
        if audio_only:
            # Prendre le meilleur bitrate
            best = max(audio_only, key=lambda f: f.get("abr") or 0)
            return best["url"]
        # Fallback : meilleur format dispo
        return info["url"]


def make_audio_source(source_url: str, volume: float) -> discord.PCMVolumeTransformer:
    ffmpeg_opts = FFMPEG_STREAM_OPTIONS if is_url(source_url) else FFMPEG_FILE_OPTIONS
    audio = discord.FFmpegPCMAudio(source_url, **ffmpeg_opts)
    return discord.PCMVolumeTransformer(audio, volume=volume)


def play_audio(vc: discord.VoiceClient, source_url: str, guild_id: int, loop: asyncio.AbstractEventLoop):

    async def restart():
        await asyncio.sleep(0.5)
        state = music_state.get(guild_id, {})
        if not state.get("loop") or not vc.is_connected():
            return
        try:
            new_url = resolve_source(state["source"]) if is_url(state["source"]) else state["source"]
            audio = make_audio_source(new_url, state.get("volume", 0.5))
            vc.play(audio, after=lambda e: on_after(e))
        except Exception as ex:
            print(f"Erreur restart : {ex}")

    def on_after(error):
        if error:
            print(f"Erreur lecture : {error}")
        state = music_state.get(guild_id, {})
        if state.get("loop") and vc.is_connected():
            asyncio.run_coroutine_threadsafe(restart(), loop)

    audio = make_audio_source(source_url, music_state.get(guild_id, {}).get("volume", 0.5))
    vc.play(audio, after=lambda e: on_after(e))


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot connecté : {bot.user} | Commandes slash synchronisées.")


@bot.tree.command(name="connect", description="Connecte le bot à ton canal vocal")
async def connect(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ Tu dois être dans un canal vocal d'abord !", ephemeral=True)
        return
    await interaction.response.defer()
    channel = interaction.user.voice.channel
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.move_to(channel)
        await interaction.followup.send(f"🔀 Déplacé vers **{channel.name}**.")
    else:
        await channel.connect()
        await interaction.followup.send(f"🎵 Connecté à **{channel.name}** !")


@bot.tree.command(name="deconecte", description="Déconnecte le bot du canal vocal")
async def deconecte(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if vc:
        music_state.pop(interaction.guild_id, None)
        vc.stop()
        await vc.disconnect()
        await interaction.followup.send("👋 Bot déconnecté.")
    else:
        await interaction.followup.send("❌ Le bot n'est pas dans un canal vocal.")


@bot.tree.command(name="play", description="Joue un lien YouTube ou un fichier MP3 (en boucle infinie)")
@app_commands.describe(
    lien="URL YouTube ou recherche texte (ex: 'lofi hip hop')",
    fichier="Fichier audio MP3 à jouer"
)
async def play(
    interaction: discord.Interaction,
    lien: str = None,
    fichier: discord.Attachment = None,
):
    await interaction.response.defer()

    vc = interaction.guild.voice_client
    if not vc:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ Rejoins un canal vocal d'abord !")
            return
        vc = await interaction.user.voice.channel.connect()

    if not lien and not fichier:
        await interaction.followup.send("❌ Fournis un lien ou un fichier audio.")
        return

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
            await asyncio.sleep(0.3)

        music_state[interaction.guild_id] = {
            "source": source_key,
            "loop": True,
            "paused": False,
            "volume": 0.5,
        }

        loop = asyncio.get_event_loop()
        play_audio(vc, source_url, interaction.guild_id, loop)

        await interaction.followup.send(
            f"▶️ **En cours** : `{label}`\n🔁 Boucle infinie activée."
        )

    except Exception as e:
        print(f"Erreur play : {e}")
        await interaction.followup.send(f"❌ Erreur : `{e}`")


@bot.tree.command(name="pause", description="Met en pause ou reprend la lecture")
async def pause(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.followup.send("❌ Le bot n'est pas dans un canal vocal.")
        return
    state = music_state.get(interaction.guild_id, {})
    if vc.is_playing():
        vc.pause()
        state["paused"] = True
        await interaction.followup.send("⏸️ Pause.")
    elif vc.is_paused():
        vc.resume()
        state["paused"] = False
        await interaction.followup.send("▶️ Reprise.")
    else:
        await interaction.followup.send("❌ Aucune musique en cours.")


@bot.tree.command(name="volume", description="Règle le volume (0–100)")
@app_commands.describe(niveau="Volume entre 0 et 100")
async def volume(interaction: discord.Interaction, niveau: int):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
        vol = max(0.0, min(1.0, niveau / 100))
        vc.source.volume = vol
        state = music_state.get(interaction.guild_id, {})
        state["volume"] = vol
        await interaction.followup.send(f"🔊 Volume : **{niveau}%**")
    else:
        await interaction.followup.send("❌ Aucune musique en cours.")


bot.run(TOKEN)

