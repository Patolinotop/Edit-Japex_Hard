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

BOT_NAME = "Edit_Japex"
PUBLIC_MODEL_NAME = "Japex Neural Core – Ultimation"

VERSION_MAJOR = 2
VERSION_MINOR = 6

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

CHAT_GERAL_ID = int(os.getenv("CHAT_GERAL_ID", "1450594073596395548"))

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "520"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.55"))

REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", "45"))
VISION_TIMEOUT_S = int(os.getenv("VISION_TIMEOUT_S", "75"))
EXTRA_TYPING_SECONDS = float(os.getenv("EXTRA_TYPING_SECONDS", "1.0"))

AUTHORIZED_IDS_ENV = os.getenv("AUTHORIZED_IDS", "").strip()
STATE_FILE = os.getenv("STATE_FILE", "admin_state.json")

HIST_TTL_S = int(os.getenv("HIST_TTL_S", "900"))
MAX_TEXT_ATTACHMENT_CHARS = int(os.getenv("MAX_TEXT_ATTACHMENT_CHARS", "12000"))

CHANNEL_HISTORY_SCAN = int(os.getenv("CHANNEL_HISTORY_SCAN", "80"))
CONTEXT_PER_USER = int(os.getenv("CONTEXT_PER_USER", "10"))

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("bot")
discord.utils.setup_logging(level=logging.INFO)

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

bot_busy = False
state_lock = asyncio.Lock()
user_violation: dict[int, dict[str, Any]] = {}

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
    return 0.45 + min(len(text) * 0.010, 1.6)

async def reply_soft(message: discord.Message, text: str):
    if not text:
        text = "..."
    try:
        async with message.channel.typing():
            await asyncio.sleep(EXTRA_TYPING_SECONDS)
            await asyncio.sleep(typing_delay(text))
        try:
            await message.reply(text)
        except Exception as e:
            log.warning("reply failed, fallback send err=%r", e)
            await message.channel.send(text)
    except Exception as e:
        log.error("reply_soft err=%r", e)

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

def sanitize_reply(text: str) -> str:
    t = strip_questions((text or "").strip())
    t = re.sub(
        r"\b(como|quando|onde|qual|quais|quem|porque|por que|pq|oque|o que)\b\s*$",
        "",
        t,
        flags=re.I
    ).strip()
    t = re.sub(r"[:\-–—]\s*$", "", t).strip()
    return t or "..."

def bump_violation(uid: int, vtype: str) -> int:
    now = time.time()
    d = user_violation.get(uid)
    if not d or (now - d.get("last_ts", 0) > HIST_TTL_S) or d.get("type") != vtype:
        user_violation[uid] = {"type": vtype, "count": 1, "last_ts": now}
        return 1
    d["count"] += 1
    d["last_ts"] = now
    return int(d["count"])

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

async def collect_recent_messages_for_users(
    channel: discord.abc.Messageable,
    user_ids: set[int],
    scan_limit: int,
    per_user: int,
) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {uid: [] for uid in user_ids}
    try:
        if not hasattr(channel, "history"):
            return out
        async for m in channel.history(limit=scan_limit):
            if not m.author or getattr(m.author, "bot", False):
                continue
            uid = int(getattr(m.author, "id", 0))
            if uid not in user_ids:
                continue
            content = (m.content or "").strip()
            if not content and getattr(m, "attachments", None):
                content = "[mensagem com anexo]"
            try:
                ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = ""
            out[uid].append(f"{ts} | {m.author.display_name}: {content}")
        for uid in out:
            out[uid] = list(reversed(out[uid]))[-per_user:]
        return out
    except Exception as e:
        log.warning("collect_recent_messages_for_users err=%r", e)
        return out

# ================= CONTENT ANALYSIS (ALVO) =================
# Isso NÃO é keyword de acusação. É análise do conteúdo real do alvo.
def count_links(text: str) -> int:
    return len(re.findall(r"https?://\S+", text or "", flags=re.I))

def count_mentions(text: str) -> int:
    return (text or "").count("<@")

def looks_like_emoji_flood(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if not any(ch.isalnum() for ch in t) and len(t) >= 18:
        if len(set(t)) <= 4:
            return True
    return False

def looks_like_repeat_flood(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 40:
        return False
    if re.search(r"(.{6,})\1\1", t):
        return True
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if len(lines) >= 6 and len(set(lines)) <= 2:
        return True
    return False

def looks_like_direct_insult(text: str) -> bool:
    # heurística mínima: xingamento + alvo (menção ou "vc/você")
    t = (text or "").lower()
    if "<@" not in t and "vc" not in t and "você" not in t and "voce" not in t:
        return False
    # lista curta de xingamentos comuns (não é "palavra-chave de acusação"; é detectar ofensa real)
    bad = ["idiota", "imbecil", "burro", "lixo", "verme", "animal", "otário", "otario", "arrombado", "vagabundo", "corno"]
    return any(b in t for b in bad)

def looks_like_threat(text: str) -> bool:
    # heurística mínima: verbo violento + alvo (menção/vc/você)
    t = (text or "").lower()
    if "<@" not in t and "vc" not in t and "você" not in t and "voce" not in t:
        return False
    verbs = ["matar", "te matar", "espancar", "te pegar", "te arrebentar", "dar um tiro", "atirar", "vou te"]
    return any(v in t for v in verbs)

def offense_signal(offense_text: str, offender_recent: str) -> bool:
    blob = (offense_text or "") + "\n" + (offender_recent or "")
    # spam/flood
    if count_links(blob) >= 2:
        return True
    if count_mentions(blob) >= 4:
        return True
    if looks_like_emoji_flood(blob):
        return True
    if looks_like_repeat_flood(blob):
        return True
    # conduta agressiva
    if looks_like_direct_insult(blob):
        return True
    if looks_like_threat(blob):
        return True
    return False

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

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(OPENROUTER_URL, headers=headers, json=payload) as r:
            data = await r.json()
            if isinstance(data, dict) and "error" in data:
                msg = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
                raise RuntimeError(f"OpenRouter error: {msg}")
            return data["choices"][0]["message"]["content"]

# ================= PROMPTS =================
def build_chat_system_prompt() -> str:
    return f"""
Você é {BOT_NAME}. Responda curto e objetivo.

REGRAS:
- 1 a 2 linhas.
- Não fale sobre evidência/violação/moderação/punição.
- Não faça perguntas, não use "?".
""".strip()

def build_moderation_system_prompt(directives: list[str]) -> str:
    directives_block = "\n".join(f"- {x}" for x in directives) if directives else "(nenhuma)"
    return f"""
Você é {BOT_NAME}. Isso é um REPORT (denúncia real). Decida punição corretamente.

Regras:
- Analise o CONTEXTO do ALVO (mensagem + histórico recente).
- Se o report for mentira, puna o REPORTER por calúnia.
- Se for verdade, puna o ALVO.
- Puna SOMENTE se houver evidência literal no contexto.
- Se punir, "evidence" precisa ser um trecho EXATO que aparece no contexto.

Saída JSON:
{{
  "action": "reply" | "timeout" | "ignore",
  "punish_target": "offender" | "reporter" | "none",
  "timeout_seconds": number,
  "reply": string,
  "reason": string,
  "violation": "none" | "profanity" | "insult" | "hate" | "threat" | "defamation" | "spam" | "impersonation" | "other",
  "evidence": string
}}

Responda SOMENTE JSON válido. Sem markdown.

ORDENS:
{directives_block}

REGRAS ABSOLUTAS:
{REGRAS_TXT}
""".strip()

def build_repair_system_prompt() -> str:
    return """
Você vai REPARAR uma saída JSON.

Regras:
- Devolva SOMENTE um JSON válido no formato exigido.
- Não invente evidência. "evidence" precisa existir literalmente no contexto fornecido.
""".strip()

# ================= ACTION HELPERS =================
async def apply_timeout(member: discord.Member, seconds: int) -> bool:
    try:
        seconds = min(max(60, int(seconds)), 86400)
        await member.timeout(timedelta(seconds=seconds))
        return True
    except discord.Forbidden as e:
        log.warning("apply_timeout FORBIDDEN member=%s(%s) seconds=%s err=%r", member.display_name, member.id, seconds, e)
        return False
    except discord.HTTPException as e:
        log.warning("apply_timeout HTTPException member=%s(%s) seconds=%s err=%r", member.display_name, member.id, seconds, e)
        return False
    except Exception as e:
        log.error("apply_timeout unexpected err=%r", e)
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

    if client.user not in message.mentions:
        return

    if bot_busy:
        return

    if not isinstance(message.author, discord.Member):
        return

    if not message.guild:
        return

    controller = message.author
    controller_text = (message.content or "").replace(f"<@{client.user.id}>", "").strip()
    low = controller_text.lower()

    if "modelo" in low:
        await reply_soft(message, PUBLIC_MODEL_NAME)
        return
    if "versão" in low or "versao" in low:
        await reply_soft(message, f"v{VERSION_MAJOR}.{VERSION_MINOR}")
        return

    async with state_lock:
        state = load_state_sync()

    if state.get("paused") and not is_controller(controller):
        return

    bot_busy = True
    t0 = time.time()

    try:
        referenced = await resolve_reference_message(message)

        # reply em mensagem do bot => CHAT
        if referenced and referenced.author and getattr(referenced.author, "bot", False):
            referenced = None

        target_msg = referenced or message

        offender: Optional[discord.Member] = None
        if referenced and isinstance(target_msg.author, discord.Member) and not target_msg.author.bot:
            offender = target_msg.author

        async with state_lock:
            st = load_state_sync()
            directives = trim_directives_to_200_words(st.get("directives", []))
            st["directives"] = directives
            save_state_sync(st)

        # anexos de texto
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

        text_blobs: list[str] = []
        for att in attachments:
            if is_text_attachment(att):
                blob = await fetch_text_attachment(att)
                if blob:
                    text_blobs.append(f"Arquivo {att.filename}:\n{blob}")

        # histórico recente (reporter + alvo)
        ctx_users = {int(controller.id)}
        if offender:
            ctx_users.add(int(offender.id))

        per_user_logs = await collect_recent_messages_for_users(
            channel=message.channel,
            user_ids=ctx_users,
            scan_limit=CHANNEL_HISTORY_SCAN,
            per_user=CONTEXT_PER_USER
        )

        reporter_recent = "\n".join(per_user_logs.get(int(controller.id), [])) or "(sem histórico recente)"
        offender_recent = "\n".join(per_user_logs.get(int(offender.id), [])) if offender else "(sem alvo)"

        offense_text = (target_msg.content or "").strip()

        base_context = (
            f"REPORTER: {controller.display_name} (id {controller.id})\n"
            f"ALVO: {(offender.display_name if offender else '(nenhum)')} (id {(offender.id if offender else '0')})\n\n"
            f"MENSAGEM DO ALVO (respondida):\n{offense_text}\n\n"
            f"MENSAGEM DO REPORTER (marcando o bot):\n{controller_text}\n\n"
            f"HISTÓRICO RECENTE DO REPORTER:\n{reporter_recent}\n\n"
            f"HISTÓRICO RECENTE DO ALVO:\n{offender_recent}\n"
        )
        if text_blobs:
            base_context += "\n\nANEXOS DE TEXTO:\n" + "\n\n".join(text_blobs)

        # ================= MODE DECISION (SEM KEYWORD DE ACUSAÇÃO) =================
        # Default CHAT.
        # Só vira REPORT se existir alvo humano + conteúdo do alvo indicar infração provável.
        auto_mode = "chat"
        if offender and referenced:
            if offense_signal(offense_text, offender_recent):
                auto_mode = "report"

        log.info(
            "mode decision | auto_mode=%s | reporter=%s(%s) offender=%s referenced=%s took=%.2fs",
            auto_mode,
            controller.display_name, controller.id,
            (offender.display_name if offender else "(none)"),
            bool(referenced),
            time.time() - t0
        )

        # ================= CHAT =================
        if auto_mode == "chat":
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            try:
                chat_raw = await call_openrouter(
                    build_chat_system_prompt(),
                    base_context,
                    end_user_id=str(controller.id),
                    force_json=False,
                    timeout_s=REQUEST_TIMEOUT_S,
                )
                await reply_soft(message, sanitize_reply(chat_raw))
            except Exception as e:
                log.error("chat call failed err=%r", e)
                await reply_soft(message, "...")
            return

        # ================= REPORT =================
        async with message.channel.typing():
            await asyncio.sleep(EXTRA_TYPING_SECONDS)

        try:
            mod_raw = await call_openrouter(
                build_moderation_system_prompt(directives),
                base_context,
                end_user_id=str(controller.id),
                model_override=ATTACHMENT_TEXT_MODEL if text_blobs else None,
                force_json=True,
                timeout_s=REQUEST_TIMEOUT_S,
            )
        except Exception as e:
            log.error("mod call failed err=%r", e)
            await reply_soft(message, "...")
            return

        js = extract_json_object(mod_raw)
        if not js:
            log.warning("mod returned non-json raw=%s", str(mod_raw)[:300])
            await reply_soft(message, sanitize_reply(mod_raw))
            return

        def parse_mod(js_text: str) -> Optional[dict]:
            try:
                return json.loads(js_text)
            except Exception:
                return None

        d = parse_mod(js)

        # repair se vier quebrado ou timeout sem evidence
        if (not d) or (str(d.get("action", "")).lower() == "timeout" and not (d.get("evidence") or "").strip()):
            log.info("repair start | bad_json_or_missing_evidence")
            repair_payload = (
                "CONTEXTO:\n"
                + base_context +
                "\n\nSAÍDA ATUAL (quebrada/incompleta):\n"
                + js +
                "\n\nRepare para o formato exigido."
            )
            try:
                repaired = await call_openrouter(
                    build_repair_system_prompt(),
                    repair_payload,
                    end_user_id=str(controller.id),
                    model_override=ATTACHMENT_TEXT_MODEL if text_blobs else None,
                    force_json=True,
                    timeout_s=REQUEST_TIMEOUT_S,
                )
                rjs = extract_json_object(repaired)
                d = parse_mod(rjs or "")
                log.info("repair done | ok=%s", bool(d))
            except Exception as e:
                log.error("repair failed err=%r", e)
                d = None

        if not d:
            await reply_soft(message, "...")
            return

        action = (d.get("action") or "reply").strip().lower()
        punish_target = (d.get("punish_target") or "none").strip().lower()
        reply = sanitize_reply(d.get("reply") or "")
        reason = (d.get("reason") or "Conduta inadequada").strip()
        violation = (d.get("violation") or "none").strip().lower()
        seconds = int(d.get("timeout_seconds", 60) or 60)
        evidence = (d.get("evidence") or "").strip()

        log.info(
            "decision | action=%s punish_target=%s violation=%s seconds=%s evidence_len=%s",
            action, punish_target, violation, seconds, len(evidence)
        )

        # Gate: timeout só se evidence literal no contexto
        if action == "timeout":
            if not evidence or evidence not in base_context:
                log.warning("blocked timeout: invalid evidence evidence=%r", evidence[:120])
                await reply_soft(message, reply)
                return

        if action == "ignore":
            return

        if action == "reply":
            await reply_soft(message, reply)
            return

        punish_member: Optional[discord.Member] = None
        if punish_target == "reporter":
            punish_member = controller
        elif punish_target == "offender":
            punish_member = offender

        if not punish_member:
            await reply_soft(message, reply)
            return

        if violation != "none":
            streak = bump_violation(punish_member.id, violation)
            if streak >= 3:
                seconds = max(seconds, 300)

        seconds = min(max(60, seconds), 86400)

        await reply_soft(message, reply)

        ok = await apply_timeout(punish_member, seconds)
        if not ok:
            log.warning("timeout NOT applied. Check: Moderate Members perm + bot role above target.")
        else:
            minutes = max(1, seconds // 60)
            await message.channel.send(
                f"🔇 {punish_member.mention}\nMotivo: {reason}\nDuração: {minutes} minuto(s)"
            )

    except Exception as e:
        log.error("on_message unexpected err=%r", e)
        await reply_soft(message, "...")
    finally:
        bot_busy = False

# ================= START =================
client.run(DISCORD_TOKEN)
