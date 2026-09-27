from alembic import context

context.configure(connection=context.config.attributes["connection"], target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
