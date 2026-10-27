"""Чтение узлов и показателей JSON-плана PostgreSQL."""


class PlanFormatError(ValueError):
    pass


def extract_plan_features(document):
    if not isinstance(document, list) or len(document) != 1:
        raise PlanFormatError("Ожидался JSON-план одной SQL-команды.")
    entry = document[0]
    if not isinstance(entry, dict) or not isinstance(entry.get("Plan"), dict):
        raise PlanFormatError("В результате EXPLAIN отсутствует Plan.")
    nodes = []

    def visit(node, path, parent):
        if not isinstance(node, dict):
            raise PlanFormatError("Некорректный узел JSON-плана.")
        rows, width = node.get("Plan Rows"), node.get("Plan Width")
        estimated = (
            rows * width
            if isinstance(rows, (int, float)) and isinstance(width, (int, float))
            else None
        )
        nodes.append(
            {
                "path": path,
                "parent_path": parent,
                "node_type": node.get("Node Type"),
                "relation": node.get("Relation Name"),
                "schema": node.get("Schema"),
                "alias": node.get("Alias"),
                "subplan_name": node.get("Subplan Name"),
                "parent_relationship": node.get("Parent Relationship"),
                "total_cost": node.get("Total Cost"),
                "plan_rows": rows,
                "plan_width": width,
                "estimated_bytes": estimated,
                "index_name": node.get("Index Name"),
                "conditions": {
                    key: node[key]
                    for key in (
                        "Filter",
                        "Index Cond",
                        "Recheck Cond",
                        "Join Filter",
                        "Hash Cond",
                        "Merge Cond",
                        "Run Condition",
                    )
                    if key in node
                },
                "sort_keys": node.get("Sort Key"),
                "output": node.get("Output"),
                "strategy": node.get("Strategy"),
                "group_keys": node.get("Group Key"),
                "presorted_keys": node.get("Presorted Key"),
                "cte_name": node.get("CTE Name"),
                "join_type": node.get("Join Type"),
                "inner_unique": node.get("Inner Unique"),
                "sort_method": node.get("Sort Method"),
                "sort_space_type": node.get("Sort Space Type"),
                "sort_space_used": node.get("Sort Space Used"),
                "actual_rows": node.get("Actual Rows"),
                "actual_loops": node.get("Actual Loops"),
                "actual_startup_time_ms": node.get("Actual Startup Time"),
                "actual_total_time_ms": node.get("Actual Total Time"),
                "parallel_aware": node.get("Parallel Aware"),
                "workers_planned": node.get("Workers Planned"),
                "workers_launched": node.get("Workers Launched"),
                "workers": node.get("Workers"),
                "incremental_sort_details": {
                    k: node[k] for k in ("Full-sort Groups", "Pre-sorted Groups") if k in node
                },
                "runtime_counters": {
                    key: node[key]
                    for key in (
                        "Rows Removed by Filter",
                        "Rows Removed by Join Filter",
                        "Rows Removed by Index Recheck",
                        "Shared Hit Blocks",
                        "Shared Read Blocks",
                        "Shared Dirtied Blocks",
                        "Shared Written Blocks",
                        "Local Hit Blocks",
                        "Local Read Blocks",
                        "Local Dirtied Blocks",
                        "Local Written Blocks",
                        "Temp Read Blocks",
                        "Temp Written Blocks",
                        "I/O Read Time",
                        "I/O Write Time",
                        "Temp I/O Read Time",
                        "Temp I/O Write Time",
                        "Heap Fetches",
                        "Hash Batches",
                        "Shared I/O Read Time",
                        "Shared I/O Write Time",
                        "Local I/O Read Time",
                        "Local I/O Write Time",
                        "HashAgg Batches",
                        "Original Hash Batches",
                        "Peak Memory Usage",
                        "Disk Usage",
                    )
                    if key in node
                },
            }
        )
        for number, child in enumerate(node.get("Plans", [])):
            visit(child, f"{path}.Plans[{number}]", path)

    visit(entry["Plan"], "Plan", None)
    root = nodes[0]
    return {
        "summary": {
            key: root[key]
            for key in ("node_type", "total_cost", "plan_rows", "plan_width", "estimated_bytes")
        },
        "planning_time_ms": entry.get("Planning Time"),
        "execution_time_ms": entry.get("Execution Time"),
        "nodes": nodes,
    }
