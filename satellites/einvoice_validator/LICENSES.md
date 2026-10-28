# Licences of the validator worker

| Component | Licence | Evidence |
|---|---|---|
| `worker.py` (this repository) | LGPL-3 (repository licence) | — |
| SaxonC-HE (`saxonche` 13.1.0, wheel sha256 `edf6ff3c3883343afd4e90714b02fc19e556cc527ea8d8f528a381d5db99408c`) | **Mozilla Public License 2.0** | Saxonica terms page (https://www.saxonica.com/license/terms.xml: the open-source Saxon-HE product is offered under MPL 2.0, retrieved 2026-10-22) and the SaxonC product page (primary licence MPL 2.0; some components Apache-2.0 / BSD-style; source files marked "incompatible with secondary licenses"). The `saxonche` wheel itself ships **no LICENSE file** and declares no licence in its METADATA, which only points to the Saxonica terms. |
| Official PINT-AE Schematron / code lists / samples | OpenPeppol publication, mounted read-only, never modified | `custom_addons/ncollection_uae_einvoicing/resources/pint_ae/<release>/MANIFEST.txt` |

## Obligations and how they are met

* MPL-2.0 is file-level copyleft: the worker runs SaxonC as an UNMODIFIED, separately installed binary package in its own
  container and does not link it into Odoo or into this repository's code, so no source of ours is subject to MPL.
* Redistributing the worker image carries MPL notices. The wheel ships none, so before the image is distributed outside
  NCollection the third-party notices published by Saxonica for the pinned release must be collected and added here.
* "Incompatible with secondary licenses": SaxonC must not be combined into a GPL work. It is not.

## Open item

The exact licence TEXT shipped with the binary package could not be read from the wheel (it contains none). The terms
page is the evidence recorded above; confirm the notices bundle with Saxonica or counsel before distributing the image.
