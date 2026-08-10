# -*- coding: utf-8 -*-
"""Natural-language to Odoo domain mapper (P5-T05 / #62).

Turns a question into a VALIDATED search domain and stops there. It does not
run the domain: no ``search``, no ``read``, no ``search_read``, no write of any
kind, and no ``sudo``. Execution belongs to the consumer (P5-T07 / #64), under
that caller's own access rights — which is the only place it can be done
correctly, because only there is the acting user known.

**This capability is OFF and mock-only. Both, deliberately.**

#375 decided that arbitrary user prose must not reach an LLM provider in
production: the widget ships curated questions, and
``ncollection_ai.enable_free_text_questions`` stays off. A mapper is, by its
nature, the free-text surface that decision defers — so it ships behind its own
switch (``ncollection_ai.enable_nl_domain_mapper``, default OFF) and, beyond
that, REFUSES unless the gateway satellite reports it is running the ``mock``
provider.

That second gate is the point. #375 also requires a zero-retention agreement
AND a recorded tenant-admin acknowledgement before any non-mock provider is
used, and that acknowledgement mechanism does not exist yet. Without the
provider check, turning on one config flag would be enough to start sending
prose to a real provider with no acknowledgement anywhere — exactly the drift
#375 exists to prevent. With it, the flag alone cannot: the satellite must also
be in mock mode. It is a SAFETY GATE, not an authorisation mechanism, and it
does not make the capability production-ready; the acknowledgement work does.

What crosses the boundary is the question plus the field table for ONE
whitelisted model — names, types, and the options a selection field accepts.
No records, no values, no aggregates. The tenant-context builder
(``ncollection.ai.context``) is never called from here, so the P5-T03 context
path is untouched and no tenant data can reach this prompt by any route.
"""

import json
import urllib.error
import urllib.request

from odoo import api, models
from odoo.exceptions import AccessError, UserError

from . import domain_schema

#: Who may map. The same three the rest of this module answers to — narrow now,
#: widened later on evidence rather than on a guess (#375).
_ALLOWED_GROUPS = (
    'ncollection_core.group_role_accountant',
    'ncollection_core.group_role_ceo',
    'base.group_system',
)

#: A search question is a sentence. Longer is a paste or an attempt to push the
#: real instruction out of the window, and it is refused before anything is
#: sent rather than truncated — truncation silently changes what was asked.
_MAX_QUESTION_CHARS = 500

#: The satellite's unauthenticated liveness route. It names no tenant and
#: carries no credential (`gateway.py` /healthz), which is why reading it here
#: is safe; we read exactly one key from it.
_HEALTH_PATH = '/healthz'
_HEALTH_TIMEOUT = 5
_HEALTH_MAX_BYTES = 4096

#: The only provider this capability may run against for now (see #375).
_REQUIRED_PROVIDER = 'mock'

_PROMPT = """Translate the question into an Odoo search domain.

Model: {model}

Allowed fields (use no others):
{fields}

Operators: {operators}

Rules:
- Reply with JSON only: {{"domain": [...]}}
- Use Odoo prefix notation, e.g. ["&", ["a", "=", 1], ["b", ">", 2]]
- Match many2one fields by integer id only.
- Dates are "YYYY-MM-DD".
- If the question cannot be expressed with these fields, reply {{"domain": null}}

Question: {question}
"""


class AiDomainMapper(models.AbstractModel):
    _name = 'ncollection.ai.domain.mapper'
    _description = 'Natural-language to Odoo domain mapper (P5-T05)'

    # ---------------------------------------------------------------- gates

    def _mapper_enabled(self):
        """Whether this workspace has switched the mapper on. Default NO.

        Its own parameter, NOT ``enable_free_text_questions``. #375 treats free
        text as a separate, later decision; sharing one flag would mean a
        future decision to enable either capability silently enabled the other.
        """
        return self.env['ir.config_parameter'].sudo().get_param(
            'ncollection_ai.enable_nl_domain_mapper', ''
        ).strip().lower() in ('1', 'true', 'yes')

    def _gateway_provider(self):
        """The provider the satellite reports, or None if it cannot be read.

        Unreachable is treated as unknown, and unknown refuses — a gate that
        fails open is not a gate. Nothing is sent to reach this answer: it is a
        GET of the liveness route, which carries no prompt.
        """
        base = self.env['ncollection.ai.gateway']._base_url()
        try:
            request = urllib.request.Request(
                '%s%s' % (base, _HEALTH_PATH), method='GET')
            with urllib.request.urlopen(
                    request, timeout=_HEALTH_TIMEOUT) as response:
                payload = json.loads(
                    response.read(_HEALTH_MAX_BYTES).decode('utf-8'))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
                OSError):
            return None
        provider = payload.get('provider')
        return provider if isinstance(provider, str) else None

    # ---------------------------------------------------------------- prompt

    def _schema_lines(self, model):
        """The minimum schema disclosure: name, type, and selection options.

        Built from the whitelist, never from the live registry, so what can be
        described is exactly what can be validated — a field this cannot name
        is a field the model cannot be told about.
        """
        lines = []
        for field, spec in sorted(domain_schema.schema_for(model).items()):
            if spec['type'] == 'selection':
                lines.append('- %s (selection: %s)'
                             % (field, ' | '.join(spec['values'])))
            else:
                lines.append('- %s (%s)' % (field, spec['type']))
        return '\n'.join(lines)

    def _build_prompt(self, question, model):
        operators = ', '.join(sorted({
            op
            for spec in domain_schema.schema_for(model).values()
            for op in domain_schema._OPS_BY_TYPE[spec['type']]
        }))
        return _PROMPT.format(model=model, fields=self._schema_lines(model),
                              operators=operators, question=question)

    # ---------------------------------------------------------------- public

    @api.model
    def map_question(self, question, model):
        """Return ``{'model': ..., 'domain': [...]}`` for a question.

        The domain is validated and inert. This method never executes it.
        Raises AccessError for a caller who may not map, and UserError for
        every refusal — including a payload that did not survive validation.
        """
        # Authorisation first: a check that runs after the prompt is built has
        # already spent the tenant's budget on behalf of someone not allowed to
        # ask. AccessError maps to HTTP 403, which is what an RPC caller should
        # see for a refusal of identity rather than of content.
        if not any(self.env.user.has_group(g) for g in _ALLOWED_GROUPS):
            raise AccessError(self.env._(
                "Natural-language search is available to the Accountant, the "
                "CEO and the workspace owner."))

        if not self._mapper_enabled():
            raise UserError(self.env._(
                "Natural-language search is not enabled on this workspace. "
                "Sending typed text to an external provider must be switched "
                "on deliberately."))

        if not isinstance(question, str) or not question.strip():
            raise UserError(self.env._("Ask a question to search for."))
        if len(question) > _MAX_QUESTION_CHARS:
            raise UserError(self.env._(
                "That question is too long to search with (limit %s "
                "characters).", _MAX_QUESTION_CHARS))

        if model not in domain_schema.allowed_models():
            raise UserError(self.env._(
                "Natural-language search covers: %s.",
                ', '.join(domain_schema.allowed_models())))

        # The mock-only gate — see the module docstring. Before the prompt is
        # built, so a refusal costs nothing and sends nothing.
        provider = self._gateway_provider()
        if provider != _REQUIRED_PROVIDER:
            raise UserError(self.env._(
                "Natural-language search is limited to the test provider on "
                "this deployment. Using a live AI provider requires the "
                "provider terms and a recorded administrator acknowledgement "
                "that are not in place yet."))

        payload = self.env['ncollection.ai.gateway']._complete(
            self._build_prompt(question, model), max_tokens=512)

        return {'model': model,
                'domain': self._validated_domain(payload, model)}

    # ---------------------------------------------------------------- parsing

    def _validated_domain(self, payload, model):
        """Parse the provider payload STRICTLY, then validate it.

        ``json.loads`` and nothing else. The result is data; it is never
        evaluated, executed, or used to look anything up. A payload that is not
        JSON, or is JSON of the wrong shape, is refused with the same message
        as one that is well formed but unsafe — the caller learns that the
        search could not be built, not how close the payload got.
        """
        text = payload.get('text') if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise UserError(self._refusal())

        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            raise UserError(self._refusal()) from None

        if not isinstance(parsed, dict) or 'domain' not in parsed:
            raise UserError(self._refusal())

        raw = parsed['domain']
        # An explicit null is the model saying it could not express the
        # question — a refusal by design, not a malformed payload.
        if raw is None:
            raise UserError(self.env._(
                "That question could not be turned into a search over %s.",
                model))

        try:
            return domain_schema.validate(model, raw)
        except domain_schema.DomainRejected:
            # The rejection reason is deliberately not surfaced: it is derived
            # from provider output, and echoing it would render attacker-chosen
            # text to a user.
            raise UserError(self._refusal()) from None

    def _refusal(self):
        return self.env._(
            "That question could not be turned into a safe search. Try "
            "describing what you are looking for in simpler terms.")
