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

# Modelo principal (texto / moderação normal)
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b").strip()
OPENROUTER_MODELS = os.getenv("OPENROUTER_MODELS", "").strip()  # fallback list (csv)

# Modelos especiais
# - VISION_MODEL: deve ser um modelo que aceite imagem (ex: openai/gpt-4o-mini)
# - ATTACHMENT_TEXT_MODEL: para anexos de texto (txt) e parsing rápido
# - COMMAND_MODEL: para interpretar comandos admin (fallback)
VISION_MODEL = os.getenv("VISION_MODEL", "openai/gpt-4o-mini").strip()
ATTACHMENT_TEXT_MODEL = os.getenv("ATTACHMENT_TEXT_MODEL", "openai/gpt-5-nano").strip()
COMMAND_MODEL = os.getenv("COMMAND_MODEL", "openai/gpt-5-nano").strip()

BOT_NAME = "Edit_Japex"
PUBLIC_MODEL_NAME = "Japex Neural Core – Ultimation"

VERSION_MAJOR = 1
VERSION_MINOR = 8

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Chat onde ausência gramatical é permitida
CHAT_GERAL_ID = 1450594073596395548

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "300"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.55"))
REQUEST_TIMEOUT_S = int(os.getenv("REQUEST_TIMEOUT_S", "45"))
EXTRA_TYPING_SECONDS = float(os.getenv("EXTRA_TYPING_SECONDS", "2.0"))

# Admins (fallback) - csv de ids: "123,456"
AUTHORIZED_IDS_ENV = os.getenv("AUTHORIZED_IDS", "").strip()

# Estado persistente (memória de ordens/admin)
STATE_FILE = os.getenv("STATE_FILE", "admin_state.json")

# Janela de histórico para spam/insistência
HIST_MAX = int(os.getenv("HIST_MAX", "8"))
HIST_TTL_S = int(os.getenv("HIST_TTL_S", "900"))  # 15 min

# Tamanho máx que vamos baixar/colar de anexos de texto
MAX_TEXT_ATTACHMENT_CHARS = int(os.getenv("MAX_TEXT_ATTACHMENT_CHARS", "12000"))

# ================= DISCORD =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)

bot_busy = False

# Históricos por usuário (para quem pinga o bot)
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

# ================= STATE (PERSISTENTE) =================
DEFAULT_STATE = {
    "paused": False,              # se True: responde só admins
    "ignored_user_ids": {},       # { "123": {"until": 0 or epoch} }
    "directives": []              # lista de strings curtas (ordens)
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
    """
    Pega IDs (17-20 dígitos) em linhas que mencionem dono/mod/equipe/autoriz.
    BUG corrigido: keywords precisam estar em minúsculo, porque a linha foi lowercased.
    """
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

def is_controller(member: discord.Member) -> bool:
    """
    Quem o bot deve obedecer:
    - está na lista (regras.txt / env)
    - ou tem permissão admin
    - ou tem cargo acima do bot (hierarquia)
    """
    try:
        if is_authorized(member):
            return True
        if getattr(member.guild_permissions, "administrator", False):
            return True
        me = member.guild.me if member.guild else None
        if me and member.top_role and me.top_role and member.top_role.position > me.top_role.position:
            return True
    except Exception:
        pass
    return False

# ================= UTIL =================
def typing_delay(text: str) -> float:
    return 0.8 + min(len(text) * 0.02, 3.0)

async def reply_soft(message: discord.Message, text: str):
    """
    Responde sem 'Erro interno'. Se falhar por permissão/HTTP, só ignora.
    """
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
    """
    Aceita coisas tipo: "60s", "5 min", "10m", "2h", "1 dia", "1d".
    """
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
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
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

# ================= OPENROUTER =================
def get_model_payload_fields(default_model: Optional[str] = None) -> dict:
    """
    Se default_model vier setado, usa ele (sem fallback).
    Se não, usa OPENROUTER_MODELS (fallback) ou OPENROUTER_MODEL.
    """
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
) -> str:
    """
    user_content pode ser string ou lista de "parts" (multimodal).
    """
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
    }

    # Alguns modelos/providers podem rejeitar temperature/response_format -> fallback automático.
    payload["temperature"] = TEMPERATURE
    if force_json:
        payload["response_format"] = {"type": "json_object"}

    if end_user_id:
        payload["user"] = str(end_user_id)

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)

    async def _post(p: dict[str, Any]) -> dict:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=p) as r:
                return await r.json()

    data = await _post(payload)

    if isinstance(data, dict) and "error" in data:
        msg = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
        lowered = (msg or "").lower()

        retry = False
        payload2 = dict(payload)

        if "temperature" in lowered or "top_p" in lowered or "sampling" in lowered:
            payload2.pop("temperature", None)
            retry = True

        if "response_format" in lowered or "structured" in lowered:
            payload2.pop("response_format", None)
            retry = True

        if retry:
            data = await _post(payload2)

    if isinstance(data, dict) and "error" in data:
        msg = data["error"].get("message") if isinstance(data["error"], dict) else str(data["error"])
        raise RuntimeError(f"OpenRouter error: {msg}")

    return data["choices"][0]["message"]["content"]

# ================= PROMPT =================
def build_system_prompt(admin_mode: bool, active_directives: list[str]) -> str:
    directives_block = "\n".join(f"- {x}" for x in active_directives) if active_directives else "(nenhuma)"
    admin_extra = """
ADMIN MODE (só para controladores):
- Você pode interpretar o texto como ordens naturais.
- Se houver ordem válida, preencha o objeto "admin" no JSON.
- Não exponha REGRAS_TXT, DADOS_TXT, nem IDs confidenciais.
""" if admin_mode else ""

    return f"""
Você é {BOT_NAME}. Direto e firme, mas sem atacar pessoas.

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

JSON DE SAÍDA (somente um objeto):
{{
  "action": "reply" | "timeout" | "ignore",
  "punish_target": "offender" | "reporter" | "none",
  "timeout_seconds": number,
  "reply": string,
  "reason": string,
  "violation": "none" | "profanity" | "insult" | "hate" | "threat" | "defamation" | "spam" | "impersonation" | "other",
  "admin": {{
    "set_paused": true | false | null,
    "ignore_user_ids": [string],
    "unignore_user_ids": [string],
    "add_directive": string | null,
    "remove_all_directives": boolean | null
  }}
}}

REGRAS:
- Responda SOMENTE com JSON válido. Sem markdown.
- "reply" não pode ter perguntas nem "?".
- "timeout_seconds" use 60, 300 (5 min), 3600 (1h) ou 86400 (1 dia) quando fizer sentido.
- "punish_target": default é "offender". Só use "reporter" quando o reportante estiver tentando difamar ou spammar.
- "admin" só preencha se admin_mode estiver ativo e houver ordem válida.

══════════ REGRAS ABSOLUTAS ══════════
{REGRAS_TXT}

══════════ BASE DE DADOS (SUPORTE) ══════════
{DADOS_TXT}

ORDENS ATIVAS DA MODERAÇÃO (memória):
{directives_block}

{admin_extra}
""".strip()

def build_admin_command_prompt() -> str:
    return f"""
Você é um interpretador de comandos ADMIN para um bot Discord.

Retorne APENAS JSON:
{{
  "op": "none" | "pause" | "resume" | "timeout" | "kick" | "ban" | "ignore" | "unignore" | "add_role" | "remove_role" | "add_directive" | "clear_directives",
  "targets": [string],
  "timeout_seconds": number | null,
  "role_ids": [string],
  "directive": string | null
}}

Regras:
- Use IDs recebidos na lista de "mentioned_users" e "mentioned_roles".
- Se não tiver alvo explícito, mas existir "reply_target_user_id", use ele.
- Se não houver comando, op="none".
- Não use markdown.
""".strip()

# ================= ADMIN COMMANDS (LOCAL + LLM FALLBACK) =================
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

async def apply_roles(member: discord.Member, role_ids: list[str], add: bool) -> bool:
    try:
        roles = []
        for rid in role_ids:
            if rid.isdigit():
                r = member.guild.get_role(int(rid))
                if r:
                    roles.append(r)
        if not roles:
            return False
        if add:
            await member.add_roles(*roles, reason="Admin command")
        else:
            await member.remove_roles(*roles, reason="Admin command")
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False

def pick_target_ids(
    mentioned_users: list[discord.abc.User],
    reply_target_user_id: Optional[int]
) -> list[str]:
    ids = [str(u.id) for u in mentioned_users if u and not getattr(u, "bot", False)]
    if not ids and reply_target_user_id:
        ids = [str(reply_target_user_id)]
    return ids

async def handle_admin_command(
    message: discord.Message,
    controller: discord.Member,
    reply_target_user_id: Optional[int],
) -> bool:
    """
    Retorna True se executou algum comando admin e encerrou o fluxo.
    Comandos "naturais" mais comuns (sem depender do modelo).
    """
    if not message.guild:
        return False

    content_raw = message.content or ""
    content = content_raw.replace(f"<@{client.user.id}>", "").strip()
    low = content.lower()

    mentioned_users = [u for u in message.mentions if u.id != client.user.id]
    target_ids = pick_target_ids(mentioned_users, reply_target_user_id)

    # Pausar / retomar
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

    # Ignorar / des-ignorar
    if any(k in low for k in ["ignora", "não responde", "nao responde", "mute chat"]):
        if target_ids:
            async with state_lock:
                st = load_state_sync()
                for uid in target_ids:
                    st.setdefault("ignored_user_ids", {})[uid] = {"until": 0}
                save_state_sync(st)
            await reply_soft(message, "...")
            return True

    if any(k in low for k in ["designora", "unignore", "volta a responder", "responde de novo"]):
        if target_ids:
            async with state_lock:
                st = load_state_sync()
                for uid in target_ids:
                    st.get("ignored_user_ids", {}).pop(uid, None)
                save_state_sync(st)
            await reply_soft(message, "...")
            return True

    # Timeout/mute
    if any(k in low for k in ["muta", "mute", "timeout", "silencia", "castiga"]):
        secs = parse_duration_seconds(low) or 300
        ok_any = False
        for uid in target_ids:
            mem = message.guild.get_member(int(uid)) if uid.isdigit() else None
            if mem:
                ok_any |= await apply_timeout(mem, secs)
        await reply_soft(message, "...")
        return True

    # Kick
    if any(k in low for k in ["expulsa", "kick", "chuta"]):
        for uid in target_ids:
            mem = message.guild.get_member(int(uid)) if uid.isdigit() else None
            if mem:
                await apply_kick(message.guild, mem, reason="Admin command")
        await reply_soft(message, "...")
        return True

    # Ban
    if any(k in low for k in ["ban", "bane", "banir"]):
        for uid in target_ids:
            mem = message.guild.get_member(int(uid)) if uid.isdigit() else None
            if mem:
                await apply_ban(message.guild, mem, reason="Admin command")
        await reply_soft(message, "...")
        return True

    # Roles (usa role mentions por id)
    role_ids = [str(r.id) for r in getattr(message, "role_mentions", [])]
    if role_ids and any(k in low for k in ["dar cargo", "add cargo", "adiciona cargo", "promove", "seta cargo", "dá cargo"]):
        for uid in target_ids:
            mem = message.guild.get_member(int(uid)) if uid.isdigit() else None
            if mem:
                await apply_roles(mem, role_ids, add=True)
        await reply_soft(message, "...")
        return True

    if role_ids and any(k in low for k in ["tirar cargo", "remove cargo", "remover cargo", "rebaixa"]):
        for uid in target_ids:
            mem = message.guild.get_member(int(uid)) if uid.isdigit() else None
            if mem:
                await apply_roles(mem, role_ids, add=False)
        await reply_soft(message, "...")
        return True

    # Diretivas (memória)
    if any(k in low for k in ["memoriza:", "ordem:", "diretiva:"]):
        parts = content.split(":", 1)
        directive = parts[1].strip() if len(parts) > 1 else ""
        if directive:
            async with state_lock:
                st = load_state_sync()
                st.setdefault("directives", []).append(directive)
                st["directives"] = trim_directives_to_200_words(st["directives"])
                save_state_sync(st)
            await reply_soft(message, "...")
            return True

    if any(k in low for k in ["limpa diretivas", "remove diretivas", "zera diretivas"]):
        async with state_lock:
            st = load_state_sync()
            st["directives"] = []
            save_state_sync(st)
        await reply_soft(message, "...")
        return True

    return False

async def handle_admin_command_with_model(
    message: discord.Message,
    controller: discord.Member,
    reply_target_user_id: Optional[int],
) -> bool:
    if not message.guild:
        return False

    content = (message.content or "").replace(f"<@{client.user.id}>", "").strip()
    mentioned_users = [u for u in message.mentions if u.id != client.user.id]
    mentioned_roles = getattr(message, "role_mentions", [])

    prompt = build_admin_command_prompt()
    user = {
        "text": content,
        "reply_target_user_id": str(reply_target_user_id) if reply_target_user_id else None,
        "mentioned_users": [{"id": str(u.id), "name": str(u)} for u in mentioned_users],
        "mentioned_roles": [{"id": str(r.id), "name": r.name} for r in mentioned_roles],
    }

    try:
        raw = await call_openrouter(
            prompt,
            json.dumps(user, ensure_ascii=False),
            end_user_id=str(controller.id),
            model_override=COMMAND_MODEL,
            force_json=True
        )
        js = extract_json_object(raw)
        if not js:
            return False
        d = json.loads(js)
    except Exception:
        return False

    op = (d.get("op") or "none").strip().lower()
    targets = d.get("targets") or []
    role_ids = d.get("role_ids") or []
    secs = d.get("timeout_seconds", None)
    directive = d.get("directive", None)

    if not isinstance(targets, list):
        targets = []
    if not isinstance(role_ids, list):
        role_ids = []

    if op == "none":
        return False

    if op == "pause":
        async with state_lock:
            st = load_state_sync()
            st["paused"] = True
            save_state_sync(st)
        await reply_soft(message, "...")
        return True

    if op == "resume":
        async with state_lock:
            st = load_state_sync()
            st["paused"] = False
            save_state_sync(st)
        await reply_soft(message, "...")
        return True

    if op in ["ignore", "unignore"]:
        async with state_lock:
            st = load_state_sync()
            if op == "ignore":
                for uid in targets:
                    if isinstance(uid, str) and uid.isdigit():
                        st.setdefault("ignored_user_ids", {})[uid] = {"until": 0}
            else:
                for uid in targets:
                    if isinstance(uid, str):
                        st.get("ignored_user_ids", {}).pop(uid, None)
            save_state_sync(st)
        await reply_soft(message, "...")
        return True

    if op == "add_directive" and isinstance(directive, str) and directive.strip():
        async with state_lock:
            st = load_state_sync()
            st.setdefault("directives", []).append(directive.strip())
            st["directives"] = trim_directives_to_200_words(st["directives"])
            save_state_sync(st)
        await reply_soft(message, "...")
        return True

    if op == "clear_directives":
        async with state_lock:
            st = load_state_sync()
            st["directives"] = []
            save_state_sync(st)
        await reply_soft(message, "...")
        return True

    ok_any = False
    if op == "timeout":
        secs2 = int(secs) if isinstance(secs, (int, float)) else 300
        for uid in targets:
            if isinstance(uid, str) and uid.isdigit():
                mem = message.guild.get_member(int(uid))
                if mem:
                    ok_any |= await apply_timeout(mem, secs2)

    if op == "kick":
        for uid in targets:
            if isinstance(uid, str) and uid.isdigit():
                mem = message.guild.get_member(int(uid))
                if mem:
                    ok_any |= await apply_kick(message.guild, mem, reason="Admin command")

    if op == "ban":
        for uid in targets:
            if isinstance(uid, str) and uid.isdigit():
                mem = message.guild.get_member(int(uid))
                if mem:
                    ok_any |= await apply_ban(message.guild, mem, reason="Admin command")

    if op == "add_role":
        for uid in targets:
            if isinstance(uid, str) and uid.isdigit():
                mem = message.guild.get_member(int(uid))
                if mem:
                    ok_any |= await apply_roles(mem, [str(x) for x in role_ids], add=True)

    if op == "remove_role":
        for uid in targets:
            if isinstance(uid, str) and uid.isdigit():
                mem = message.guild.get_member(int(uid))
                if mem:
                    ok_any |= await apply_roles(mem, [str(x) for x in role_ids], add=False)

    await reply_soft(message, "...")
    return True

# ================= MESSAGE TARGETING =================
async def resolve_reference_message(message: discord.Message) -> Optional[discord.Message]:
    """
    Se a pessoa respondeu (reply) em cima de outra mensagem e marcou o bot,
    pega a mensagem original pra punir o autor correto.
    """
    try:
        if not message.reference or not message.reference.message_id:
            return None
        if message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
            return message.reference.resolved
        return await message.channel.fetch_message(message.reference.message_id)
    except Exception:
        return None

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

    # só reage quando marcado
    if client.user not in message.mentions:
        return

    # ocupado = ignora e NÃO chama OpenRouter
    if bot_busy:
        return

    if not isinstance(message.author, discord.Member):
        return

    controller = message.author  # quem marcou o bot
    guild = message.guild
    if not guild:
        return

    controller_text = (message.content or "").replace(f"<@{client.user.id}>", "").strip()
    low = controller_text.lower()

    # comandos simples locais
    if "modelo" in low:
        await reply_soft(message, PUBLIC_MODEL_NAME)
        return
    if "versão" in low or "versao" in low:
        await reply_soft(message, f"v{VERSION_MAJOR}.{VERSION_MINOR}")
        return

    # carrega estado
    async with state_lock:
        state = load_state_sync()

    # se pausado, só controladores
    if state.get("paused") and not is_controller(controller):
        return

    # ignore list (para quem marca o bot)
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

    # Se for reply em cima de outra msg, o ALVO (offender) é quem escreveu a msg original.
    referenced = await resolve_reference_message(message)
    target_msg = referenced or message
    offender_user = target_msg.author if isinstance(target_msg.author, discord.Member) else None
    offender = offender_user if offender_user else controller  # fallback

    reply_target_user_id = None
    if referenced and isinstance(referenced.author, discord.Member):
        reply_target_user_id = int(referenced.author.id)

    # ===== Admin commands =====
    if is_controller(controller):
        try:
            did = await handle_admin_command(message, controller, reply_target_user_id)
            if did:
                return
            did2 = await handle_admin_command_with_model(message, controller, reply_target_user_id)
            if did2:
                return
        except Exception:
            return

    bot_busy = True
    try:
        # ===== Gramática: permitido só no chat-geral =====
        # Aplica só quando a própria pessoa está falando com o bot (não quando está reportando alguém via reply).
        if (referenced is None) and message.channel.id != CHAT_GERAL_ID and absence_grammar(controller_text):
            async with message.channel.typing():
                await asyncio.sleep(EXTRA_TYPING_SECONDS)
            ok = await apply_timeout(controller, 60)
            if ok:
                await reply_soft(message, "Fala direito.")
                await punishment_report(message.channel, controller, "Ausência gramatical", 60)
            return

        # ===== Spam local (sem LLM) para quem pinga =====
        update_history(controller.id, controller_text)

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

        # ===== Diretivas ativas (memória) =====
        async with state_lock:
            st = load_state_sync()
            directives = trim_directives_to_200_words(st.get("directives", []))
            st["directives"] = directives
            save_state_sync(st)

        admin_mode = is_controller(controller)

        # ===== Contexto para o modelo =====
        roles = roles_for_prompt(controller)
        roles_str = ", ".join(roles) if roles else "(sem cargos)"
        top_roles = ", ".join(roles[:5]) if roles else "(sem cargos)"

        # anexos: pega do target_msg (mensagem reportada) + do controller msg
        attachments = []
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

        offense_text = (target_msg.content or "").strip()
        reporter_text = controller_text

        mentioned_users = [u for u in message.mentions if u.id != client.user.id]
        mentioned_map = [{"id": str(u.id), "name": str(u)} for u in mentioned_users]

        system_prompt = build_system_prompt(admin_mode=admin_mode, active_directives=directives)

        base_text = (
            f"CONTROLADOR: {controller.display_name} (id {controller.id})\n"
            f"CONTROLADOR cargos: {top_roles}\n"
            f"CONTROLADOR todos cargos: {roles_str}\n"
            f"Mencionados pelo controlador: {json.dumps(mentioned_map, ensure_ascii=False)}\n\n"
            f"ALVO (possível infrator): {getattr(offender, 'display_name', str(offender))} (id {offender.id})\n"
            f"MENSAGEM DO ALVO:\n{offense_text}\n\n"
            f"COMENTÁRIO DO CONTROLADOR (se houver):\n{reporter_text}\n"
        )

        if text_blobs:
            base_text += "\n\nANEXOS DE TEXTO:\n" + "\n\n".join(text_blobs)

        user_content: Any
        chosen_model: Optional[str] = None

        if image_urls:
            parts = [{"type": "text", "text": base_text}]
            for url in image_urls[:4]:
                parts.append({"type": "image_url", "imageUrl": {"url": url}})
            user_content = parts
            chosen_model = VISION_MODEL
        elif text_blobs:
            user_content = base_text
            chosen_model = ATTACHMENT_TEXT_MODEL
        else:
            user_content = base_text
            chosen_model = None

        async with message.channel.typing():
            await asyncio.sleep(EXTRA_TYPING_SECONDS)
            raw = await call_openrouter(
                system_prompt,
                user_content,
                end_user_id=str(controller.id),
                model_override=chosen_model,
                force_json=True
            )

            js = extract_json_object(raw)
            if not js:
                return

            try:
                d = json.loads(js)
            except Exception:
                return

        action = (d.get("action") or "reply").strip().lower()
        punish_target = (d.get("punish_target") or "offender").strip().lower()
        reply = strip_questions((d.get("reply") or "").strip())
        reason = (d.get("reason") or "Conduta inadequada").strip()
        violation = (d.get("violation") or "none").strip().lower()
        seconds = int(d.get("timeout_seconds", 0) or 0)

        punish_member: Optional[discord.Member] = None
        if punish_target == "reporter":
            punish_member = controller
        elif punish_target == "offender":
            punish_member = offender if isinstance(offender, discord.Member) else None
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

        # Admin (estado) ainda pode ser atualizado via modelo
        if admin_mode and isinstance(d.get("admin"), dict):
            adm = d["admin"]
            changed = False
            async with state_lock:
                st = load_state_sync()

                sp = adm.get("set_paused", None)
                if sp is True:
                    st["paused"] = True
                    changed = True
                elif sp is False:
                    st["paused"] = False
                    changed = True

                ig = adm.get("ignore_user_ids") or []
                if isinstance(ig, list):
                    for uid in ig:
                        if isinstance(uid, str) and uid.isdigit():
                            st.setdefault("ignored_user_ids", {})[uid] = {"until": 0}
                            changed = True

                un = adm.get("unignore_user_ids") or []
                if isinstance(un, list):
                    for uid in un:
                        if isinstance(uid, str):
                            st.get("ignored_user_ids", {}).pop(uid, None)
                            changed = True

                add_dir = adm.get("add_directive", None)
                if isinstance(add_dir, str) and add_dir.strip():
                    st.setdefault("directives", []).append(add_dir.strip())
                    st["directives"] = trim_directives_to_200_words(st["directives"])
                    changed = True

                if adm.get("remove_all_directives") is True:
                    st["directives"] = []
                    changed = True

                if changed:
                    save_state_sync(st)

        if action == "ignore":
            return

        if action == "timeout":
            if not punish_member:
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
        try:
            await reply_soft(message, "...")
        except Exception:
            pass
    finally:
        bot_busy = False

# ================= START =================
client.run(DISCORD_TOKEN)
