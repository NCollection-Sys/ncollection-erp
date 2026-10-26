-- ============================================================================
--  Kill switch for a REHEARSAL COPY of a database (scripts/upgrade/rehearse_upgrade.sh). Never run it on a real one.
-- ============================================================================
--  WHY (pre-commit review H2): a copy carries its source's live scheduled work. The shared Odoo server runs the crons
--  of EVERY database it can list (no db_name: cron_database_list() falls back to list_dbs; DESIGN_CRON_AND_QUEUE_
--  TOPOLOGY.md section 5.1), and a copy of the platform database holds "advance fleet migrations" (drives -u against
--  REAL tenants), config-sync reconcile, backups and dunning; a tenant copy holds queued mail to real recipients.
--
--  WHEN: appended to the restore stream, so it commits in the SAME transaction as the data (psql --single-transaction):
--  no other session can ever see the copy with an active cron. Re-applied right after the targeted upgrade, which can
--  re-activate data-file crons. Odoo's own `odoo neutralize` runs next, for each installed module's neutralize.sql.
--
--  WHAT: every cron off (autovacuum too) with its next call pushed out and its triggers gone (a re-activated cron is
--  then still not due); queue jobs that could still run cancelled; mail servers and fetchmail off; queued mail
--  cancelled. Optional tables are checked with to_regclass, so it applies to any database. Idempotent.
-- ============================================================================
SET search_path TO public;

UPDATE ir_cron SET active = false, nextcall = '9999-12-31 00:00:00' WHERE active OR nextcall < '9999-12-31';
DELETE FROM ir_cron_trigger;

DO $nc$
BEGIN
    IF to_regclass('public.queue_job') IS NOT NULL THEN
        UPDATE queue_job SET state = 'cancelled'
         WHERE state IN ('wait_dependencies', 'pending', 'enqueued', 'started');
    END IF;
    IF to_regclass('public.ir_mail_server') IS NOT NULL THEN
        UPDATE ir_mail_server SET active = false WHERE active AND name <> 'neutralization - disable emails';
    END IF;
    IF to_regclass('public.fetchmail_server') IS NOT NULL THEN
        UPDATE fetchmail_server SET active = false WHERE active;
    END IF;
    IF to_regclass('public.mail_mail') IS NOT NULL THEN
        UPDATE mail_mail SET state = 'cancel' WHERE state = 'outgoing';
    END IF;
END
$nc$;
