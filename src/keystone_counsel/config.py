"""Configuration for Keystone Counsel.

Uses pydantic BaseSettings with KEYSTONE_ env prefix, same convention
as keystone-engage. Reads from environment variables or .env file.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "KEYSTONE_"}

    database_url: str = ""
    ollama_base_url: str = "http://100.112.161.86:11434"
    ollama_chat_model: str = "qwen2.5:7b-instruct"
    ollama_embed_model: str = "nomic-embed-text"
    retrieval_top_k: int = 5
    confidence_threshold: float = 0.50
    corpus_dir: str = "data/corpus"
    env: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
