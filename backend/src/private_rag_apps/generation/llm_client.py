import openai

from private_rag_apps.core.config import settings


def get_llm_client() -> openai.OpenAI:
    """settings.llm_provider に応じたOpenAI互換クライアントを返す"""
    if settings.llm_provider == "ollama":
        return openai.OpenAI(api_key=settings.ollama_api_key, base_url=settings.ollama_base_url)
    return openai.OpenAI(api_key=settings.openai_api_key)
