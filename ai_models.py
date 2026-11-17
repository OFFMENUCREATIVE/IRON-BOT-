"""One place that picks the AI model for every Iron Bot call.

BOT_MODEL (Railway variable) sets it; unset means the cheapest model.
If Anthropic says the model doesn't exist, the call retries once on
BOT_MODEL_FALLBACK so the bot never goes silent over a model name."""
import logging
import os

import anthropic

log = logging.getLogger(__name__)

CHEAPEST = 'claude-haiku-5-5'
FALLBACK = 'claude-haiku-4-5'


def model():
    return (os.environ.get('BOT_MODEL') or '').strip() or CHEAPEST


def create(client, **kwargs):
    """client.messages.create with the configured model (any model= passed is ignored)."""
    kwargs['model'] = model()
    try:
        return client.messages.create(**kwargs)
    except anthropic.NotFoundError:
        fallback = (os.environ.get('BOT_MODEL_FALLBACK') or '').strip() or FALLBACK
        if kwargs['model'] == fallback:
            raise
        log.warning('Model %s not available, using %s', kwargs['model'], fallback)
        kwargs['model'] = fallback
        return client.messages.create(**kwargs)
