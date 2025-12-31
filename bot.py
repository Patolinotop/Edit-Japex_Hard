import discord
import os
import re
import aiohttp
from dotenv import load_dotenv
from datetime import timedelta
from collections import deque, Counter

# ================= CONFIG =================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise RuntimeError("DISCORD_TOKEN ou GROQ_API_KEY não definidos")

# ===== IDENTIDADE DO BOT =====
BOT_NAME = "JapexEvolutionX"
BOT_VERSION = "0.3"  # atualize manualmente

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"

MAX_TOKENS = 60

INSULTS = [
    "burro", "idiota", "animal", "imundo", "lixo", "merda",
    "fdp", "viado", "retardado"
]

# =========================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

bot_busy = False
offenses = {}
user_history = {}

BRACKET_REGEX = re.compile(r"\[.*?\]")
EMOJI_REGEX = re.compile(r"<a?:\w+:\d+>|[\U00010000-\U0010ffff]", re.UNICODE)

# ================= FUNÇÕES =================

def highest_role(member: discord.Member):
    roles = [r for r in member.roles if r.name != "@everyone"]
    if not roles:
        return member.display_name
    role = max(roles, key=lambda r: r.position)
    return BRACKET_REGEX.sub("", role.name).strip() or member.display_name

def read_dados():
    try:
        with open("dados.txt", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return ""

def is_insult(text):
    return any(w in text.lower() for w in INSULTS)

def is_spam(history):
    return len(history) >= 3 and len(set(history)) == 1

def emoji_spam(text):
    emojis = EMOJI_REGEX.findall(text)
    return len(emojis) >= 4 and Counter(emojis).most_common(1)[0][1] >= 3

def bad_grammar(text):
    if text.strip() in ["?", "??", "???"]:
        return True
    if len(text) < 3:
        return True
    if text.isupper() and len(text) > 5:
        return True
    if not any(c.isalpha() for c in text):
        return True
    return False

async def call_groq(system_prompt, user_prompt):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.35,
        "max_tokens": MAX_TOKENS
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(GROQ_URL, json=payload, headers=headers, timeout=30) as resp:
            data = await resp.json()
            if "choices" not in data:
                raise RuntimeError(data)
            text = data["choices"][0]["message"]["content"].strip()
            return text.rstrip(".") + "."

# ================= EVENTS =================

@client.event
async def on_ready():
    print(f"✅ {BOT_NAME} conectado | v{BOT_VERSION}")

@client.event
async def on_message(message: discord.Message):
    global bot_busy

    if message.author.bot:
        return

    if client.user not in message.mentions:
        return

    if bot_busy:
        return

    content = message.content.replace(f"<@{client.user.id}>", "").strip()
    if not content:
        return

    bot_busy = True

    try:
        member = message.author
        role_name = highest_role(member)

        history = user_history.setdefault(member.id, deque(maxlen=5))
        history.append(content)

        low = content.lower()

        # ===== VERSÃO / MODELO =====
        if "versão" in low:
            await message.reply(f"Versão atual: {BOT_VERSION}.")
            return

        if "modelo" in low:
            await message.reply(f"{BOT_NAME} v{BOT_VERSION}.")
            return

        # ===== PUNIÇÃO DISCORD =====
        violation = (
            is_insult(content)
            or is_spam(history)
            or emoji_spam(content)
            or bad_grammar(content)
        )

        if violation:
            count = offenses.get(member.id, 0) + 1
            offenses[member.id] = count

            if count == 1:
                await message.reply(f"Se expressa melhor, {role_name}.")
                await member.timeout(timedelta(seconds=60))
            elif count == 2:
                await message.reply(f"Último aviso, {role_name}.")
                await member.timeout(timedelta(minutes=10))
            else:
                await message.reply(f"Chega, {role_name}.")
                await member.timeout(timedelta(hours=1))
            return

        dados = read_dados()

        # ===== PROMPT COM PERSONALIDADE =====
        system_prompt = f"""
Você é {BOT_NAME}, um bot de Discord.
Identidade fixa:
- Homem
- Heterossexual
- Tem opiniões próprias
- Pode responder perguntas políticas
- Não é neutro forçado
- Fala normalmente, como pessoa real

Você NÃO está em um jogo.
Você NÃO executa regras do jogo.
Os dados abaixo são apenas DOCUMENTAÇÃO para consulta.

Regras de resposta:
- Leve em conta o histórico do usuário
- Seja direto e humano
- Não faça RP
- Não dê ordens militares
- Não moralize
- Sempre finalize com ponto final
- Se for opinião, dê a opinião sem pedir desculpas

DOCUMENTAÇÃO (APENAS REFERÊNCIA):
{dados}
"""

        user_prompt = f"""
Histórico recente do usuário:
{chr(10).join(history)}

Mensagem atual:
{content}
"""

        async with message.channel.typing():
            reply = await call_groq(system_prompt, user_prompt)

        await message.reply(reply)

    except Exception as e:
        print("❌ ERRO REAL:", repr(e))

    finally:
        bot_busy = False

# ================= START =================
client.run(DISCORD_TOKEN)
