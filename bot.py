import discord
import os
import re
from groq import Groq
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)
groq = Groq(api_key=GROQ_API_KEY)

# xingamentos simples (ajuste se quiser)
INSULTS = [
    "burro", "idiota", "animal", "imundo", "lixo", "merda"
]

# controle de reincidência
offenses = {}

# regex para remover [Rct], [Cmdt] etc
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
    text = text.lower()
    return any(word in text for word in INSULTS)

@client.event
async def on_ready():
    print(f"✅ Bot conectado como {client.user}")

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    member = message.author
    content = message.content
    role_name = highest_role(member)
    dados = read_dados()

    # DETECÇÃO DE XINGAMENTO
    if is_insult(content):
        count = offenses.get(member.id, 0) + 1
        offenses[member.id] = count

        if count == 1:
            await message.reply(f"Silêncio, {role_name}. Animal.")
            await member.timeout(
                timedelta(seconds=60),
                reason="Calúnia ou xingamento leve"
            )
            return

        elif count == 2:
            await message.reply(f"Já avisei, {role_name}. Imundo.")
            await member.timeout(
                timedelta(minutes=60),
                reason="Insistência em xingamento"
            )
            return

        else:
            await message.reply(f"Chega, {role_name}. Aprende a se comportar.")
            await member.timeout(
                timedelta(hours=3),
                reason="Reincidência contínua"
            )
            return

    # ===== RESPOSTA NORMAL (IA) =====
    system_prompt = f"""
Você é uma IA ajudante de servidor Discord.
Mantenha respeito, boa gramática e conduta.
Não seja moralista nem formal demais.
Não invente informações.

SEMPRE utilize APENAS as informações abaixo como base de verdade:
{dados}

Se não encontrar a resposta nos dados, diga que não consta nos registros.

Caso o usuário desrespeite, responda curto e ríspido.
Não peça desculpas.
Não dê sermão.
"""

    user_prompt = f"""
O usuário possui o cargo mais alto: {role_name}

Pergunta:
{content}
"""

    try:
        completion = groq.chat.completions.create(
            model="mixtral-8x7b-32768",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.4,
            max_tokens=500
        )

        reply = completion.choices[0].message.content
        await message.reply(reply[:2000])

    except Exception as e:
        await message.reply("Erro interno ao processar a resposta.")

client.run(DISCORD_TOKEN)
