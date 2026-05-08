
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.embeddings import Embeddings

from langchain_community.embeddings import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from config.config import Configs


from utils.log_common import build_logger

logger = build_logger()

def get_embeddings(
        embed_model: str = None,
) -> Embeddings:

    try:
        if Configs.llm_config.embedding_type == "openai":
            # TODO
            return OpenAIEmbeddings()
        elif Configs.llm_config.embedding_type == "ollama":
            return OllamaEmbeddings(
                base_url=Configs.llm_config.embedding_url,
                model=embed_model,
            )
        else:
            return HuggingFaceEmbeddings(model_name=embed_model)
    except Exception as e:
        logger.exception(f"failed to create Embeddings for model: {embed_model}.")