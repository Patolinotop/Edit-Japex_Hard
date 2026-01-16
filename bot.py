import os
import re
import json
import time
import aiohttp
import asyncio
import discord
import logging
from dotenv import load_dotenv
from datetime import timedelta
from typing import Any, Optional

# ================= CONFIG =================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b").strip()
OPENROUTER_MODELS = os.getenv("OPENROUTER_MODELS", "").strip()

VISION_MODEL = os.getenv("VISION_MODEL", "openai/gpt-4o-mini").strip()
ATTACHMENT_TEXT_MODEL = os.getenv("ATTACHMENT_TEXT_MODEL", "openai/gpt-5-nano").strip()
COMMAND_MODEL = os.getenv("COMMAND_MODEL", "openai/gpt-5-nano").strip()

BOT_NAME = "Edit_Japex"
PUBLIC_MODEL_NAME = "Japex Neural Core – Ultimation"

VERSION_MAJOR = 2
VERSION_MINOR = 2  # bump

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

CHAT_GERAL_ID = int(os.getenv("CHAT_GERAL_ID", "1450594073596395548"))

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "360"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.55"))

REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", "45"))
VISION_TIMEOUT_S = int(os.getenv("VISION_TIMEOUT_S", "75"))
EXTRA_TYPING_SECONDS = float(os.getenv("EXTRA_TYPING_SECONDS", "1.2"))

AUTHORIZED_IDS_ENV = os.getenv("AUTHORIZED_IDS", "").strip()
STATE_FILE = os.getenv("STATE_FILE", "admin_state.json")

HIST_MAX = int(os.getenv("HIST_MAX", "8"))
HIST_TTL_S = int(os.getenv("HIST_TTL_S", "900"))

MAX_TEXT_ATTACHMENT_CHARS = int(os.getenv("MAX_TEXT_ATTACHMENT_CHARS", "12000"))

# --- NOVO: contexto recente do canal ---
CHANNEL_HISTORY_SCAN = int(os.getenv("CHANNEL_HISTORY_SCAN", "60"))  # quantas msgs varrer
CONTEXT_PER_USER = int(os.getenv("CONTEXT_PER_USER", "8"))           # quantas msgs por usuário incluir

# --- NOVO: punição por report falso / abuso ---
FALSE_REPORT_TIMEOUT_S = int(os.getenv("FALSE_REPORT_TIMEOUT_S", "60"))
FALSE_REPORT_ESCALATE_S = int(os.getenv("FALSE_REPORT_ESCALATE_S", "300"))

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("bot")

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
        m = guild.get_member(client.user.id) if client.user else None
        return m
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
    return 0.55 + min(len(text) * 0.012, 2.0)

async def reply_soft(message: discord.Message, text: str):
    if not text:
        return
    try:
        async with message.channel.typing():
            await asyncio.sleep(EXTRA_TYPING_SECONDS)
            await asyncio.sleep(typing_delay(text))
        try:
            await message.reply(text)
        except (discord.Forbidden, discord.HTTPException) as e:
            # fallback: send normal message if reply fails
            log.warning("reply_soft: reply failed, fallback send. err=%r", e)
            try:
                await message.channel.send(text)
            except (discord.Forbidden, discord.HTTPException) as e2:
                log.error("reply_soft: send failed too. err=%r", e2)
    except Exception as e:
        log.error("reply_soft: unexpected err=%r", e)

async def send_soft(channel: discord.abc.Messageable, text: str):
    if not text:
        return
    try:
        await channel.send(text)
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("send_soft failed err=%r", e)

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

def bump_violation(uid: int, vtype: str) -> int:
    now = time.time()
    d = user_violation.get(uid)
    if not d or (now - d.get("last_ts", 0) > HIST_TTL_S) or d.get("type") != vtype:
        user_violation[uid] = {"type": vtype, "count": 1, "last_ts": now}
        return 1
    d["count"] += 1
    d["last_ts"] = now
    return int(d["count"])

def parse_duration_seconds(text: str) -> Optional[int]:
    if not text:
        return None
    low = text.lower()
    m = re.search(r"\b(\d{1,5})\s*(s|seg|segs|sec|secs|m|min|mins|h|hr|hrs|hora|horas|d|dia|dias)\b", low)
    if not m:
        return None
    n = int(m.group(1))
    u = m.group(2)
    if u.startswith(("s", "seg", "sec")):
        return n
    if u.startswith(("m", "min")):
        return n * 60
    if u.startswith(("h", "hr", "hora")):
        return n * 3600
    if u.startswith(("d", "dia")):
        return n * 86400
    return None

def is_image_attachment(att: discord.Attachment) -> bool:
    ct = (att.content_type or "").lower()
    fn = (att.filename or "").lower()
    if ct.startswith("image/"):
        return True
    return any(fn.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"])

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
    except Exception as e:
        log.warning("fetch_text_attachment err=%r", e)
        return ""

# ================= CONTEXT (NO KEYWORDS) =================
async def collect_recent_messages_for_users(
    channel: discord.abc.Messageable,
    user_ids: set[int],
    scan_limit: int,
    per_user: int,
) -> dict[int, list[str]]:
    """
    Pega as últimas mensagens do canal e monta um mini-histórico por usuário.
    """
    out: dict[int, list[str]] = {uid: [] for uid in user_ids}
    try:
        if not hasattr(channel, "history"):
            return out

        async for m in channel.history(limit=scan_limit):
            if m.author and int(getattr(m.author, "id", 0)) in user_ids and not getattr(m.author, "bot", False):
                content = (m.content or "").strip()
                if not content and getattr(m, "attachments", None):
                    # registra que tinha anexo
                    content = "[mensagem com anexo]"
                ts = ""
                try:
                    ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    ts = ""
                line = f"{ts} | {m.author.display_name}: {content}"
                out[int(m.author.id)].append(line)

        # channel.history retorna do mais novo pro mais velho,
        # então vamos inverter pra ficar cronológico e cortar.
        for uid in list(out.keys()):
            out[uid] = list(reversed(out[uid]))[-per_user:]
        return out
    except Exception as e:
        log.warning("collect_recent_messages_for_users err=%r", e)
        return out

def evidence_is_in_context(evidence: str, context_blob: str) -> bool:
    ev = (evidence or "").strip()
    if not ev:
        return False
    # match bem simples: substring
    return ev in (context_blob or "")

# ================= OPENROUTER =================
def get_model_payload_fields(default_model: Optional[str] = None) -> dict:
    if default_model:
        return {"model": default_model}
    if OPENROUTER_MODELS:
        models = [m.strip() for m in OPENROUTER_MODELS.split(",") if m.strip()]
        if models:
            return {"models": models, "route": "fallback"}
    return {"model": OPENROUTER_MODEL}

async def call_openrouter(
    system_prompt: str,
    user_content: Any,
    end_user_id: Optional[str] = None,
    model_override: Optional[str] = None,
    force_json: bool = True,
    timeout_s: Optional[int] = None,
) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://railway.app",
        "X-Title": BOT_NAME,
    }

    payload: dict[str, Any] = {
        **get_model_payload_fields(model_override),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    if force_json:
        payload["response_format"] = {"type": "json_object"}
    if end_user_id:
        payload["user"] = str(end_user_id)

    timeout = aiohttp.ClientTimeout(total=int(timeout_s or REQUEST_TIMEOUT_S))

    async def _post(p: dict[str, Any]) -> dict:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=p) as r:
                data = await r.json()
                if r.status >= 400:
                    log.warning("OpenRouter HTTP %s data=%s", r.status, str(data)[:500])
                return data

    data = await _post(payload)

    if isinstance(data, dict) and "error" in data:
        msg = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
        lowered = (msg or "").lower()

        payload2 = dict(payload)
        retried = False

        if "response_format" in lowered or "structured" in lowered:
            payload2.pop("response_format", None)
            retried = True
        if "temperature" in lowered or "sampling" in lowered:
            payload2.pop("temperature", None)
            retried = True

        if retried:
            log.info("OpenRouter retry without some fields. reason=%s", msg)
            data = await _post(payload2)

    if isinstance(data, dict) and "error" in data:
        msg = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
        raise RuntimeError(f"OpenRouter error: {msg}")

    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"OpenRouter bad response: {repr(e)} | data={str(data)[:800]}")

# ================= PROMPTS =================
def build_system_prompt(active_directives: list[str]) -> str:
    directives_block = "\n".join(f"- {x}" for x in active_directives) if active_directives else "(nenhuma)"

    return f"""
Você é {BOT_NAME}. Direto e firme.

IMPORTANTE:
- Você está vendo DUAS pessoas: REPORTER (quem marcou o bot) e ALVO (mensagem respondida).
- O reporter pode estar só conversando, pode estar reportando, ou pode estar mentindo.
- Você DEVE decidir primeiro: isto é um REPORT (moderação) ou é conversa normal.
- Só aplique punição se houver evidência NO TEXTO/CONTEXTO das mensagens recentes.
- Se o reporter acusar sem evidência, puna o REPORTER por "Calúnia (acusação falsa)".
- Se houver evidência, puna o infrator real.
- Se for conversa normal, NUNCA mute ninguém.

EVIDÊNCIA:
- Se action="timeout", você DEVE incluir um campo extra "evidence" com uma citação EXATA (trecho) que aparece no contexto.
- Essa evidência deve ser um trecho literal de uma das mensagens recentes do culpado (reporter ou alvo).
- Não invente evidência.

ESTILO:
- Respostas curtas, frias e objetivas.
- Não faça perguntas.
- Não puxe assunto.
- Não use "?" no reply.

AÇÕES:
- "reply": responder sem punir
- "timeout": punir alguém
- "ignore": não fazer nada

JSON DE SAÍDA (somente um objeto; campos extra permitidos):
{{
  "action": "reply" | "timeout" | "ignore",
  "punish_target": "offender" | "reporter" | "none",
  "timeout_seconds": number,
  "reply": string,
  "reason": string,
  "violation": "none" | "profanity" | "insult" | "hate" | "threat" | "defamation" | "spam" | "impersonation" | "other",
  "evidence": string
}}

Regras:
- Responda SOMENTE com JSON válido (sem markdown).
- timeout_seconds use 60, 300, 3600 ou 86400 quando fizer sentido.
- Se não tiver evidência clara, action="reply" e punish_target="none".

══════════ REGRAS ABSOLUTAS ══════════
{REGRAS_TXT}

══════════ BASE DE DADOS (SUPORTE) ══════════
{DADOS_TXT}

ORDENS ATIVAS DA MODERAÇÃO (memória):
{directives_block}
""".strip()

def build_vision_system_prompt() -> str:
    return """
Você descreve imagens de forma objetiva e curta.

Regras:
- NÃO use JSON.
- NÃO faça perguntas.
- Se parecer montagem/edição/print falso, diga "PARECE EDITADA" ou "NÃO PARECE EDITADA".
- Saída: 3-8 linhas no máximo.
""".strip()

# ================= ACTION HELPERS =================
async def apply_timeout(member: discord.Member, seconds: int) -> bool:
    try:
        seconds = min(max(60, int(seconds)), 86400)
        await member.timeout(timedelta(seconds=seconds))
        return True
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("apply_timeout failed member=%s(%s) seconds=%s err=%r", member.display_name, member.id, seconds, e)
        return False

async def apply_kick(guild: discord.Guild, user: discord.Member, reason: str = "") -> bool:
    try:
        await guild.kick(user, reason=reason or None)
        return True
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("apply_kick failed err=%r", e)
        return False

async def apply_ban(guild: discord.Guild, user: discord.Member, reason: str = "") -> bool:
    try:
        await guild.ban(user, reason=reason or None, delete_message_days=0)
        return True
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("apply_ban failed err=%r", e)
        return False

# ================= MESSAGE TARGETING =================
async def resolve_reference_message(message: discord.Message) -> Optional[discord.Message]:
    try:
        if not message.reference or not message.reference.message_id:
            return None
        if message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
            return message.reference.resolved
        return await message.channel.fetch_message(message.reference.message_id)
    except Exception as e:
        log.info("resolve_reference_message err=%r", e)
        return None

# ================= VISION FLOW =================
async def describe_images_with_vision(image_urls: list[str], context_text: str, end_user_id: str) -> str:
    parts = [{"type": "text", "text": context_text}]
    for url in image_urls[:4]:
        parts.append({"type": "image_url", "image_url": {"url": url}})

    raw = await call_openrouter(
        build_vision_system_prompt(),
        parts,
        end_user_id=end_user_id,
        model_override=VISION_MODEL,
        force_json=False,
        timeout_s=VISION_TIMEOUT_S,
    )
    return (raw or "").strip()

# ================= ADMIN COMMANDS (LOCAL) =================
def get_targets_from_message(message: discord.Message, reply_target: Optional[discord.Member]) -> list[discord.Member]:
    targets = [m for m in message.mentions if isinstance(m, discord.Member) and not m.bot and (client.user is None or m.id != client.user.id)]
    if not targets and reply_target:
        targets = [reply_target]
    return targets

def extract_admin_text(message: discord.Message) -> str:
    if not client.user:
        return (message.content or "").strip()
    return (message.content or "").replace(f"<@{client.user.id}>", "").strip()

async def handle_admin_commands(message: discord.Message, controller: discord.Member, reply_target: Optional[discord.Member]) -> bool:
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
        await reply_soft(message, "...")
        return True

    if any(k in low for k in ["retoma", "resume", "despausa", "volta"]):
        async with state_lock:
            st = load_state_sync()
            st["paused"] = False
            save_state_sync(st)
        await reply_soft(message, "...")
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
            await reply_soft(message, "...")
            return True

    if any(k in low for k in ["designora", "unignore", "volta a responder", "responde de novo"]):
        if targets:
            async with state_lock:
                st = load_state_sync()
                for t in targets:
                    st.get("ignored_user_ids", {}).pop(str(t.id), None)
                save_state_sync(st)
            await reply_soft(message, "...")
            return True

    # timeout / mute
    if any(k in low for k in ["muta", "mute", "timeout", "silencia"]):
        secs = parse_duration_seconds(low) or 300
        ok_any = False
        for t in targets:
            ok_any |= await apply_timeout(t, secs)
        await reply_soft(message, "...")
        return True

    # kick
    if any(k in low for k in ["expulsa", "kick", "chuta"]):
        ok_any = False
        for t in targets:
            ok_any |= await apply_kick(message.guild, t, reason="Admin command")
        await reply_soft(message, "...")
        return True

    # ban
    if any(k in low for k in ["ban", "bane", "banir"]):
        ok_any = False
        for t in targets:
            ok_any |= await apply_ban(message.guild, t, reason="Admin command")
        await reply_soft(message, "...")
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
            await reply_soft(message, "...")
            return True

    if any(k in low for k in ["limpa diretivas", "zera diretivas", "apaga diretivas"]):
        async with state_lock:
            st = load_state_sync()
            st["directives"] = []
            save_state_sync(st)
        await reply_soft(message, "...")
        return True

    return False

# ================= EVENTS =================
@client.event
async def on_ready():
    log.info("✅ %s online | v%s.%s", BOT_NAME, VERSION_MAJOR, VERSION_MINOR)
    if not AUTHORIZED_IDS:
        log.warning("⚠️ Nenhum AUTHORIZED_ID detectado. Configure AUTHORIZED_IDS no .env ou coloque IDs nas REGRAS.")

@client.event
async def on_message(message: discord.Message):
    global bot_busy

    if message.author.bot:
        return

    # só reage quando marcado
    if client.user not in message.mentions:
        return

    # evita concorrência
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

    # alvo por reply
    referenced = await resolve_reference_message(message)
    target_msg = referenced or message

    reply_target_member: Optional[discord.Member] = None
    if referenced and isinstance(referenced.author, discord.Member):
        reply_target_member = referenced.author

    offender = target_msg.author if isinstance(target_msg.author, discord.Member) else controller

    # ===== ADMIN COMMANDS =====
    if is_controller(controller):
        try:
            did_admin = await handle_admin_commands(message, controller, reply_target_member)
            if did_admin:
                return
        except Exception as e:
            log.error("admin_commands err=%r", e)
            return

    bot_busy = True
    try:
        # gramática (só quando falando direto com o bot, não reportando via reply)
        if referenced is None and message.channel.id != CHAT_GERAL_ID and absence_grammar(controller_text):
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            ok = await apply_timeout(controller, 60)
            if ok:
                await reply_soft(message, "Fala direito.")
                await punishment_report(message.channel, controller, "Ausência gramatical", 60)
            return

        # histórico local do controlador
        update_history(controller.id, controller_text)
        if isinstance(offender, discord.Member):
            update_history(offender.id, (target_msg.content or "").strip())

        # diretivas
        async with state_lock:
            st = load_state_sync()
            directives = trim_directives_to_200_words(st.get("directives", []))
            st["directives"] = directives
            save_state_sync(st)

        system_prompt = build_system_prompt(directives)

        # anexos: alvo + controlador
        attachments: list[discord.Attachment] = []
        try:
            attachments.extend(list(getattr(target_msg, "attachments", [])))
        except Exception:
            pass
        try:
            if target_msg.id != message.id:
                attachments.extend(list(getattr(message, "attachments", [])))
        except Exception:
            pass

        image_urls: list[str] = []
        text_blobs: list[str] = []

        for att in attachments:
            if is_image_attachment(att):
                image_urls.append(att.url)
            elif is_text_attachment(att):
                blob = await fetch_text_attachment(att)
                if blob:
                    text_blobs.append(f"Arquivo {att.filename}:\n{blob}")

        roles = roles_for_prompt(controller)
        roles_str = ", ".join(roles) if roles else "(sem cargos)"
        top_roles = ", ".join(roles[:5]) if roles else "(sem cargos)"

        offense_text = (target_msg.content or "").strip()
        reporter_text = controller_text

        # --- NOVO: histórico recente de ambos (no canal) ---
        ctx_users = {int(controller.id)}
        if isinstance(offender, discord.Member):
            ctx_users.add(int(offender.id))

        per_user_logs = await collect_recent_messages_for_users(
            channel=message.channel,
            user_ids=ctx_users,
            scan_limit=CHANNEL_HISTORY_SCAN,
            per_user=CONTEXT_PER_USER
        )

        reporter_recent = "\n".join(per_user_logs.get(int(controller.id), [])) or "(sem histórico recente)"
        offender_recent = ""
        if isinstance(offender, discord.Member):
            offender_recent = "\n".join(per_user_logs.get(int(offender.id), [])) or "(sem histórico recente)"
        else:
            offender_recent = "(alvo não é Member)"

        base_context = (
            f"CONTROLADOR/REPORTER: {controller.display_name} (id {controller.id})\n"
            f"CONTROLADOR top cargos: {top_roles}\n"
            f"CONTROLADOR todos cargos: {roles_str}\n\n"
            f"ALVO (mensagem respondida): {getattr(offender, 'display_name', 'unknown')} (id {getattr(offender, 'id', '0')})\n\n"
            f"MENSAGEM DO ALVO (a mensagem respondida):\n{offense_text}\n\n"
            f"TEXTO DO REPORTER (mensagem atual marcando o bot):\n{reporter_text}\n\n"
            f"HISTÓRICO RECENTE DO REPORTER (canal):\n{reporter_recent}\n\n"
            f"HISTÓRICO RECENTE DO ALVO (canal):\n{offender_recent}\n"
        )

        if text_blobs:
            base_context += "\n\nANEXOS DE TEXTO:\n" + "\n\n".join(text_blobs)

        # ===== ETAPA 1 (IMAGEM): descrição =====
        image_description = ""
        if image_urls:
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            try:
                image_description = await describe_images_with_vision(
                    image_urls=image_urls,
                    context_text=base_context,
                    end_user_id=str(controller.id),
                )
            except Exception as e:
                log.warning("vision err=%r", e)
                image_description = ""

        # ===== ETAPA 2: decisão final =====
        final_user_prompt = base_context
        if image_description:
            final_user_prompt += "\n\nDESCRIÇÃO DA IMAGEM (vision):\n" + image_description

        chosen_model = ATTACHMENT_TEXT_MODEL if text_blobs else None

        async with message.channel.typing():
            await asyncio.sleep(EXTRA_TYPING_SECONDS)

        raw = ""
        try:
            raw = await call_openrouter(
                system_prompt,
                final_user_prompt,
                end_user_id=str(controller.id),
                model_override=chosen_model,
                force_json=True,
                timeout_s=REQUEST_TIMEOUT_S,
            )
        except Exception as e:
            # logs no console, resposta mínima no discord
            log.error("OpenRouter call failed err=%r", e)
            await reply_soft(message, "...")
            return

        js = extract_json_object(raw)
        if not js:
            log.info("Model returned non-json: %s", (raw or "")[:300])
            txt = strip_questions((raw or "").strip())
            await reply_soft(message, txt if txt else "...")
            return

        try:
            d = json.loads(js)
        except Exception as e:
            log.info("JSON parse failed err=%r raw=%s", e, js[:400])
            txt = strip_questions((raw or "").strip())
            await reply_soft(message, txt if txt else "...")
            return

        action = (d.get("action") or "reply").strip().lower()
        punish_target = (d.get("punish_target") or "none").strip().lower()
        reply = strip_questions((d.get("reply") or "").strip())
        reason = (d.get("reason") or "Conduta inadequada").strip()
        violation = (d.get("violation") or "none").strip().lower()
        seconds = int(d.get("timeout_seconds", 0) or 0)
        evidence = (d.get("evidence") or "").strip()

        # --- NOVO: GATE DE EVIDÊNCIA (sem isso não pune) ---
        # junta contexto em um blob pra validar evidence literal
        context_blob = final_user_prompt
        if image_description:
            context_blob += "\n" + image_description

        if action == "timeout":
            # exige evidence literal
            if not evidence or not evidence_is_in_context(evidence, context_blob):
                log.info("Blocked timeout: missing/invalid evidence. punish_target=%s violation=%s", punish_target, violation)

                # se o modelo tentou punir sem evidência, tratamos como report sem base:
                # punição leve no reporter só se isso foi um reply (provável “denúncia”)
                if referenced is not None:
                    streak = bump_violation(controller.id, "defamation")
                    secs = FALSE_REPORT_TIMEOUT_S if streak < 2 else FALSE_REPORT_ESCALATE_S
                    await reply_soft(message, "Acusação sem base.")
                    ok = await apply_timeout(controller, secs)
                    if ok:
                        await punishment_report(message.channel, controller, "Calúnia (acusação falsa)", secs)
                else:
                    # se não era report, só responde normal
                    await reply_soft(message, reply or "...")
                return

        punish_member: Optional[discord.Member] = None
        if punish_target == "reporter":
            punish_member = controller
        elif punish_target == "offender" and isinstance(offender, discord.Member):
            punish_member = offender
        else:
            punish_member = None

        # normaliza seconds
        if seconds <= 0:
            seconds = 60
        seconds = min(max(60, seconds), 86400)

        # se for conversa normal, garante que não puna ninguém
        if action == "reply":
            await reply_soft(message, reply or "...")
            return

        if action == "ignore":
            return

        if action == "timeout":
            if not punish_member:
                log.info("timeout without punish_member. punish_target=%s", punish_target)
                await reply_soft(message, reply or "...")
                return

            # escalonamento leve por reincidência (sem keyword)
            if violation in ["hate", "threat", "spam", "defamation", "insult", "profanity", "impersonation", "other"]:
                streak = bump_violation(punish_member.id, violation)
                if violation == "hate" and streak >= 3:
                    seconds = max(seconds, 86400)
                elif violation in ["threat"] and streak >= 2:
                    seconds = max(seconds, 3600)
                elif streak >= 3:
                    seconds = max(seconds, 300)

            await reply_soft(message, reply or "...")

            ok = await apply_timeout(punish_member, seconds)
            if ok:
                await punishment_report(message.channel, punish_member, reason, seconds)
            else:
                # não spam no discord, só log
                log.warning("timeout failed for %s(%s)", punish_member.display_name, punish_member.id)
            return

        # fallback
        await reply_soft(message, reply or "...")

    except Exception as e:
        log.error("on_message unexpected err=%r", e)
        await reply_soft(message, "...")
    finally:
        bot_busy = False

# ================= START =================
client.run(DISCORD_TOKEN)
