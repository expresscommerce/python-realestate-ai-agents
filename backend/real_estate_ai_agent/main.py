"""FastAPI entry point for PropertyBot."""

import logging
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chat import process_chat
from .config import CORS_ORIGINS
from .session import clear_history, get_history, save_history

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

app = FastAPI(title="PropertyBot", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    response: str


class SessionResponse(BaseModel):
    session_id: str


class SessionHistoryResponse(BaseModel):
    session_id: str
    history: list[dict]


class HealthResponse(BaseModel):
    status: str


@app.post("/api/session", response_model=SessionResponse)
async def create_session():
    sid = str(uuid.uuid4())
    save_history(sid, [])
    return SessionResponse(session_id=sid)


@app.get("/api/session/{session_id}", response_model=SessionHistoryResponse)
async def get_session_history(session_id: str):
    return SessionHistoryResponse(session_id=session_id, history=get_history(session_id))


@app.delete("/api/session/{session_id}", response_model=HealthResponse)
async def delete_session(session_id: str):
    clear_history(session_id)
    return HealthResponse(status="ok")


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    history = get_history(sid)
    history.append({"role": "user", "content": req.message})

    try:
        reply = process_chat(history)
    except Exception as exc:
        logging.getLogger(__name__).error("Chat pipeline error: %s", exc)
        reply = "I ran into an unexpected issue. Please try again."
        history.append({"role": "assistant", "content": reply})

    save_history(sid, history)
    return ChatResponse(session_id=sid, response=reply)


@app.get("/api/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(
            str(index),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )
    return {"message": "PropertyBot API is running."}


def serve():
    print("\n  PropertyBot starting at http://localhost:8000\n")
    uvicorn.run(
        "real_estate_ai_agent.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    serve()
