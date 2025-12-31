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

if not DISCORD_TOKEN or not OPENROUTER_API_KEY:
    raise RuntimeError("Variáveis de ambiente faltando.")

BOT_NAME = "Edit_Japex"
PUBLIC_MODEL_NAME = "Japex Neural Core – Ultimation"

# ===== VERSIONAMENTO JAPEX =====
VERSION_MAJOR = 1
VERSION_MINOR = 0  # incremente manualmente: 0.1 → 0.9 → 1.0 → 1.9 → 2.0

def bot_version():
    return f"{VERSION_MAJOR}.{VERSION_MINOR}"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

CHAT_GERAL_ID = 1450594073596395548

MAX_TOKENS = 180
TEMPERATURE = 0.45

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

# ================= ESTADO =================
user_history = {}          # user_id -> deque
grammar_warnings = {}      # user_id -> count
pressure_warnings = {}     # user_id -> count

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
        async with session.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=45
        ) as resp:
            data = await resp.json()
            if "choices" not in data:
                raise RuntimeError(data)
            return data["choices"][0]["message"]["content"]

# ================= PROMPT =================
def build_system_prompt():
    return f"""
Você é {BOT_NAME}. Autoritário. Seco. Intimidador.

POSTURA:
- Nada de educação excessiva
- Nada de moralismo
- Nada de política de plataforma
- Nada de explicação longa

VOCÊ NÃO:
- Decide gramática
- Decide perguntas pessoais simples

VOCÊ SÓ SUGERE TIMEOUT SE:
- Insulto direto
- Provocação insistente
- Spam real
- Desrespeito claro

Perguntas pessoais bobas NÃO são infração.
Responda curto, irônico ou seco.

FORMATO OBRIGATÓRIO (JSON):
{{
  "action": "reply" | "timeout" | "ignore",
  "timeout_seconds": number,
  "reply": string,
  "reason": string
}}
""".strip()

# ================= EVENTS =================
@client.event
async def on_ready():
    print(f"✅ {BOT_NAME} online | v{bot_version()}")
    print(f"Modelo interno: {OPENROUTER_MODEL}")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if client.user not in message.mentions:
        return

    member = message.author
    content = message.content.replace(
        f"<@{client.user.id}>", ""
    ).strip()

    if not content:
        return

    low = content.lower()

    # ===== COMANDOS FIXOS =====
    if "modelo" in low:
        await message.reply(PUBLIC_MODEL_NAME)
        return

    if "versão" in low or "versao" in low:
        await message.reply(f"v{bot_version()}")
        return

    # ===== GRAMÁTICA (FORA DO CHAT GERAL) =====
    if message.channel.id != CHAT_GERAL_ID:
        if absence_grammar(content):
            c = grammar_warnings.get(member.id, 0) + 1
            grammar_warnings[member.id] = c

            if c == 1:
                await message.reply("?")
                return
            elif c == 2:
                await message.reply("Use gramática.")
                return
            else:
                await message.reply("Silêncio, animal.")
                try:
                    await member.timeout(timedelta(minutes=1))
                    await punishment_report(
                        message.channel,
                        member,
                        "Ausência gramatical",
                        60
                    )
                except:
                    pass
                return
    else:
        grammar_warnings.pop(member.id, None)

    # ===== HISTÓRICO =====
    history = user_history.setdefault(
        member.id, deque(maxlen=6)
    )
    history.append(content)

    # ===== IA =====
    system_prompt = build_system_prompt()
    user_prompt = f"""
Histórico:
{chr(10).join(history)}

Mensagem atual:
{content}
""".strip()

    try:
        async with message.channel.typing():
            raw = await call_openrouter(system_prompt, user_prompt)

        js = safe_json(raw)
        if not js:
            await message.reply("Fala direito.")
            return

        decision = json.loads(js)
        action = decision.get("action", "reply")
        timeout_seconds = int(decision.get("timeout_seconds", 0))
        reply = (decision.get("reply") or "").strip()
        reason = decision.get("reason", "Conduta inadequada")

        # ===== FAILSAFE ANTI-MUTE BESTA =====
        casual = ["onde", "mora", "hetero", "idade", "quem é", "vc é"]
        if action == "timeout":
            if any(k in low for k in casual):
                action = "reply"
                timeout_seconds = 0

        # ===== PRESSÃO + MUTE =====
        if action == "timeout":
            w = pressure_warnings.get(member.id, 0) + 1
            pressure_warnings[member.id] = w

            if w == 1:
                await message.reply("Se controla.")
                return

            await message.reply("Silêncio, animal.")
            try:
                await member.timeout(
                    timedelta(seconds=max(60, timeout_seconds))
                )
                await punishment_report(
                    message.channel,
                    member,
                    reason,
                    max(60, timeout_seconds)
                )
            except:
                await message.reply("Sem permissão pra mutar.")
            return

        # ===== RESPOSTA NORMAL =====
        if not reply:
            reply = "?"
        await message.reply(reply)

    except Exception as e:
        print("ERRO:", repr(e))
        await message.reply("Erro interno.")

# ================= START =================
client.run(DISCORD_TOKEN)
