from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse

app = FastAPI()
clients = []

@app.get("/")
def root():
    return PlainTextResponse("Server is running")

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.append(ws)
    try:
        while True:
            msg = await ws.receive_text()
            for c in clients:
                await c.send_text(msg)
    except:
        clients.remove(ws)
