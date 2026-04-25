#!/bin/bash
# Met à jour yt-dlp à chaque démarrage (YouTube change souvent)
echo "🔄 Mise à jour de yt-dlp..."
pip install -q --upgrade yt-dlp
echo "✅ yt-dlp mis à jour : $(yt-dlp --version)"
exec python bot.py
