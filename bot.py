import os
import json
import re
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
    "meta-llama/llama-3.1-8b-instruct"
)

if not DISCORD_TOKEN or not OPENROUTER_API_KEY:
    raise RuntimeError("Variáveis de ambiente não definidas.")

BOT_NAME = "JapexEvolutionX"
BOT_VERSION = "1.0"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

CHAT_GERAL_ID = 1450594073596395548

MAX_TOKENS = 220
TEMPERATURE = 0.4

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

# ================= ESTADO =================
user_history = {}        # user_id -> deque
grammar_warnings = {}    # user_id -> count

# ================= UTIL =================
BRACKET_REGEX = re.compile(r"\[.*?\]")

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
def build_system_prompt(dados):
    return f"""
Você é {BOT_NAME}, moderador automático de um servidor Discord.

FUNÇÃO:
- Conversar normalmente quando não houver infração.
- Aplicar moderação somente em casos claros de abuso.

IMPORTANTE:
- Gramática JÁ É TRATADA FORA DA IA.
- NÃO considere gramática ou mensagens curtas como infração.
- NÃO discuta regras com usuários.
- NÃO faça textão.
- Seja seco, direto e humano.

FORMATO OBRIGATÓRIO (JSON):
{{
  "action": "reply" | "timeout" | "ignore",
  "timeout_seconds": number,
  "reply": string,
  "reason": string
}}

CRITÉRIOS:
- Spam real / flood
- Insulto direto
- Assédio ou calúnia
- Ódio a grupo protegido

Na dúvida, responda normalmente.

DOCUMENTAÇÃO (referência):
{dados}
""".strip()

def build_user_prompt(role_name, history, content):
    hist = "\n".join(history) if history else ""
    return f"""
Autor: {role_name}

Histórico recente:
{hist}

Mensagem atual:
{content}
""".strip()

# ================= EVENTS =================
@client.event
async def on_ready():
    print(f"✅ {BOT_NAME} online | v{BOT_VERSION}")
    print(f"Modelo: {OPENROUTER_MODEL}")

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

    # ===== GRAMÁTICA (FORA DO CHAT GERAL) =====
    if message.channel.id != CHAT_GERAL_ID:
        if absence_grammar(content):
            count = grammar_warnings.get(member.id, 0) + 1
            grammar_warnings[member.id] = count

            if count == 1:
                await message.reply("?")
                return

            elif count == 2:
                await message.reply("Por favor, utilize gramática.")
                return

            else:
                await message.reply("Silêncio, animal.")
                try:
                    await member.timeout(timedelta(minutes=1))
                except:
                    pass
                return

    # reset se escreveu direito
    grammar_warnings.pop(member.id, None)

    # ===== HISTÓRICO =====
    history = user_history.setdefault(
        member.id, deque(maxlen=8)
    )
    history.append(content)

    # ===== COMANDOS SIMPLES =====
    low = content.lower()
    if "versão" in low:
        await message.reply(f"Versão {BOT_VERSION}.")
        return
    if "modelo" in low:
        await message.reply(f"{OPENROUTER_MODEL}.")
        return

    # ===== IA =====
    dados = read_dados()
    system_prompt = build_system_prompt(dados)
    user_prompt = build_user_prompt(
        highest_role(member),
        history,
        content
    )

    try:
        async with message.channel.typing():
            raw = await call_openrouter(
                system_prompt,
                user_prompt
            )

        json_text = safe_json(raw)
        if not json_text:
            await message.reply("Seja mais claro.")
            return

        decision = json.loads(json_text)

        action = decision.get("action", "reply")
        timeout_seconds = int(decision.get("timeout_seconds", 0))
        reply = (decision.get("reply") or "").strip()

        timeout_seconds = max(
            0, min(timeout_seconds, 3600)
        )

        if action == "timeout":
            try:
                await member.timeout(
                    timedelta(seconds=timeout_seconds)
                )
            except:
                reply += " (Sem permissão pra timeout.)"

            if not reply:
                reply = "Comportamento inadequado."
            await message.reply(reply)
            return

        if action == "ignore":
            return

        if not reply:
            reply = "?"
        await message.reply(reply)

    except Exception as e:
        print("ERRO:", repr(e))
        await message.reply("Erro interno.")

# ================= START =================
client.run(DISCORD_TOKEN)
