# -*- coding: utf-8 -*-
"""Anomaly Alerts were removed from the product (owner decision, 2026-11-10).

RETENTION. The model `ncollection.alert` and its table `ncollection_alert` go away with the feature. Odoo drops a removed
model's table, so any history is first copied to a plain table, ``ncollection_alert_archive`` (no model, no ACL, no
menu: nothing in the product reads it). Nothing is copied when the table is empty or absent. The archive is only
created, never dropped; an operator may export and drop it after the retention period.
Idempotent: a second run finds nothing to copy and leaves the archive alone.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT to_regclass('ncollection_alert')")
    if not cr.fetchone()[0]:
        return
    cr.execute("SELECT count(*) FROM ncollection_alert")
    count = cr.fetchone()[0]
    if not count:
        return
    cr.execute("SELECT to_regclass('ncollection_alert_archive')")
    if cr.fetchone()[0]:
        _logger.warning("Anomaly Alerts: ncollection_alert_archive already exists; %s live row(s) not re-archived.", count)
        return
    cr.execute("CREATE TABLE ncollection_alert_archive AS TABLE ncollection_alert")
    _logger.info("Anomaly Alerts removal: archived %s alert row(s) into ncollection_alert_archive.", count)
