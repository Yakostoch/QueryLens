def connect_postgresql(*, host, port, database, user, password, sslmode):
    """Open a PostgreSQL connection using values supplied by the dialog."""
    import psycopg

    return psycopg.connect(
        host=host,
        port=port,
        dbname=database,
        user=user,
        password=password,
        sslmode=sslmode,
        connect_timeout=5,
    )
