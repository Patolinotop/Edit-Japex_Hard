import os
import json
import aiohttp
import asyncio
import discord
from dotenv import load_dotenv
from datetime import timedelta

# ================= CONFIG =================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "meta-llama/llama-3.1-70b-instruct"
).strip()

BOT_NAME = "Edit_Japex"
PUBLIC_MODEL_NAME = "Japex Neural Core – Ultimation"

VERSION_MAJOR = 1
VERSION_MINOR = 4  # ⬆️ atualização

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CHAT_GERAL_ID = 1450594073596395548

MAX_TOKENS = 250
TEMPERATURE = 0.45

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

bot_busy = False
grammar_warnings = {}
pressure_warnings = {}

# ================= FILE LOAD =================
def load_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

REGRAS_TXT = load_file("regras.txt")
DADOS_TXT = load_file("dados.txt")

# ================= UTIL =================
def absence_grammar(text: str):
    t = text.strip()
    if len(t) < 3:
        return True
    if t.isupper() and len(t) > 4:
        return True
    if not any(c.isalpha() for c in t):
        return True
    if t in ["?", "??", "???"]:
        return True
    return False

def safe_json(text: str):
    text = text.strip()
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return text[s:e+1]
    return None

def strip_questions(text: str):
    banned = ["?", "e você", "posso ajudar", "quer dizer", "me diga"]
    for b in banned:
        text = text.replace(b, "")
    return text.strip()

def typing_delay(text: str):
    return 1.2 + min(len(text) * 0.03, 4.0)

async def punishment_report(channel, member, reason, seconds):
    minutes = max(1, seconds // 60)
    await channel.send(
        f"🔇 {member.mention}\n"
        f"Motivo: {reason}\n"
        f"Duração: {minutes} minuto(s)"
    )

# ================= OPENROUTER =================
async def call_openrouter(system_prompt, user_prompt):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://railway.app",
        "X-Title": BOT_NAME
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(OPENROUTER_URL, headers=headers, json=payload) as r:
            data = await r.json()
            return data["choices"][0]["message"]["content"]

# ================= PROMPT =================
def build_system_prompt():
    return f"""
Você é {BOT_NAME}. Direto, firme e consciente.

══════════ REGRAS ABSOLUTAS ══════════
{REGRAS_TXT}

══════════ BASE DE DADOS (SUPORTE) ══════════
{DADOS_TXT}

USO DOS DADOS:
- Os dados acima NÃO são verdades absolutas.
- NÃO siga como RP.
- Use SOMENTE se a mensagem do usuário for uma dúvida relacionada.
- Se não fizer sentido, ignore completamente.
- Nunca cite o arquivo ou diga que está usando dados.

COMPORTAMENTO:
- Nunca faça perguntas.
- Nunca puxe assunto.
- Responda e encerre.

FORMATO DE RESPOSTA (JSON):
{{
  "action": "reply" | "timeout",
  "timeout_seconds": number,
  "reply": string,
  "reason": string
}}
""".strip()

# ================= EVENTS =================
@client.event
async def on_ready():
    print(f"✅ {BOT_NAME} online | v{VERSION_MAJOR}.{VERSION_MINOR}")

@client.event
async def on_message(message: discord.Message):
    global bot_busy

    if message.author.bot:
        return

    if client.user not in message.mentions:
        return

    if bot_busy:
        return

    member = message.author
    content = message.content.replace(f"<@{client.user.id}>", "").strip()
    low = content.lower()

    if "modelo" in low:
        await message.reply(PUBLIC_MODEL_NAME)
        return

    if "versão" in low or "versao" in low:
        await message.reply(f"v{VERSION_MAJOR}.{VERSION_MINOR}")
        return

    bot_busy = True

    try:
        if message.channel.id != CHAT_GERAL_ID and absence_grammar(content):
            c = grammar_warnings.get(member.id, 0) + 1
            grammar_warnings[member.id] = c

            await asyncio.sleep(1.2)

            if c == 1:
                await message.reply("?")
                return
            elif c == 2:
                await message.reply("Escreve direito.")
                return
            else:
                await message.reply("Silêncio.")
                await member.timeout(timedelta(minutes=1))
                await punishment_report(message.channel, member, "Spam", 60)
                return
        else:
            grammar_warnings.pop(member.id, None)

        raw = await call_openrouter(build_system_prompt(), content)
        js = safe_json(raw)

        if not js:
            await message.reply("Fala direito.")
            return

        d = json.loads(js)
        action = d.get("action", "reply")
        reply = strip_questions((d.get("reply") or "").strip())
        reason = d.get("reason", "Conduta inadequada")
        seconds = max(60, int(d.get("timeout_seconds", 60)))

        await asyncio.sleep(typing_delay(reply))

        if action == "timeout":
            await message.reply("Se controla.")
            await member.timeout(timedelta(seconds=seconds))
            await punishment_report(message.channel, member, reason, seconds)
            return

        if not reply:
            reply = "?"

        await message.reply(reply)

    except Exception as e:
        print("ERRO:", repr(e))
        await message.reply("Erro interno.")

    finally:
        bot_busy = False

# ================= START =================
client.run(DISCORD_TOKEN)
