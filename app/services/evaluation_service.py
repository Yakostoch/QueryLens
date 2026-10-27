"""Сравнение результатов правил с проверенной разметкой."""

from app.services.analysis_presentation import resolve, summarize
from app.services.dataset_service import (
    RULES,
    DatasetError,
    training_splits,
    validate_record,
    validate_review,
)

POLICY = "concise_findings_v1"


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _scores(counts):
    tp, fp, fn, tn = (counts[name] for name in ("tp", "fp", "fn", "tn"))
    answered = counts["decisions"] - counts["abstained"]
    return dict(
        **counts,
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, tp + fn),
        f1=_ratio(2 * tp, 2 * tp + fp + fn),
        coverage=_ratio(answered, counts["decisions"]),
        accuracy_when_answered=_ratio(tp + tn, answered),
        strict_accuracy=_ratio(tp + tn, counts["decisions"]),
    )


def _empty_counts():
    return dict(tp=0, fp=0, fn=0, tn=0, abstained=0, decisions=0)


def predictions(record):
    """Возвращает все полезные замечания, включая скрытые кратким представлением."""
    payload = {**record["input"], **record["automatic_analysis"]}
    tree = payload["diagnostic_tree"]
    states = {}
    for group in tree["root"]["children"]:
        for rule in group["children"]:
            unknown = rule["state"] == "needs_evidence" or any(
                c["state"] == "needs_evidence" for c in rule.get("children", [])
            )
            states[rule["rule_id"]] = "abstain" if unknown else "clear"
    view = summarize(payload)
    visible, all_flags = set(), set()
    for index, item in enumerate(view["all_findings"]):
        ref = item["source_ref"]
        source = resolve(payload, ref)
        rule = source.get("rule_id") if isinstance(source, dict) else None
        if ref.startswith("/sql_features/source_connection_graph/"):
            rule = "AP12"
        if rule not in RULES:
            continue  # Измерение само по себе не задаёт метку антипаттерна.
        if item["label"] == "Недостаточно данных":
            if states.get(rule) != "flag":
                states[rule] = "abstain"
        else:
            states[rule] = "flag"
            all_flags.add(rule)
            if index < 3:
                visible.add(rule)
    return states, sorted(visible), sorted(all_flags)


def evaluate_records(records, *, manifest=None, split="test"):
    by_id = {}
    for record in records:
        validate_record(record)
        if record["case_id"] in by_id:
            raise DatasetError("Повторный case_id в оценке.")
        by_id[record["case_id"]] = record
    selected = set(by_id)
    scope = "development_not_independent_holdout"
    if manifest is not None:
        if not isinstance(manifest, dict):
            raise DatasetError("Manifest должен быть объектом JSON.")
        _, expected = training_splits(records, seed=manifest.get("seed", "querylens-v1"))
        given = manifest.get("assignments")
        if not isinstance(given, list):
            raise DatasetError("В manifest нет распределения примеров.")
        # Проверяем всё разделение, чтобы связанные примеры не попали в разные выборки.
        canonical = lambda rows: sorted((r["case_id"], r["group_id"], r["split"]) for r in rows)
        try:
            matches = canonical(given) == canonical(expected["assignments"])
        except (KeyError, TypeError) as error:
            raise DatasetError("Некорректный manifest.") from error
        if not matches:
            raise DatasetError(
                "Manifest не соответствует текущим записям и семьям; сформируйте разделение заново."
            )
        selected = {r["case_id"] for r in given if r["split"] == split}
        scope = "family_partition_" + split
    counts = _empty_counts()
    per_rule = {rule: _empty_counts() for rule in sorted(RULES)}
    cases, excluded = [], dict(unapproved=0, no_assessed_rules=0, outside_partition=0)
    known_cases, strict_matches, unknown_expected, unknown_abstained = 0, 0, 0, 0
    for record in records:
        if record["case_id"] not in selected:
            excluded["outside_partition"] += 1
            continue
        if not validate_review(record):
            excluded["unapproved"] += 1
            continue
        review = record["review"]
        assessed = sorted(set(review.get("assessed_rules") or []))
        if not assessed:
            excluded["no_assessed_rules"] += 1
            continue
        states, visible, flags = predictions(record)
        truth = set(review["labels"]) if review["verdict"] in {"problem", "potential"} else set()
        details, all_correct = [], True
        for rule in assessed:
            state = states.get(rule, "abstain")
            if review["verdict"] == "insufficient_data":
                unknown_expected += 1
                unknown_abstained += state == "abstain"
                details.append(dict(rule_id=rule, expected="unknown", predicted=state))
                continue
            positive = rule in truth
            for counter in (counts, per_rule[rule]):
                counter["decisions"] += 1
                if state == "abstain":
                    counter["abstained"] += 1
                    if positive:
                        counter["fn"] += 1
                elif state == "flag":
                    counter["tp" if positive else "fp"] += 1
                else:
                    counter["fn" if positive else "tn"] += 1
            correct = state != "abstain" and (state == "flag") == positive
            all_correct = all_correct and correct
            details.append(
                dict(rule_id=rule, expected="flag" if positive else "clear", predicted=state)
            )
        if review["verdict"] != "insufficient_data":
            known_cases += 1
            strict_matches += all_correct
        cases.append(
            dict(
                case_id=record["case_id"],
                family_id=review["family_id"],
                verdict=review["verdict"],
                shown_warning_rules=visible,
                all_warning_rules=flags,
                assessments=details,
            )
        )
    return dict(
        schema_version=1,
        policy=POLICY,
        scope=scope,
        status="evaluated" if known_cases or unknown_expected else "awaiting_review",
        input_records=len(records),
        scored_cases=known_cases,
        excluded=excluded,
        micro=_scores(counts),
        per_rule={r: _scores(c) for r, c in per_rule.items()},
        strict_case_match=_ratio(strict_matches, known_cases),
        unknown_expectations=dict(
            decisions=unknown_expected,
            abstained=unknown_abstained,
            rate=_ratio(unknown_abstained, unknown_expected),
        ),
        cases=cases,
        limitations=[
            "Метрики относятся к выбранным проверяющим правилам assessed_rules, а не ко всем возможным причинам медленного SQL.",
            "problem и potential оцениваются как замечание; оценка не доказывает фактическое замедление и не различает тяжесть.",
            "Неотмеченные правила не считаются отрицательными примерами. Pending/rejected и записи без assessed_rules исключаются.",
            "Отказ от вывода уменьшает coverage и strict_accuracy; отказ на положительном примере входит в FN.",
            "Учитываются все краткие замечания; первые три показаны отдельно. Качество текста пояснений оценивается отдельно.",
            "Семьи и ручная разметка требуют независимой проверки. Разделение по семьям само по себе не делает набор независимым внешним бенчмарком.",
        ],
    )


def format_evaluation(result):
    if result["status"] == "awaiting_review":
        return (
            "Качество пока не измерено.\nНет одобренных примеров с выбранными проверенными правилами.\n"
            f"Всего записей: {result['input_records']}. Сначала заполните разметку и «Проверено»."
        )
    metric = lambda value: "нет данных" if value is None else f"{100 * value:.1f}%"
    return "\n".join(
        [
            f"Оценено примеров: {result['scored_cases']}",
            "Полное совпадение по проверенным правилам: " + metric(result["strict_case_match"]),
            "Precision: " + metric(result["micro"]["precision"]),
            "Recall: " + metric(result["micro"]["recall"]),
            "F1: " + metric(result["micro"]["f1"]),
            "Доля выводов без отказа (coverage): " + metric(result["micro"]["coverage"]),
            "Отказ при ожидаемой нехватке данных: "
            + metric(result["unknown_expectations"]["rate"]),
            "Оценка относится к замечаниям, а не к доказанным причинам задержки.",
            "Режим: " + result["scope"],
        ]
    )
