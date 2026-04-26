import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import os
import re
import glob
import base64

# ─── TOKEN ───────────────────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ Variable DISCORD_TOKEN manquante dans Railway !")

# ─── Cookies YouTube (optionnel) ─────────────────────────────────
COOKIES_PATH = None
_cookies_b64 = os.environ.get("YOUTUBE_COOKIES")
if _cookies_b64:
    COOKIES_PATH = "/tmp/yt_cookies.txt"
    with open(COOKIES_PATH, "wb") as _f:
        _f.write(base64.b64decode(_cookies_b64))
    print("🍪 Cookies YouTube chargés.")

# ─── Options yt-dlp ──────────────────────────────────────────────
def get_ydl_opts():
    opts = {
        "format": "bestaudio/best",
        "outtmpl": "/tmp/music.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        # Contourner la détection bot YouTube
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
    }
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    return opts

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# ─── Intents (TOUS les privileged activés) ───────────────────────
intents = discord.Intents.default()
intents.message_content = True   # Privileged — activer dans portal
intents.members = True           # Privileged — activer dans portal
intents.presences = True         # Privileged — activer dans portal

bot = commands.Bot(command_prefix="!", intents=intents)
music_state: dict[int, dict] = {}


def is_url(s: str) -> bool:
    return bool(re.match(r"https?://", s.strip()))


def clean_tmp():
    for f in glob.glob("/tmp/music.*"):
        try:
            os.remove(f)
        except:
            pass


def download_audio(query: str) -> str:
    """Télécharge l'audio en /tmp/music.mp3 et retourne le chemin."""
    clean_tmp()
    search = query if is_url(query) else f"ytsearch1:{query}"
    with yt_dlp.YoutubeDL(get_ydl_opts()) as ydl:
        ydl.download([search])
    files = glob.glob("/tmp/music.*")
    if not files:
        raise Exception("Fichier audio introuvable après téléchargement")
    return files[0]


def make_source(path: str, volume: float) -> discord.PCMVolumeTransformer:
    audio = discord.FFmpegPCMAudio(path, **FFMPEG_OPTIONS)
    return discord.PCMVolumeTransformer(audio, volume=volume)


def play_audio(vc: discord.VoiceClient, path: str, guild_id: int, loop: asyncio.AbstractEventLoop):

    def on_after(error):
        if error:
            print(f"Erreur lecture : {error}")
        state = music_state.get(guild_id, {})
        if state.get("loop") and vc.is_connected():
            asyncio.run_coroutine_threadsafe(restart(), loop)

    async def restart():
        await asyncio.sleep(0.5)
        state = music_state.get(guild_id, {})
        if not state.get("loop") or not vc.is_connected():
            return
        try:
            source_key = state["source"]
            if is_url(source_key):
                new_path = await loop.run_in_executor(None, download_audio, source_key)
            else:
                new_path = source_key
            src = make_source(new_path, state.get("volume", 0.5))
            vc.play(src, after=lambda e: on_after(e))
        except Exception as ex:
            print(f"Erreur restart : {ex}")

    src = make_source(path, music_state.get(guild_id, {}).get("volume", 0.5))
    vc.play(src, after=lambda e: on_after(e))


# ─── Événements ──────────────────────────────────────────────────
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Bot connecté : {bot.user} | {len(synced)} commandes synchronisées.")
    except Exception as e:
        print(f"Erreur sync commandes : {e}")


# ─── Commandes slash ─────────────────────────────────────────────

@bot.tree.command(name="connect", description="Connecte le bot à ton canal vocal")
async def cmd_connect(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ Tu dois être dans un canal vocal !", ephemeral=True)
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
async def cmd_deconecte(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if vc:
        music_state.pop(interaction.guild_id, None)
        vc.stop()
        await vc.disconnect()
        await interaction.followup.send("👋 Déconnecté.")
    else:
        await interaction.followup.send("❌ Le bot n'est pas dans un canal vocal.")


@bot.tree.command(name="play", description="Joue un lien YouTube ou une recherche en boucle infinie")
@app_commands.describe(
    lien="URL YouTube ou recherche texte (ex: lofi hip hop)",
    fichier="Fichier audio MP3 à jouer"
)
async def cmd_play(
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
        await interaction.followup.send("❌ Fournis un lien YouTube ou un fichier audio.")
        return

    try:
        loop = asyncio.get_event_loop()

        if fichier:
            file_path = f"/tmp/{fichier.filename}"
            await fichier.save(file_path)
            source_key = file_path
            audio_path = file_path
            label = fichier.filename
        else:
            await interaction.followup.send("⏳ Téléchargement en cours, patiente…")
            source_key = lien.strip()
            audio_path = await loop.run_in_executor(None, download_audio, source_key)
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

        play_audio(vc, audio_path, interaction.guild_id, loop)
        await interaction.followup.send(f"▶️ **En lecture** : `{label}`\n🔁 Boucle infinie activée.")

    except Exception as e:
        print(f"Erreur /play : {e}")
        await interaction.followup.send(f"❌ Erreur : `{e}`\n\nSi YouTube bloque, essaie un autre lien ou ajoute des cookies.")


@bot.tree.command(name="pause", description="Met en pause ou reprend la lecture")
async def cmd_pause(interaction: discord.Interaction):
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


@bot.tree.command(name="stop", description="Arrête la musique et désactive la boucle")
async def cmd_stop(interaction: discord.Interaction):
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        state = music_state.get(interaction.guild_id, {})
        state["loop"] = False
        vc.stop()
        await interaction.followup.send("⏹️ Musique arrêtée.")
    else:
        await interaction.followup.send("❌ Aucune musique en cours.")


@bot.tree.command(name="volume", description="Règle le volume (0–100)")
@app_commands.describe(niveau="Volume entre 0 et 100")
async def cmd_volume(interaction: discord.Interaction, niveau: int):
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
