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
MODEL_CHAT = "gpt-4o"
MODEL_CTRL = "gpt-5-nano"

MODEL_PUBLIC_NAME = "JapexUltimation1.6"

# ================== PATHS ==================
PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
CAMINHO_DADOS = os.path.join(PASTA_ATUAL, "dados.txt")
CAMINHO_ORDENS = os.path.join(PASTA_ATUAL, "ordens.txt")
CAMINHO_IGNORE = os.path.join(PASTA_ATUAL, "ignorar.txt")
CAMINHO_SILENCIO = os.path.join(PASTA_ATUAL, "silencio.flag")

# ================== IDs FIXOS ==================
JAPEX_ID = 1331505963622076476
LALOMAIO_ID = 1251950121068007496
SANTIAGO_ID = 1401691898816762018
PURTUGA_ID = 1429995893305643082
RIQUEJOO_ID = None
BADD_ID = 1319506938391957575  # autoridade invisível

JAPEX_MENTION = f"<@{JAPEX_ID}>"

CHEFOES_PUBLICOS = [
    ("japex", "Fundador", 0),
    ("lalomaio", "Criador do Exército", 1),
    ("santiago", "Administrador", 2),
    ("purtuga", "Supremo Tribunal Militar", 3),
    ("riquejoo", "Moderador", 4),
]
CHEFOES_IDS = {
    "lalomaio": LALOMAIO_ID,
    "santiago": SANTIAGO_ID,
    "purtuga": PURTUGA_ID,
    "riquejoo": RIQUEJOO_ID,
}

# ================== SUPORTE ==================
SUPPORT_CHANNEL_ID = 1450602972773089493
SUPPORT_CHANNEL_MENTION = f"<#{SUPPORT_CHANNEL_ID}>"

# ================== PATENTES EB ==================
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
ocupado = asyncio.Lock()

# ================== ANTI DUPLICAÇÃO ==================
_PROCESSED: Dict[int, float] = {}
PROCESSED_TTL = 120.0

# ================== HISTÓRICO ==================
HISTORICO: Dict[int, List[dict]] = {}
MAX_MSGS_CONTEXT = 3

# ================== RATE / DIGITANDO ==================
MIN_DELAY_SECONDS = 1.2
EXTRA_TYPING_RANGE = (1.6, 2.4)
USER_COOLDOWN_SECONDS = 1.6
_last_user_action: Dict[int, float] = {}

MAX_MASS_TARGETS = 20

# ================== UTIL ==================
def normalizar_espacos(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def norm(s: str) -> str:
    return normalizar_espacos(s).lower()

def is_japex(uid: int) -> bool:
    return uid == JAPEX_ID

def is_badd(uid: int) -> bool:
    return uid == BADD_ID

def esta_silenciado() -> bool:
    return os.path.exists(CAMINHO_SILENCIO)

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
    return any(low.startswith(s) for s in starters)

_BAD_END = {
    "em","no","na","nos","nas","de","do","da","dos","das","pra","pro","para","por",
    "com","sem","e","ou","que","a","o","as","os","um","uma"
}

def sanitizar_resposta(msg: str) -> str:
    msg = normalizar_espacos(msg).replace("\n", " ")
    msg = normalizar_espacos(msg)
    if not msg:
        return "Entendido."
    parts = msg.split()
    if parts:
        last = parts[-1].strip(".,;:!?)\"]}").lower()
        if last in _BAD_END:
            if last == "em":
                msg = " ".join(parts[:-1]).rstrip()
                msg = (msg + " em paz").strip()
            else:
                msg = " ".join(parts[:-1]).rstrip()
    if not re.search(r"[.!?…]$", msg):
        msg = msg.rstrip(" ,;:") + "."
    if len(msg) > 280:
        msg = msg[:280].rstrip() + "..."
    return msg

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

IGNORADOS: Set[int] = carregar_ignorados()

# ================== CHEFÕES ==================
def chefe_publico_info(member: discord.Member) -> Optional[Tuple[str, str, int]]:
    if is_japex(member.id):
        return ("japex", "Fundador", 0)
    for key, titulo, rank in CHEFOES_PUBLICOS:
        if key == "japex":
            continue
        cid = CHEFOES_IDS.get(key)
        if cid and member.id == cid:
            return (key, titulo, rank)
    return None

# ================== PATENTES ==================
def rank_patente(member: discord.Member) -> Optional[int]:
    best = None
    for role in getattr(member, "roles", []):
        rnome = role.name or ""
        for tag, _, ordem in PATENTES:
            if tag in rnome:
                best = ordem if best is None else min(best, ordem)
    if best is not None:
        return best
    for role in getattr(member, "roles", []):
        rnome = role.name or ""
        for _, titulo, ordem in PATENTES:
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
        for _, titulo, ordem in PATENTES:
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
        for tag, _, ordem in PATENTES:
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
    if is_japex(author.id) or is_badd(author.id):
        return True
    if chefe_publico_info(author) is not None:
        return True
    if not guild or not cliente.user:
        return False
    bm = guild.get_member(cliente.user.id)
    if not bm:
        return False
    a = rank_patente(author)
    b = rank_patente(bm)
    if a is None or b is None:
        return False
    return a < b

# ================== PERMS / HIERARQUIA ==================
def bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    if not guild or not cliente.user:
        return None
    return guild.get_member(cliente.user.id)

def bot_has_perm(guild: discord.Guild, perm_name: str) -> bool:
    bm = bot_member(guild)
    if not bm:
        return False
    perms = bm.guild_permissions
    return getattr(perms, perm_name, False)

def bot_can_act_on(guild: discord.Guild, target: discord.Member) -> bool:
    bm = bot_member(guild)
    if not bm or not target:
        return False
    try:
        return bm.top_role > target.top_role
    except:
        return False

def bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
    bm = bot_member(guild)
    if not bm or not role:
        return False
    try:
        return (not role.is_default()) and (bm.top_role > role)
    except:
        return False

# ================== DISCORD ACTIONS ==================
async def mutar(member: discord.Member, segundos: int) -> Tuple[bool, str]:
    try:
        ate = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=segundos)
        await member.edit(timed_out_until=ate, reason="moderação")
        return True, ""
    except Exception as e:
        return False, repr(e)

async def desmutar(member: discord.Member) -> Tuple[bool, str]:
    try:
        await member.edit(timed_out_until=None, reason="moderação")
        return True, ""
    except Exception as e:
        return False, repr(e)

async def banir(member: discord.Member) -> Tuple[bool, str]:
    try:
        if member.guild:
            await member.guild.ban(member, reason="moderação", delete_message_seconds=0)
            return True, ""
        return False, "sem guild"
    except Exception as e:
        return False, repr(e)

async def remove_role(member: discord.Member, role: discord.Role) -> Tuple[bool, str]:
    try:
        await member.remove_roles(role, reason="moderação")
        return True, ""
    except Exception as e:
        return False, repr(e)

# ================== IA: INTERPRETA ORDEM (JSON) ==================
def interpretar_ordem_superior_sync(texto: str, mentions: List[dict], meta: dict) -> dict:
    schema = {
        "action": "none",
        "target_user_ids": [],
        "duration_seconds": None,
        "reason": ""
    }

    prompt = (
        "Interprete como ORDEM somente se for ordem. Se for conversa/pergunta, action=none.\n"
        "Ações:\n"
        "- mute/unmute/ban (target_user_ids + duration_seconds opcional)\n"
        "- remove_all_roles (target_user_ids)  // 'tire todos os cargos'\n"
        "Retorne JSON apenas.\n"
        f"META: {json.dumps(meta, ensure_ascii=False)[:900]}\n"
        f"MENSAGEM: {texto}\n"
        f"MENTIONS: {json.dumps(mentions, ensure_ascii=False)}\n"
        f"JSON_BASE: {json.dumps(schema, ensure_ascii=False)}"
    )

    r = openai.responses.create(
        model=MODEL_CTRL,
        input=[
            {"role": "system", "content": "Responda apenas JSON válido."},
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
        allowed = {"mute","unmute","ban","remove_all_roles","none"}
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

        dur = obj.get("duration_seconds", None)
        if isinstance(dur, (int, float)):
            duration_seconds = int(max(1, min(86400, dur)))
        elif isinstance(dur, str) and dur.isdigit():
            duration_seconds = int(max(1, min(86400, int(dur))))
        else:
            duration_seconds = None
        if action == "mute" and duration_seconds is None:
            duration_seconds = 60

        reason = normalizar_espacos(str(obj.get("reason", "")))[:160]
        return {"action": action, "target_user_ids": out_ids, "duration_seconds": duration_seconds, "reason": reason}
    except:
        return schema

async def interpretar_ordem_superior(texto: str, mentions: List[dict], meta: dict) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(interpretar_ordem_superior_sync, texto, mentions, meta), timeout=12)
    except:
        return {"action":"none","target_user_ids":[],"duration_seconds":None,"reason":""}

# ================== EXEC ORDENS (NUNCA MENTE) ==================
async def executar_ordem(ordem: dict, guild: discord.Guild) -> Tuple[bool, str]:
    action = ordem.get("action", "none")
    tids: List[int] = ordem.get("target_user_ids", []) or []
    dur = ordem.get("duration_seconds", None)
    reason = ordem.get("reason", "") or ""

    if action == "none":
        return False, reason

    if not tids:
        return False, "Faltou marcar o alvo."

    members: List[discord.Member] = []
    for uid in tids[:MAX_MASS_TARGETS]:
        m = guild.get_member(int(uid))
        if isinstance(m, discord.Member):
            members.append(m)
    if not members:
        return False, "Não achei o alvo no servidor."

    # -------- mute/unmute/ban --------
    if action in {"mute","unmute","ban"}:
        if action in {"mute","unmute"} and not bot_has_perm(guild, "moderate_members"):
            return False, "Eu não tenho permissão de moderar membros (timeout)."
        if action == "ban" and not bot_has_perm(guild, "ban_members"):
            return False, "Eu não tenho permissão de banir membros."

        for m in members:
            if not bot_can_act_on(guild, m):
                return False, f"Não posso agir em {m.display_name}: cargo acima/igual ao meu."

        if action == "unmute":
            okc = 0
            last_err = ""
            for m in members:
                ok, err = await desmutar(m)
                okc += 1 if ok else 0
                last_err = err or last_err
                await asyncio.sleep(0.15)
            if okc == 0:
                return False, f"Falhou ao desmutar ({last_err or 'sem detalhes'})."
            return True, f"Desmutados: {okc}."

        if action == "mute":
            seconds = int(dur) if isinstance(dur, int) else 60
            seconds = max(1, min(86400, seconds))
            okc = 0
            last_err = ""
            for m in members:
                ok, err = await mutar(m, seconds)
                okc += 1 if ok else 0
                last_err = err or last_err
                await asyncio.sleep(0.15)
            if okc == 0:
                return False, f"Falhou ao mutar ({last_err or 'sem detalhes'})."
            mot = reason or "Conduta inadequada."
            if len(members) == 1:
                return True, f"Mutado: {members[0].display_name} | {seconds}s | Motivo: {mot}."
            return True, f"Mutados: {okc} | {seconds}s | Motivo: {mot}."

        if action == "ban":
            okc = 0
            last_err = ""
            for m in members:
                ok, err = await banir(m)
                okc += 1 if ok else 0
                last_err = err or last_err
                await asyncio.sleep(0.2)
            if okc == 0:
                return False, f"Falhou ao banir ({last_err or 'sem detalhes'})."
            mot = reason or "Infração grave."
            if len(members) == 1:
                return True, f"Banido: {members[0].display_name} | permanente | Motivo: {mot}."
            return True, f"Banidos: {okc} | Motivo: {mot}."

    # -------- remove_all_roles --------
    if action == "remove_all_roles":
        if not bot_has_perm(guild, "manage_roles"):
            return False, "Eu não tenho permissão de gerenciar cargos."

        alvo = members[0]  # 1 alvo por segurança
        if not bot_can_act_on(guild, alvo):
            return False, f"Não posso mexer em {alvo.display_name}: cargo acima/igual ao meu."

        removable = []
        kept_managed = 0
        kept_higher = 0

        for r in list(getattr(alvo, "roles", [])):
            if r.is_default():
                continue
            if r.managed:
                kept_managed += 1
                continue
            if not bot_can_manage_role(guild, r):
                kept_higher += 1
                continue
            removable.append(r)

        if not removable:
            return False, f"Não há cargos removíveis em {alvo.display_name} (gerenciados/acima do meu cargo)."

        removed = 0
        last_err = ""
        for r in removable:
            ok, err = await remove_role(alvo, r)
            removed += 1 if ok else 0
            last_err = err or last_err
            await asyncio.sleep(0.12)

        if removed == 0:
            return False, f"Falhou ao remover cargos ({last_err or 'sem detalhes'})."
        return True, f"Cargos removidos: {removed} | Alvo: {alvo.display_name}."

    return False, "Essa ordem não está ativa."

# ================== IA: CHAT (menos moralista) ==================
PERSONA_BASE = (
    "Você é um personagem militar (do jogo) com postura e respeito, mas conversa normal.\n"
    "Responda curto e direto, UMA linha.\n"
    "NUNCA faça perguntas e NUNCA puxe assunto.\n"
    "Pode ter opiniões próprias.\n"
    "Se perguntarem 'qual é seu modelo', responda exatamente: JapexUltimation1.6\n"
    "Se perguntarem quem criou/fez/programou: 'Foi o Baddx_xd.'\n"
    "Sempre finalize com pontuação.\n"
    "Nunca diga que é IA.\n"
)

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

async def gerar_resposta(texto: str, author: discord.Member) -> str:
    if pergunta_modelo(texto):
        return MODEL_PUBLIC_NAME
    if pergunta_criador(texto):
        return "Foi o Baddx_xd."

    # pequeno, mas suficiente pra fechar 1 linha
    max_tokens = 44

    msgs = [
        {"role": "system", "content": PERSONA_BASE},
        {"role": "user", "content": texto},
    ]
    out = await asyncio.wait_for(asyncio.to_thread(chat_sync, msgs, max_tokens), timeout=12)
    return sanitizar_resposta(out)

# ================== HELPERS ==================
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
    if cliente.user not in mensagem.mentions:
        return

    loop_time = asyncio.get_event_loop().time()
    if already_processed(mensagem.id, loop_time):
        return

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

        if (mensagem.author.id in IGNORADOS) and (not is_japex(mensagem.author.id)):
            return

        texto_limpo = remover_mencao_bot(mensagem.content)
        if not texto_limpo:
            return

        # ========= SUPERIOR: tenta ordem; se não for ordem e não for pergunta -> só ACK =========
        if guild and autoridade_sobre_bot(mensagem.author, guild):
            mentions = []
            for m in mensagem.mentions:
                if cliente.user and m.id == cliente.user.id:
                    continue
                if isinstance(m, discord.Member):
                    mentions.append({"user_id": m.id, "display_name": m.display_name})

            ordem = await interpretar_ordem_superior(texto_limpo, mentions, {"author_id": mensagem.author.id})

            if ordem.get("action") != "none":
                async with channel.typing():
                    await asyncio.sleep(extra)
                ok, resp = await executar_ordem(ordem, guild)
                await channel.send(sanitizar_resposta(resp if resp else ack_superior(mensagem.author)))
                return

            # não é ordem: se não for pergunta, não vira moralista — só obedece/ack
            if not parece_pergunta(texto_limpo):
                await channel.send(ack_superior(mensagem.author))
                return
            # se for pergunta, cai pra chat normal

        # ========= CONVERSA NORMAL =========
        async with channel.typing():
            await asyncio.sleep(extra)
            resposta = await gerar_resposta(texto_limpo, mensagem.author)

        await mensagem.reply(resposta)

    finally:
        if ocupado.locked():
            ocupado.release()

cliente.run(TOKEN_DISCORD)
