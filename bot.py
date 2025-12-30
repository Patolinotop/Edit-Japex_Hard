import os
import re
import json
import random
import asyncio
import datetime
from typing import Dict, List, Optional, Set, Tuple

import discord
from dotenv import load_dotenv
from openai import OpenAI

# ================== ENV ==================
load_dotenv()
TOKEN_DISCORD = os.getenv("DISCORD_BOT_TOKEN")
CHAVE_OPENAI = os.getenv("OPENAI_API_KEY")

if not TOKEN_DISCORD or not CHAVE_OPENAI:
    raise SystemExit("faltou DISCORD_BOT_TOKEN ou OPENAI_API_KEY no .env")

openai = OpenAI(api_key=CHAVE_OPENAI)

# ================== MODELS ==================
MODEL_CHAT = "gpt-5.1"       # chat principal (melhor qualidade)
MODEL_CTRL = "gpt-5-mini"    # ordens/moderação (barato e confiável)

MODEL_PUBLIC_NAME = "JapexUltimation1.2"

# ================== PATHS ==================
PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
CAMINHO_DADOS = os.path.join(PASTA_ATUAL, "dados.txt")
CAMINHO_ORDENS = os.path.join(PASTA_ATUAL, "ordens.txt")
CAMINHO_IGNORE = os.path.join(PASTA_ATUAL, "ignorar.txt")
CAMINHO_SILENCIO = os.path.join(PASTA_ATUAL, "silencio.flag")

# ================== IDs / CHEFÕES ==================
JAPEX_ID = 1331505963622076476  # Fundador
BADD_ID = 0  # <<< TROQUE PARA SEU ID REAL (invisível, mas obedece quando menciona)

JAPEX_MENTION = f"<@{JAPEX_ID}>"

# Chefões públicos (não inclui Badd; só reconhece se perguntarem)
CHEFOES_PUBLICOS = [
    ("japex", "Fundador", 0),
    ("lalomaio", "Criador do Exército", 1),
    ("santiago", "Administrador", 2),
    ("purtuga", "Supremo Tribunal Militar", 3),
    ("riquejoo", "Moderador", 4),
]

CHEFOES_IDS = {
    "lalomaio": None,
    "santiago": None,
    "purtuga": None,
    "riquejoo": None,
}

# ================== SUPORTE ==================
SUPPORT_CHANNEL_ID = 1450602972773089493
SUPPORT_CHANNEL_MENTION = f"<#{SUPPORT_CHANNEL_ID}>"

# ================== PATENTES EB (ordem menor = mais alto) ==================
PATENTES = [
    ("[S-Cmdt]", "Sub Comandante", 2),
    ("[MR]", "Marechal", 3),
    ("[Gen-Ex]", "General do Exército", 4),
    ("[Gen-Div]", "General de Divisão", 5),
    ("[Gen-B]", "General de Brigada", 6),
    ("[Cel]", "Coronel", 7),
    ("[Ten-Cel]", "Tenente-coronel", 8),
    ("[Maj]", "Major", 9),
    ("[Cap]", "Capitão", 10),
    ("[1°Ten]", "Primeiro Tenente", 11),
    ("[2°Ten]", "Segundo Tenente", 12),
    ("[Asp]", "Aspirante", 13),
    ("[ST]", "Subtenente", 14),
    ("[1°Sgt]", "Primeiro Sargento", 15),
    ("[2°Sgt]", "Segundo Sargento", 16),
    ("[3°Sgt]", "Terceiro Sargento", 17),
    ("[Cb]", "Cabo", 18),
    ("[Sld]", "Soldado", 19),
    ("[Rct]", "Recruta", 20),
]

# ================== DISCORD ==================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True

cliente = discord.Client(intents=intents)

# lock global: se ocupado, ignora (não enfileira)
ocupado = asyncio.Lock()

# ================== ANTI DUPLICAÇÃO ==================
_PROCESSED: Dict[int, float] = {}
PROCESSED_TTL = 120.0

# ================== CONTEXTO / HISTÓRICO ==================
HISTORICO: Dict[int, List[dict]] = {}
MAX_MSGS_CONTEXT = 3

# ================== RATE / DIGITANDO ==================
MIN_DELAY_SECONDS = 1.2
EXTRA_TYPING_RANGE = (1.6, 2.4)  # +1–2s
USER_COOLDOWN_SECONDS = 1.6
_last_user_action: Dict[int, float] = {}

# ================== MASS LIMIT ==================
MAX_MASS_TARGETS = 20

# ================== UTIL ==================
def normalizar_espacos(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def norm(s: str) -> str:
    return normalizar_espacos(s).lower()

def is_japex(uid: int) -> bool:
    return uid == JAPEX_ID

def is_badd(uid: int) -> bool:
    return (BADD_ID != 0) and (uid == BADD_ID)

def esta_silenciado() -> bool:
    return os.path.exists(CAMINHO_SILENCIO)

def set_silencio(on: bool) -> None:
    try:
        if on:
            with open(CAMINHO_SILENCIO, "w", encoding="utf-8") as f:
                f.write("1")
        else:
            if os.path.exists(CAMINHO_SILENCIO):
                os.remove(CAMINHO_SILENCIO)
    except:
        pass

def _cleanup_processed(loop_time: float) -> None:
    to_del = [mid for mid, ts in _PROCESSED.items() if (loop_time - ts) > PROCESSED_TTL]
    for mid in to_del:
        _PROCESSED.pop(mid, None)

def already_processed(message_id: int, loop_time: float) -> bool:
    _cleanup_processed(loop_time)
    if message_id in _PROCESSED:
        return True
    _PROCESSED[message_id] = loop_time
    return False

async def respeitar_delay_e_cooldown(user_id: int) -> bool:
    now = asyncio.get_event_loop().time()
    if not is_japex(user_id):
        last = _last_user_action.get(user_id, 0.0)
        if (now - last) < USER_COOLDOWN_SECONDS:
            return False
        _last_user_action[user_id] = now
        await asyncio.sleep(MIN_DELAY_SECONDS)
    else:
        await asyncio.sleep(0.25)
    return True

def typing_extra(author_id: int) -> float:
    if is_japex(author_id):
        return 0.9
    return random.uniform(*EXTRA_TYPING_RANGE)

def parece_pergunta(texto: str) -> bool:
    t = (texto or "").strip()
    if not t:
        return False
    low = t.lower().strip()
    if low.endswith("?"):
        return True
    starters = (
        "quem", "o que", "oq", "qual", "quais", "por que", "porque", "pq",
        "quando", "onde", "como", "quanto", "me diz", "me diga", "fala", "explique", "explica"
    )
    return any(low.startswith(s) for s in starters) or ("quem" in low and ("programou" in low or "criou" in low or "fez" in low))

def needs_support_hint(texto: str) -> bool:
    t = norm(texto)
    keys = [
        "erro", "bug", "nao funciona", "não funciona", "falhando", "ajuda",
        "suporte", "ticket", "problema", "denuncia", "denúncia", "report",
        "ban injusto", "mute injusto", "apelacao", "apelação"
    ]
    return any(k in t for k in keys)

def is_serious_issue(texto: str) -> bool:
    t = norm(texto)
    keys = ["raid", "invadiram", "hack", "vazou", "vazamento", "dox", "ameaça", "ameaça", "extorsão", "extorsao"]
    return any(k in t for k in keys)

def sanitizar_resposta(msg: str) -> str:
    msg = normalizar_espacos(msg).replace("\n", " ")
    msg = msg.replace("?", ".")
    msg = re.sub(r"\balguma ordem\b\.?", "", msg, flags=re.IGNORECASE).strip()
    msg = normalizar_espacos(msg)
    if len(msg) > 280:
        msg = msg[:280].rstrip() + "..."
    return msg if msg else "Entendido."

# ================== IGNORADOS ==================
def carregar_ignorados() -> Set[int]:
    s: Set[int] = set()
    try:
        if not os.path.exists(CAMINHO_IGNORE):
            return s
        with open(CAMINHO_IGNORE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    s.add(int(line))
    except:
        pass
    return s

def salvar_ignorados(s: Set[int]) -> None:
    try:
        with open(CAMINHO_IGNORE, "w", encoding="utf-8") as f:
            for uid in sorted(s):
                f.write(str(uid) + "\n")
    except:
        pass

IGNORADOS: Set[int] = carregar_ignorados()

# ================== ORDENS PERSISTENTES ==================
def carregar_ordens() -> str:
    try:
        if not os.path.exists(CAMINHO_ORDENS):
            return ""
        with open(CAMINHO_ORDENS, "r", encoding="utf-8") as f:
            return f.read().strip()
    except:
        return ""

def salvar_ordens(texto: str) -> None:
    try:
        with open(CAMINHO_ORDENS, "w", encoding="utf-8") as f:
            f.write(texto.strip())
    except:
        pass

def limitar_ordens(texto: str, max_chars: int = 360) -> str:
    texto = normalizar_espacos(texto)
    if len(texto) <= max_chars:
        return texto
    return texto[-max_chars:].strip()

def adicionar_ordem(nova: str) -> None:
    nova = normalizar_espacos(nova)
    if not nova:
        return
    atual = carregar_ordens()
    combinado = (atual + "\n" + f"- {nova}").strip() if atual else f"- {nova}"
    salvar_ordens(limitar_ordens(combinado, max_chars=360))

def limpar_ordens() -> None:
    try:
        if os.path.exists(CAMINHO_ORDENS):
            os.remove(CAMINHO_ORDENS)
    except:
        pass

# ================== CHEFÕES (públicos) ==================
def chefe_publico_info(member: discord.Member) -> Optional[Tuple[str, str, int]]:
    if is_japex(member.id):
        return ("japex", "Fundador", 0)

    dn = norm(member.display_name)
    un = norm(getattr(member, "name", "") or "")

    for key, titulo, rank in CHEFOES_PUBLICOS:
        if key == "japex":
            continue
        cid = CHEFOES_IDS.get(key)
        if cid and member.id == cid:
            return (key, titulo, rank)

    for key, titulo, rank in CHEFOES_PUBLICOS:
        if key == "japex":
            continue
        if key in dn or key in un:
            return (key, titulo, rank)

    return None

# ================== PATENTES ==================
def rank_patente(member: discord.Member) -> Optional[int]:
    best = None
    for role in getattr(member, "roles", []):
        rnome = role.name or ""
        for tag, titulo, ordem in PATENTES:
            if tag in rnome:
                best = ordem if best is None else min(best, ordem)
    if best is not None:
        return best
    for role in getattr(member, "roles", []):
        rnome = role.name or ""
        for tag, titulo, ordem in PATENTES:
            if titulo.lower() in rnome.lower():
                best = ordem if best is None else min(best, ordem)
    return best

def best_patente_title(member: discord.Member) -> Optional[str]:
    best_title = None
    best_ord = 999
    for role in getattr(member, "roles", []):
        rnome = role.name or ""
        for tag, titulo, ordem in PATENTES:
            if tag in rnome and ordem < best_ord:
                best_title = titulo
                best_ord = ordem
    if best_title:
        return best_title
    for role in getattr(member, "roles", []):
        rnome = role.name or ""
        for tag, titulo, ordem in PATENTES:
            if titulo.lower() in rnome.lower() and ordem < best_ord:
                best_title = titulo
                best_ord = ordem
    return best_title

def roles_curto(member: discord.Member, max_roles: int = 8) -> List[str]:
    roles = []
    for r in getattr(member, "roles", []):
        if not r or not r.name:
            continue
        if r.is_default():
            continue
        roles.append(r.name.strip())

    def key(nome: str) -> int:
        for tag, titulo, ordem in PATENTES:
            if tag in nome:
                return ordem
        return 999

    roles.sort(key=key)
    return roles[:max_roles]

def vocativo(member: discord.Member) -> str:
    if is_japex(member.id):
        return "Senhor Japex"
    info = chefe_publico_info(member)
    if info:
        return info[1]
    pat = best_patente_title(member)
    return pat if pat else member.display_name

def ack_superior(member: discord.Member) -> str:
    if is_japex(member.id):
        return "Sim, Senhor Japex."
    if is_badd(member.id):
        v = best_patente_title(member) or member.display_name
        return f"Sim, {v}."
    return f"Sim, {vocativo(member)}."

def autoridade_sobre_bot(author: discord.Member, guild: discord.Guild) -> bool:
    if is_japex(author.id):
        return True
    if is_badd(author.id):
        return True
    if chefe_publico_info(author) is not None:
        return True
    if not guild or not cliente.user:
        return False
    bot_member = guild.get_member(cliente.user.id)
    if not bot_member:
        return False
    a = rank_patente(author)
    b = rank_patente(bot_member)
    if a is None or b is None:
        return False
    return a < b

# ================== DADOS.TXT (BUSCA CURTA) ==================
STOPWORDS = {
    "a","o","os","as","de","do","da","dos","das","e","em","no","na","nos","nas",
    "um","uma","uns","umas","para","por","com","sem","que","é","ser","se","ao",
    "à","às","ou","como","mais","menos","muito","pouco","já","não","sim","nao",
    "sobre","isso","isto","aquele","aquela","aquilo","meu","minha","seu","sua",
    "pra","pro","pq","porque"
}
_dados_cache = {"mtime": None, "blocos": []}  # List[Tuple[titulo, texto, tokens]]

def _tokenizar(s: str) -> Set[str]:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9áàâãéèêíìîóòôõúùûç°\s]", " ", s, flags=re.IGNORECASE)
    parts = [p for p in s.split() if p and p not in STOPWORDS and len(p) > 2]
    return set(parts)

def carregar_blocos_dados() -> List[Tuple[str, str, Set[str]]]:
    try:
        if not os.path.exists(CAMINHO_DADOS):
            return []
        mtime = os.path.getmtime(CAMINHO_DADOS)
        if _dados_cache["mtime"] == mtime and _dados_cache["blocos"]:
            return _dados_cache["blocos"]

        with open(CAMINHO_DADOS, "r", encoding="utf-8") as f:
            raw = f.read().replace("\r\n", "\n").strip()
        if not raw:
            return []

        partes = re.split(r"(?m)^\s*##\s+", raw)
        blocos: List[Tuple[str, str, Set[str]]] = []

        if partes and not raw.lstrip().startswith("##"):
            titulo = "GERAL"
            texto = partes[0].strip()
            toks = _tokenizar(titulo + " " + texto)
            blocos.append((titulo, texto, toks))
            partes = partes[1:]

        for p in partes:
            p = p.strip()
            if not p:
                continue
            linhas = p.split("\n", 1)
            titulo = normalizar_espacos(linhas[0])[:60] if linhas else "BLOCO"
            texto = linhas[1].strip() if len(linhas) > 1 else ""
            texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
            toks = _tokenizar(titulo + " " + texto)
            blocos.append((titulo, texto, toks))

        _dados_cache["mtime"] = mtime
        _dados_cache["blocos"] = blocos
        return blocos
    except:
        return []

def buscar_contexto_dados(pergunta: str, max_chars: int = 650) -> str:
    blocos = carregar_blocos_dados()
    if not blocos:
        return ""
    q_tokens = _tokenizar(pergunta)
    if not q_tokens:
        return ""
    melhor_score = 0
    melhor = None
    for titulo, texto, toks in blocos:
        inter = len(q_tokens.intersection(toks))
        if inter > melhor_score:
            melhor_score = inter
            melhor = (titulo, texto)
    if not melhor or melhor_score < 2:
        return ""
    titulo, texto = melhor
    contexto = normalizar_espacos(f"[{titulo}] {texto}")
    if len(contexto) > max_chars:
        contexto = contexto[:max_chars].rstrip() + "..."
    return contexto

# ================== HISTÓRICO ==================
def adicionar_historico(channel_id: int, author_id: int, role: str, content: str) -> None:
    content = normalizar_espacos(content)
    if not content:
        return
    HISTORICO.setdefault(channel_id, []).append({"author_id": author_id, "role": role, "content": content})
    HISTORICO[channel_id] = HISTORICO[channel_id][-60:]

def historico_filtrado(channel_id: int, user_id: int) -> List[dict]:
    hist = HISTORICO.get(channel_id, [])
    filtrado = [m for m in hist if (m["author_id"] == user_id or m["author_id"] == JAPEX_ID)]
    ultimas = filtrado[-MAX_MSGS_CONTEXT:]
    return [{"role": m["role"], "content": m["content"]} for m in ultimas]

# ================== AÇÕES DISCORD ==================
async def mutar(member: discord.Member, segundos: int) -> bool:
    try:
        ate = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=segundos)
        await member.edit(timed_out_until=ate, reason="moderação")
        return True
    except:
        return False

async def desmutar(member: discord.Member) -> bool:
    try:
        await member.edit(timed_out_until=None, reason="moderação")
        return True
    except:
        return False

async def banir(member: discord.Member) -> bool:
    try:
        if member.guild:
            await member.guild.ban(member, reason="moderação", delete_message_seconds=0)
            return True
        return False
    except:
        return False

async def enviar_no_canal(guild: discord.Guild, canal_ref: str, texto: str, fallback_channel: discord.TextChannel) -> bool:
    try:
        if not guild:
            await fallback_channel.send(texto)
            return True

        canal_ref = (canal_ref or "").strip()
        if not canal_ref or canal_ref.lower() == "current":
            await fallback_channel.send(texto)
            return True

        if canal_ref.isdigit():
            ch = guild.get_channel(int(canal_ref))
            if isinstance(ch, discord.TextChannel):
                await ch.send(texto)
                return True

        name = canal_ref[1:] if canal_ref.startswith("#") else canal_ref
        name = name.strip().lower()

        for ch in guild.text_channels:
            if ch.name.lower() == name:
                await ch.send(texto)
                return True

        for ch in guild.text_channels:
            if name in ch.name.lower():
                await ch.send(texto)
                return True

        await fallback_channel.send(texto)
        return True
    except:
        return False

def achar_role_por_nome(guild: discord.Guild, role_name: str) -> Optional[discord.Role]:
    if not guild or not role_name:
        return None
    rn = role_name.strip().lower()
    for r in guild.roles:
        if (r.name or "").strip().lower() == rn:
            return r
    for r in guild.roles:
        if rn in ((r.name or "").strip().lower()):
            return r
    return None

# ================== IA: ORDEM LIVRE (JSON) ==================
def interpretar_ordem_superior_sync(texto: str, mentions: List[dict], meta: dict) -> dict:
    schema = {
        "action": "none",
        "target_user_ids": [],
        "role_name": None,
        "duration_seconds": None,
        "channel": "current",
        "message": None,
        "order_text": None,
        "reason": ""
    }

    prompt = (
        "Interprete como ORDEM somente se for ordem. Se for pergunta/conversa, retorne action=none.\n"
        "Responda APENAS JSON.\n"
        "Ações:\n"
        "- mute/unmute/ban/ignore/unignore (target_user_ids)\n"
        "- mass_mute_role/mass_ban_role/mass_unmute_role (role_name)\n"
        "- mention_users (target_user_ids + message opcional)\n"
        "- mention_role (role_name + message opcional)\n"
        "- say_channel (channel + message)\n"
        "- silence_on/off, add_order(order_text), reset_orders, none\n"
        f"Limite de massa: {MAX_MASS_TARGETS}. Se pedir mais, action=none e reason.\n"
        "Se não der tecnicamente, action=none e reason curto.\n"
        f"META: {json.dumps(meta, ensure_ascii=False)[:900]}\n"
        f"MENSAGEM: {texto}\n"
        f"MENTIONS: {json.dumps(mentions, ensure_ascii=False)}\n"
        f"JSON_BASE: {json.dumps(schema, ensure_ascii=False)}"
    )

    r = openai.responses.create(
        model=MODEL_CTRL,
        input=[
            {"role": "system", "content": "Responda apenas JSON válido, sem texto extra."},
            {"role": "user", "content": prompt},
        ],
        max_output_tokens=220,
        temperature=0.1,
    )

    raw = (r.output_text or "").strip()
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        return schema

    try:
        obj = json.loads(m.group(0))
        action = str(obj.get("action", "none")).strip()
        allowed = {
            "mute","unmute","ban","ignore","unignore",
            "mass_mute_role","mass_ban_role","mass_unmute_role",
            "mention_users","mention_role","say_channel",
            "silence_on","silence_off","add_order","reset_orders","none"
        }
        if action not in allowed:
            action = "none"

        tids = obj.get("target_user_ids", [])
        out_ids: List[int] = []
        if isinstance(tids, list):
            for x in tids[:MAX_MASS_TARGETS]:
                if isinstance(x, int):
                    out_ids.append(x)
                elif isinstance(x, str) and x.isdigit():
                    out_ids.append(int(x))

        role_name = obj.get("role_name", None)
        role_name = normalizar_espacos(str(role_name))[:60] if role_name else None

        dur = obj.get("duration_seconds", None)
        if isinstance(dur, (int, float)):
            duration_seconds = int(max(1, min(86400, dur)))
        elif isinstance(dur, str) and dur.isdigit():
            duration_seconds = int(max(1, min(86400, int(dur))))
        else:
            duration_seconds = None
        if action == "mute" and duration_seconds is None:
            duration_seconds = 60

        channel = obj.get("channel", "current")
        channel = normalizar_espacos(str(channel))[:80] if channel else "current"

        message = obj.get("message", None)
        message = normalizar_espacos(str(message))[:600] if message else None

        order_text = obj.get("order_text", None)
        order_text = normalizar_espacos(str(order_text))[:260] if order_text else None

        reason = normalizar_espacos(str(obj.get("reason", "")))[:160]

        return {
            "action": action,
            "target_user_ids": out_ids,
            "role_name": role_name,
            "duration_seconds": duration_seconds,
            "channel": channel,
            "message": message,
            "order_text": order_text,
            "reason": reason,
        }
    except:
        return schema

async def interpretar_ordem_superior(texto: str, mentions: List[dict], meta: dict) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(interpretar_ordem_superior_sync, texto, mentions, meta), timeout=12)
    except:
        return {
            "action":"none","target_user_ids":[],"role_name":None,"duration_seconds":None,
            "channel":"current","message":None,"order_text":None,"reason":""
        }

# ================== EXEC ORDENS ==================
async def executar_ordem(ordem: dict, guild: discord.Guild, fallback_channel: discord.TextChannel) -> Tuple[bool, str]:
    action = ordem.get("action", "none")
    tids: List[int] = ordem.get("target_user_ids", []) or []
    role_name = ordem.get("role_name", None)
    dur = ordem.get("duration_seconds", None)
    channel_ref = ordem.get("channel", "current")
    msg = ordem.get("message", None)
    order_text = ordem.get("order_text", None)
    reason = ordem.get("reason", "") or ""

    if action == "none":
        return (False, reason or "")

    if action == "reset_orders":
        limpar_ordens()
        return (True, "Sim.")

    if action == "silence_on":
        set_silencio(True)
        adicionar_ordem("Ficar em silêncio até nova ordem.")
        return (True, "Sim.")

    if action == "silence_off":
        set_silencio(False)
        return (True, "Sim.")

    if action == "add_order":
        if not order_text:
            return (False, "Negado.")
        adicionar_ordem(order_text)
        return (True, "Sim.")

    if action in {"ignore","unignore"}:
        if not tids:
            return (False, "Negado.")
        for uid in tids[:MAX_MASS_TARGETS]:
            if action == "ignore":
                IGNORADOS.add(int(uid))
            else:
                IGNORADOS.discard(int(uid))
        salvar_ignorados(IGNORADOS)
        return (True, "Sim.")

    def members_from_ids(ids: List[int]) -> List[discord.Member]:
        out = []
        for uid in ids[:MAX_MASS_TARGETS]:
            m = guild.get_member(int(uid)) if guild else None
            if isinstance(m, discord.Member):
                out.append(m)
        return out

    if action in {"mention_role","mass_mute_role","mass_ban_role","mass_unmute_role"}:
        if not guild or not role_name:
            return (False, "Negado.")
        role = achar_role_por_nome(guild, role_name)
        if not role:
            return (False, "Negado: cargo não encontrado.")
        members = [m for m in guild.members if role in getattr(m, "roles", []) and not m.bot]
        if action.startswith("mass_") and len(members) > MAX_MASS_TARGETS:
            return (False, f"Negado: limite {MAX_MASS_TARGETS} por ordem.")

        if action == "mention_role":
            text = role.mention if not msg else f"{role.mention} {msg}"
            await enviar_no_canal(guild, channel_ref, text, fallback_channel)
            return (True, "Sim.")

        if action == "mass_unmute_role":
            ok_count = 0
            for m in members[:MAX_MASS_TARGETS]:
                if await desmutar(m):
                    ok_count += 1
                await asyncio.sleep(0.25)
            return (True, f"Desmutados: {ok_count} | Cargo: {role.name}")

        if action == "mass_mute_role":
            seconds = int(dur) if isinstance(dur, int) else 60
            seconds = max(1, min(86400, seconds))
            ok_count = 0
            for m in members[:MAX_MASS_TARGETS]:
                if await mutar(m, seconds):
                    ok_count += 1
                await asyncio.sleep(0.25)
            mot = reason or "Conduta inadequada."
            return (True, f"Mutados: {ok_count} | Cargo: {role.name} | {seconds}s | Motivo: {mot}")

        if action == "mass_ban_role":
            ok_count = 0
            for m in members[:MAX_MASS_TARGETS]:
                if await banir(m):
                    ok_count += 1
                await asyncio.sleep(0.35)
            mot = reason or "Infração grave."
            return (True, f"Banidos: {ok_count} | Cargo: {role.name} | Motivo: {mot}")

    if action == "mention_users":
        if not tids:
            return (False, "Negado.")
        ms = members_from_ids(tids)
        if not ms:
            return (False, "Negado.")
        mentions = " ".join([m.mention for m in ms[:MAX_MASS_TARGETS]])
        text = mentions if not msg else f"{mentions} {msg}"
        await enviar_no_canal(guild, channel_ref, text, fallback_channel)
        return (True, "Sim.")

    if action == "say_channel":
        if not msg:
            return (False, "Negado.")
        ok = await enviar_no_canal(guild, channel_ref, msg, fallback_channel)
        return (ok, "Sim." if ok else "Negado.")

    if action in {"mute","unmute","ban"}:
        if not tids:
            return (False, "Negado.")
        members = members_from_ids(tids)
        if not members:
            return (False, "Negado.")
        if len(members) > MAX_MASS_TARGETS:
            return (False, f"Negado: limite {MAX_MASS_TARGETS} por ordem.")

        if action == "unmute":
            ok_count = 0
            for m in members:
                if await desmutar(m):
                    ok_count += 1
                await asyncio.sleep(0.2)
            return (True, f"Desmutados: {ok_count}")

        if action == "mute":
            seconds = int(dur) if isinstance(dur, int) else 60
            seconds = max(1, min(86400, seconds))
            ok_count = 0
            for m in members:
                if await mutar(m, seconds):
                    ok_count += 1
                await asyncio.sleep(0.2)
            mot = reason or "Conduta inadequada."
            if len(members) == 1:
                return (True, f"Mutado: {members[0].display_name} | {seconds}s | Motivo: {mot}")
            return (True, f"Mutados: {ok_count} | {seconds}s | Motivo: {mot}")

        if action == "ban":
            ok_count = 0
            for m in members:
                if await banir(m):
                    ok_count += 1
                await asyncio.sleep(0.3)
            mot = reason or "Infração grave."
            if len(members) == 1:
                return (True, f"Banido: {members[0].display_name} | permanente | Motivo: {mot}")
            return (True, f"Banidos: {ok_count} | Motivo: {mot}")

    return (False, "Negado.")

# ================== IA: MODERAÇÃO (punição por denúncia/menção direta) ==================
ALLOWED_DISCIPLINE = ["none", "mute_60", "mute_300", "mute_900", "ban"]

def decidir_punicao_e_motivo_sync(payload: dict) -> dict:
    schema = {"action": "none", "reason": ""}

    prompt = (
        "Você é um moderador firme e justo.\n"
        "Escolha UMA ação: none, mute_60, mute_300, mute_900, ban.\n"
        "Pune: calúnia/difamação, assédio, humilhação, ameaça, desrespeito grave.\n"
        "Se envolver desrespeito direto ao Senhor Japex, puna imediatamente.\n"
        "Se não houver evidência clara, escolha none.\n"
        "reason curto (0,5–1 linha), objetivo.\n"
        "Responda APENAS JSON: {\"action\":\"...\",\"reason\":\"...\"}\n"
        f"PAYLOAD: {json.dumps(payload, ensure_ascii=False)[:2200]}"
    )

    r = openai.responses.create(
        model=MODEL_CTRL,
        input=[
            {"role": "system", "content": "Responda apenas JSON válido."},
            {"role": "user", "content": prompt},
        ],
        max_output_tokens=120,
        temperature=0.1,
    )

    raw = (r.output_text or "").strip()
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        return schema

    try:
        obj = json.loads(m.group(0))
        act = str(obj.get("action", "none")).strip()
        if act not in ALLOWED_DISCIPLINE:
            act = "none"
        reason = normalizar_espacos(str(obj.get("reason", "")))[:140]
        if act == "none":
            reason = ""
        return {"action": act, "reason": reason}
    except:
        return schema

async def decidir_punicao_e_motivo(payload: dict) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(decidir_punicao_e_motivo_sync, payload), timeout=12)
    except:
        return {"action": "none", "reason": ""}

def duracao_por_action(act: str) -> int:
    return {"mute_60": 60, "mute_300": 300, "mute_900": 900}.get(act, 0)

# ================== IA: CHAT ==================
PERSONA_BASE = (
    "Você é um personagem militar (do jogo) com conduta e formalidade, mas conversa normal.\n"
    "Responda curto e direto, de preferência em UMA linha.\n"
    "NUNCA faça perguntas e NUNCA puxe assunto.\n"
    "Pode ter opiniões próprias (inclusive política e sobre membros), sem neutralidade fingida.\n"
    "Tratamento:\n"
    "- Se author_id == fundador_id: trate como 'Senhor Japex' com bajulação formal.\n"
    "- Se author_id != fundador_id: PROIBIDO chamar o autor de Japex/Fundador.\n"
    "Regras:\n"
    "- Se perguntarem 'qual é seu modelo', responda exatamente: JapexUltimation1.2\n"
    "- Só diga quem criou a IA se perguntarem explicitamente 'quem criou/fez/programou'. Resposta: 'Foi o Baddx_xd.'\n"
    "- Se perguntarem donos/chefões/adms, NÃO inclua o criador da IA.\n"
    "Vocativo:\n"
    "- Prefira vocativo por patente/cargo se fizer sentido; senão use o nome.\n"
    "- Use formato: 'Sim, <Vocativo>.' / 'Negativo, <Vocativo>.' quando couber.\n"
    "Nunca diga que é IA.\n"
)

def montar_system(author: discord.Member, contexto_dados: str) -> str:
    roles = roles_curto(author, max_roles=8)
    guess = best_patente_title(author) or author.display_name
    ordens = carregar_ordens()
    chefes_txt = ", ".join([t for (_, t, _) in CHEFOES_PUBLICOS])

    extra = (
        f"author_id={author.id} fundador_id={JAPEX_ID}. "
        f"display_name={author.display_name}. roles={roles}. best_guess={guess}. "
        f"CHEFOES_PUBLICOS={chefes_txt}. "
        "Limite forte: 26 tokens (ou 40 se pedirem explicação/texto). Saída em UMA linha.\n"
    )
    if ordens:
        extra += " ORDENS DO FUNDADOR: " + ordens
    if contexto_dados:
        extra += " BASE DO SERVIDOR: " + contexto_dados
    return PERSONA_BASE + " " + extra

def quer_texto(texto: str) -> bool:
    t = (texto or "").lower()
    return any(g in t for g in [
        "faça um texto", "faz um texto", "texto gramatical",
        "explique", "explica", "detalhe", "detalha",
        "passo a passo", "redação", "redacao"
    ])

def chat_sync(mensagens: List[dict], max_tokens: int) -> str:
    r = openai.responses.create(
        model=MODEL_CHAT,
        input=mensagens,
        max_output_tokens=max_tokens,
        temperature=0.6,
    )
    return (r.output_text or "").strip() or "Entendido."

def pergunta_modelo(texto: str) -> bool:
    t = norm(texto)
    return ("qual" in t and "modelo" in t) or ("seu modelo" in t) or ("qual é o modelo" in t)

def pergunta_criador(texto: str) -> bool:
    t = norm(texto)
    return ("quem" in t) and any(k in t for k in ["programou", "criou", "fez", "criador"])

async def gerar_resposta(texto: str, author: discord.Member, channel_id: int) -> str:
    # respostas determinísticas (economiza token + evita alucinação)
    if pergunta_modelo(texto):
        return MODEL_PUBLIC_NAME
    if pergunta_criador(texto):
        return "Foi o Baddx_xd."

    usar_texto = quer_texto(texto)
    max_tokens = 40 if usar_texto else 26

    contexto = buscar_contexto_dados(texto, max_chars=650)
    system = montar_system(author, contexto)

    msgs: List[dict] = [{"role": "system", "content": system}]
    msgs.extend(historico_filtrado(channel_id, author.id))
    msgs.append({"role": "user", "content": texto})

    try:
        out = await asyncio.wait_for(asyncio.to_thread(chat_sync, msgs, max_tokens), timeout=12)
        resp = sanitizar_resposta(out)

        # ajuda prática: suporte / ping Japex (somente se parecer caso sério)
        if needs_support_hint(texto) and SUPPORT_CHANNEL_MENTION not in resp:
            resp = sanitizar_resposta(f"{resp} | Suporte: {SUPPORT_CHANNEL_MENTION}")
        if is_serious_issue(texto) and (JAPEX_MENTION not in resp):
            resp = sanitizar_resposta(f"{resp} | {JAPEX_MENTION}")

        return resp
    except Exception as e:
        print("ERRO OPENAI CHAT:", repr(e))
        resp = sanitizar_resposta(f"Entendido, {vocativo(author)}.")
        if needs_support_hint(texto):
            resp = sanitizar_resposta(f"{resp} | Suporte: {SUPPORT_CHANNEL_MENTION}")
        return resp

# ================== REPLY ==================
async def pegar_mensagem_referenciada(msg: discord.Message) -> Optional[discord.Message]:
    try:
        if not msg.reference:
            return None
        if isinstance(msg.reference.resolved, discord.Message):
            return msg.reference.resolved
        if msg.reference.message_id and msg.channel:
            return await msg.channel.fetch_message(msg.reference.message_id)
    except:
        return None
    return None

def remover_mencao_bot(texto: str) -> str:
    if cliente.user:
        texto = texto.replace(cliente.user.mention, "")
    return normalizar_espacos(texto)

# ================== EVENTS ==================
@cliente.event
async def on_ready():
    print(f"bot ligado ({MODEL_CHAT} + {MODEL_CTRL}) | {MODEL_PUBLIC_NAME}")

@cliente.event
async def on_message(mensagem: discord.Message):
    if mensagem.author.bot:
        return
    if not isinstance(mensagem.author, discord.Member):
        return

    # só age se o bot foi mencionado
    if cliente.user not in mensagem.mentions:
        return

    loop_time = asyncio.get_event_loop().time()
    if already_processed(mensagem.id, loop_time):
        return

    # lock: não enfileira
    try:
        await asyncio.wait_for(ocupado.acquire(), timeout=0.02)
    except asyncio.TimeoutError:
        return

    try:
        if not await respeitar_delay_e_cooldown(mensagem.author.id):
            return

        guild = mensagem.guild
        channel = mensagem.channel
        extra = typing_extra(mensagem.author.id)

        if esta_silenciado() and (not guild or not autoridade_sobre_bot(mensagem.author, guild)):
            return

        if (mensagem.author.id in IGNORADOS) and (not is_japex(mensagem.author.id)):
            return

        texto_limpo = remover_mencao_bot(mensagem.content)

        # ---------- ORDENS (se autoridade) ----------
        if guild and autoridade_sobre_bot(mensagem.author, guild):
            mentions = []
            for m in mensagem.mentions:
                if cliente.user and m.id == cliente.user.id:
                    continue
                if isinstance(m, discord.Member):
                    mentions.append({"user_id": m.id, "display_name": m.display_name})

            meta = {
                "channel": getattr(channel, "name", ""),
                "mass_limit": MAX_MASS_TARGETS,
                "capabilities": [
                    "mute/unmute/ban/ignore/unignore",
                    "mass_*_role", "mention_users/mention_role",
                    "say_channel", "silence_on/off",
                    "add_order/reset_orders"
                ],
            }

            ordem = await interpretar_ordem_superior(texto_limpo, mentions, meta)

            # FIX: se não for ordem e parecer pergunta -> cai pra chat normal
            if ordem.get("action") != "none" or not parece_pergunta(texto_limpo):
                async with channel.typing():
                    await asyncio.sleep(extra)

                ok, resp = await executar_ordem(ordem, guild, channel)
                if ok:
                    if resp and resp != "Sim.":
                        await channel.send(resp)
                    else:
                        await channel.send(ack_superior(mensagem.author))
                else:
                    # sem ACK burro aqui: só responde se tiver reason; caso contrário, cai para chat abaixo
                    if resp:
                        await channel.send(sanitizar_resposta(resp))
                return

        # ---------- REPLY-DENÚNCIA ----------
        ref = await pegar_mensagem_referenciada(mensagem)
        if ref and not ref.author.bot and guild:
            alvo_ref = guild.get_member(ref.author.id)
            if alvo_ref:
                payload = {
                    "mode": "reply_report",
                    "reporter_id": mensagem.author.id,
                    "target_id": alvo_ref.id,
                    "target_text": normalizar_espacos(ref.content or "")[:900],
                    "report_text": normalizar_espacos(texto_limpo)[:500],
                    "mentions_bot": True,
                }
                decision = await decidir_punicao_e_motivo(payload)
                act = decision.get("action", "none")
                reason = decision.get("reason", "")
                if act != "none":
                    async with channel.typing():
                        await asyncio.sleep(extra)
                    if act == "ban":
                        ok = await banir(alvo_ref)
                        await channel.send(
                            f"Banido: {alvo_ref.display_name} | permanente | Motivo: {reason or 'Infração grave.'}"
                            if ok else "Negado."
                        )
                        return
                    dur = duracao_por_action(act)
                    ok = await mutar(alvo_ref, dur)
                    await channel.send(
                        f"Mutado: {alvo_ref.display_name} | {dur}s | Motivo: {reason or 'Conduta inadequada.'}"
                        if ok else "Negado."
                    )
                    return

        # ---------- CONVERSA NORMAL ----------
        if not texto_limpo:
            return

        async with channel.typing():
            await asyncio.sleep(extra)
            resposta = await gerar_resposta(texto_limpo, mensagem.author, channel.id)

        adicionar_historico(channel.id, mensagem.author.id, "user", texto_limpo)
        adicionar_historico(channel.id, mensagem.author.id, "assistant", resposta)

        await mensagem.reply(resposta)

    finally:
        if ocupado.locked():
            ocupado.release()

cliente.run(TOKEN_DISCORD)
