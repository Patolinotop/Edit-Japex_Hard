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
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct")

if not DISCORD_TOKEN or not OPENROUTER_API_KEY:
    raise RuntimeError("DISCORD_TOKEN ou OPENROUTER_API_KEY não definidos")

BOT_NAME = "JapexEvolutionX"
BOT_VERSION = "0.4"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MAX_TOKENS = 200
TEMPERATURE = 0.4

# Apenas para “tom firme” sem virar baixaria/assédio
FIRM_PHRASES = [
    "Vamos manter o respeito.",
    "Sem ataques pessoais.",
    "Fala direito e eu respondo.",
    "Último aviso: sem desrespeito."
]

BRACKET_REGEX = re.compile(r"\[.*?\]")

# Histórico por usuário
user_history = {}  # {user_id: deque([...])}

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

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

def safe_json_extract(text: str):
    """
    Tenta extrair um JSON de dentro do texto (caso o modelo 'vaze' algo).
    """
    text = text.strip()
    # Caso já seja JSON puro:
    if text.startswith("{") and text.endswith("}"):
        return text
    # Tenta achar o primeiro bloco {...}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end+1]
    return None

async def call_openrouter(system_prompt: str, user_prompt: str):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Opcional, mas recomendado pelo OpenRouter:
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
        # Ajuda a forçar formato:
        "response_format": {"type": "json_object"},
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(OPENROUTER_URL, json=payload, headers=headers, timeout=45) as resp:
            data = await resp.json()
            if "choices" not in data:
                raise RuntimeError(data)
            return data["choices"][0]["message"]["content"].strip()

async def apply_timeout(member: discord.Member, seconds: int):
    """
    Aplica timeout. Requer permissões do bot e intents corretos.
    """
    seconds = max(0, min(int(seconds), 60 * 60 * 24))  # até 24h
    if seconds <= 0:
        return
    await member.timeout(timedelta(seconds=seconds))

# ================= PROMPT (IA decide ação) =================
def build_system_prompt(dados: str):
    return f"""
Você é {BOT_NAME}, um moderador conversacional em um servidor do Discord.

Objetivo:
- Responder como uma pessoa real (tom humano).
- Se a mensagem for ofensiva/abusiva/spam/calúnia/assédio: aplicar moderação com timeout proporcional.
- NÃO use xingamentos, humilhação, slurs ou ataques pessoais. Seja firme e direto.

Você deve retornar APENAS um JSON válido com estes campos:
{{
  "action": "reply" | "timeout" | "ignore",
  "timeout_seconds": number,
  "reply": string,
  "reason": string
}}

Regras de moderação (guia, você pode ajustar):
- "ausência gramatical" (mensagem vazia, só caps, só emoji, só '???', etc.): timeout 60s
- "spam" (repetição, flood, menção insistente): timeout 120–600s
- "insulto leve" (ofensa genérica a pessoa): timeout 300–900s
- "assédio / calúnia" (acusação grave sem prova, perseguição): timeout 900–3600s
- "ódio a grupo protegido" / slurs: timeout 3600s (e reply curto)

Resposta:
- Se action="timeout", reply deve ser curto e firme (sem humilhar).
- Se action="reply", responda a pergunta normalmente.
- Se action="ignore", reply pode ser "".

Contexto extra (documentação, só referência):
{dados}
""".strip()

def build_user_prompt(role_name: str, history: deque, content: str):
    hist = "\n".join(history) if history else ""
    return f"""
Cargo/nome do autor (para tom de resposta, sem bajular): {role_name}

Histórico recente do autor:
{hist}

Mensagem atual:
{content}
""".strip()

# ================= EVENTS =================
@client.event
async def on_ready():
    print(f"✅ {BOT_NAME} conectado | v{BOT_VERSION} | model={OPENROUTER_MODEL}")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Só reage quando mencionam o bot
    if client.user not in message.mentions:
        return

    content = message.content.replace(f"<@{client.user.id}>", "").strip()
    if not content:
        return

    member = message.author
    role_name = highest_role(member)

    history = user_history.setdefault(member.id, deque(maxlen=8))
    history.append(content)

    # Comandos básicos locais (não precisa IA)
    low = content.lower()
    if "versão" in low:
        await message.reply(f"Versão atual: {BOT_VERSION}.")
        return
    if "modelo" in low:
        await message.reply(f"Modelo atual: {OPENROUTER_MODEL}.")
        return

    dados = read_dados()
    system_prompt = build_system_prompt(dados)
    user_prompt = build_user_prompt(role_name, history, content)

    try:
        async with message.channel.typing():
            raw = await call_openrouter(system_prompt, user_prompt)

        json_text = safe_json_extract(raw)
        if not json_text:
            # fallback seguro
            await message.reply("Não entendi direito. Reformula sem flood/spam.")
            return

        decision = json.loads(json_text)

        action = decision.get("action", "reply")
        timeout_seconds = int(decision.get("timeout_seconds", 0) or 0)
        reply = (decision.get("reply") or "").strip()

        # Segurança: evita timeouts absurdos por bug de modelo
        timeout_seconds = max(0, min(timeout_seconds, 60 * 60 * 24))

        if action == "timeout":
            # aplica timeout
            try:
                await apply_timeout(member, timeout_seconds)
            except Exception as e:
                # sem permissão? avisa no reply
                if not reply:
                    reply = "Vou moderar isso, mas não tenho permissão pra aplicar timeout aqui."
                else:
                    reply += " (Sem permissão pra timeout.)"

            if not reply:
                reply = "Mensagem fora das regras. Mantém o respeito."
            await message.reply(reply)

        elif action == "ignore":
            # opcionalmente não responder
            if reply:
                await message.reply(reply)
            return

        else:
            # reply normal
            if not reply:
                reply = "Ok."
            # garante ponto final (se você quiser esse estilo)
            if reply[-1] not in ".!?":
                reply += "."
            await message.reply(reply)

    except Exception as e:
        print("❌ ERRO:", repr(e))
        await message.reply("Deu erro aqui. Tenta de novo em alguns segundos.")

# ================= START =================
client.run(DISCORD_TOKEN)
