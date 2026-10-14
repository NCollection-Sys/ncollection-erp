=========================
NCollection Cheque Print
=========================

Prints the variable data of an outbound ``account.payment`` onto a PRE-PRINTED bank cheque.

What it does
============

* Cheque layouts are data (``ncollection.cheque.template`` plus its fields, in millimetres); a drag-and-drop designer
  edits them.
* The PDF is drawn at the exact physical size of the template (no A4 wrapper, no browser scaling), with Arabic shaping
  and right-to-left text handled in pure Python.
* Printer profiles hold the offset and scale that calibrate one printer.
* Every print, reprint and void is an append-only line of the print job's history; a reprint or a void needs a reason.
* Print Jobs > New opens the print wizard: choose the payment and the template, then print.

It never generates MICR lines, logos or security elements. The shipped template sizes are PROVISIONAL until a
template is confirmed against a physical cheque.

A companion of Accounting: it is licensed exactly when a tenant has Accounting.

More: ``docs/markdown/CHEQUE_PRINTING.md``.
