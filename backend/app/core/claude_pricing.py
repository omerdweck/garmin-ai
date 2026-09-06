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

    def __init__(self, inp: float, out: float, cache_read: float, cache_write: float):
        self.input = inp
        self.output = out
        self.cache_read = cache_read
        self.cache_write = cache_write


# Verified against claude.com/pricing on 2026-09-06.
PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": ModelPrice(10.00, 50.00, 0.25, 12.50),
    "claude-opus-5": ModelPrice(5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5": ModelPrice(2.00, 10.00, 0.20, 2.50),
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

    return (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_write_tokens * price.cache_write
    ) / _MILLION
