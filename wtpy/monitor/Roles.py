"""Monitor role capabilities; existing WT names remain valid."""

READ_ROLES = frozenset(("guest", "viewer", "operator", "admin", "superman", "trader", "researcher", "risker"))
OPERATE_ROLES = frozenset(("operator", "admin", "superman", "trader"))
RESEARCH_ROLES = frozenset(("admin", "superman", "researcher"))
ADMIN_ROLES = frozenset(("admin", "superman"))


def can_read(user):
    return user.get("role") in READ_ROLES


def can_operate(user):
    return user.get("role") in OPERATE_ROLES


def can_admin(user):
    return user.get("role") in ADMIN_ROLES


def group_allowed(user, groupid):
    products = user.get("products", [])
    if isinstance(products, str):
        products = [item.strip() for item in products.split(",") if item.strip()]
    return not products or groupid in products


def can_research_read(user):
    return user.get("role") in RESEARCH_ROLES or user.get("role") == "guest"
