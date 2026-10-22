"""Contract labels and equal-budget instances; no code import or trade submission."""
from copy import deepcopy
import re


def contract_codes(params):
    values = [params.get("code")] if isinstance(params.get("code"), str) else params.get("codes", [])
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values if isinstance(value, str)
        and re.fullmatch(r"[A-Z][A-Z0-9]*\.[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)?", value)))


def contract_plan(plan, strategyid, selected, catalog):
    result = deepcopy(plan)
    rows = result["strategies"]
    template = next((row for row in rows if row["id"] == strategyid), None)
    if template is None:
        raise ValueError("策略实例不存在，请刷新计划")
    if not isinstance(template.get("params", {}).get("code"), str):
        raise ValueError("快捷管理适用于单个code参数；多合约策略请编辑原有构造参数")
    if not isinstance(selected, list) or any(not isinstance(code, str) for code in selected):
        raise ValueError("请选择合约列表")
    selected = list(dict.fromkeys(selected))
    group = template.get("contract_group") or template["id"]
    members = [row for row in rows if (row.get("contract_group") or row["id"]) == group]
    existing = {row["params"]["code"]: row for row in members}
    if len(existing) != len(members):
        raise ValueError("同组合约重复，请先分别编辑实例")
    known = {row["code"]: row for row in catalog}
    reference = known.get(template["params"]["code"])
    for code in selected:
        if code in existing:
            continue
        item = known.get(code)
        if not item or not reference or (item["exchange"], item["product"]) != (reference["exchange"], reference["product"]):
            raise ValueError("快捷添加请选择同品种的具体分月合约；跨品种须先配置兼容策略")
    budget_fields = ("margin_fraction", "risk_fraction")
    if len(selected) > 1 and any(key not in row["params"] for row in members for key in budget_fields):
        raise ValueError("策略未提供可均分的保证金和风险参数，请分别配置实例预算")
    if any((row.get("library_id"), row.get("class_name"), row.get("trader"), row["params"].get("capital_limit")) !=
           (template.get("library_id"), template.get("class_name"), template.get("trader"), template["params"].get("capital_limit")) for row in members):
        raise ValueError("同组合约的策略、通道或参考资金不同，请先统一配置")
    totals = {key: sum(row["params"].get(key, 0) for row in members) for key in budget_fields}
    if any(value > 1 for value in totals.values()):
        raise ValueError("同组预算比例合计超过100%，请先核对各实例参数")
    identifiers = {row["id"] for row in rows}
    replacements = []
    for code in selected:
        row = deepcopy(existing.get(code, template))
        if code not in existing:
            stem = group[:15] + "_" + re.sub(r"[^A-Za-z0-9_]", "_", code)[:19]
            candidate, index = stem, 2
            while candidate in identifiers:
                candidate = stem + "_" + str(index); index += 1
            row["id"] = candidate
            identifiers.add(candidate)
            row["name"] = template.get("name", group).split(" · ")[0] + " · " + code
        row["params"]["code"] = code
        row["contracts"] = [code]
        row["contract_group"] = group
        for key in budget_fields:
            if key in row["params"]:
                row["params"][key] = totals[key]/len(selected)
        replacements.append(row)
    if len(rows)-len(members)+len(replacements) > 100:
        raise ValueError("组合最多100个实例")
    first = min(rows.index(row) for row in members)
    result["strategies"] = [row for row in rows if row not in members]
    result["strategies"][first:first] = replacements
    return {"plan": result, "added": [code for code in selected if code not in existing],
            "removed": [code for code in existing if code not in selected],
            "instances": [{"id": row["id"], "code": row["params"]["code"],
                "capital_limit": row["params"].get("capital_limit"),
                **{key: row["params"].get(key) for key in budget_fields}} for row in replacements]}
