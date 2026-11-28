from backend.database import settings_collection

MODULES = ("dashboard", "tasks", "documents", "schedules", "meetings", "comms", "approvals", "employees", "reports", "assistant", "settings")
DEFAULT_PERMISSIONS = {
    "admin": list(MODULES),
    "manager": [module for module in MODULES if module != "settings"],
    "employee": [module for module in MODULES if module not in {"employees", "reports", "settings"}],
}


def modules_for(role: str) -> list[str]:
    if role not in DEFAULT_PERMISSIONS:
        return []
    if role == "admin":
        return list(MODULES)
    permissions = system_config().get("permissions") or {}
    configured = permissions.get(role, DEFAULT_PERMISSIONS[role])
    ceiling = set(MODULES) - {"settings"}
    return [module for module in MODULES if module in configured and module in ceiling]


def system_config() -> dict:
    return settings_collection.find_one({"_id": "global_config"}, {"_id": 0}) or {}
