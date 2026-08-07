# -*- coding: utf-8 -*-
"""Client for the AI gateway satellite (P5-T03 / #60).

HTTP ONLY — THIS IS THE ARCHITECTURAL BOUNDARY
----------------------------------------------
The gateway is a separate container (P5-T02 / #59). This module talks to it over
HTTP and MUST NOT import anything from `satellites/`. That is not stylistic:
ARCHITECTURE_DATA_PLATFORM §10.3 says satellites are extracted precisely so they
"should not share a process with the ORM", and §10.4 has them reachable over
"Odoo public interfaces" only. An import would collapse the process boundary
that makes the topology worth having.

The satellite holds the provider keys. This module holds none, sends none, and
has no idea which provider is configured.

WHY THE TENANT CALLS OUT TO A CONTAINER RATHER THAN CALLING THE PROVIDER
-----------------------------------------------------------------------
ARCHITECTURE_SECURITY §11: "no tenant DB makes any outbound call". The call this
module makes is to a service on the internal compose network — it never leaves
the host. Only the satellite has a route to the internet, and only for one
allowlisted host.

NOT ON THE CRON WORKER
----------------------
§11 also warns that a held-open call must not starve the cron worker (#310,
`max_cron_threads = 1`). Calls here are synchronous and user-initiated by
design; queued/async invocation is P5-T04's problem, not this module's. The
deadline below bounds the damage if one is made from somewhere it should not be.
"""
import json
import urllib.error
import urllib.request

from odoo import api, models
from odoo.exceptions import UserError

# Mirrors the satellite's own bounds (satellites/ai_gateway/providers.py), which
# in turn mirror config_sync.py. Three copies of one number would drift; the
# comment is the link until there is a shared config surface worth building.
_TIMEOUT = 30
_MAX_RESPONSE_BYTES = 256 * 1024

_DEFAULT_URL = 'http://ai-gateway:8080'


class AiGatewayClient(models.AbstractModel):
    """Thin HTTP client. All policy — budgets, breaker, provider — is the
    satellite's; this module must not re-implement any of it, or the two will
    eventually disagree about whether a request was allowed."""

    _name = 'ncollection.ai.gateway'
    _description = 'AI gateway satellite client (HTTP, no provider keys)'

    def _base_url(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'ncollection_ai.gateway_url', _DEFAULT_URL).rstrip('/')

    @api.model
    def complete(self, prompt, max_tokens=1024):
        """Send a SANITISED prompt to the gateway. Returns its JSON payload.

        The caller is responsible for having sanitised already — see
        ncollection.ai.pii. This module deliberately does not sanitise: doing it
        in two places means one of them eventually stops matching the policy,
        and §5 puts the policy tenant-side in one auditable place.
        """
        # The tenant name identifies the budget bucket. env.cr.dbname IS the
        # tenant on this platform (db-per-tenant), so there is nothing to look
        # up and nothing a caller could spoof by passing the wrong value.
        body = json.dumps({
            'tenant': self.env.cr.dbname,
            'prompt': prompt,
            'max_tokens': max_tokens,
        }).encode('utf-8')

        request = urllib.request.Request(
            '%s/v1/complete' % self._base_url(), data=body, method='POST',
            headers={'content-type': 'application/json'})

        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                return json.loads(
                    response.read(_MAX_RESPONSE_BYTES).decode('utf-8'))
        except urllib.error.HTTPError as exc:
            # The gateway's error bodies are deliberately friendly and carry no
            # tenant content (P5-T02), so surfacing the message is safe and
            # useful — a user hitting their AI allowance should be told that,
            # not shown a stack trace.
            try:
                detail = json.loads(exc.read().decode('utf-8'))
                message = detail.get('message') or detail.get('error')
            except (ValueError, OSError):
                message = 'HTTP %s' % exc.code
            # NOT wrapped in self.env._(): `message` is the gateway's own
            # error text, so there is no msgid for it in any catalogue and
            # the call was a no-op that read as though it did something.
            raise UserError(message) from None
        except urllib.error.URLError as exc:
            # Most often: the satellite is not running. That is a normal state —
            # it is an opt-in overlay — so the message says so rather than
            # leaving someone to debug a socket error.
            raise UserError(self.env._(
                "The AI service is not reachable (%s). If this is a development "
                "environment, start it with `make ai-up`.", exc.reason
            )) from None
        except (ValueError, OSError) as exc:
            raise UserError(self.env._(
                "The AI service returned an unreadable response (%s).",
                type(exc).__name__)) from None
