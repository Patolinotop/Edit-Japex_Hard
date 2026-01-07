import os
import asyncio
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

from bot import client, bot_pronto  # IMPORTANTE
from bot import start_bot            # IMPORTANTE

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "1457588380174127299"))

app = FastAPI()

def formatar_data(dt: datetime):
    try:
        return dt.strftime("%d/%m/%Y %H:%M")
    except:
        return str(dt)

async def buscar_mensagens(nome: str, limite: int = 2000):
    nome_busca = nome.strip()
    if nome_busca == "":
        return []

    canal = client.get_channel(CHANNEL_ID)
    if canal is None:
        canal = await client.fetch_channel(CHANNEL_ID)

    resultados = []

    async for msg in canal.history(limit=limite, oldest_first=False):
        conteudo = msg.content or ""
        if nome_busca.lower() in conteudo.lower():
            resultados.append({
                "mensagem": conteudo,
                "autor": str(msg.author),
                "data": formatar_data(msg.created_at),
                "message_id": str(msg.id)
            })

    return resultados

@app.get("/buscar")
async def buscar(nome: str = ""):
    try:
        await asyncio.wait_for(bot_pronto.wait(), timeout=25)
    except:
        return JSONResponse({"ok": False, "erro": "Bot não ficou pronto a tempo."})

    try:
        resultados = await buscar_mensagens(nome)
        return JSONResponse({"ok": True, "resultados": resultados})
    except Exception as e:
        return JSONResponse({"ok": False, "erro": str(e)})

@app.get("/")
async def home():
    return {"ok": True, "msg": "API online"}

@app.on_event("startup")
async def startup():
    asyncio.create_task(start_bot())  # DISCORD INICIA AQUI

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
