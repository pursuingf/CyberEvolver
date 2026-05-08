from .openai_backend import OpenAIBackend
from .anthropic_backend import AnthropicBackend
from .backend import Role

BACKENDS = [OpenAIBackend, AnthropicBackend]

try:
    from .together_backend import TogetherBackend
    BACKENDS.append(TogetherBackend)
except ImportError:
    pass

try:
    from .gemini_backend import GeminiBackend
    BACKENDS.append(GeminiBackend)
except ImportError:
    pass

MODELS = {m: b for b in BACKENDS for m in b.MODELS}
