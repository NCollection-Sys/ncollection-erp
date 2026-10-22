============================
NCollection Purchase Documents
============================

* **Duplicate** (Arabic: نسخة مماثلة) in the request for quotation / purchase order header. The new record is built by
  the model's own ``copy()``: a new number, the draft state, no receipts, no bills, no chatter history. It runs as the
  user, so it needs the same right as "New".
