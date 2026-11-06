#!/usr/bin/env bash
# Make sure a DEVELOPMENT-ONLY AI gateway master key exists in the gitignored .env, and that the gateway runs the MOCK
# provider. Idempotent: an existing key is kept (rotating it would rotate every tenant's derived key), and nothing here
# ever prints the key.
#
#   scripts/ai/ensure_dev_gateway_key.sh [path/to/.env]
#
# Refuses (exit 1) when:
#   * NC_AI_PROVIDER is set to anything but "mock"   (this helper is for local development, never a real provider),
#   * NC_AI_API_KEY is non-empty                       (no provider credential may sit next to a development key),
#   * the file is tracked by git                       (a secret must never enter the repository).
# It generates a fresh random value; it never reuses a value found anywhere else on the machine.
set -eu
ENV_FILE="${1:-.env}"

if [ -f "$ENV_FILE" ] && git ls-files --error-unmatch "$ENV_FILE" >/dev/null 2>&1; then
  echo "refuse: $ENV_FILE is tracked by git; a secret must not live in a tracked file" >&2; exit 1
fi
[ -f "$ENV_FILE" ] || : > "$ENV_FILE"

value_of() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true; }

provider="$(value_of NC_AI_PROVIDER)"
if [ -n "$provider" ] && [ "$provider" != "mock" ]; then
  echo "refuse: NC_AI_PROVIDER=$provider; this helper only prepares the MOCK provider" >&2; exit 1
fi
if [ -n "$(value_of NC_AI_API_KEY)" ]; then
  echo "refuse: NC_AI_API_KEY is set in $ENV_FILE; remove provider credentials before using the development key" >&2; exit 1
fi

if [ -n "$(value_of NC_AI_GATEWAY_KEY)" ]; then
  echo "NC_AI_GATEWAY_KEY already present in $ENV_FILE (kept; value not shown)"
else
  key="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 48)"
  [ "${#key}" -ge 32 ] || { echo "refuse: could not generate a key" >&2; exit 1; }
  if grep -qE '^NC_AI_GATEWAY_KEY=' "$ENV_FILE"; then
    sed -i "s|^NC_AI_GATEWAY_KEY=.*|NC_AI_GATEWAY_KEY=$key|" "$ENV_FILE"
  else
    printf 'NC_AI_GATEWAY_KEY=%s\n' "$key" >> "$ENV_FILE"
  fi
  echo "generated a new development NC_AI_GATEWAY_KEY in $ENV_FILE (value not shown)"
fi
grep -qE '^NC_AI_PROVIDER=' "$ENV_FILE" || printf 'NC_AI_PROVIDER=mock\n' >> "$ENV_FILE"
echo "provider: mock; no external API is configured"
