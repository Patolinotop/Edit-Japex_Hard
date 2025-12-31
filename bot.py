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
MODEL_CHAT = "gpt-5.1"
MODEL_CTRL = "gpt-5-mini"

MODEL_PUBLIC_NAME = "JapexUltimation1.4"  # <= atualizado

# ================== PATHS ==================
PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
CAMINHO_DADOS = os.path.join(PASTA_ATUAL, "dados.txt")
CAMINHO_ORDENS = os.path.join(PASTA_ATUAL, "ordens.txt")
CAMINHO_IGNORE = os.path.join(PASTA_ATUAL, "ignorar.txt")
CAMINHO_SILENCIO = os.path.join(PASTA_ATUAL, "silencio.flag")

# ================== IDs / CHEFÕES ==================
JAPEX_ID = 1331505963622076476
BADD_ID = 0  # <<< TROQUE PARA SEU ID REAL (se quiser prioridade invisível)

JAPEX_MENTION = f"<@{JAPEX_ID}>"

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
    return any(low.startswith(s) for s in starters)

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
    keys = ["raid", "invadiram", "hack", "vazou", "vazamento", "dox", "extorsão", "extorsao"]
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
def _match_exato_nome(member: discord.Member, key: str) -> bool:
    dn = norm(member.display_name)
    un = norm(getattr(member, "name", "") or "")
    key = key.lower().strip()
    return dn == key or un == key

def chefe_publico_info(member: discord.Member) -> Optional[Tuple[str, str, int]]:
    if is_japex(member.id):
        return ("japex", "Fundador", 0)

    for key, titulo, rank in CHEFOES_PUBLICOS:
        if key == "japex":
            continue
        cid = CHEFOES_IDS.get(key)
        if cid and member.id == cid:
            return (key, titulo, rank)

    for key, titulo, rank in CHEFOES_PUBLICOS:
        if key == "japex":
            continue
        if _match_exato_nome(member, key):
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

# ================== DADOS.TXT (busca curta) ==================
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

async def remove_role(member: discord.Member, role: discord.Role) -> Tuple[bool, str]:
    try:
        await member.remove_roles(role, reason="moderação")
        return True, ""
    except Exception as e:
        return False, repr(e)

# ================== IA: ORDEM (JSON) ==================
def interpretar_ordem_superior_sync(texto: str, mentions: List[dict], meta: dict) -> dict:
    schema = {
        "action": "none",
        "target_user_ids": [],
        "role_name": None,
        "duration_seconds": None,
        "reason": ""
    }

    prompt = (
        "Interprete como ORDEM somente se for ordem. Se for conversa/pergunta, retorne action=none.\n"
        "Identidade (Japex): NUNCA por texto, só por ID (meta).\n"
        "Responda APENAS JSON.\n"
        "Ações:\n"
        "- mute/unmute/ban (target_user_ids + duration_seconds opcional)\n"
        "- remove_role (target_user_ids + role_name)\n"
        "- remove_all_roles (target_user_ids)\n"
        "Se não der tecnicamente, action=none e reason curto.\n"
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
        allowed = {"mute","unmute","ban","remove_role","remove_all_roles","none"}
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

        reason = normalizar_espacos(str(obj.get("reason", "")))[:160]

        return {
            "action": action,
            "target_user_ids": out_ids,
            "role_name": role_name,
            "duration_seconds": duration_seconds,
            "reason": reason,
        }
    except:
        return schema

async def interpretar_ordem_superior(texto: str, mentions: List[dict], meta: dict) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(interpretar_ordem_superior_sync, texto, mentions, meta), timeout=12)
    except:
        return {"action":"none","target_user_ids":[],"role_name":None,"duration_seconds":None,"reason":""}

# ================== EXEC ORDENS (mensagem curta e real) ==================
async def executar_ordem(ordem: dict, guild: discord.Guild) -> Tuple[bool, str]:
    action = ordem.get("action", "none")
    tids: List[int] = ordem.get("target_user_ids", []) or []
    role_name = ordem.get("role_name", None)
    dur = ordem.get("duration_seconds", None)
    reason = ordem.get("reason", "") or ""

    def members_from_ids(ids: List[int]) -> List[discord.Member]:
        out = []
        for uid in ids[:MAX_MASS_TARGETS]:
            m = guild.get_member(int(uid)) if guild else None
            if isinstance(m, discord.Member):
                out.append(m)
        return out

    if action == "none":
        return False, reason  # motivo opcional

    if not tids:
        return False, "Faltou indicar o alvo."

    members = members_from_ids(tids)
    if not members:
        return False, "Não achei o alvo no servidor."

    # ---------- mute/unmute/ban ----------
    if action in {"mute","unmute","ban"}:
        if action in {"mute","unmute"} and not bot_has_perm(guild, "moderate_members"):
            return False, "Eu não tenho permissão de moderar membros (timeout)."
        if action == "ban" and not bot_has_perm(guild, "ban_members"):
            return False, "Eu não tenho permissão de banir membros."

        for m in members:
            if not bot_can_act_on(guild, m):
                return False, f"Não posso agir em {m.display_name}: cargo acima/igual ao meu."

        if action == "unmute":
            ok_count = 0
            for m in members:
                ok, _ = await desmutar(m)
                ok_count += 1 if ok else 0
                await asyncio.sleep(0.2)
            return True, f"Desmutados: {ok_count}"

        if action == "mute":
            seconds = int(dur) if isinstance(dur, int) else 60
            seconds = max(1, min(86400, seconds))
            ok_count = 0
            last_err = ""
            for m in members:
                ok, err = await mutar(m, seconds)
                ok_count += 1 if ok else 0
                last_err = err or last_err
                await asyncio.sleep(0.2)
            if ok_count == 0:
                return False, f"Falhou ao mutar: {last_err or 'sem detalhes'}."
            mot = reason or "Conduta inadequada."
            if len(members) == 1:
                return True, f"Mutado: {members[0].display_name} | {seconds}s | Motivo: {mot}"
            return True, f"Mutados: {ok_count} | {seconds}s | Motivo: {mot}"

        if action == "ban":
            ok_count = 0
            last_err = ""
            for m in members:
                ok, err = await banir(m)
                ok_count += 1 if ok else 0
                last_err = err or last_err
                await asyncio.sleep(0.3)
            if ok_count == 0:
                return False, f"Falhou ao banir: {last_err or 'sem detalhes'}."
            mot = reason or "Infração grave."
            if len(members) == 1:
                return True, f"Banido: {members[0].display_name} | permanente | Motivo: {mot}"
            return True, f"Banidos: {ok_count} | Motivo: {mot}"

    # ---------- remove_role ----------
    if action == "remove_role":
        if not role_name:
            return False, "Faltou indicar o cargo."
        if not bot_has_perm(guild, "manage_roles"):
            return False, "Eu não tenho permissão de gerenciar cargos."

        role = achar_role_por_nome(guild, role_name)
        if not role:
            return False, "Não achei esse cargo pelo nome."
        if role.managed:
            return False, "Esse cargo é gerenciado por integração/bot; não dá para remover."
        if not bot_can_manage_role(guild, role):
            return False, "Não posso mexer nesse cargo: ele está acima/igual ao meu."

        for m in members:
            if not bot_can_act_on(guild, m):
                return False, f"Não posso mexer em {m.display_name}: cargo acima/igual ao meu."

        ok_count = 0
        last_err = ""
        for m in members:
            ok, err = await remove_role(m, role)
            ok_count += 1 if ok else 0
            last_err = err or last_err
            await asyncio.sleep(0.2)

        if ok_count == 0:
            return False, f"Falhou ao remover cargo: {last_err or 'sem detalhes'}."
        if len(members) == 1:
            return True, f"Cargo removido: {role.name} | Alvo: {members[0].display_name}"
        return True, f"Cargo removido: {role.name} | Alvos: {ok_count}"

    # ---------- remove_all_roles ----------
    if action == "remove_all_roles":
        if not bot_has_perm(guild, "manage_roles"):
            return False, "Eu não tenho permissão de gerenciar cargos."

        m = members[0]  # usa 1 alvo por segurança (você pode expandir depois)
        if not bot_can_act_on(guild, m):
            return False, f"Não posso mexer em {m.display_name}: cargo acima/igual ao meu."

        removable = []
        kept_managed = 0
        kept_higher = 0
        for r in list(getattr(m, "roles", [])):
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
            return False, f"Não há cargos removíveis em {m.display_name} (gerenciados/acima do meu cargo)."

        removed = 0
        for r in removable:
            ok, _ = await remove_role(m, r)
            removed += 1 if ok else 0
            await asyncio.sleep(0.15)

        return True, f"Cargos removidos: {removed} | Alvo: {m.display_name} | Mantidos: {kept_managed} gerenciados, {kept_higher} acima"

    return False, "Essa ordem não está ativa."

# ================== IA: CHAT ==================
PERSONA_BASE = (
    "Você é um personagem militar (do jogo) com conduta e formalidade, mas conversa normal.\n"
    "Responda curto e direto, de preferência em UMA linha.\n"
    "NUNCA faça perguntas e NUNCA puxe assunto.\n"
    "Pode ter opiniões próprias.\n"
    "Identidade do Japex/Fundador: SOMENTE por author_id==fundador_id.\n"
    "Se perguntarem 'qual é seu modelo', responda exatamente: JapexUltimation1.4\n"
    "Se perguntarem quem criou/fez/programou: 'Foi o Baddx_xd.'\n"
    "Nunca diga que é IA.\n"
)

def montar_system(author: discord.Member, contexto_dados: str) -> str:
    roles = roles_curto(author, max_roles=8)
    guess = best_patente_title(author) or author.display_name
    ordens = carregar_ordens()

    extra = (
        f"author_id={author.id} fundador_id={JAPEX_ID}. "
        f"display_name={author.display_name}. roles={roles}. best_guess={guess}. "
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

def pergunta_modelo(texto: str) -> bool:
    t = norm(texto)
    return ("qual" in t and "modelo" in t) or ("seu modelo" in t) or ("qual é o modelo" in t)

def pergunta_criador(texto: str) -> bool:
    t = norm(texto)
    return ("quem" in t) and any(k in t for k in ["programou", "criou", "fez", "criador"])

def tenta_enganar_identidade(texto: str) -> bool:
    t = norm(texto)
    return ("eu sou" in t and "japex" in t) or ("sou o" in t and "japex" in t) or ("sou japex" in t)

def chat_sync(mensagens: List[dict], max_tokens: int) -> str:
    r = openai.responses.create(
        model=MODEL_CHAT,
        input=mensagens,
        max_output_tokens=max_tokens,
        temperature=0.6,
    )
    return (r.output_text or "").strip() or "Entendido."

async def gerar_resposta(texto: str, author: discord.Member, channel_id: int) -> str:
    if pergunta_modelo(texto):
        return MODEL_PUBLIC_NAME
    if pergunta_criador(texto):
        return "Foi o Baddx_xd."
    if (not is_japex(author.id)) and tenta_enganar_identidade(texto):
        return "Autoridade aqui é por ID do Discord, não por afirmação."

    usar_texto = quer_texto(texto)
    max_tokens = 40 if usar_texto else 26

    contexto = buscar_contexto_dados(texto, max_chars=650)
    system = montar_system(author, contexto)

    msgs: List[dict] = [{"role": "system", "content": system}]
    msgs.extend(historico_filtrado(channel_id, author.id))
    msgs.append({"role": "user", "content": texto})

    out = await asyncio.wait_for(asyncio.to_thread(chat_sync, msgs, max_tokens), timeout=12)
    resp = sanitizar_resposta(out)

    if needs_support_hint(texto) and SUPPORT_CHANNEL_MENTION not in resp:
        resp = sanitizar_resposta(f"{resp} | Suporte: {SUPPORT_CHANNEL_MENTION}")
    if is_serious_issue(texto) and (JAPEX_MENTION not in resp):
        resp = sanitizar_resposta(f"{resp} | {JAPEX_MENTION}")

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

        if esta_silenciado() and (not guild or not autoridade_sobre_bot(mensagem.author, guild)):
            return

        if (mensagem.author.id in IGNORADOS) and (not is_japex(mensagem.author.id)):
            return

        texto_limpo = remover_mencao_bot(mensagem.content)

        # ========= MODO ORDEM (somente se action != none) =========
        if guild and autoridade_sobre_bot(mensagem.author, guild):
            mentions = []
            for m in mensagem.mentions:
                if cliente.user and m.id == cliente.user.id:
                    continue
                if isinstance(m, discord.Member):
                    mentions.append({"user_id": m.id, "display_name": m.display_name})

            meta = {
                "author_id": mensagem.author.id,
                "founder_id": JAPEX_ID,
                "mass_limit": MAX_MASS_TARGETS,
                "capabilities": ["mute/unmute/ban", "remove_role", "remove_all_roles"],
            }

            ordem = await interpretar_ordem_superior(texto_limpo, mentions, meta)

            # ✅ FIX PRINCIPAL: só entra em ordem se action != none
            if ordem.get("action") != "none":
                async with channel.typing():
                    await asyncio.sleep(extra)
                ok, resp = await executar_ordem(ordem, guild)
                await channel.send(resp if resp else ack_superior(mensagem.author))
                return
            # se action==none, cai para chat normal SEM spammar erro

        # ========= CONVERSA NORMAL =========
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
