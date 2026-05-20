-- gsfluent v2 — postgres init
-- Runs once on first boot via docker-entrypoint-initdb.d.
-- The database (gsfluent_v2) and role (gsfluent) are created by the postgres
-- image's POSTGRES_DB / POSTGRES_USER env vars. This script adds extensions.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

GRANT ALL PRIVILEGES ON DATABASE gsfluent_v2 TO gsfluent;
