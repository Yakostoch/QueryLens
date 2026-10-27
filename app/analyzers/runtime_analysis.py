"""Обработка измерений с учётом циклов и неполного чтения узлов."""


def analyze_runtime(plan, *, measured=False):
    result = dict(
        schema_version=1,
        status="not_measured",
        execution_time_ms=None,
        planning_time_ms=None,
        node_observations=[],
        limitations=[
            "Execution Time — серверное выполнение под EXPLAIN, без передачи результата приложению; накладные расходы измерения возможны.",
            "Actual Rows и Rows Removed усредняются по loops; произведение на loops приблизительно и не означает уникальные строки.",
            "Буферы включают дочерние операции и повторные обращения; их нельзя суммировать по дереву или приравнивать к физическому I/O.",
            "Временные блоки показывают временный I/O, а не его единственную причину. Worker-данные не прибавляются к общим счётчикам.",
            "LIMIT, semi/anti join и параллелизм ограничивают сравнение оценок с фактом; ошибка оценки не устанавливается по одному отношению.",
        ],
    )
    if not measured:
        return result
    result.update(
        status="measured",
        execution_time_ms=plan.get("execution_time_ms"),
        planning_time_ms=plan.get("planning_time_ms"),
    )
    nodes = plan["nodes"]
    restricted = any(
        n["node_type"] == "Limit"
        or n.get("join_type") in {"Semi", "Anti"}
        or n.get("parallel_aware")
        or n["node_type"] in {"Gather", "Gather Merge"}
        or n.get("parent_relationship") in {"SubPlan", "InitPlan"}
        or n.get("conditions", {}).get("Run Condition")
        for n in nodes
    )
    for n in nodes:
        rows, loops = n.get("actual_rows"), n.get("actual_loops")
        valid = isinstance(rows, (int, float)) and isinstance(loops, (int, float))
        counters = n.get("runtime_counters") or {}
        item = dict(
            path=n["path"],
            node_type=n["node_type"],
            actual_rows_per_loop=rows,
            actual_loops=loops,
            approximate_total_output_rows=rows * loops if valid else None,
            actual_total_time_ms_per_loop=n.get("actual_total_time_ms"),
            plan_rows=n.get("plan_rows"),
            estimate_to_actual_ratio=None,
            row_comparison_status="not_comparable" if restricted else "not_evaluated",
            runtime_counters=counters,
            sort_space_type=n.get("sort_space_type"),
            sort_space_used_kb=n.get("sort_space_used"),
            sort_method=n.get("sort_method"),
            disk_sort_observed=n.get("sort_space_type") == "Disk",
            temporary_io_observed=any(
                counters.get(k, 0) > 0 for k in ("Temp Read Blocks", "Temp Written Blocks")
            ),
            multi_batch_hash_observed=max(
                counters.get("Hash Batches", 0), counters.get("HashAgg Batches", 0)
            )
            > 1,
            incremental_sort_details=n.get("incremental_sort_details"),
            worker_disk_sort_observed=any(
                w.get("Sort Space Type") == "Disk" for w in n.get("workers") or []
            ),
            workers_planned=n.get("workers_planned"),
            workers_launched=n.get("workers_launched"),
            worker_details=n.get("workers"),
        )
        estimated = n.get("plan_rows")
        if valid and loops == 0:
            item["row_comparison_status"] = "not_executed"
        elif valid and not restricted and isinstance(estimated, (int, float)):
            # Отношение оценок и измерений само по себе не устанавливает причину.
            if rows > 0 and estimated > 0:
                item["estimate_to_actual_ratio"] = max(rows / estimated, estimated / rows)
                item["row_comparison_status"] = "ratio_only_not_cause"
            elif rows == estimated:
                item["row_comparison_status"] = "both_zero"
            else:
                item["row_comparison_status"] = "zero_mismatch"
        result["node_observations"].append(item)
    return result
