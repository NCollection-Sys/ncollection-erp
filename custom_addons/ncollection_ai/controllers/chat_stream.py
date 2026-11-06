# -*- coding: utf-8 -*-
"""The chat widget's STREAMING endpoint (P5-T06 / #63).

One route, ``POST /ncollection/ai/chat/stream``, newline-delimited JSON:

    {"type": "delta", "text": "..."}      zero or more, already re-hydrated
    {"type": "done",  "messages": [...]}  the two persisted rows, then the end
    {"type": "error", "message": "..."}   the answer failed; nothing was saved

WHY IT IS SAFE TO ADD A ROUTE
  * Admission is the SAME code as the batch RPCs (``_stream_plan`` ->
    ``_resolve_suggested`` / ``_admit_typed``): the financial gate, the #375
    free-text flag, the size cap, both secret scans, the registry lookup. It
    runs inside the request, as the user, BEFORE the first byte is streamed, so
    every refusal is an ordinary 4xx and nothing has left the database.
  * ``auth='user'`` and Odoo's own CSRF token (a form POST, not JSON).
  * The satellite gateway still decides budget / rate / breaker before its
    first byte, and the key never leaves the environment.
  * Persistence happens ONCE, at the end, on a fresh cursor as the same user,
    and only after the provider sent its final ``done``: a cut-off answer is
    never saved (the batch path's "no transcript without an answer" rule).
  * Re-hydration of the identity tokens is incremental but the SAVED text is
    always ``_rehydrate(full_text)``, identical to the batch path.

WHAT IT COSTS. A streamed answer holds one HTTP worker for its duration (a few
seconds; the gateway bounds a stream at 60 s). It runs on a web worker, NOT on
the cron worker, which is the #310 constraint the design names. The response
carries ``X-Accel-Buffering: no`` so nginx forwards each line as it arrives.
"""
import json
import logging

from odoo import api, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


def _line(event):
    return (json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8')


class AiChatStream(http.Controller):

    @http.route('/ncollection/ai/chat/stream', type='http', auth='user',
                methods=['POST'])
    def stream(self, kind='', value='', **_kwargs):
        env = request.env
        try:
            plan = env['ncollection.ai.chat']._stream_plan(kind, value)
            prepared = plan['prepared']
            events = env['ncollection.ai.gateway']._stream(
                prepared['prompt'], prepared['max_answer_tokens'])
        except (AccessError, UserError) as exc:
            status = 403 if isinstance(exc, AccessError) else 400
            return request.make_json_response(
                {'message': str(exc)}, status=status)

        # Everything the generator needs is captured NOW: it runs after the
        # request's cursor is closed, so it must not touch ``env``.
        registry, uid, lang = env.registry, env.uid, env.lang
        pii = env['ncollection.ai.pii']
        rehydrator = pii._stream_rehydrator(prepared['mapping'])
        mapping = prepared['mapping']
        display = plan['display']
        dropped = prepared['dropped_sections']
        generic = env._("The assistant could not answer.")

        def generate():
            parts, usage = [], {}
            try:
                for event in events:
                    if 'delta' in event:
                        parts.append(event['delta'])
                        text = rehydrator.feed(event['delta'])
                        if text:
                            yield _line({'type': 'delta', 'text': text})
                    elif event.get('done'):
                        usage = event.get('usage', {})
                tail = rehydrator.flush()
                if tail:
                    yield _line({'type': 'delta', 'text': tail})
                answer = pii._rehydrate(''.join(parts), mapping)
                if not answer.strip():
                    raise UserError(generic)
                with registry.cursor() as cr:
                    saved_env = api.Environment(cr, uid, {'lang': lang})
                    result = saved_env['ncollection.ai.chat']._persist_exchange(
                        display, {'answer': answer, 'usage': usage,
                                  'dropped_sections': dropped})
                yield _line(dict(result, type='done'))
            except UserError as exc:
                yield _line({'type': 'error', 'message': str(exc)})
            except Exception:  # noqa: BLE001 - the stream must end with a line
                _logger.exception("AI chat stream failed")
                yield _line({'type': 'error', 'message': generic})
            finally:
                close = getattr(events, 'close', None)
                if close:
                    close()

        return request.make_response(generate(), headers=[
            ('Content-Type', 'application/x-ndjson; charset=utf-8'),
            ('Cache-Control', 'no-store'),
            ('X-Accel-Buffering', 'no'),
        ])
