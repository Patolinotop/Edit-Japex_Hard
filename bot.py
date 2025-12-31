import discord
import os
import re
import aiohttp
from dotenv import load_dotenv
from datetime import timedelta
from collections import deque

# ================== CONFIG ==================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise RuntimeError("DISCORD_TOKEN ou GROQ_API_KEY não definidos")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"  # MODELO ATUAL E ATIVO

INSULTS = ["burro", "idiota", "animal", "imundo", "lixo", "merda", "fdp", "viado"]

# ===========================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

bot_busy = False
offenses = {}
user_history = {}

BRACKET_REGEX = re.compile(r"\[.*?\]")

# ================== FUNÇÕES ==================

def highest_role(member: discord.Member):
    roles = [r for r in member.roles if r.name != "@everyone"]
    if not roles:
        return "Usuário"
    role = max(roles, key=lambda r: r.position)
    return BRACKET_REGEX.sub("", role.name).strip()

def read_dados():
    try:
        with open("dados.txt", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return ""

def is_insult(text: str):
    t = text.lower()
    return any(word in t for word in INSULTS)

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
        "temperature": 0.3,
        "max_tokens": 20
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(GROQ_URL, json=payload, headers=headers, timeout=30) as resp:
            data = await resp.json()
            if "choices" not in data:
                raise RuntimeError(data)
            return data["choices"][0]["message"]["content"]

# ================== EVENTS ==================

@client.event
async def on_ready():
    print(f"✅ Conectado como {client.user}")

@client.event
async def on_message(message: discord.Message):
    global bot_busy

    if message.author.bot:
        return

    # Só responde se mencionar o bot
    if client.user not in message.mentions:
        return

    if bot_busy:
        await message.reply("Já estou respondendo. Aguarde.")
        return

    bot_busy = True

    try:
        member = message.author
        role_name = highest_role(member)

        content = message.content.replace(f"<@{client.user.id}>", "").strip()

        # Histórico (últimas 5 mensagens do usuário)
        history = user_history.setdefault(member.id, deque(maxlen=5))
        history.append(content)

        # ===== XINGAMENTO / OFENSA =====
        if is_insult(content):
            count = offenses.get(member.id, 0) + 1
            offenses[member.id] = count

            if count == 1:
                await message.reply(f"Fala direito, {role_name}. Animal.")
                await member.timeout(timedelta(seconds=60))
            elif count == 2:
                await message.reply(f"Já avisei, {role_name}. Imundo.")
                await member.timeout(timedelta(hours=1))
            else:
                await message.reply(f"Chega, {role_name}.")
                await member.timeout(timedelta(hours=3))

            return

        dados = read_dados()

        # ===== PROMPT CORRIGIDO (SEM RP) =====
        system_prompt = f"""
Você é um BOT DE DISCORD.
Você NÃO está dentro de um jogo, RP ou simulação.
Você NÃO executa ações do sistema descrito.
Você NÃO interpreta personagens.

Os dados abaixo são APENAS DOCUMENTAÇÃO e REGRAS
para explicar aos usuários como o sistema funciona.

Seu papel:
- Conversar normalmente no Discord
- Ajudar com dúvidas
- Explicar regras e hierarquia
- Ser direto e claro

IMPORTANTE:
- Nunca trate o usuário como se estivesse em treinamento, fila ou alistamento
- Nunca diga que alguém foi aprovado ou reprovado
- Nunca assuma que algo está acontecendo no sistema
- Apenas explique, nunca simule

Tom:
- Natural
- Direto
- Sem RP
- Sem moralismo
- Sem formalidade excessiva

Use SOMENTE os dados abaixo como referência factual.
Se algo não constar, diga que não há registro.

DOCUMENTAÇÃO:
{dados}
"""

        user_prompt = f"""
Cargo do usuário: {role_name}

Histórico recente:
{chr(10).join(history)}

Pergunta atual:
{content}
"""

        async with message.channel.typing():
            reply = await call_groq(system_prompt, user_prompt)

        await message.reply(reply[:2000])

    except Exception as e:
        print("❌ ERRO REAL:", repr(e))
        await message.reply("Erro ao responder.")

    finally:
        bot_busy = False

# ================== START ==================
client.run(DISCORD_TOKEN)
