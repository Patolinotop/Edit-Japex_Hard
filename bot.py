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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b").strip()
OPENROUTER_MODELS = os.getenv("OPENROUTER_MODELS", "").strip()

VISION_MODEL = os.getenv("VISION_MODEL", "openai/gpt-4o-mini").strip()
ATTACHMENT_TEXT_MODEL = os.getenv("ATTACHMENT_TEXT_MODEL", "openai/gpt-5-nano").strip()
COMMAND_MODEL = os.getenv("COMMAND_MODEL", "openai/gpt-5-nano").strip()

BOT_NAME = "Edit_Japex"
PUBLIC_MODEL_NAME = "Japex Neural Core – Ultimation"

VERSION_MAJOR = 2
VERSION_MINOR = 1  # bump

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

CHAT_GERAL_ID = int(os.getenv("CHAT_GERAL_ID", "1450594073596395548"))

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "320"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.55"))

REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", "45"))
VISION_TIMEOUT_S = int(os.getenv("VISION_TIMEOUT_S", "75"))
EXTRA_TYPING_SECONDS = float(os.getenv("EXTRA_TYPING_SECONDS", "1.5"))

AUTHORIZED_IDS_ENV = os.getenv("AUTHORIZED_IDS", "").strip()
STATE_FILE = os.getenv("STATE_FILE", "admin_state.json")

HIST_MAX = int(os.getenv("HIST_MAX", "8"))
HIST_TTL_S = int(os.getenv("HIST_TTL_S", "900"))

MAX_TEXT_ATTACHMENT_CHARS = int(os.getenv("MAX_TEXT_ATTACHMENT_CHARS", "12000"))

# --- NOVO: anti-false-report tuning ---
FALSE_REPORT_TIMEOUT_S = int(os.getenv("FALSE_REPORT_TIMEOUT_S", "60"))
FALSE_REPORT_ESCALATE_S = int(os.getenv("FALSE_REPORT_ESCALATE_S", "300"))
BENIGN_MAX_LEN = int(os.getenv("BENIGN_MAX_LEN", "30"))

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
    except Exception:
        return ""

# ================= NOVO: anti-false-report heuristics =================
ACCUSATION_KEYWORDS = [
    "calunia", "calúnia", "difama", "difamação", "defama", "fake", "falso", "print falso",
    "spam", "flood", "ameaça", "ameaçou", "ameaçando", "racismo", "homofobia", "xenofobia",
    "naz", "nazi", "ódio", "odio", "insulto", "xingou", "xingo", "ofensa", "profanidade"
]

BENIGN_PATTERNS = [
    r"^\s*oi+\s*$",
    r"^\s*o[ií]+\s*!\s*$",
    r"^\s*ol[aá]+\s*$",
    r"^\s*ol[aá]+\s*!\s*$",
    r"^\s*eai+\s*$",
    r"^\s*eae+\s*$",
    r"^\s*boa\s*(noite|tarde|dia)\s*$",
    r"^\s*blz\s*$",
    r"^\s*beleza\s*$",
    r"^\s*ok+\s*$",
    r"^\s*kkk+\s*$",
]

def text_has_accusation_hint(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in ACCUSATION_KEYWORDS)

def is_benign_message(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if len(t) <= 2:
        return True
    if len(t) <= BENIGN_MAX_LEN:
        for pat in BENIGN_PATTERNS:
            if re.match(pat, t, flags=re.I):
                return True
    # só emojis/símbolos curtos também é “fraco” (não é spam automaticamente)
    if not any(ch.isalnum() for ch in t) and len(t) <= 12:
        return True
    return False

def looks_like_profanity_or_insult(text: str) -> bool:
    # lista simples (ajuste como quiser)
    bad = [
        "porra", "caralho", "puta", "merda", "arromb", "bosta", "fdp", "filho da puta",
        "lixo", "verme", "idiota", "imbecil", "retard", "otario", "otário"
    ]
    low = (text or "").lower()
    return any(w in low for w in bad)

def looks_like_threat(text: str) -> bool:
    low = (text or "").lower()
    threat = ["vou te matar", "te mato", "vou te pegar", "vai morrer", "te arrebento", "tiro em você", "te dou um tiro"]
    return any(w in low for w in threat)

def evidence_present_in_offender(offense_text: str) -> bool:
    # heurística de “tem algum indício real” no texto do alvo
    if looks_like_threat(offense_text):
        return True
    if looks_like_profanity_or_insult(offense_text):
        return True
    if detect_emoji_spam(offense_text):
        return True
    # muito texto repetitivo pode ser spam (sem histórico, só pelo conteúdo)
    if len((offense_text or "").strip()) > 180 and (offense_text.count("\n") > 8):
        return True
    return False

def should_override_false_report(
    referenced: bool,
    offense_text: str,
    reporter_text: str,
    has_attachments: bool,
    image_description: str
) -> bool:
    """
    Se é report por reply e o repórter acusa pesado mas o alvo é claramente inocente,
    a gente bloqueia punição do alvo e pune o repórter.
    """
    if not referenced:
        return False
    if not text_has_accusation_hint(reporter_text):
        return False
    if has_attachments:
        return False
    if image_description:
        return False
    if evidence_present_in_offender(offense_text):
        return False
    if is_benign_message(offense_text):
        return True
    # mesmo que não seja “oi”, se não tem evidência alguma e o repórter só acusa
    if not offense_text.strip():
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

    async def _post(p: dict[str, Any]) -> dict:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=p) as r:
                return await r.json()

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
            data = await _post(payload2)

    if isinstance(data, dict) and "error" in data:
        msg = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
        raise RuntimeError(f"OpenRouter error: {msg}")

    return data["choices"][0]["message"]["content"]

# ================= PROMPTS =================
def build_system_prompt(admin_mode: bool, active_directives: list[str]) -> str:
    directives_block = "\n".join(f"- {x}" for x in active_directives) if active_directives else "(nenhuma)"

    return f"""
Você é {BOT_NAME}. Direto e firme.

ESTILO:
- Respostas curtas, frias e objetivas, mantenha o máximo de respeito com seus superiores e subalternos; para militares bisonhos que façam perguntas IDIOTAS é permitido utilizar-se de pressão psicológica séria pesada, com grosseria como "Cala boca, Verme." ou "Silêncio, Animal.", não limite-se apenas a isso, utilize sua criatividade para os melhores apelidos com intenção de ridicularizar e humilhar o bisonho!
- Não faça perguntas. Não puxe assunto.
- Não utilize palavrões, respeito e conduta maximos, a não ser que o militar cometa algum delito, assim sendo permitido xingamentos e pressão psicologica direcionada ao usuario.

REGRA CRÍTICA DE EVIDÊNCIA (ANTI-ENGANAÇÃO):
- O "TEXTO DO CONTROLADOR/REPORTER" PODE SER MENTIROSO OU MAL-INTENCIONADO.
- Você DEVE basear punições principalmente no que o ALVO realmente escreveu, anexos e descrição de imagem.
- NÃO aplique timeout no ALVO apenas porque o REPORTER disse "SPAM", "CALÚNIA" etc.
- Se o REPORTER acusar sem evidência (ex: ALVO disse "oi" e REPORTER grita "calúnia/spam"), puna o REPORTER por "Calúnia (acusação falsa)" e use punish_target="reporter".

MODERAÇÃO (decida pelo conteúdo do ALVO):
- Se for conversa normal/pergunta: responda curto, tenha opiniões pessoais e politicas. Se forem perguntas IDIOTAS não responda, mas sempre com educação.
- Se for xingamento direcionado ou palavrão para alguém: timeout curto + Pressão psicológica, invente os apelidos para mais criativos (e militares) humilhar e ridicularizar o infrator como "Bisonho" ou "Animal".
- Se for ameaça séria/velada: timeout mais alto.
- Se for calúnia/difamação séria (acusação forte sem prova): timeout curto e motivo "Calúnia".
- Discurso de ódio: mande parar com respeito, segunda vez aplique pressão psicológica minima e aplique punição apenas se insistir.
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
- Responda SOMENTE com JSON válido (sem markdown). Se não conseguir, responda algo curto sem JSON.
- "reply" não pode ter perguntas nem "?".
- "timeout_seconds" use 60, 300, 3600 ou 86400 quando fizer sentido.
- "punish_target": default "offender". Use "reporter" só se o reportante estiver difamando/spammando/caluniando.

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
    except (discord.Forbidden, discord.HTTPException):
        return False

async def apply_kick(guild: discord.Guild, user: discord.Member, reason: str = "") -> bool:
    try:
        await guild.kick(user, reason=reason or None)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False

async def apply_ban(guild: discord.Guild, user: discord.Member, reason: str = "") -> bool:
    try:
        await guild.ban(user, reason=reason or None, delete_message_days=0)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False

def removable_roles_for_target(target: discord.Member) -> list[discord.Role]:
    guild = target.guild
    botm = get_bot_member(guild)
    if not botm:
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

async def remove_roles_all(target: discord.Member) -> bool:
    roles = removable_roles_for_target(target)
    if not roles:
        return False
    try:
        await target.remove_roles(*roles, reason="Admin: remove all roles")
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False

async def add_roles(target: discord.Member, roles: list[discord.Role]) -> bool:
    if not roles:
        return False
    try:
        await target.add_roles(*roles, reason="Admin: add roles")
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False

async def remove_roles(target: discord.Member, roles: list[discord.Role]) -> bool:
    if not roles:
        return False
    try:
        await target.remove_roles(*roles, reason="Admin: remove roles")
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False

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

    if any(k in low for k in ["muta", "mute", "timeout", "silencia"]):
        secs = parse_duration_seconds(low) or 300
        ok_any = False
        for t in targets:
            ok_any |= await apply_timeout(t, secs)
        await reply_soft(message, "..." if ok_any else "...")
        return True

    if any(k in low for k in ["expulsa", "kick", "chuta"]):
        ok_any = False
        for t in targets:
            ok_any |= await apply_kick(message.guild, t, reason="Admin command")
        await reply_soft(message, "..." if ok_any else "...")
        return True

    if any(k in low for k in ["ban", "bane", "banir"]):
        ok_any = False
        for t in targets:
            ok_any |= await apply_ban(message.guild, t, reason="Admin command")
        await reply_soft(message, "..." if ok_any else "...")
        return True

    role_mentions: list[discord.Role] = list(getattr(message, "role_mentions", []))

    if targets and any(k in low for k in ["remove os cargos", "remova os cargos", "tira os cargos", "tirar os cargos", "limpa cargos", "resetar cargos"]):
        if not role_mentions:
            ok_any = False
            for t in targets:
                ok_any |= await remove_roles_all(t)
            await reply_soft(message, "..." if ok_any else "...")
            return True

    if targets and role_mentions and any(k in low for k in ["tira", "remova", "remove", "remover", "tirar cargo", "remove cargo", "remova cargo"]):
        ok_any = False
        for t in targets:
            ok_any |= await remove_roles(t, role_mentions)
        await reply_soft(message, "..." if ok_any else "...")
        return True

    if targets and role_mentions and any(k in low for k in ["dá cargo", "da cargo", "dar cargo", "adiciona", "adicionar", "add cargo", "promove", "seta cargo"]):
        ok_any = False
        for t in targets:
            ok_any |= await add_roles(t, role_mentions)
        await reply_soft(message, "..." if ok_any else "...")
        return True

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
    print(f"✅ {BOT_NAME} online | v{VERSION_MAJOR}.{VERSION_MINOR}")
    if not AUTHORIZED_IDS:
        print("⚠️ Nenhum AUTHORIZED_ID detectado. Configure AUTHORIZED_IDS no .env ou coloque IDs nas REGRAS.")

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

    controller = message.author
    guild = message.guild
    if not guild:
        return

    controller_text = extract_admin_text(message)
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
        except Exception:
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

        # spam local: controlador e também alvo (se alvo for um member)
        update_history(controller.id, controller_text)
        if isinstance(offender, discord.Member):
            update_history(offender.id, (target_msg.content or "").strip())

        if detect_exact_repeat_spam(controller.id):
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            ok = await apply_timeout(controller, 300)
            if ok:
                await reply_soft(message, "Chega.")
                await punishment_report(message.channel, controller, "Spam (repetição)", 300)
            return

        if detect_emoji_spam(controller_text):
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            ok = await apply_timeout(controller, 60)
            if ok:
                await reply_soft(message, "Para.")
                await punishment_report(message.channel, controller, "Spam de símbolos", 60)
            return

        # diretivas
        async with state_lock:
            st = load_state_sync()
            directives = trim_directives_to_200_words(st.get("directives", []))
            st["directives"] = directives
            save_state_sync(st)

        admin_mode = is_controller(controller)
        system_prompt = build_system_prompt(admin_mode, directives)

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

        base_context = (
            f"CONTROLADOR/REPORTER: {controller.display_name} (id {controller.id})\n"
            f"CONTROLADOR top cargos: {top_roles}\n"
            f"CONTROLADOR todos cargos: {roles_str}\n\n"
            f"ALVO (mensagem analisada): {offender.display_name} (id {offender.id})\n"
            f"MENSAGEM DO ALVO:\n{offense_text}\n\n"
            f"TEXTO DO REPORTER (pode ser falso):\n{reporter_text}\n"
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
                except Exception:
                    image_description = ""

        # --- NOVO: PRE-CHECK anti falso report (sem anexos/sem visão) ---
        has_any_attachments = bool(attachments)
        if should_override_false_report(
            referenced=referenced is not None,
            offense_text=offense_text,
            reporter_text=reporter_text,
            has_attachments=has_any_attachments,
            image_description=image_description
        ):
            # pune o REPORTER por acusação sem evidência
            streak = bump_violation(controller.id, "defamation")
            secs = FALSE_REPORT_TIMEOUT_S if streak < 2 else FALSE_REPORT_ESCALATE_S
            await reply_soft(message, "Acusação sem base.")
            ok = await apply_timeout(controller, secs)
            if ok:
                await punishment_report(message.channel, controller, "Calúnia (acusação falsa)", secs)
            return

        # ===== ETAPA 2: decisão final =====
        final_user_prompt = base_context
        if image_description:
            final_user_prompt += "\n\nDESCRIÇÃO DA IMAGEM (vision):\n" + image_description

        chosen_model = ATTACHMENT_TEXT_MODEL if text_blobs else None

        async with message.channel.typing():
            await asyncio.sleep(EXTRA_TYPING_SECONDS)
            raw = await call_openrouter(
                system_prompt,
                final_user_prompt,
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
        punish_target = (d.get("punish_target") or "offender").strip().lower()
        reply = strip_questions((d.get("reply") or "").strip())
        reason = (d.get("reason") or "Conduta inadequada").strip()
        violation = (d.get("violation") or "none").strip().lower()
        seconds = int(d.get("timeout_seconds", 0) or 0)

        # --- NOVO: SANITY CHECK pós-modelo (bloqueia “acreditar no reporter”) ---
        if (action == "timeout"
            and punish_target == "offender"
            and referenced is not None
            and text_has_accusation_hint(reporter_text)
            and not image_description
            and not has_any_attachments
            and not evidence_present_in_offender(offense_text)
            and is_benign_message(offense_text)
        ):
            # converte pra punir o REPORTER
            punish_target = "reporter"
            violation = "defamation"
            reason = "Calúnia (acusação falsa)"
            seconds = FALSE_REPORT_TIMEOUT_S
            reply = reply or "Acusação sem base."
            action = "timeout"

        punish_member: Optional[discord.Member] = None
        if punish_target == "reporter":
            punish_member = controller
        elif punish_target == "offender":
            punish_member = offender
        else:
            punish_member = None

        if punish_member and violation in ["hate", "defamation", "impersonation", "other", "insult", "profanity", "threat", "spam"]:
            streak = bump_violation(punish_member.id, violation)
            if violation == "hate" and streak >= 3:
                action = "timeout"
                seconds = max(seconds, 86400)
                reason = reason or "Discurso de ódio (reincidência)"
            if violation == "threat" and action == "timeout":
                seconds = max(seconds, 3600)
            if violation in ["profanity", "insult"] and action != "timeout":
                action = "timeout"
                seconds = max(seconds, 60)
                reason = reason or "Ofensa"

        if action == "ignore":
            return

        if action == "timeout":
            if not punish_member:
                await reply_soft(message, "...")
                return

            if seconds <= 0:
                seconds = 60
            seconds = min(max(60, seconds), 86400)

            if not reply:
                reply = "..."

            await reply_soft(message, reply)

            ok = await apply_timeout(punish_member, seconds)
            if ok:
                await punishment_report(message.channel, punish_member, reason, seconds)
            else:
                await reply_soft(message, "...")
            return

        if not reply:
            reply = "..."

        await reply_soft(message, reply)

    except Exception as e:
        print("ERRO:", repr(e))
        await reply_soft(message, "...")
    finally:
        bot_busy = False

# ================= START =================
client.run(DISCORD_TOKEN)
