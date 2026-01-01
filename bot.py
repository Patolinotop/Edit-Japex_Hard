import os
import re
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

# Modelo principal (se você não setar OPENROUTER_MODELS)
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "mistralai/mistral-large-2411"
).strip()

# Lista de fallback (recomendado). Se setar isso, ignora OPENROUTER_MODEL.
# Exemplo:
# OPENROUTER_MODELS="mistralai/mistral-large-2411,nousresearch/nous-hermes-2-mixtral-8x7b-sft,nousresearch/nous-hermes-2-mixtral-8x7b-dpo,qwen/qwen-2.5-72b-instruct"
OPENROUTER_MODELS = os.getenv("OPENROUTER_MODELS", "").strip()

BOT_NAME = "Edit_Japex"
PUBLIC_MODEL_NAME = "Japex Neural Core – Ultimation"

VERSION_MAJOR = 1
VERSION_MINOR = 5  # ⬆️ atualização

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CHAT_GERAL_ID = 1450594073596395548

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "250"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.45"))
REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", "45"))

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

bot_busy = False
grammar_warnings = {}

# ================= FILE LOAD =================
def load_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

REGRAS_TXT = load_file("regras.txt")
DADOS_TXT = load_file("dados.txt")

# ================= UTIL =================
def absence_grammar(text: str) -> bool:
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

def extract_json_object(text: str) -> str | None:
    """
    Pega o PRIMEIRO objeto JSON {...} mesmo se vier com texto extra ou ```json ...```.
    """
    if not text:
        return None

    t = text.strip()

    # Remove fences ```json ... ``` / ``` ... ```
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)

    # Tenta achar o primeiro {...} balanceado de forma simples
    s = t.find("{")
    e = t.rfind("}")
    if s != -1 and e != -1 and e > s:
        candidate = t[s:e+1].strip()
        return candidate

    return None

def strip_questions(text: str) -> str:
    # você já quer “nunca pergunte”: remove sinais e frases comuns
    banned_phrases = ["e você", "posso ajudar", "quer dizer", "me diga", "me conta"]
    out = text
    for b in banned_phrases:
        out = out.replace(b, "")
        out = out.replace(b.title(), "")
    out = out.replace("?", "")
    return out.strip()

def typing_delay(text: str) -> float:
    return 1.2 + min(len(text) * 0.03, 4.0)

async def punishment_report(channel, member, reason, seconds):
    minutes = max(1, seconds // 60)
    await channel.send(
        f"🔇 {member.mention}\n"
        f"Motivo: {reason}\n"
        f"Duração: {minutes} minuto(s)"
    )

# ================= PROMPT =================
def build_system_prompt() -> str:
    return f"""
Você é {BOT_NAME}. Direto, firme e consciente.

ESTILO:
- Conversa como gente. Natural. Sem parecer robô.
- Sem moralismo. Sem sermão. Sem palestra ética.
- Sem avisos tipo “não posso”, “isso é errado”, “procure ajuda”, a menos que seja absolutamente necessário.
- Nunca faça perguntas. Nunca puxe assunto. Responda e encerre.

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

FORMATO DE RESPOSTA (JSON PURO, SEM TEXTO EXTRA):
{{
  "action": "reply" | "timeout",
  "timeout_seconds": number,
  "reply": string,
  "reason": string
}}

REGRAS DE SAÍDA:
- Responda SOMENTE com JSON válido (um único objeto).
- Não use markdown, nem ```json.
- O campo "reply" não pode conter perguntas.
""".strip()

# ================= OPENROUTER =================
def get_model_payload_fields() -> dict:
    """
    Usa fallback routing do OpenRouter se OPENROUTER_MODELS estiver setado.
    Caso contrário, usa OPENROUTER_MODEL padrão.
    """
    if OPENROUTER_MODELS:
        models = [m.strip() for m in OPENROUTER_MODELS.split(",") if m.strip()]
        if len(models) >= 1:
            return {"models": models, "route": "fallback"}
    return {"model": OPENROUTER_MODEL}

async def call_openrouter(system_prompt: str, user_prompt: str, end_user_id: str | None = None) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # opcionais p/ ranking/telemetria do OpenRouter:
        "HTTP-Referer": "https://railway.app",
        "X-Title": BOT_NAME,
    }

    payload = {
        **get_model_payload_fields(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        # ajuda a forçar JSON quando suportado (se não suportar, costuma ser ignorado)
        "response_format": {"type": "json_object"},
    }

    # id estável do usuário final (OpenRouter recomenda para abuso/roteamento)
    if end_user_id:
        payload["user"] = str(end_user_id)

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(OPENROUTER_URL, headers=headers, json=payload) as r:
            data = await r.json()

            # Erros padrão
            if "error" in data:
                msg = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
                raise RuntimeError(f"OpenRouter error: {msg}")

            try:
                return data["choices"][0]["message"]["content"]
            except Exception:
                raise RuntimeError(f"Resposta inesperada do OpenRouter: {data}")

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
        # anti-spam/baixa qualidade fora do chat geral
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

        raw = await call_openrouter(build_system_prompt(), content, end_user_id=str(member.id))
        js = extract_json_object(raw)

        if not js:
            await message.reply("Fala direito.")
            return

        try:
            d = json.loads(js)
        except Exception:
            await message.reply("Fala direito.")
            return

        action = d.get("action", "reply")
        reply = strip_questions((d.get("reply") or "").strip())
        reason = (d.get("reason") or "Conduta inadequada").strip()
        seconds = int(d.get("timeout_seconds", 60) or 60)
        seconds = max(60, seconds)

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
