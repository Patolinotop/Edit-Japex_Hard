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

BOT_NAME = "JapexEvolutionX"
BOT_VERSION = "0.4"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"
MAX_TOKENS = 60

# ===== LISTAS CRÍTICAS =====
INSULTS = [
    "burro", "idiota", "animal", "imundo", "lixo", "merda",
    "fdp", "viado", "retardado", "inutil", "bisonho"
]

# Frases PROIBIDAS de aparecer na resposta
FORBIDDEN_OUTPUT = [
    "alistamento", "recrutamento", "fila", "aguarde",
    "não pode falar", "permaneça", "aprovado", "reprovado"
]

# Saudações simples (não chamam IA)
GREETINGS = [
    "oi", "ola", "olá", "boa noite", "bom dia", "boa tarde"
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

def contains_insult(text):
    t = text.lower()
    return any(w in t for w in INSULTS)

def emoji_spam(text):
    emojis = EMOJI_REGEX.findall(text)
    return len(emojis) >= 4 and Counter(emojis).most_common(1)[0][1] >= 3

def is_spam(history):
    return len(history) >= 3 and len(set(history)) == 1

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

def sanitize_output(text: str):
    low = text.lower()
    for bad in FORBIDDEN_OUTPUT:
        if bad in low:
            return "Isso é explicado nos dados quando perguntado diretamente."
    return text

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
        "temperature": 0.25,
        "max_tokens": MAX_TOKENS
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(GROQ_URL, json=payload, headers=headers, timeout=30) as resp:
            data = await resp.json()
            if "choices" not in data:
                raise RuntimeError(data)
            text = data["choices"][0]["message"]["content"].strip()
            text = text.rstrip(".") + "."
            return sanitize_output(text)

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

    content = message.content.replace(f"<@{client.user.id}>", "").strip().lower()
    if not content:
        return

    member = message.author
    role_name = highest_role(member)

    # ===== HISTÓRICO =====
    history = user_history.setdefault(member.id, deque(maxlen=5))
    history.append(content)

    # ===== XINGAMENTO: SEMPRE PRIORIDADE =====
    if contains_insult(content):
        offenses[member.id] = offenses.get(member.id, 0) + 1
        await message.reply("Silêncio, animal!")
        try:
            await member.timeout(timedelta(minutes=10))
        except:
            pass
        return

    # ===== SAUDAÇÕES: NÃO CHAMA IA =====
    if content in GREETINGS:
        await message.reply("Boa noite.")
        return

    # ===== SPAM / EMOJI / LIXO =====
    if is_spam(history) or emoji_spam(content) or bad_grammar(content):
        await message.reply("Para de spammar.")
        try:
            await member.timeout(timedelta(minutes=5))
        except:
            pass
        return

    # ===== MODELO / VERSÃO =====
    if "versão" in content:
        await message.reply(f"Versão atual: {BOT_VERSION}.")
        return

    if "modelo" in content:
        await message.reply(f"{BOT_NAME} v{BOT_VERSION}.")
        return

    if bot_busy:
        return

    bot_busy = True

    try:
        dados = read_dados()

        system_prompt = f"""
Você é {BOT_NAME}, um bot de Discord.
REGRAS ABSOLUTAS:
- Nunca assuma recrutamento ativo.
- Nunca faça perguntas sobre alistamento.
- Nunca use linguagem operacional do jogo.
- Nunca dê ordens.
- Não inicie conversa.
- Vá direto à resposta.
- Use os dados apenas se a pergunta for EXPLÍCITA.

Responda curto, direto e normal.
Sempre finalize com ponto final.

DOCUMENTAÇÃO (PASSIVA):
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
