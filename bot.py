import os
import re
import json
import time
import aiohttp
import asyncio
import discord
from dotenv import load_dotenv
from datetime import timedelta
from typing import Any, Optional

# ================= CONFIG =================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# ===== Provider selection (default: Hugging Face Router) =====
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "hf").strip().lower()  # "hf" or "openrouter"

# Hugging Face Router (OpenAI-compatible)
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_CHAT_URL = os.getenv("HF_CHAT_URL", "https://router.huggingface.co/v1/chat/completions").strip()
HF_COMPLETIONS_URL = os.getenv("HF_COMPLETIONS_URL", "https://router.huggingface.co/v1/completions").strip()

HF_MODEL = (os.getenv("HF_MODEL") or os.getenv("MODEL") or "meta-llama/Meta-Llama-3-8B-Instruct").strip()
HF_MODELS = os.getenv("HF_MODELS", "").strip()  # "modelA,modelB,..."

ATTACHMENT_TEXT_MODEL = os.getenv("ATTACHMENT_TEXT_MODEL", "").strip()

# OpenRouter (opcional)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_URL = os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b").strip()
OPENROUTER_MODELS = os.getenv("OPENROUTER_MODELS", "").strip()

BOT_NAME = "Edit_Japex"
PUBLIC_MODEL_NAME = "Japex Neural Core – Ultimation"

VERSION_MAJOR = 2
VERSION_MINOR = 3

CHAT_GERAL_ID = int(os.getenv("CHAT_GERAL_ID", "1450594073596395548"))

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "320"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.55"))

REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", "45"))
EXTRA_TYPING_SECONDS = float(os.getenv("EXTRA_TYPING_SECONDS", "1.5"))

AUTHORIZED_IDS_ENV = os.getenv("AUTHORIZED_IDS", "").strip()
STATE_FILE = os.getenv("STATE_FILE", "admin_state.json")

HIST_MAX = int(os.getenv("HIST_MAX", "8"))
HIST_TTL_S = int(os.getenv("HIST_TTL_S", "900"))

MAX_TEXT_ATTACHMENT_CHARS = int(os.getenv("MAX_TEXT_ATTACHMENT_CHARS", "12000"))

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

bot_busy = False

user_hist: dict[int, list[tuple[float, str]]] = {}
user_violation: dict[int, dict[str, Any]] = {}

state_lock = asyncio.Lock()

# ================= FILE LOAD =================
def load_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

REGRAS_TXT = load_file("regras.txt")
DADOS_TXT = load_file("dados.txt")

# ================= STATE =================
DEFAULT_STATE = {
    "paused": False,
    "ignored_user_ids": {},
    "directives": []
}

def load_state_sync() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                for k, v in DEFAULT_STATE.items():
                    d.setdefault(k, v)
                return d
    except Exception:
        pass
    return dict(DEFAULT_STATE)

def save_state_sync(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def approx_word_count(s: str) -> int:
    return len(re.findall(r"\S+", s or ""))

def trim_directives_to_200_words(directives: list[str]) -> list[str]:
    out = list(directives)
    while sum(approx_word_count(x) for x in out) > 200 and out:
        out.pop(0)
    return out

# ================= AUTH IDS =================
def extract_authorized_ids_from_regras(regras: str) -> set[int]:
    ids: set[int] = set()
    keywords = ["fundador", "criador", "programador", "administrador", "equipe", "autoriz", "admin", "dono", "owner", "dev"]
    for line in (regras or "").splitlines():
        low = line.lower()
        if any(k in low for k in keywords):
            for m in re.findall(r"\b(\d{17,20})\b", line):
                try:
                    ids.add(int(m))
                except Exception:
                    pass
    return ids

AUTHORIZED_IDS: set[int] = set()
AUTHORIZED_IDS |= extract_authorized_ids_from_regras(REGRAS_TXT)
if AUTHORIZED_IDS_ENV:
    for x in AUTHORIZED_IDS_ENV.split(","):
        x = x.strip()
        if x.isdigit():
            AUTHORIZED_IDS.add(int(x))

def is_authorized(user: discord.abc.User) -> bool:
    return int(user.id) in AUTHORIZED_IDS

def get_bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.get_member(client.user.id) if client.user else None
    except Exception:
        return None

def is_controller(member: discord.Member) -> bool:
    """
    Quem o bot deve obedecer:
    - está na lista (regras/env)
    - ou tem permission admin
    - ou tem cargo acima do bot
    """
    try:
        if is_authorized(member):
            return True
        if getattr(member.guild_permissions, "administrator", False):
            return True
        if member.guild:
            me = get_bot_member(member.guild)
            if me and member.top_role and me.top_role and member.top_role.position > me.top_role.position:
                return True
    except Exception:
        pass
    return False

# ================= UTIL =================
def typing_delay(text: str) -> float:
    return 0.7 + min(len(text) * 0.015, 2.5)

async def reply_soft(message: discord.Message, text: str):
    if not text:
        return
    try:
        async with message.channel.typing():
            await asyncio.sleep(EXTRA_TYPING_SECONDS)
            await asyncio.sleep(typing_delay(text))
        await message.reply(text)
    except (discord.Forbidden, discord.HTTPException):
        return

async def send_soft(channel: discord.abc.Messageable, text: str):
    if not text:
        return
    try:
        await channel.send(text)
    except (discord.Forbidden, discord.HTTPException):
        return

async def punishment_report(channel, member: discord.Member, reason: str, seconds: int):
    minutes = max(1, seconds // 60)
    await send_soft(
        channel,
        f"🔇 {member.mention}\n"
        f"Motivo: {reason}\n"
        f"Duração: {minutes} minuto(s)"
    )

def absence_grammar(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 3:
        return True
    if t.isupper() and len(t) > 4:
        return True
    if not any(c.isalpha() for c in t):
        return True
    if t in ["?", "??", "???"]:
        return True
    return False

def extract_json_object(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    s = t.find("{")
    e = t.rfind("}")
    if s != -1 and e != -1 and e > s:
        return t[s:e+1].strip()
    return None

def strip_questions(text: str) -> str:
    out = (text or "").replace("?", "")
    out = re.sub(r"\b(e você|posso ajudar|me diga|me conta)\b", "", out, flags=re.I)
    return out.strip()

def roles_for_prompt(member: discord.Member) -> list[str]:
    roles_sorted = sorted(
        [r for r in member.roles if r.name != "@everyone"],
        key=lambda r: r.position,
        reverse=True
    )
    return [r.name for r in roles_sorted]

def update_history(uid: int, content: str):
    now = time.time()
    lst = user_hist.get(uid, [])
    lst.append((now, content))
    lst = [(ts, c) for (ts, c) in lst if now - ts <= HIST_TTL_S]
    lst = lst[-HIST_MAX:]
    user_hist[uid] = lst

def detect_exact_repeat_spam(uid: int) -> bool:
    lst = user_hist.get(uid, [])
    if len(lst) < 3:
        return False
    last3 = [c for _, c in lst[-3:]]
    return last3[0] == last3[1] == last3[2] and len(last3[0].strip()) > 0

def detect_emoji_spam(content: str) -> bool:
    t = (content or "").strip()
    if not t:
        return False
    if any(ch.isalnum() for ch in t):
        return False
    if len(t) < 10:
        return False
    if len(set(t)) <= 3:
        return True
    return False

def bump_violation(uid: int, vtype: str) -> int:
    now = time.time()
    d = user_violation.get(uid)
    if not d or (now - d.get("last_ts", 0) > HIST_TTL_S) or d.get("type") != vtype:
        user_violation[uid] = {"type": vtype, "count": 1, "last_ts": now}
        return 1
    d["count"] += 1
    d["last_ts"] = now
    return int(d["count"])

# ✅ FIX: agora reconhece "minuto(s)", "segundo(s)", etc.
def parse_duration_seconds(text: str) -> Optional[int]:
    if not text:
        return None
    low = text.lower()

    # exemplos aceitos:
    # "10 min", "10 minutos", "1 minuto", "30s", "30 segundos", "2h", "2 horas", "1 dia", "3 dias"
    m = re.search(
        r"\b(\d{1,5})\s*"
        r"(s|seg|segs|segundo|segundos|sec|secs|"
        r"m|min|mins|minuto|minutos|"
        r"h|hr|hrs|hora|horas|"
        r"d|dia|dias)\b",
        low
    )
    if not m:
        return None
    n = int(m.group(1))
    u = m.group(2)

    if u.startswith(("s", "seg", "sec")):
        return n
    if u.startswith(("m", "min", "minuto")):
        return n * 60
    if u.startswith(("h", "hr", "hora")):
        return n * 3600
    if u.startswith(("d", "dia")):
        return n * 86400
    return None

def is_text_attachment(att: discord.Attachment) -> bool:
    ct = (att.content_type or "").lower()
    fn = (att.filename or "").lower()
    if ct.startswith("text/"):
        return True
    return any(fn.endswith(ext) for ext in [".txt", ".md", ".log", ".json", ".csv"])

async def fetch_text_attachment(att: discord.Attachment) -> str:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(att.url) as r:
                if r.status != 200:
                    return ""
                raw = await r.text(errors="ignore")
                raw = raw.strip()
                if len(raw) > MAX_TEXT_ATTACHMENT_CHARS:
                    raw = raw[:MAX_TEXT_ATTACHMENT_CHARS] + "\n...[cortado]"
                return raw
    except Exception:
        return ""

# ================= LLM PROVIDER ABSTRACTION =================
def get_candidate_models(default_model: str, models_csv: str, model_override: Optional[str] = None) -> list[str]:
    if model_override and model_override.strip():
        return [model_override.strip()]
    if models_csv:
        xs = [m.strip() for m in models_csv.split(",") if m.strip()]
        if xs:
            return xs
    return [default_model]

def flatten_messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        role = (m.get("role") or "").upper()
        content = m.get("content")
        if isinstance(content, list):
            # multimodal list -> keep only text chunks
            txts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    txts.append(str(item.get("text") or ""))
            content_str = "\n".join([t for t in txts if t])
        else:
            content_str = str(content or "")
        parts.append(f"{role}:\n{content_str}\n")
    parts.append("ASSISTANT:\n")
    return "\n".join(parts).strip()

async def hf_call_chat(messages: list[dict[str, Any]], model: str, response_format: Optional[dict[str, Any]], end_user_id: Optional[str], timeout_s: int) -> str:
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN não configurado.")
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    if end_user_id:
        payload["user"] = str(end_user_id)
    if response_format:
        payload["response_format"] = response_format

    timeout = aiohttp.ClientTimeout(total=int(timeout_s))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(HF_CHAT_URL, headers=headers, json=payload) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                msg = ""
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict):
                        msg = err.get("message", "") or str(err)
                    elif isinstance(err, str):
                        msg = err
                    else:
                        msg = str(data)
                else:
                    msg = str(data)
                raise RuntimeError(f"HF chat error (HTTP {r.status}): {msg}")
            return data["choices"][0]["message"]["content"]

async def hf_call_completions(prompt: str, model: str, timeout_s: int) -> str:
    if not HF_TOKEN:
        raise RuntimeError("HF_TOKEN não configurado.")
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }

    timeout = aiohttp.ClientTimeout(total=int(timeout_s))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(HF_COMPLETIONS_URL, headers=headers, json=payload) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                raise RuntimeError(f"HF completions error (HTTP {r.status}): {data}")
            ch0 = (data.get("choices") or [{}])[0]
            return (ch0.get("text") or "").strip()

async def openrouter_call_chat(messages: list[dict[str, Any]], model: str, response_format: Optional[dict[str, Any]], end_user_id: Optional[str], timeout_s: int) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY não configurado.")
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://railway.app",
        "X-Title": BOT_NAME,
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    if response_format:
        payload["response_format"] = response_format
    if end_user_id:
        payload["user"] = str(end_user_id)

    timeout = aiohttp.ClientTimeout(total=int(timeout_s))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(OPENROUTER_URL, headers=headers, json=payload) as r:
            data = await r.json(content_type=None)
            if isinstance(data, dict) and "error" in data:
                msg = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
                raise RuntimeError(f"OpenRouter error: {msg}")
            return data["choices"][0]["message"]["content"]

async def llm_generate(
    system_prompt: str,
    user_content: Any,
    end_user_id: Optional[str],
    model_override: Optional[str],
    force_json: bool,
    timeout_s: int,
) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    response_format = {"type": "json_object"} if force_json else None

    if LLM_PROVIDER == "openrouter":
        for m in get_candidate_models(OPENROUTER_MODEL, OPENROUTER_MODELS, model_override):
            return await openrouter_call_chat(messages, m, response_format, end_user_id, timeout_s)

    # HF default: chat -> completions
    last_err: Optional[Exception] = None
    for m in get_candidate_models(HF_MODEL, HF_MODELS, model_override):
        try:
            return await hf_call_chat(messages, m, response_format, end_user_id, timeout_s)
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            is_not_chat = ("not a chat model" in msg)
            if not is_not_chat:
                await asyncio.sleep(0.5)

        # completions fallback (router)
        try:
            prompt = flatten_messages_to_prompt(messages)
            return await hf_call_completions(prompt, m, timeout_s)
        except Exception as e:
            last_err = e

    raise RuntimeError(f"Falha ao chamar modelo (chat/completions). Último erro: {last_err!r}")

# ================= PROMPTS =================
def build_system_prompt(admin_mode: bool, active_directives: list[str]) -> str:
    directives_block = "\n".join(f"- {x}" for x in active_directives) if active_directives else "(nenhuma)"

    return f"""
Você é {BOT_NAME}. Moderador firme, objetivo e profissional.

ESTILO:
- Respostas curtas, frias e objetivas, mantenha o máximo de respeito com seus superiores e subalternos; para militares bisonhos que façam perguntas IDIOTAS é permitido utilizar-se de pressão psicológica séria pesada, com grosseria como "Cala boca, Verme." ou "Silêncio, Animal."
- Não faça perguntas. Não puxe assunto.
- Não utilize palavrões, respeito e conduta maximos, a não ser que o militar cometa algum delito, assim sendo permitido xingamentos e pressão psicologica direcionada ao usuario.

MODERAÇÃO (decida pelo conteúdo do ALVO):
- Se for conversa normal/pergunta: responda curto, tenha opiniões pessoais e politicas.
- Se for xingamento direcionado ou palavrão para alguém: timeout curto.
- Se for ameaça séria/velada: timeout mais alto.
- Se for calúnia/difamação séria (acusação forte sem prova): timeout curto e motivo "Calúnia".
- Discurso de ódio: mande parar e aplique punição apenas se insistir.
- Spam: repetição idêntica / flood -> timeout 5 min.
- Se houver IMAGEM: descreva o que aparece e avalie se a imagem parece editada. Se for claramente montagem usada pra acusar alguém, trate como "defamation" e sugira punir o REPORTER (punish_target="reporter").
- Se alguém cometer algum delito, permissão total para utilizar de pressão psicológica extrema, direcionada ao usuário, o fazendo questionar de sua vida e existência. Lembre-se, todos são militares(alguns civis), mas todos tem chance de serem bons, se fizerão cagada, a culpa é total deles, você está encarregado de punir.
- Se tiver descrição de IMAGEM: use isso como evidência extra (não invente).

JSON DE SAÍDA (somente um objeto):
{{
  "action": "reply" | "timeout" | "ignore",
  "punish_target": "offender" | "reporter" | "none",
  "timeout_seconds": number,
  "reply": string,
  "reason": string,
  "violation": "none" | "profanity" | "insult" | "hate" | "threat" | "defamation" | "spam" | "impersonation" | "other"
}}

REGRAS:
- Responda SOMENTE com JSON válido (sem markdown). Se não conseguir, responda curto sem JSON.
- "reply" não pode ter perguntas nem "?".
- timeout_seconds use 60, 300, 3600 ou 86400 quando fizer sentido.

══════════ REGRAS ABSOLUTAS ══════════
{REGRAS_TXT}

══════════ BASE DE DADOS (SUPORTE) ══════════
{DADOS_TXT}

ORDENS ATIVAS DA MODERAÇÃO (memória):
{directives_block}
""".strip()

# ================= ACTION HELPERS =================
def can_timeout(botm: discord.Member, target: discord.Member) -> tuple[bool, str]:
    if not getattr(botm.guild_permissions, "moderate_members", False):
        return False, "Sem permissão de Moderar Membros."
    if target.guild.owner_id == target.id:
        return False, "Não posso punir o dono do servidor."
    if target.id == botm.id:
        return False, "Não posso punir o próprio bot."
    if target.top_role and botm.top_role and target.top_role.position >= botm.top_role.position:
        return False, "Alvo acima ou igual ao cargo do bot."
    return True, ""

async def apply_timeout(member: discord.Member, seconds: int) -> tuple[bool, str]:
    try:
        seconds = min(max(60, int(seconds)), 86400)
        botm = get_bot_member(member.guild)
        if not botm:
            return False, "Falha ao localizar o membro do bot no servidor."
        ok, why = can_timeout(botm, member)
        if not ok:
            return False, why
        await member.timeout(timedelta(seconds=seconds))
        return True, ""
    except (discord.Forbidden, discord.HTTPException):
        return False, "Discord bloqueou a ação (permissão/erro HTTP)."
    except Exception:
        return False, "Erro interno ao aplicar timeout."

async def clear_timeout(member: discord.Member) -> tuple[bool, str]:
    try:
        botm = get_bot_member(member.guild)
        if not botm:
            return False, "Falha ao localizar o membro do bot no servidor."
        # precisa de permissão e hierarquia também
        ok, why = can_timeout(botm, member)
        if not ok:
            return False, why
        await member.timeout(None)
        return True, ""
    except (discord.Forbidden, discord.HTTPException):
        return False, "Discord bloqueou a ação (permissão/erro HTTP)."
    except Exception:
        return False, "Erro interno ao remover timeout."

def removable_roles_for_target(target: discord.Member) -> list[discord.Role]:
    guild = target.guild
    botm = get_bot_member(guild)
    if not botm:
        return []
    if not getattr(botm.guild_permissions, "manage_roles", False):
        return []

    bot_top = botm.top_role.position
    out: list[discord.Role] = []
    for r in target.roles:
        if r.name == "@everyone":
            continue
        if r.managed:
            continue
        if r.position >= bot_top:
            continue
        out.append(r)
    return out

async def remove_roles_all(target: discord.Member) -> tuple[bool, str]:
    roles = removable_roles_for_target(target)
    if not roles:
        return False, "Sem cargos removíveis (ou sem permissão/hierarquia)."
    try:
        await target.remove_roles(*roles, reason="Admin: remove all roles")
        return True, ""
    except (discord.Forbidden, discord.HTTPException):
        return False, "Discord bloqueou (permissão/hierarquia)."
    except Exception:
        return False, "Erro interno removendo cargos."

async def add_roles(target: discord.Member, roles: list[discord.Role]) -> tuple[bool, str]:
    if not roles:
        return False, "Nenhum cargo mencionado."
    try:
        botm = get_bot_member(target.guild)
        if not botm or not getattr(botm.guild_permissions, "manage_roles", False):
            return False, "Sem permissão de Gerenciar Cargos."
        # filtra cargos que o bot pode mexer
        bot_top = botm.top_role.position
        safe = [r for r in roles if (not r.managed) and r.position < bot_top and r.name != "@everyone"]
        if not safe:
            return False, "Não posso adicionar esses cargos (hierarquia/managed)."
        await target.add_roles(*safe, reason="Admin: add roles")
        return True, ""
    except (discord.Forbidden, discord.HTTPException):
        return False, "Discord bloqueou (permissão/hierarquia)."
    except Exception:
        return False, "Erro interno adicionando cargos."

async def remove_roles(target: discord.Member, roles: list[discord.Role]) -> tuple[bool, str]:
    if not roles:
        return False, "Nenhum cargo mencionado."
    try:
        botm = get_bot_member(target.guild)
        if not botm or not getattr(botm.guild_permissions, "manage_roles", False):
            return False, "Sem permissão de Gerenciar Cargos."
        bot_top = botm.top_role.position
        safe = [r for r in roles if (not r.managed) and r.position < bot_top and r.name != "@everyone"]
        if not safe:
            return False, "Não posso remover esses cargos (hierarquia/managed)."
        await target.remove_roles(*safe, reason="Admin: remove roles")
        return True, ""
    except (discord.Forbidden, discord.HTTPException):
        return False, "Discord bloqueou (permissão/hierarquia)."
    except Exception:
        return False, "Erro interno removendo cargos."

# ================= MESSAGE TARGETING =================
async def resolve_reference_message(message: discord.Message) -> Optional[discord.Message]:
    try:
        if not message.reference or not message.reference.message_id:
            return None
        if message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
            return message.reference.resolved
        return await message.channel.fetch_message(message.reference.message_id)
    except Exception:
        return None

def extract_admin_text(message: discord.Message) -> str:
    if not client.user:
        return (message.content or "").strip()
    return (message.content or "").replace(f"<@{client.user.id}>", "").strip()

def get_targets_from_message(message: discord.Message, reply_target: Optional[discord.Member]) -> list[discord.Member]:
    targets = [m for m in message.mentions if isinstance(m, discord.Member) and not m.bot and (client.user is None or m.id != client.user.id)]
    if not targets and reply_target:
        targets = [reply_target]
    return targets

def has_word(low: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", low) is not None

def is_unmute_command(low: str) -> bool:
    # ✅ FIX: não confundir "desmutar" com "muta"
    keys = ["desmuta", "desmutar", "unmute", "remove timeout", "tirar timeout", "retira timeout", "desilencia", "des-silencia"]
    return any(k in low for k in keys)

def is_mute_command(low: str) -> bool:
    # palavra inteira (evita pegar "desmutar" como "muta")
    return any(has_word(low, w) for w in ["muta", "mute", "timeout", "silencia", "silencie"])

# ================= ADMIN COMMANDS (LOCAL + ROBUSTO) =================
async def handle_admin_commands(message: discord.Message, controller: discord.Member, reply_target: Optional[discord.Member]) -> bool:
    """
    Admin commands SEM depender do modelo.
    Corrigido:
    - duração com "minuto(s)"
    - desmutar (remove timeout)
    - cargos (add/remove/reset)
    """
    if not message.guild:
        return False

    text = extract_admin_text(message)
    low = text.lower()

    # pause/resume
    if any(k in low for k in ["pausa", "pause", "pausar bot", "silencia bot"]):
        async with state_lock:
            st = load_state_sync()
            st["paused"] = True
            save_state_sync(st)
        await reply_soft(message, "Ok.")
        return True

    if any(k in low for k in ["retoma", "resume", "despausa", "volta"]):
        async with state_lock:
            st = load_state_sync()
            st["paused"] = False
            save_state_sync(st)
        await reply_soft(message, "Ok.")
        return True

    targets = get_targets_from_message(message, reply_target)

    # ignore/unignore
    if any(k in low for k in ["ignora", "não responde", "nao responde", "pare de responder"]):
        if targets:
            async with state_lock:
                st = load_state_sync()
                for t in targets:
                    st.setdefault("ignored_user_ids", {})[str(t.id)] = {"until": 0}
                save_state_sync(st)
            await reply_soft(message, "Ok.")
        else:
            await reply_soft(message, "Para ignorar, responda a mensagem do alvo ou mencione o alvo.")
        return True

    if any(k in low for k in ["designora", "unignore", "volta a responder", "responde de novo"]):
        if targets:
            async with state_lock:
                st = load_state_sync()
                for t in targets:
                    st.get("ignored_user_ids", {}).pop(str(t.id), None)
                save_state_sync(st)
            await reply_soft(message, "Ok.")
        else:
            await reply_soft(message, "Para designorar, responda a mensagem do alvo ou mencione o alvo.")
        return True

    # ✅ DESMUTAR PRIMEIRO (antes do mute)
    if is_unmute_command(low):
        if not targets:
            await reply_soft(message, "Para desmutar, responda a mensagem do alvo ou mencione o alvo.")
            return True
        ok_any = False
        last_fail = ""
        for t in targets:
            ok, why = await clear_timeout(t)
            ok_any |= ok
            if not ok:
                last_fail = why
        if ok_any:
            await reply_soft(message, "Feito.")
        else:
            await reply_soft(message, f"Não consegui desmutar. {last_fail}".strip())
        return True

    # ===== CARGOS =====
    role_mentions: list[discord.Role] = list(getattr(message, "role_mentions", []))

    # resetar/limpar cargos
    if any(k in low for k in ["remove os cargos", "remova os cargos", "tira os cargos", "tirar os cargos", "limpa cargos", "resetar cargos"]):
        if not targets:
            await reply_soft(message, "Para limpar cargos, responda a mensagem do alvo ou mencione o alvo.")
            return True
        ok_any = False
        last_fail = ""
        for t in targets:
            ok, why = await remove_roles_all(t)
            ok_any |= ok
            if not ok:
                last_fail = why
        if ok_any:
            await reply_soft(message, "Feito.")
        else:
            await reply_soft(message, f"Não consegui limpar cargos. {last_fail}".strip())
        return True

    # remover cargos específicos
    if role_mentions and any(k in low for k in ["tira", "remova", "remove", "remover", "tirar cargo", "remove cargo", "remova cargo"]):
        if not targets:
            await reply_soft(message, "Para remover cargo, responda a mensagem do alvo ou mencione o alvo.")
            return True
        ok_any = False
        last_fail = ""
        for t in targets:
            ok, why = await remove_roles(t, role_mentions)
            ok_any |= ok
            if not ok:
                last_fail = why
        if ok_any:
            await reply_soft(message, "Feito.")
        else:
            await reply_soft(message, f"Não consegui remover cargo. {last_fail}".strip())
        return True

    # adicionar cargos específicos
    if role_mentions and any(k in low for k in ["dá cargo", "da cargo", "dar cargo", "adiciona", "adicionar", "add cargo", "promove", "seta cargo"]):
        if not targets:
            await reply_soft(message, "Para dar cargo, responda a mensagem do alvo ou mencione o alvo.")
            return True
        ok_any = False
        last_fail = ""
        for t in targets:
            ok, why = await add_roles(t, role_mentions)
            ok_any |= ok
            if not ok:
                last_fail = why
        if ok_any:
            await reply_soft(message, "Feito.")
        else:
            await reply_soft(message, f"Não consegui dar cargo. {last_fail}".strip())
        return True

    # ===== MUTE/TIMEOUT =====
    if is_mute_command(low):
        if not targets:
            await reply_soft(message, "Para punir, responda a mensagem do alvo ou mencione o alvo.")
            return True

        # ✅ FIX: agora "10 minutos" funciona
        secs = parse_duration_seconds(low) or 300

        ok_any = False
        last_fail = ""
        for t in targets:
            ok, why = await apply_timeout(t, secs)
            ok_any |= ok
            if not ok:
                last_fail = why

        if ok_any:
            await reply_soft(message, f"Feito ({secs}s).")
        else:
            await reply_soft(message, f"Não consegui punir. {last_fail}".strip())
        return True

    # diretivas (memória)
    if any(k in low for k in ["diretiva:", "ordem:", "memoriza:"]):
        parts = text.split(":", 1)
        directive = parts[1].strip() if len(parts) > 1 else ""
        if directive:
            async with state_lock:
                st = load_state_sync()
                st.setdefault("directives", []).append(directive)
                st["directives"] = trim_directives_to_200_words(st["directives"])
                save_state_sync(st)
            await reply_soft(message, "Ok.")
        else:
            await reply_soft(message, "Diretiva vazia ignorada.")
        return True

    if any(k in low for k in ["limpa diretivas", "zera diretivas", "apaga diretivas"]):
        async with state_lock:
            st = load_state_sync()
            st["directives"] = []
            save_state_sync(st)
        await reply_soft(message, "Ok.")
        return True

    return False

# ================= EVENTS =================
@client.event
async def on_ready():
    print(f"✅ {BOT_NAME} online | v{VERSION_MAJOR}.{VERSION_MINOR}")
    print(f"⚙️ Provider: {LLM_PROVIDER}")
    print(f"🧠 HF_MODEL: {HF_MODEL}")
    if not AUTHORIZED_IDS:
        print("⚠️ Nenhum AUTHORIZED_ID detectado. Configure AUTHORIZED_IDS no .env ou coloque IDs nas REGRAS.")
    if LLM_PROVIDER == "hf" and not HF_TOKEN:
        print("⚠️ HF_TOKEN não configurado. IA não funcionará.")
    if LLM_PROVIDER == "openrouter" and not OPENROUTER_API_KEY:
        print("⚠️ OPENROUTER_API_KEY não configurado. IA não funcionará.")

@client.event
async def on_message(message: discord.Message):
    global bot_busy

    if message.author.bot:
        return

    # só reage quando marcado
    if client.user not in message.mentions:
        return

    if bot_busy:
        return

    if not isinstance(message.author, discord.Member):
        return

    controller = message.author
    guild = message.guild
    if not guild:
        return

    controller_text = extract_admin_text(message)
    low = controller_text.lower()

    # comandos simples
    if "modelo" in low:
        await reply_soft(message, PUBLIC_MODEL_NAME)
        return
    if "versão" in low or "versao" in low:
        await reply_soft(message, f"v{VERSION_MAJOR}.{VERSION_MINOR}")
        return

    # carrega estado
    async with state_lock:
        state = load_state_sync()

    # pausado => só controller
    if state.get("paused") and not is_controller(controller):
        return

    # ignore list (para quem pinga o bot)
    ignored = state.get("ignored_user_ids", {}).get(str(controller.id))
    if ignored:
        until = ignored.get("until", 0)
        if until == 0 or time.time() < float(until):
            return
        else:
            async with state_lock:
                st = load_state_sync()
                st.get("ignored_user_ids", {}).pop(str(controller.id), None)
                save_state_sync(st)

    referenced = await resolve_reference_message(message)

    reply_target_member: Optional[discord.Member] = None
    if referenced and isinstance(referenced.author, discord.Member):
        reply_target_member = referenced.author

    # ===== ADMIN COMMANDS (SEM IA) =====
    if is_controller(controller):
        try:
            did_admin = await handle_admin_commands(message, controller, reply_target_member)
            if did_admin:
                return
        except Exception:
            return

    bot_busy = True
    try:
        # gramática (só quando falando direto com o bot, não reportando via reply)
        if referenced is None and message.channel.id != CHAT_GERAL_ID and absence_grammar(controller_text):
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            ok, why = await apply_timeout(controller, 60)
            if ok:
                await reply_soft(message, "Escreva com clareza.")
                await punishment_report(message.channel, controller, "Ausência gramatical", 60)
            else:
                await reply_soft(message, f"Não consegui aplicar punição. {why}".strip())
            return

        # spam local do controlador
        update_history(controller.id, controller_text)
        if detect_exact_repeat_spam(controller.id):
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            ok, why = await apply_timeout(controller, 300)
            if ok:
                await reply_soft(message, "Chega.")
                await punishment_report(message.channel, controller, "Spam (repetição)", 300)
            else:
                await reply_soft(message, f"Não consegui aplicar punição. {why}".strip())
            return

        if detect_emoji_spam(controller_text):
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            ok, why = await apply_timeout(controller, 60)
            if ok:
                await reply_soft(message, "Pare.")
                await punishment_report(message.channel, controller, "Spam de símbolos", 60)
            else:
                await reply_soft(message, f"Não consegui aplicar punição. {why}".strip())
            return

        # diretivas
        async with state_lock:
            st = load_state_sync()
            directives = trim_directives_to_200_words(st.get("directives", []))
            st["directives"] = directives
            save_state_sync(st)

        admin_mode = is_controller(controller)
        system_prompt = build_system_prompt(admin_mode, directives)

        # alvo para o modelo (nunca o admin por padrão)
        offender: Optional[discord.Member] = None
        if reply_target_member and not reply_target_member.bot:
            offender = reply_target_member
        else:
            mentioned_targets = [m for m in message.mentions if isinstance(m, discord.Member) and not m.bot and (client.user is None or m.id != client.user.id)]
            if mentioned_targets:
                offender = mentioned_targets[0]

        offense_text = (referenced.content or "").strip() if referenced else ""

        roles = roles_for_prompt(controller)
        roles_str = ", ".join(roles) if roles else "(sem cargos)"
        top_roles = ", ".join(roles[:5]) if roles else "(sem cargos)"

        base_context = (
            f"CONTROLADOR: {controller.display_name} (id {controller.id})\n"
            f"CONTROLADOR top cargos: {top_roles}\n"
            f"CONTROLADOR todos cargos: {roles_str}\n\n"
        )

        if offender:
            base_context += (
                f"ALVO (possível infrator): {offender.display_name} (id {offender.id})\n"
                f"MENSAGEM DO ALVO:\n{offense_text}\n\n"
            )
        else:
            base_context += "ALVO: (nenhum)\nMENSAGEM DO ALVO:\n(sem referência)\n\n"

        base_context += f"TEXTO DO CONTROLADOR:\n{controller_text}\n"

        # anexos de texto
        attachments: list[discord.Attachment] = []
        try:
            if referenced:
                attachments.extend(list(getattr(referenced, "attachments", [])))
        except Exception:
            pass
        try:
            attachments.extend(list(getattr(message, "attachments", [])))
        except Exception:
            pass

        text_blobs: list[str] = []
        for att in attachments:
            if is_text_attachment(att):
                blob = await fetch_text_attachment(att)
                if blob:
                    text_blobs.append(f"Arquivo {att.filename}:\n{blob}")

        if text_blobs:
            base_context += "\n\nANEXOS DE TEXTO:\n" + "\n\n".join(text_blobs)

        chosen_model = ATTACHMENT_TEXT_MODEL if (text_blobs and ATTACHMENT_TEXT_MODEL) else None

        async with message.channel.typing():
            await asyncio.sleep(EXTRA_TYPING_SECONDS)
            raw = await llm_generate(
                system_prompt=system_prompt,
                user_content=base_context,
                end_user_id=str(controller.id),
                model_override=chosen_model,
                force_json=True,
                timeout_s=REQUEST_TIMEOUT_S,
            )

        js = extract_json_object(raw)
        if not js:
            txt = strip_questions((raw or "").strip())
            await reply_soft(message, txt if txt else "...")
            return

        try:
            d = json.loads(js)
        except Exception:
            txt = strip_questions((raw or "").strip())
            await reply_soft(message, txt if txt else "...")
            return

        action = (d.get("action") or "reply").strip().lower()
        reply = strip_questions((d.get("reply") or "").strip())

        if action == "ignore":
            return

        await reply_soft(message, reply or "Ok.")

    except Exception as e:
        print("ERRO:", repr(e))
        await reply_soft(message, "Erro interno.")
    finally:
        bot_busy = False

# ================= START =================
client.run(DISCORD_TOKEN)
