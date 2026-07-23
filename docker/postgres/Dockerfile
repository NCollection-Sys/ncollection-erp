# ============================================================================
#  NCollection ERP — PostgreSQL + pgBackRest  (ticket P2-T04)
# ============================================================================
#  postgres:16 with pgBackRest installed for continuous WAL archiving + PITR
#  (ARCHITECTURE_DATA_PLATFORM §5). Used ONLY by the backup overlay
#  (docker-compose.backup.yml) — dev/base still runs plain postgres:16.
#
#  The repo/spool/log dirs are created here owned by `postgres` so the named
#  volumes inherit that ownership on first mount (archive_command runs as the
#  postgres OS user and must be able to write the repo).
# ============================================================================
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends pgbackrest \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/lib/pgbackrest /var/spool/pgbackrest /var/log/pgbackrest \
    && chown -R postgres:postgres /var/lib/pgbackrest /var/spool/pgbackrest /var/log/pgbackrest
