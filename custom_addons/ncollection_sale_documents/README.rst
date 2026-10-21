=========================
NCollection Sale Documents
=========================

* **Duplicate** (Arabic: نسخة مماثلة) in the quotation / sales order header. The new record is built by the model's
  own ``copy()``: a new number, the draft state, no invoices, no deliveries, no chatter history. It runs as the user,
  so it needs the same right as "New".
* **Preview** opens the very PDF that Print produces (same report, same route), instead of the customer-portal page,
  which is a different template.
