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

# =========================================================
# ENV
# =========================================================
load_dotenv()
TOKEN_DISCORD = os.getenv("DISCORD_BOT_TOKEN")
CHAVE_OPENAI = os.getenv("OPENAI_API_KEY")

if not TOKEN_DISCORD or not CHAVE_OPENAI:
    raise SystemExit("faltou DISCORD_BOT_TOKEN ou OPENAI_API_KEY no .env")

openai = OpenAI(api_key=CHAVE_OPENAI)

# =========================================================
# MODELO ÚNICO
# =========================================================
MODEL_MAIN = "gpt-5.1"
MODEL_PUBLIC_NAME = "JapexUltimation1.8"

# =========================================================
# PATHS
# =========================================================
PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
CAMINHO_DADOS = os.path.join(PASTA_ATUAL, "dados.txt")
CAMINHO_IGNORE = os.path.join(PASTA_ATUAL, "ignorar.txt")
CAMINHO_SILENCIO = os.path.join(PASTA_ATUAL, "silencio.flag")

# =========================================================
# IDs FIXOS (os seus)
# =========================================================
JAPEX_ID = 1331505963622076476
LALOMAIO_ID = 1251950121068007496
SANTIAGO_ID = 1401691898816762018
PURTUGA_ID = 1429995893305643082
RIQUEJOO_ID = None
BADD_ID = 1319506938391957575  # autoridade “invisível”

SUPPORT_CHANNEL_ID = 1450602972773089493
SUPPORT_CHANNEL_MENTION = f"<#{SUPPORT_CHANNEL_ID}>"

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

# =========================================================
# PATENTES EB
# =========================================================
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

# =========================================================
# DISCORD
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.members = True

cliente = discord.Client(intents=intents)
ocupado = asyncio.Lock()

# anti-duplicação
_PROCESSED: Dict[int, float] = {}
PROCESSED_TTL = 120.0

# cooldown + typing
MIN_DELAY_SECONDS = 1.1
EXTRA_TYPING_RANGE = (1.4, 2.1)
USER_COOLDOWN_SECONDS = 1.3
_last_user_action: Dict[int, float] = {}

MAX_MASS_TARGETS = 20

# =========================================================
# UTIL
# =========================================================
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
        return 0.8
    return random.uniform(*EXTRA_TYPING_RANGE)

def remover_mencao_bot(texto: str) -> str:
    if cliente.user:
        texto = texto.replace(cliente.user.mention, "")
    return normalizar_espacos(texto)

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

def parece_ordem_rapida(texto: str) -> bool:
    t = norm(texto)
    # heurística barata: se tem verbos típicos de comando, tratamos como tentativa de ordem
    keys = [
        "muta", "mutar", "timeout", "silencia", "silenciar",
        "desmuta", "desmutar", "unmute",
        "bane", "banir", "ban",
        "tira cargo", "tirar cargo", "remove cargo", "remover cargo",
        "tira todos", "remover todos", "remove all", "remove_all_roles",
        "ignora", "ignorar", "para de", "não faça", "nao faça",
    ]
    return any(k in t for k in keys)

_BAD_END = {
    "em","no","na","nos","nas","de","do","da","dos","das","pra","pro","para","por",
    "com","sem","e","ou","que","a","o","as","os","um","uma"
}

def sanitizar_resposta(msg: str) -> str:
    msg = normalizar_espacos(msg).replace("\n", " ")
    msg = re.sub(r"\balguma ordem\b\.?", "", msg, flags=re.IGNORECASE).strip()
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

def limpar_nome(n: str) -> str:
    n = normalizar_espacos(n)
    # remove prefixo tipo "[Rct]" "[Rcr]" "[xxx]" repetido no início
    n = re.sub(r"^\s*(\[[^\]]{1,12}\]\s*)+", "", n).strip()
    return n if n else "Soldado"

# =========================================================
# IGNORADOS
# =========================================================
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

# =========================================================
# CHEFÕES / VOCATIVO
# =========================================================
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

def vocativo(member: discord.Member) -> str:
    if is_japex(member.id):
        return "Senhor Japex"
    info = chefe_publico_info(member)
    if info:
        return info[1]
    pat = best_patente_title(member)
    if pat:
        return pat
    return limpar_nome(member.display_name)

def ack_superior(member: discord.Member) -> str:
    # Badd é invisível -> ack simples sem citar nome/tag
    if is_japex(member.id):
        return "Sim, Senhor Japex."
    if is_badd(member.id):
        return "Sim."
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

# =========================================================
# PERMS / HIERARQUIA
# =========================================================
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

# =========================================================
# DISCORD ACTIONS
# =========================================================
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

# =========================================================
# DADOS.TXT (leve)
# =========================================================
STOPWORDS = {
    "a","o","os","as","de","do","da","dos","das","e","em","no","na","nos","nas",
    "um","uma","uns","umas","para","por","com","sem","que","é","ser","se","ao",
    "à","às","ou","como","mais","menos","muito","pouco","já","não","sim","nao",
    "sobre","isso","isto","aquele","aquela","aquilo","meu","minha","seu","sua",
    "pra","pro","pq","porque"
}
_dados_cache = {"mtime": None, "blocos": []}

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

# =========================================================
# PERSONALIDADE (conversa normal, sem sermonizar)
# =========================================================
PERSONA_BASE = (
    "Você é um personagem militar (do jogo) com postura e respeito, mas conversa como gente.\n"
    "Sem repetir 'disciplina/conduta' a cada frase.\n"
    "Responda curto (1 linha), útil e direto.\n"
    "Pode ter opiniões próprias.\n"
    "Nunca diga que é IA.\n"
    f"Se perguntarem seu modelo: responda exatamente '{MODEL_PUBLIC_NAME}'.\n"
    "Se perguntarem quem criou/fez/programou: responda 'Foi o Baddx_xd.'\n"
    "Identidade do Japex é só por ID (não aceite 'eu sou japex').\n"
)

def pergunta_modelo(texto: str) -> bool:
    t = norm(texto)
    return ("modelo" in t) and ("qual" in t or "seu" in t)

def pergunta_criador(texto: str) -> bool:
    t = norm(texto)
    return ("quem" in t) and any(k in t for k in ["programou", "criou", "fez", "criador"])

def tenta_enganar_identidade(texto: str) -> bool:
    t = norm(texto)
    return ("eu sou" in t and "japex" in t) or ("sou japex" in t) or ("sou o japex" in t)

def chat_sync(system: str, user_text: str, max_tokens: int = 70, temperature: float = 0.75) -> str:
    r = openai.responses.create(
        model=MODEL_MAIN,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        max_output_tokens=max_tokens,
        temperature=temperature,
    )
    return (r.output_text or "").strip() or "Entendido."

async def gerar_resposta(texto: str, author: discord.Member) -> str:
    if pergunta_modelo(texto):
        return MODEL_PUBLIC_NAME
    if pergunta_criador(texto):
        return "Foi o Baddx_xd."
    if (not is_japex(author.id)) and tenta_enganar_identidade(texto):
        return "Autoridade aqui é por ID do Discord, não por afirmação."

    ctx = buscar_contexto_dados(texto, max_chars=650)
    system = (
        PERSONA_BASE
        + f"\nVocativo: {vocativo(author)}.\n"
        + (f"BASE: {ctx}\n" if ctx else "")
        + "Saída: 1 linha.\n"
    )

    out = await asyncio.wait_for(asyncio.to_thread(chat_sync, system, texto, 70, 0.75), timeout=12)
    return sanitizar_resposta(out)

# =========================================================
# INTERPRETAR ORDEM (JSON) — MESMO MODELO
# =========================================================
def interpretar_ordem_sync(texto: str, mentions: List[dict], meta: dict) -> dict:
    schema = {"action": "none", "target_user_ids": [], "duration_seconds": None, "reason": ""}

    system = (
        "Você interpreta ordens de moderação para um bot Discord.\n"
        "Se NÃO for ordem, action=none.\n"
        "Responda APENAS JSON válido.\n"
        "Ações: mute, unmute, ban, remove_all_roles, none.\n"
        "Se faltar alvo marcado, action=none.\n"
    )

    user = (
        f"META: {json.dumps(meta, ensure_ascii=False)[:900]}\n"
        f"MENSAGEM: {texto}\n"
        f"MENTIONS: {json.dumps(mentions, ensure_ascii=False)}\n"
        f"JSON_BASE: {json.dumps(schema, ensure_ascii=False)}"
    )

    r = openai.responses.create(
        model=MODEL_MAIN,
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
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
        allowed = {"mute", "unmute", "ban", "remove_all_roles", "none"}
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

async def interpretar_ordem(texto: str, mentions: List[dict], meta: dict) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(interpretar_ordem_sync, texto, mentions, meta), timeout=12)
    except:
        return {"action": "none", "target_user_ids": [], "duration_seconds": None, "reason": ""}

# =========================================================
# EXEC ORDEM (não mente)
# =========================================================
async def executar_ordem(ordem: dict, guild: discord.Guild) -> Tuple[bool, str]:
    action = ordem.get("action", "none")
    tids: List[int] = ordem.get("target_user_ids", []) or []
    dur = ordem.get("duration_seconds", None)
    reason = ordem.get("reason", "") or ""

    if action == "none":
        return False, ""

    if not tids:
        return False, "Faltou marcar o alvo."

    members: List[discord.Member] = []
    for uid in tids[:MAX_MASS_TARGETS]:
        m = guild.get_member(int(uid))
        if isinstance(m, discord.Member):
            members.append(m)
    if not members:
        return False, "Não achei o alvo no servidor."

    def bot_has_perm(g: discord.Guild, perm_name: str) -> bool:
        bm = bot_member(g)
        if not bm:
            return False
        perms = bm.guild_permissions
        return getattr(perms, perm_name, False)

    if action in {"mute", "unmute", "ban"}:
        if action in {"mute", "unmute"} and not bot_has_perm(guild, "moderate_members"):
            return False, "Eu não tenho permissão de moderar membros (timeout)."
        if action == "ban" and not bot_has_perm(guild, "ban_members"):
            return False, "Eu não tenho permissão de banir membros."

        for m in members:
            if not bot_can_act_on(guild, m):
                return False, f"Não posso agir em {limpar_nome(m.display_name)}: cargo acima/igual ao meu."

        if action == "unmute":
            okc, last_err = 0, ""
            for m in members:
                ok, err = await desmutar(m)
                okc += 1 if ok else 0
                last_err = err or last_err
                await asyncio.sleep(0.12)
            if okc == 0:
                return False, f"Falhou ao desmutar ({last_err or 'sem detalhes'})."
            return True, f"Desmutados: {okc}."

        if action == "mute":
            seconds = int(dur) if isinstance(dur, int) else 60
            seconds = max(1, min(86400, seconds))
            okc, last_err = 0, ""
            for m in members:
                ok, err = await mutar(m, seconds)
                okc += 1 if ok else 0
                last_err = err or last_err
                await asyncio.sleep(0.12)
            if okc == 0:
                return False, f"Falhou ao mutar ({last_err or 'sem detalhes'})."
            mot = reason or "Conduta inadequada."
            if len(members) == 1:
                return True, f"Mutado: {limpar_nome(members[0].display_name)} | {seconds}s | Motivo: {mot}."
            return True, f"Mutados: {okc} | {seconds}s | Motivo: {mot}."

        if action == "ban":
            okc, last_err = 0, ""
            for m in members:
                ok, err = await banir(m)
                okc += 1 if ok else 0
                last_err = err or last_err
                await asyncio.sleep(0.18)
            if okc == 0:
                return False, f"Falhou ao banir ({last_err or 'sem detalhes'})."
            mot = reason or "Infração grave."
            if len(members) == 1:
                return True, f"Banido: {limpar_nome(members[0].display_name)} | permanente | Motivo: {mot}."
            return True, f"Banidos: {okc} | Motivo: {mot}."

    if action == "remove_all_roles":
        if not bot_has_perm(guild, "manage_roles"):
            return False, "Eu não tenho permissão de gerenciar cargos."

        alvo = members[0]
        if not bot_can_act_on(guild, alvo):
            return False, f"Não posso mexer em {limpar_nome(alvo.display_name)}: cargo acima/igual ao meu."

        removable = []
        for r in list(getattr(alvo, "roles", [])):
            if r.is_default() or r.managed:
                continue
            if not bot_can_manage_role(guild, r):
                continue
            removable.append(r)

        if not removable:
            return False, f"Não há cargos removíveis em {limpar_nome(alvo.display_name)}."

        removed, last_err = 0, ""
        for r in removable:
            ok, err = await remove_role(alvo, r)
            removed += 1 if ok else 0
            last_err = err or last_err
            await asyncio.sleep(0.10)

        if removed == 0:
            return False, f"Falhou ao remover cargos ({last_err or 'sem detalhes'})."
        return True, f"Cargos removidos: {removed} | Alvo: {limpar_nome(alvo.display_name)}."

    return False, "Ordem inválida."

# =========================================================
# AUTO-MODERAÇÃO (sem mention) — barato + inteligente
# =========================================================
PALAVRAS_BRIGA = [
    "idiota", "burro", "lixo", "verme", "otário", "otario", "vagabundo",
    "arrombado", "fdp", "foda-se", "foda se", "seu merda", "mlk",
    "vai tomar no", "vai se foder"
]
KW_DIFAMACAO = ["calunia", "calúnia", "difamacao", "difamação", "mentiroso", "acusação", "acusacao"]
KW_DESRESPEITO = ["desrespeito", "insulto", "humilha", "ameaça", "ameaca"]

def should_check_infraction(texto: str) -> bool:
    t = norm(texto)
    if any(p in t for p in PALAVRAS_BRIGA):
        return True
    if any(k in t for k in KW_DIFAMACAO + KW_DESRESPEITO):
        return True
    if "japex" in t and any(p in t for p in PALAVRAS_BRIGA + ["corrupto", "ladrão", "ladrao"]):
        return True
    return False

def moderation_flagged(texto: str) -> bool:
    try:
        r = openai.moderations.create(model="omni-moderation-latest", input=texto)
        res = r.model_dump()["results"][0]
        if not res.get("flagged"):
            return False
        cats = res.get("categories", {}) or {}
        keys = [
            "hate", "hate/threatening",
            "harassment", "harassment/threatening",
            "violence", "violence/graphic",
            "sexual/minors"
        ]
        return any(bool(cats.get(k)) for k in keys)
    except:
        return False

def recomendar_punicao_sync(texto: str) -> dict:
    schema = {"action":"none", "duration_seconds": 0, "reason": ""}
    system = (
        "Você decide punição de chat em servidor Discord.\n"
        "Se não houver infração, action=none.\n"
        "Se houver, action=mute e duração curta.\n"
        "Motivo: bem curto.\n"
        "Saída: APENAS JSON.\n"
    )
    user = f"TEXTO: {texto}\nJSON_BASE: {json.dumps(schema, ensure_ascii=False)}"
    r = openai.responses.create(
        model=MODEL_MAIN,
        input=[{"role":"system","content":system},{"role":"user","content":user}],
        max_output_tokens=120,
        temperature=0.1,
    )
    raw = (r.output_text or "").strip()
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        return schema
    try:
        obj = json.loads(m.group(0))
        action = str(obj.get("action","none")).strip()
        if action not in {"none","mute"}:
            action = "none"
        dur = obj.get("duration_seconds", 0)
        if isinstance(dur, (int,float)):
            dur = int(max(0, min(3600, dur)))
        elif isinstance(dur, str) and dur.isdigit():
            dur = int(max(0, min(3600, int(dur))))
        else:
            dur = 0
        reason = normalizar_espacos(str(obj.get("reason","")))[:140]
        return {"action": action, "duration_seconds": dur, "reason": reason}
    except:
        return schema

async def recomendar_punicao(texto: str) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(recomendar_punicao_sync, texto), timeout=10)
    except:
        return {"action":"none","duration_seconds":0,"reason":""}

async def aplicar_auto_punicao(msg: discord.Message, alvo: discord.Member, motivo_ctx: str) -> Optional[str]:
    guild = msg.guild
    if not guild:
        return None
    if not bot_has_perm(guild, "moderate_members"):
        return None
    if not bot_can_act_on(guild, alvo):
        return None

    flagged = await asyncio.to_thread(moderation_flagged, motivo_ctx)
    if not flagged and not any(k in norm(motivo_ctx) for k in (KW_DIFAMACAO + KW_DESRESPEITO + ["japex"])):
        return None

    rec = await recomendar_punicao(motivo_ctx)
    if rec.get("action") != "mute":
        return None

    seconds = int(rec.get("duration_seconds") or 0)
    if seconds <= 0:
        seconds = 60
    reason = rec.get("reason") or "Conduta inadequada."

    ok, _ = await mutar(alvo, seconds)
    if not ok:
        return None
    return f"Mutado: {limpar_nome(alvo.display_name)} | {seconds}s | Motivo: {reason}."

# =========================================================
# EVENTOS
# =========================================================
@cliente.event
async def on_ready():
    print(f"bot ligado ({MODEL_MAIN}) | {MODEL_PUBLIC_NAME}")

@cliente.event
async def on_message(mensagem: discord.Message):
    if mensagem.author.bot:
        return
    if not isinstance(mensagem.author, discord.Member):
        return

    loop_time = asyncio.get_event_loop().time()
    if already_processed(mensagem.id, loop_time):
        return

    # ---------------------------
    # AUTO-MODERAÇÃO (sem mention)
    # ---------------------------
    try:
        if mensagem.guild and mensagem.content:
            txt = mensagem.content
            if should_check_infraction(txt):
                rep = await aplicar_auto_punicao(mensagem, mensagem.author, txt)
                if rep:
                    async with mensagem.channel.typing():
                        await asyncio.sleep(0.7)
                    await mensagem.channel.send(rep)
    except:
        pass

    # ---------------------------
    # Só responde se for mencionado
    # ---------------------------
    if cliente.user not in mensagem.mentions:
        return

    if not await respeitar_delay_e_cooldown(mensagem.author.id):
        return

    # trava geral anti-spam
    if ocupado.locked():
        return

    async with ocupado:
        guild = mensagem.guild
        channel = mensagem.channel
        extra = typing_extra(mensagem.author.id)

        if (mensagem.author.id in IGNORADOS) and (not is_japex(mensagem.author.id)):
            return

        texto_limpo = remover_mencao_bot(mensagem.content)
        if not texto_limpo:
            return

        # referência (quando mencionam o bot respondendo uma msg)
        referenced = None
        try:
            if mensagem.reference:
                if isinstance(mensagem.reference.resolved, discord.Message):
                    referenced = mensagem.reference.resolved
                elif mensagem.reference.message_id:
                    referenced = await channel.fetch_message(mensagem.reference.message_id)
        except:
            referenced = None

        # =====================================================
        # SUPERIOR: tenta ordem se parecer ordem OU se tiver menção alvo
        # =====================================================
        if guild and autoridade_sobre_bot(mensagem.author, guild):
            mentions = []
            for m in mensagem.mentions:
                if cliente.user and m.id == cliente.user.id:
                    continue
                if isinstance(m, discord.Member):
                    mentions.append({"user_id": m.id, "display_name": limpar_nome(m.display_name)})

            tentar_ordem = bool(mentions) or parece_ordem_rapida(texto_limpo)

            if tentar_ordem:
                ordem = await interpretar_ordem(texto_limpo, mentions, {"author_id": mensagem.author.id, "founder_id": JAPEX_ID})
                if ordem.get("action") != "none":
                    async with channel.typing():
                        await asyncio.sleep(extra)
                    ok, resp = await executar_ordem(ordem, guild)
                    await channel.send(sanitizar_resposta(resp or ack_superior(mensagem.author)))
                    return

            # se é reply denunciando, tenta punir o autor da msg referenciada
            if referenced and referenced.author and isinstance(referenced.author, discord.Member):
                alvo = referenced.author
                contexto = referenced.content or ""
                if contexto and should_check_infraction(contexto):
                    rep = await aplicar_auto_punicao(mensagem, alvo, contexto)
                    if rep:
                        async with channel.typing():
                            await asyncio.sleep(extra)
                        await channel.send(rep)
                        return

            # >>> mudança principal:
            # superior agora conversa normal quando não é ordem
            # (sem ficar preso em "Sim, ...")
            async with channel.typing():
                await asyncio.sleep(extra)
            resposta = await gerar_resposta(texto_limpo, mensagem.author)
            await mensagem.reply(resposta)
            return

        # =====================================================
        # NÃO-SUPERIOR: se for reply com menção ao bot, pode punir autor da msg
        # =====================================================
        if referenced and referenced.author and isinstance(referenced.author, discord.Member):
            alvo = referenced.author
            contexto = referenced.content or ""
            if contexto and should_check_infraction(contexto):
                rep = await aplicar_auto_punicao(mensagem, alvo, contexto)
                if rep:
                    async with channel.typing():
                        await asyncio.sleep(extra)
                    await channel.send(rep)
                    return

        # =====================================================
        # CONVERSA NORMAL
        # =====================================================
        async with channel.typing():
            await asyncio.sleep(extra)
        resposta = await gerar_resposta(texto_limpo, mensagem.author)
        await mensagem.reply(resposta)

cliente.run(TOKEN_DISCORD)
