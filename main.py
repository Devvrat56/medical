from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys
import secrets
from starlette.middleware.sessions import SessionMiddleware

# Initialize logging (medical-chatbot specific)
logger = logging.getLogger("medical_chatbot")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))

# FastAPI app (medical-only)
app = FastAPI(
    title="Medical Chatbot API",
    description="Dermatology-focused skincare assistant",
    version="1.0",
    docs_url="/docs",
)

# Minimal CORS (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with frontend URL in prod
    allow_methods=["POST", "GET"],
)

# Session middleware (optional, for auth if needed)
app.add_middleware(SessionMiddleware, secret_key=secrets.token_urlsafe(32))

# Import ONLY medical chatbot routes
from routes.medical_chatbot import medical_chat_router  # Fix typo in path if needed

# Mount medical endpoints
app.include_router(
    medical_chat_router,
    prefix="/medical",
    tags=["Dermatology Assistant"]
)

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "Medical chatbot operational"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,  # Default port for medical service
        log_level="info"
    )