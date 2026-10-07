def connect_postgresql(*, host, port, database, user, password, sslmode):
    """Подключение только к loopback; ввод адреса сервера не вызывает DNS."""
    host = host.strip().lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Разрешены только localhost, 127.0.0.1 или ::1.")
    if not database or "=" in database or "://" in database:
        raise ValueError("Введите имя базы, а не строку подключения.")
    if not user or not 1 <= int(port) <= 65535:
        raise ValueError("Проверьте пользователя и порт.")
    import psycopg

    return psycopg.connect(
        host=host,
        hostaddr="::1" if host == "::1" else "127.0.0.1",
        port=port,
        dbname=database,
        user=user,
        password=password,
        sslmode=sslmode,
        gssencmode="disable",
        connect_timeout=5,
        application_name="QueryLens",
        options="-c default_transaction_read_only=on -c statement_timeout=5000",
    )
