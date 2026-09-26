import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

class Settings(BaseModel):
    bot_host: str = Field(default_factory=lambda: os.getenv("BOT_HOST", "0.0.0.0"))
    bot_port: int = Field(default_factory=lambda: int(os.getenv("BOT_PORT", "8080")))
    bot_url: str = Field(default_factory=lambda: os.getenv("BOT_URL", "http://localhost:8080"))
    
    # LLM Settings
    llm_provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", "gemini"))
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", "")))
    gemini_model: str = Field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    gemini_fallback_models: list[str] = Field(default_factory=lambda: [
        model.strip()
        for model in os.getenv(
            "GEMINI_FALLBACK_MODELS",
            "gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
        ).split(",")
        if model.strip()
    ])
    
    openai_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = Field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    
    anthropic_api_key: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = Field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"))
    
    groq_api_key: str = Field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    groq_model: str = Field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile"))

    # Bot Metadata
    team_name: str = Field(default_factory=lambda: os.getenv("TEAM_NAME", "Team Vera AI Engine"))
    team_members: list[str] = Field(default_factory=lambda: [os.getenv("TEAM_MEMBER", "Gaganpreet Singh")])
    model_name: str = Field(default_factory=lambda: os.getenv("BOT_MODEL_NAME", "gemini-3.8-flash"))
    approach: str = Field(default_factory=lambda: os.getenv("BOT_APPROACH", "Deterministic context-projected strategy router with async structured LLM composition, FactRegistry validation & bounded state machine"))
    contact_email: str = Field(default_factory=lambda: os.getenv("CONTACT_EMAIL", "gaganxpreet@gmail.com"))
    submitted_at: str = Field(default_factory=lambda: os.getenv("SUBMITTED_AT", "2026-09-26T00:00:00Z"))
    version: str = "1.0.0"

settings = Settings()
