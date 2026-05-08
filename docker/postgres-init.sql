-- Postgres init: runs once on first volume init.
-- The default POSTGRES_DB is created by the entrypoint; we add the
-- separate `mlflow` database here so MLflow does not share alembic
-- state with the application schema.
CREATE DATABASE mlflow;
