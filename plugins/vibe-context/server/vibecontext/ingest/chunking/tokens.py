import logging
from functools import lru_cache

import tiktoken

from vibecontext.ingest.resources import TOKEN_ENCODING

log = logging.getLogger("vibecontext.ingest")


@lru_cache(maxsize=1)
def _encoding():
    try:
        return tiktoken.get_encoding(TOKEN_ENCODING)
    except Exception as error:
        log.warning("tiktoken vocabulary unavailable, estimating token counts: %s", error)
        return None


def count_tokens(text: str) -> int:
    encoding = _encoding()
    if encoding is None:
        # About 4 characters per token for prose; close enough to size chunks.
        return max(1, len(text) // 4)
    return len(encoding.encode(text, disallowed_special=()))
