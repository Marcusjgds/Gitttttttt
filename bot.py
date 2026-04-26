import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import os
import re
import glob

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("❌ Variable d'environnement DISCORD_TOKEN manquante !")

# ─── yt-dlp : téléchargement audio en MP3 ────────────────────────
YDL_DOWNLOAD_OPTIONS = {
    "format": "bestaudio/best",
    "outtmpl": "/tmp/music.%(ext)s",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["android"],
        }
    },
    "postprocessors": [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": "192",
    }],
}

FFMPEG_OPTIONS = {"options": "-vn"}

# ─── Bot setup ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)
music_state: dict[int, dict] = {}


def is_url(s: str) -> bool:
    return bool(re.match(r"https?://", s))


def download_audio(query: str) -> str:
    """Télécharge l'audio en /tmp/music.mp3 et retourne le chemin."""
    # Supprimer l'ancien fichier
    for f in glob.glob("/tmp/music.*"):
        os.remove(f)

    opts = dict(YDL_DOWNLOAD_OPTIONS)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([query])

    # Trouver le fichier téléchargé
    files = glob.glob("/tmp/music.*")
    if not files:
        raise Exception("Fichier audio introuvable après téléchargement")
    return files[0]


def make_source(path: str, volume: float) -> discord.PCMVolumeTransformer:
    audio = discord.FFmpegPCMAudio(path, **FFMPEG_OPTIONS)
    return discord.PCMVolumeTransformer(audio, volume=volume)


def play_audio(vc: discord.VoiceClient, path: str, guild_id: int, loop: asyncio.AbstractEventLoop):

    async def restart():
        await asyncio.sleep(0.5)
        state = music_state.get(guild_id, {})
        if not state.get("loop") or not vc.is_connected():
            return
        try:
            source_key = state["source"]
            # Re-télécharger si URL (le stream local peut expirer)
            if is_url(source_key):
                new_path = await loop.run_in_executor(None, download_audio, source_key)
            else:
                new_path = source_key
            audio = make_source(new_path, state.get("volume", 0.5))
            vc.play(audio, after=lambda e: on_after(e))
        except Exception as ex:
            print(f"Erreur restart : {ex}")

    def on_after(error):
        if error:
            print(f"Erreur lecture : {error}")
        state = music_state.get(guild_id, {})
        if state.get("loop") and vc.is_connected():
            asyncio.run_coroutine_threadsafe(restart(), loop)

    audio = make_source(path, music_state.get(guild_id, {}).get("volume", 0.5))
    vc.play(audio, after=lambda e: on_after(e))


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot connecté : {bot.user} | Commandes slash synchronisées.")


@bot.event
async def on_voice_state_update(member, before, after):
    """Nettoie l'état si le bot est déconnecté de force."""
    if member.id != bot.user.id:
        return
    if before.channel and not after.channel:
        # Le bot a été déconnecté
        guild_id = before.channel.guild.id
        music_state.pop(guild_id, None)
        print(f"⚠️ Bot déconnecté du vocal sur {before.channel.guild.name}")


@bot.tree.command(name="connect", description="Connecte le bot à ton canal vocal")
async def connect(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ Tu dois être dans un canal vocal d'abord !", ephemeral=True)
        return
    await interaction.response.defer()
    channel = interaction.user.voice.channel
    # Déconnecter proprement si déjà connecté (évite le 4006)
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect(force=True)
        await asyncio.sleep(1)
    
    vc = await channel.connect(timeout=60, reconnect=True, self_deaf=True)
    await asyncio.sleep(1)
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


@bot.tree.command(name="play", description="Joue un lien YouTube ou un fichier MP3 en boucle infinie")
@app_commands.describe(
    lien="URL YouTube ou texte de recherche (ex: 'lofi hip hop')",
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
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect(force=True)
            await asyncio.sleep(1)
        vc = await interaction.user.voice.channel.connect(timeout=60, reconnect=True, self_deaf=True)
        await asyncio.sleep(1)
    
    # Attendre que le voice client soit vraiment prêt (max 10s)
    for _ in range(20):
        if vc.is_connected():
            break
        await asyncio.sleep(0.5)
    else:
        await interaction.followup.send("❌ Impossible de se connecter au canal vocal.")
        return

    if not lien and not fichier:
        await interaction.followup.send("❌ Fournis un lien ou un fichier audio.")
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
            await interaction.followup.send("⏳ Téléchargement en cours...")
            source_key = lien
            audio_path = await loop.run_in_executor(None, download_audio, lien)
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
        await interaction.followup.send(f"▶️ **En cours** : `{label}`\n🔁 Boucle infinie activée.")

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
