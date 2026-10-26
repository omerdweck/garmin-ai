"""
What a Claude API call costs, in USD.

Kept in its own module rather than inline in claude_client so the numbers
are easy to find and audit against https://claude.com/pricing when they
change - which they do. Everything here is per *million* tokens.

An unknown model deliberately yields None rather than a fallback estimate:
a quota that silently throttles people based on a made-up price is worse
than one that reports it cannot price a call. The token counts are still
recorded either way, so history can be repriced later.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ModelPrice:
    """Per-million-token prices for one model."""

    def __init__(
        self,
        inp: float,
        out: float,
        cache_read: float,
        cache_write: float,
        long_prompt: Optional["ModelPrice"] = None,
        long_prompt_over: int = 0,
    ):
        self.input = inp
        self.output = out
        self.cache_read = cache_read
        self.cache_write = cache_write
        # Some models charge more for a long prompt: above `long_prompt_over`
        # tokens (fresh input + cache read + cache write, per request), every
        # line of that request - output included - is billed at `long_prompt`.
        self.long_prompt = long_prompt
        self.long_prompt_over = long_prompt_over


# Verified against claude.com/pricing on 2026-09-06.
PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": ModelPrice(10.00, 50.00, 0.25, 12.50),
    "claude-opus-5": ModelPrice(5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5": ModelPrice(2.00, 10.00, 0.20, 2.50),
    # Verified 2026-10-26 (platform.claude.com model pages): the same input
    # and output rates as Sonnet 5, same tokenizer. Cache read is listed as
    # $0.20 on one source and $0.10 on another; the higher is used, so the
    # budget never under-counts.
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00, 0.20, 2.50),
    # Haiku 5.5 is priced by prompt length: up to 100k tokens it's 1/20 of
    # Sonnet, above that 5x more on every line of the request. Our prompts are
    # a few thousand tokens, but a runaway one is priced honestly.
    "claude-haiku-5-5": ModelPrice(
        0.10, 0.50, 0.01, 0.125,
        long_prompt=ModelPrice(0.50, 2.50, 0.05, 0.625),
        long_prompt_over=100_000,
    ),
    "claude-haiku-4-5-20251001": ModelPrice(1.00, 5.00, 0.10, 1.25),
}

_MILLION = 1_000_000


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Optional[float]:
    """
    USD for one call, or None if this model has no published price here.

    Note that `input_tokens` from the API already excludes cached tokens -
    they arrive as separate counters - so the three input figures are added,
    not overlapped.
    """
    price = PRICES.get(model)
    if price is None:
        logger.warning("No price known for model %s - usage recorded without a cost", model)
        return None

    prompt = input_tokens + cache_read_tokens + cache_write_tokens
    if price.long_prompt is not None and prompt > price.long_prompt_over:
        price = price.long_prompt

    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_write_tokens * price.cache_write
    ) / _MILLION
