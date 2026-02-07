import os
import json
import re
from datetime import timedelta, datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Apenas esses IDs podem usar /idbloq (e afins)
ALLOWED_COMMAND_USERS = {
    1331505963622076476,
    1319506938391957575,
}

DATA_FILE = "blocked.json"

def load_blocked_ids() -> set[int]:
    if not os.path.exists(DATA_FILE):
        return set()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ids = data.get("blocked_ids", [])
        return {int(x) for x in ids}
    except Exception:
        # se der ruim no arquivo, não quebra o bot
        return set()

def save_blocked_ids(blocked: set[int]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"blocked_ids": sorted(list(blocked))}, f, ensure_ascii=False, indent=2)

blocked_ids = load_blocked_ids()

intents = discord.Intents.default()
intents.message_content = True   # precisa habilitar no Dev Portal também
intents.members = True           # recomendado p/ moderação/timeout de forma estável

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # sincroniza slash commands globalmente
        await self.tree.sync()

bot = MyBot()

def extract_id_from_text(text: str) -> int | None:
    """
    Aceita:
    - "123456789012345678"
    - "<@123...>" ou "<@!123...>"
    """
    if not text:
        return None
    text = text.strip()

    # Se for só número
    if text.isdigit():
        return int(text)

    # Se for menção <@123> / <@!123>
    m = re.match(r"^<@!?(\d{15,25})>$", text)
    if m:
        return int(m.group(1))

    # Se tiver um número grande no meio (fallback)
    m2 = re.search(r"(\d{15,25})", text)
    if m2:
        return int(m2.group(1))

    return None

def is_authorized(interaction: discord.Interaction) -> bool:
    return interaction.user and interaction.user.id in ALLOWED_COMMAND_USERS

async def reply_denied(interaction: discord.Interaction):
    await interaction.response.send_message(
        "❌ Você não tem permissão pra usar esse comando.",
        ephemeral=True
    )

@bot.event
async def on_ready():
    print(f"✅ Logado como {bot.user} (ID: {bot.user.id})")
    print(f"🔒 IDs bloqueados carregados: {sorted(list(blocked_ids))}")

# -------- Slash commands --------

@bot.tree.command(name="idbloq", description="Bloqueia um usuário (por ID ou @). Menções nele geram timeout de 1 dia + delete.")
@app_commands.describe(alvo="ID numérico ou @menção do usuário a ser bloqueado")
async def idbloq(interaction: discord.Interaction, alvo: str):
    if not is_authorized(interaction):
        return await reply_denied(interaction)

    target_id = extract_id_from_text(alvo)
    if not target_id:
        return await interaction.response.send_message(
            "⚠️ Não consegui entender o alvo. Use um ID (só números) ou marque com @.",
            ephemeral=True
        )

    blocked_ids.add(target_id)
    save_blocked_ids(blocked_ids)

    await interaction.response.send_message(
        f"✅ Adicionado na lista bloqueada: `{target_id}`\n"
        f"Agora, **qualquer @menção** a esse ID vai resultar em **timeout de 1 dia** + **delete da mensagem**.",
        ephemeral=True
    )

@bot.tree.command(name="idunbloq", description="Remove um usuário da lista bloqueada (por ID ou @).")
@app_commands.describe(alvo="ID numérico ou @menção do usuário a ser desbloqueado")
async def idunbloq(interaction: discord.Interaction, alvo: str):
    if not is_authorized(interaction):
        return await reply_denied(interaction)

    target_id = extract_id_from_text(alvo)
    if not target_id:
        return await interaction.response.send_message(
            "⚠️ Não consegui entender o alvo. Use um ID (só números) ou marque com @.",
            ephemeral=True
        )

    if target_id in blocked_ids:
        blocked_ids.remove(target_id)
        save_blocked_ids(blocked_ids)
        msg = f"✅ Removido da lista bloqueada: `{target_id}`"
    else:
        msg = f"ℹ️ `{target_id}` não estava na lista bloqueada."

    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="idlista", description="Mostra a lista de IDs bloqueados.")
async def idlista(interaction: discord.Interaction):
    if not is_authorized(interaction):
        return await reply_denied(interaction)

    if not blocked_ids:
        return await interaction.response.send_message("📭 Lista vazia.", ephemeral=True)

    txt = "\n".join(f"- `{i}`" for i in sorted(list(blocked_ids)))
    await interaction.response.send_message(f"📌 **IDs bloqueados:**\n{txt}", ephemeral=True)

# -------- Moderação por menção --------

@bot.event
async def on_message(message: discord.Message):
    # Ignora bots e DMs
    if message.author.bot or not message.guild:
        return

    # Se não tem menções, nada a fazer
    if not message.mentions:
        return

    # Opcional: não punir os dois "donos" (pra evitar auto-armadilha)
    if message.author.id in ALLOWED_COMMAND_USERS:
        return

    mentioned_ids = {u.id for u in message.mentions}

    # Só age se mencionou alguém bloqueado
    if not (mentioned_ids & blocked_ids):
        return

    # Tenta apagar a mensagem imediatamente
    try:
        await message.delete()
    except discord.Forbidden:
        # Falta permissão de apagar
        pass
    except discord.HTTPException:
        pass

    # Dá timeout de 1 dia no autor (precisa permissão "Moderate Members")
    try:
        member = message.author
        if isinstance(member, discord.Member):
            until = datetime.now(timezone.utc) + timedelta(days=1)
            await member.timeout(until, reason="Menção a usuário bloqueado (regra do servidor).")
    except discord.Forbidden:
        # Falta permissão de moderar
        pass
    except discord.HTTPException:
        pass

if not TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN não encontrado. Configure a variável de ambiente DISCORD_TOKEN.")

bot.run(TOKEN)
