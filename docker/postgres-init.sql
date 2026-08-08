-- Postgres init: runs once on first volume init.
-- The default POSTGRES_DB is created by the entrypoint; we add the
-- separate `mlflow` database here so MLflow does not share alembic
-- state with the application schema.
--
-- mlflow gets its own Postgres role rather than reusing ${POSTGRES_USER}.
-- The win: a credential leaked from the `mlflow` container (logs, a future
-- SSRF, a compromised dependency in an MLflow plugin) is no longer the
-- *application's* own DATABASE_URL password, so it can't be used to
-- read/write projects/datasets/training_jobs — it can only touch the
-- `mlflow` database, which is expendable (it's derived state, rebuildable
-- from the runs). See docker-compose.yml's `mlflow` service comment for the
-- separate (and more modest) win from moving this password out of the
-- container's command line.
--
-- This file only runs against an EMPTY Postgres volume (first init). On an
-- existing deployment that already has a populated `postgres-data` volume,
-- re-running this script does nothing — that path is covered by
-- `scripts/deploy_pasaflow_vm.sh` Phase 5.5, which provisions the same
-- role/database idempotently (create-or-alter) on EVERY deploy. Keep the
-- two in sync: what this file creates on first init, Phase 5.5 must
-- converge existing boxes onto.
-- `\getenv` (psql meta-command, not a SQL statement) pulls the value out of
-- the postgres container's own environment at script-run time — the
-- entrypoint invokes this file with plain `psql -f`, with no `-v` flags to
-- hook into, so this is the only way to keep the password out of the SQL
-- text itself. `:"var"` quotes as an identifier (role name), `:'var'`
-- quotes as a string literal (password) — psql's usual distinction.
\getenv mlflow_db_user MLFLOW_DB_USER
\getenv mlflow_db_password MLFLOW_DB_PASSWORD
-- Role FIRST, so the database can name it as OWNER. OWNER is load-bearing
-- on PG15+: the `public` schema belongs to `pg_database_owner`, so a role
-- with only GRANT ALL ON DATABASE can connect but not CREATE TABLE — and
-- MLflow's own migrations do exactly that on first boot.
CREATE ROLE :"mlflow_db_user" LOGIN PASSWORD :'mlflow_db_password';
CREATE DATABASE mlflow OWNER :"mlflow_db_user";
GRANT ALL PRIVILEGES ON DATABASE mlflow TO :"mlflow_db_user";
