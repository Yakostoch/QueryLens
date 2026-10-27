# Контрольная матрица SQL — этап 7

131 сценарий. Синтаксический кандидат не является подтверждённой причиной замедления.
128 сценариев проверены PostgreSQL 18.6 в PGlite 0.5.12. Три параметризованных сценария проверяются только синтаксически.
Рекурсивный CTE имеет ожидаемо недоступный сбор структуры. Для N+1 нужна трасса операции приложения.
В expected_volume, expected_predicate_status и expected_operation_status заданы проверяемые выводы; consumer_context передаётся явно.

| ID | Правило | Категория | Синтаксические кандидаты | Назначение |
|---|---|---|---|---|
| ap01_star | AP01 | potential | AP01 | Нужность всех полей неизвестна. |
| ap01_alias_star | AP01 | valid_construct | AP01 | Одна строка со всеми полями может быть нужна. |
| ap01_explicit | AP01 | good | нет | Явная узкая проекция. |
| ap01_pruned | AP01 | complex | AP01 | Звёздочка подзапроса может быть устранена планировщиком. |
| ap02_lower | AP02 | potential | AP02 | Выражение и его индекс надо сопоставить. |
| ap02_arithmetic | AP02 | potential | AP02 | Переписывание требует проверки переполнения. |
| ap02_constant | AP02 | good | нет | Функция над константой. |
| ap02_cast | AP02 | potential | AP02 | Приведение над столбцом, не над константой. |
| ap03_substring | AP03 | potential | AP03 | Нужен поиск подстроки. |
| ap03_prefix | AP03 | good | нет | Известен префикс; это не начальный %. |
| ap03_escape | AP03 | good | нет | Первый % буквальный. |
| ap03_short | AP03 | edge | AP03 | Короткий шаблон не обещает полезных триграмм. |
| ap04_or | AP04 | potential | AP04 | Индексы и селективность ветвей могут различаться. |
| ap04_good_or | AP04 | valid_construct | AP04 | OR может эффективно выполняться. |
| ap04_in | AP04 | good | нет | IN не является синтаксической группой OR. |
| ap04_grouped | AP04 | complex | AP02, AP04 | Нельзя терять окружающее AND и скобки. |
| ap05_notin | AP05 | potential | AP05 | Внутренняя сторона допускает NULL. |
| ap05_notexists | AP05 | good | нет | Anti-условие без автоматического утверждения эквивалентности NOT IN. |
| ap05_filtered | AP05 | valid_construct | AP05 | NULL исключён фильтром; конструкция не обязательно плохая. |
| ap05_row | AP05 | complex | AP05 | Многоколоночная NULL-семантика. |
| ap06_distinct | AP06 | potential | AP06, AP15 | DISTINCT компенсирует кратность; нужна семантика результата. |
| ap06_exists | AP06 | good | нет | Нужен факт наличия. |
| ap06_required | AP06 | valid_construct | AP06 | Уникальные города действительно нужны. |
| ap06_distincton | AP06 | complex | AP06, AP07 | Выбор последнего заказа; DISTINCT ON сохраняет особый смысл. |
| ap07_sort | AP07 | potential | AP07 | Сортируется полный результат. |
| ap07_top | AP07 | valid_construct | AP07 | Малый LIMIT с подходящим индексом может быть эффективен. |
| ap07_noorder | AP07 | good | нет | Нет требования к порядку. |
| ap07_windowonly | AP07 | good | AP14 | Порядок окна не является внешним ORDER BY. |
| ap08_deep | AP08 | potential | AP07, AP08 | Глубокая пагинация; небольшой LIMIT не доказывает малую работу. |
| ap08_zero | AP08 | good | AP07 | Нулевой OFFSET не создаёт пропускания. |
| ap08_keyset | AP08 | good | AP07 | Курсорный вариант при известном курсоре. |
| ap08_no_limit | AP08 | edge | AP07, AP08 | OFFSET без LIMIT тоже поддерживается. |
| ap09_key | AP09 | potential | AP09, AP11 | Планировщик может сам перенести фильтр. |
| ap09_aggregate | AP09 | valid_construct | AP09, AP11 | Агрегатный фильтр нельзя просто перенести в WHERE. |
| ap09_where | AP09 | good | нет | Ранний фильтр явно записан. |
| ap09_rollup | AP09 | complex | AP09 | ROLLUP требует отдельного доказательства. |
| ap10_parent | AP10 | insufficient | AP07 | Один SQL не показывает клиентский N+1. |
| ap10_child | AP10 | insufficient | нет | Повторяемый шаблон без трассы не доказывает N+1. |
| ap10_batch | AP10 | good | нет | Пакетная загрузка не показывает число операций приложения. |
| ap10_serverloop | AP10 | complex | AP15 | Серверный Nested Loop не равен клиентскому N+1. |
| ap11_existence | AP11 | potential | AP11 | Булева проверка существования выражена явно. |
| ap11_exists | AP11 | good | нет | EXISTS не является COUNT. |
| ap11_exact | AP11 | valid_construct | AP11 | Точный подсчёт может быть нужен, контракт неизвестен. |
| ap11_nullable | AP11 | edge | нет | COUNT(column) учитывает NULL, не COUNT(*). |
| ap12_cross | AP12 | potential | AP12, AP15 | Намеренность произведения неизвестна. |
| ap12_comma_link | AP12 | valid_construct | AP15 | Связь есть в WHERE; кандидат JOIN не диагноз произведения. |
| ap12_one_row | AP12 | valid_construct | AP15 | Однострочный источник намеренный. |
| ap12_lateral | AP12 | complex | AP07, AP15 | LATERAL зависит от внешней строки. |
| ap13_materialized | AP13 | potential | AP13 | Проверяется место фильтра, не одно слово WITH. |
| ap13_inline | AP13 | valid_construct | AP13 | Встраивание может быть эффективно. |
| ap13_reused | AP13 | complex | AP13, AP15 | Материализация может экономить повторную работу. |
| ap13_recursive | AP13 | complex | AP13 | Рекурсивный CTE не переписывается как обычный. |
| ap14_rank | AP14 | potential | AP14 | Объём и порядок окна требуют плана. |
| ap14_two | AP14 | complex | AP14 | Разные окна могут требовать смены порядка. |
| ap14_count | AP14 | valid_construct | AP14 | Оконный COUNT относится к AP14, не проверке существования. |
| ap14_outerfilter | AP14 | complex | AP14 | Перенос фильтра может менять значения окна. |
| ap15_join | AP15 | potential | AP15 | Алгоритм JOIN сам по себе не ошибка. |
| ap15_left | AP15 | valid_construct | AP15 | ON и WHERE при LEFT JOIN не взаимозаменяемы. |
| ap15_expression | AP15 | complex | AP15 | Выражение в ON не считается AP02 в WHERE. |
| ap15_three | AP15 | complex | AP15 | Несколько связей и фильтров. |
| mixed_many | AP15 | complex | AP01, AP02, AP03, AP04, AP06, AP07, AP08, AP13, AP14, AP15 | Много ветвей одновременно, оконный COUNT не AP11. |
| mixed_union | AP03 | complex | AP02, AP03 | Несколько SELECT требуют разных областей. |
| literal_comment | AP01 | good | нет | Текст и комментарии не являются конструкциями. |
| projection_like | AP03 | good | нет | LIKE в проекции не фильтрует доступ. |
| parameter_like | AP03 | insufficient | нет | Шаблон неизвестен; AP03 insufficient_data. |
| parameter_offset | AP08 | insufficient | AP08 | Параметр смещения неизвестен. |
| ap02_expression_index | AP02 | valid_construct | AP02 | Индекс на выражении существует; план проверяется без принуждения. |
| ap03_trigram_selective | AP03 | valid_construct | AP03 | Селективный шаблон; при --with-trigram проверяется доступ через pg_trgm. |
| portable_quoted_join | AP12 | complex | AP02, AP03, AP15 | Независимость анализа от имён и схем PostgreSQL. |
| portable_self_join | AP12 | complex | AP02, AP03, AP15 | Независимость анализа от имён и схем PostgreSQL. |
| portable_cyrillic_join | AP12 | complex | AP02, AP03, AP15 | Независимость анализа от имён и схем PostgreSQL. |
| portable_search_path | AP12 | complex | AP02, AP03, AP15 | Независимость анализа от имён и схем PostgreSQL. |
| portable_cartesian | AP12 | complex | AP12, AP15 | Независимость анализа от имён и схем PostgreSQL. |
| portable_comma_link | AP12 | complex | AP15 | Независимость анализа от имён и схем PostgreSQL. |
| portable_outer_join | AP12 | complex | AP02, AP15 | Независимость анализа от имён и схем PostgreSQL. |
| portable_using | AP12 | complex | AP15 | Независимость анализа от имён и схем PostgreSQL. |
| volume_star_required | AP01 | good | AP01 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_star_subset | AP01 | edge | AP01 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_star_using | AP01 | edge | AP01, AP15 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_star_quoted | AP01 | edge | AP01 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_offset_zero_limit | AP08 | good | AP08 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_offset_unknown_limit | AP08 | edge | AP08 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_offset_alias | AP08 | edge | AP07, AP08 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_boolean | AP11 | edge | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_absent | AP11 | edge | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_negative | AP11 | edge | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_exact | AP11 | good | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_numeric_contract | AP11 | edge | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_threshold | AP11 | good | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_random | AP11 | edge | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_limit | AP11 | good | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_count_nested | AP11 | edge | AP11 | Контроль объёма, назначения результата и безопасного воздержания. |
| volume_offset_union | AP08 | complex | AP07, AP08 | OFFSET над UNION остаётся явным unknown, без привязки чужих показателей. |
| predicate_or_bitmap | AP04 | edge | AP02, AP04 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_or_overlap | AP04 | edge | AP04 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_or_in | AP04 | edge | AP04 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_or_projection | AP04 | edge | нет | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_nn | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_same_alias | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_nullable | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_empty | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_filter_null | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_outer_filter | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_limit | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_correlated | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_null_literal | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_tuple | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_or_quoted | AP04 | edge | AP04 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| predicate_notin_quoted | AP05 | edge | AP05 | Контроль OR/NOT IN, NULL, дубликатов и границ сопоставления. |
| op_distinct_pk | AP06 | good | AP06 | Первичный ключ одной физической таблицы сохраняет уникальность. |
| op_distinct_required | AP06 | valid_construct | AP06 | Повторы городов должны устраняться; удаления нет. |
| op_distinct_join | AP06 | complex | AP06, AP15 | Ключ пользователя не доказывает уникальность после JOIN. |
| op_distinct_on | AP06 | complex | AP06, AP07 | DISTINCT ON требует сохранения выбора строки. |
| op_distinct_inheritance | AP06 | edge | AP06 | Наследование может дать два одинаковых первичных ключа. |
| op_distinct_quoted | AP06 | good | AP06 | Каталог другой схемы и регистр имён. |
| op_sort_full | AP07 | potential | AP07 | Вход сортировки берётся из дочернего узла. |
| op_sort_limit | AP07 | complex | AP07 | Малый LIMIT не означает малый вход сортировки. |
| op_sort_index | AP07 | good | AP07 | Планировщик может получить порядок из индекса. |
| op_having_early | AP09 | valid_construct | AP09, AP11 | Проверяем ранний отбор уже в текущем плане. |
| op_having_aggregate | AP09 | good | AP09, AP11 | Условие агрегата не переносится в WHERE. |
| op_having_mixed | AP09 | complex | AP09, AP11 | Каждое условие AND проверяется отдельно. |
| op_having_rollup | AP09 | edge | AP09, AP11 | Группирующие наборы не получают простое правило переноса. |
| op_cte_materialized | AP13 | potential | AP13 | Производитель и фильтр потребителя должны быть разделены. |
| op_cte_inline | AP13 | good | AP13 | Отсутствие узлов CTE не объявляется ошибкой. |
| op_cte_reused | AP13 | complex | AP13, AP15 | Несколько потребителей, общие фильтры не угадываются. |
| op_window_named | AP14 | valid_construct | AP14 | Именованная спецификация хранится отдельно от функции. |
| op_window_multiple | AP14 | complex | AP14 | Несовместимые проходы не объединяются по догадке. |
| op_window_rank_filter | AP14 | complex | AP14 | Вложенному окну не приписываются оценки корня. |
| op_join_left | AP15 | valid_construct | AP15 | LEFT JOIN сохраняет отсутствие совпадений. |
| op_join_multiple | AP15 | complex | AP15 | Физические узлы нескольких JOIN наблюдаются на уровне запроса. |
| op_good_explicit | AP15 | good | нет | Хороший простой запрос без перечисленных операций. |

## Проверки операций этапа 7

| ID | Ожидаемый статус |
|---|---|
| op_distinct_pk | AP06: projection_unique_by_primary_key |
| op_distinct_required | AP06: dedup_plan_observed, dedup_node_not_observed |
| op_distinct_join | AP06: dedup_plan_observed, dedup_node_not_observed; AP15: join_plan_observed |
| op_distinct_on | AP06: distinct_on_requires_order_semantics |
| op_distinct_inheritance | AP06: dedup_plan_observed, dedup_node_not_observed |
| op_distinct_quoted | AP06: projection_unique_by_primary_key |
| op_sort_full | AP07: sort_input_observed |
| op_sort_limit | AP07: sort_input_observed |
| op_sort_index | AP07: no_explicit_sort, sort_input_observed |
| op_having_early | AP09: having_predicates_checked |
| op_having_aggregate | AP09: having_predicates_checked |
| op_having_mixed | AP09: having_predicates_checked |
| op_having_rollup | AP09: having_predicates_checked |
| op_cte_materialized | AP13: cte_producer_observed |
| op_cte_inline | AP13: no_explicit_cte_nodes |
| op_cte_reused | AP13: cte_producer_observed; AP15: insufficient_data |
| op_window_named | AP14: window_pass_observed |
| op_window_multiple | AP14: window_pass_mapping_unknown |
| op_window_rank_filter | AP14: insufficient_data |
| op_join_left | AP15: join_plan_observed |
| op_join_multiple | AP15: join_plan_observed |
| op_good_explicit | AP15: нет находок; AP06: нет находок; AP07: нет находок |
