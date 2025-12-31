import os
import json
import aiohttp
import discord
from dotenv import load_dotenv
from datetime import timedelta
from collections import deque

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

# ===== VERSIONAMENTO JAPEX =====
VERSION_MAJOR = 1
VERSION_MINOR = 1  # 👈 incremento aplicado (antes era 1.0)

def bot_version():
    return f"{VERSION_MAJOR}.{VERSION_MINOR}"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CHAT_GERAL_ID = 1450594073596395548

MAX_TOKENS = 200
TEMPERATURE = 0.45

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

# ================= ESTADO =================
user_history = {}
grammar_warnings = {}
pressure_warnings = {}

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
    if text.startswith("{") and text.endswith("}"):
        return text
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return text[s:e+1]
    return None

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
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"}
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(OPENROUTER_URL, headers=headers, json=payload) as r:
            data = await r.json()
            return data["choices"][0]["message"]["content"]

# ================= PROMPT (AJUSTADO) =================
def build_system_prompt():
    return f"""
Você é {BOT_NAME}. Firme, confiante e controlado.

POSTURA:
- Não seja agressivo sem motivo.
- Não seja educado demais.
- Não seja irritado por padrão.
- Escale apenas se o usuário insistir.

COMPORTAMENTO:
- Conversa normal → resposta normal.
- Pergunta pessoal simples → resposta irônica leve.
- Provocação repetida → aviso curto.
- Reincidência → timeout.

PROIBIDO:
- Moralizar
- Falar de regras
- Dar sermão
- Ser grosso sem contexto

FORMATO JSON:
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
    print(f"✅ {BOT_NAME} online | v{bot_version()}")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot or client.user not in message.mentions:
        return

    member = message.author
    content = message.content.replace(f"<@{client.user.id}>", "").strip()
    low = content.lower()

    # ===== COMANDOS FIXOS =====
    if "modelo" in low:
        await message.reply(PUBLIC_MODEL_NAME)
        return

    if "versão" in low or "versao" in low:
        await message.reply(f"v{bot_version()}")
        return

    # ===== GRAMÁTICA (fora do chat geral) =====
    if message.channel.id != CHAT_GERAL_ID and absence_grammar(content):
        c = grammar_warnings.get(member.id, 0) + 1
        grammar_warnings[member.id] = c

        if c == 1:
            await message.reply("?")
            return
        elif c == 2:
            await message.reply("Tenta escrever direito.")
            return
        else:
            await message.reply("Silêncio, animal.")
            await member.timeout(timedelta(minutes=1))
            await punishment_report(message.channel, member, "Ausência gramatical", 60)
            return
    else:
        grammar_warnings.pop(member.id, None)

    # ===== HISTÓRICO =====
    hist = user_history.setdefault(member.id, deque(maxlen=6))
    hist.append(content)

    # ===== IA =====
    raw = await call_openrouter(build_system_prompt(), content)
    js = safe_json(raw)
    if not js:
        await message.reply("Fala melhor.")
        return

    d = json.loads(js)
    action = d.get("action", "reply")
    reply = d.get("reply", "").strip()
    reason = d.get("reason", "Conduta inadequada")
    seconds = max(60, int(d.get("timeout_seconds", 60)))

    # ===== ESCALONAMENTO =====
    if action == "timeout":
        p = pressure_warnings.get(member.id, 0) + 1
        pressure_warnings[member.id] = p

        if p == 1:
            await message.reply("Se quiser continuar, melhora o tom.")
            return

        await message.reply("Silêncio, animal.")
        await member.timeout(timedelta(seconds=seconds))
        await punishment_report(message.channel, member, reason, seconds)
        return

    # ===== RESPOSTA NORMAL =====
    if not reply:
        reply = "?"
    await message.reply(reply)

# ================= START =================
client.run(DISCORD_TOKEN)
