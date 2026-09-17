# Superseded — do not run these by hand

These SQL files were the schema path before Alembic existed. Nothing executes
them, and **they are no longer the source of truth**.

Schema changes now live in `alembic/versions/` and are the only supported path:

```bash
alembic upgrade head        # apply everything pending
alembic revision --autogenerate -m "what changed"
alembic check               # fail if the models and the DB have drifted apart
```

The files are kept only as a historical record of how the schema got here. Their
effects are all folded into revision `0001`, so running one of them against a
current database will either fail or double-apply. If you are looking for a
change that these files describe, test that the current schema already has it,
then move on.

Existing databases created before Alembic need to be adopted once. Verify the
schema actually matches first — do not stamp blind:

```bash
alembic check     # only meaningful once stamped; otherwise compare schema first
alembic stamp 0001
alembic upgrade head
```
