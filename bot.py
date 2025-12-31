import discord
import os
import re
from groq import Groq
from dotenv import load_dotenv
from datetime import timedelta
from collections import deque

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN não definido")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)
groq = Groq(api_key=GROQ_API_KEY)

INSULTS = ["burro", "idiota", "animal", "imundo", "lixo", "merda"]

offenses = {}
user_history = {}
bot_busy = False

BRACKET_REGEX = re.compile(r"\[.*?\]")

def highest_role(member: discord.Member):
    roles = [r for r in member.roles if r.name != "@everyone"]
    if not roles:
        return "Usuário"
    role = max(roles, key=lambda r: r.position)
    return BRACKET_REGEX.sub("", role.name).strip()

def read_dados():
    try:
        with open("dados.txt", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return ""

def is_insult(text: str):
    t = text.lower()
    return any(word in t for word in INSULTS)

@client.event
async def on_ready():
    print(f"✅ Bot conectado como {client.user}")

@client.event
async def on_message(message: discord.Message):
    global bot_busy

    if message.author.bot:
        return

    if client.user not in message.mentions:
        return

    if bot_busy:
        await message.reply("Já estou respondendo. Aguarde.")
        return

    bot_busy = True
    member = message.author
    role_name = highest_role(member)
    content = message.content.replace(f"<@{client.user.id}>", "").strip()

    # histórico (últimas 5)
    history = user_history.setdefault(member.id, deque(maxlen=5))
    history.append(content)

    # xingamento
    if is_insult(content):
        count = offenses.get(member.id, 0) + 1
        offenses[member.id] = count

        if count == 1:
            await message.reply(f"Silêncio, {role_name}. Animal.")
            await member.timeout(timedelta(seconds=60))
        elif count == 2:
            await message.reply(f"Já avisei, {role_name}. Imundo.")
            await member.timeout(timedelta(hours=1))
        else:
            await message.reply(f"Chega, {role_name}.")
            await member.timeout(timedelta(hours=3))

        bot_busy = False
        return

    dados = read_dados()

    system_prompt = f"""
Você é uma IA ajudante de servidor Discord.
Responda curto, direto e com boa gramática.
Não seja formal nem moralista.
Não invente informações.
Use SOMENTE os dados abaixo como verdade:

{dados}

Se não constar nos dados, diga que não há registro.
"""

    user_prompt = f"""
Cargo do usuário: {role_name}

Histórico recente:
{chr(10).join(history)}

Pergunta atual:
{content}
"""

    try:
        async with message.channel.typing():
            completion = groq.chat.completions.create(
                model="mixtral-8x7b-32768",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=50,
                temperature=0.3
            )

        reply = completion.choices[0].message.content
        await message.reply(reply[:2000])

    except Exception:
        await message.reply("Erro ao responder.")

    bot_busy = False

client.run(DISCORD_TOKEN)
