# Validator worker: the ONLY place an XSLT 2.0 processor lives. It is not part of the Odoo image.
# SaxonC-HE is MPL-2.0 (see LICENSES.md). Reproducible: the base image is pinned by digest, the only dependency by
# version AND file hash (requirements.txt, pip --require-hashes), and the official rules are mounted read-only at /rules.
#
#   docker build -t nc-einvoice-validator satellites/einvoice_validator
#   docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges --network <internal-network> \
#       -v <repo>/custom_addons/ncollection_uae_einvoicing/resources/pint_ae/<release>:/rules:ro \
#       -e NC_EINVOICE_SPEC_VERSION=<release> -e NC_EINVOICE_VALIDATOR_TOKEN=<32+ chars> \
#       --memory 512m --cpus 1 nc-einvoice-validator
#
# python:3.12-slim at the digest below (linux/amd64 and linux/arm64 index digest).
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /app/requirements.txt
COPY worker.py /app/worker.py
# the licence notices travel with the image (MPL-2.0 obligations, see the file)
COPY LICENSES.md /licenses/LICENSES.md
USER 65534:65534
ENV NC_EINVOICE_RULES_DIR=/rules PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8090
# READY = the process answers AND it already verified every rule file against the manifest and compiled the stylesheets
# (it listens only after both), so a healthy container is one that holds exactly the pinned rule set.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python3", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('NC_EINVOICE_VALIDATOR_PORT','8090'), timeout=3)"]
CMD ["python3", "worker.py"]
