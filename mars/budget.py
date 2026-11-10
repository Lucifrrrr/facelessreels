"""Zentrale Budgetkontrolle für alle Projekte (Mars Colony, später Zombie Apocalypse).

Jede kostenpflichtige Aktion muss vorher authorize() aufrufen und danach
record() eintragen. Ist das Limit erreicht, wird die Aktion verweigert.
"""

from __future__ import annotations

from datetime import datetime

from .common import ROOT, read_json, write_json

LEDGER_PATH = ROOT / "state" / "budget_ledger.json"


class BudgetExceeded(RuntimeError):
    pass


def _ledger() -> dict:
    return read_json(LEDGER_PATH, {"entries": []})


def month_key(when: datetime | None = None) -> str:
    return (when or datetime.now()).strftime("%Y-%m")


def spent(cfg: dict, service: str | None = None, month: str | None = None) -> float:
    month = month or month_key()
    total = sum(e["eur"] for e in _ledger()["entries"]
                if e["month"] == month and (service is None or e["service"] == service))
    if service is None:
        total += sum(cfg["budget"]["fixed_monthly_costs_eur"].values())
    return round(total, 4)


def authorize(cfg: dict, service: str, eur: float, description: str) -> None:
    b = cfg["budget"]
    total_after = spent(cfg) + eur
    if total_after > b["monthly_total_eur"]:
        raise BudgetExceeded(f"Gesamtbudget überschritten: {total_after:.2f} € > {b['monthly_total_eur']} € ({description})")
    limit_key = f"monthly_{service}_eur"
    if limit_key in b:
        service_after = spent(cfg, service) + eur
        if service_after > b[limit_key]:
            raise BudgetExceeded(f"{service}-Budget überschritten: {service_after:.2f} € > {b[limit_key]} € ({description})")


def record(cfg: dict, service: str, eur: float, description: str, project: str, ref: str | None = None) -> None:
    ledger = _ledger()
    ledger["entries"].append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "month": month_key(),
        "project": project,
        "service": service,
        "eur": round(eur, 4),
        "description": description,
        "ref": ref,
    })
    write_json(LEDGER_PATH, ledger)


def report(cfg: dict) -> str:
    b = cfg["budget"]
    return (f"Budget {month_key()}: gesamt {spent(cfg):.2f} / {b['monthly_total_eur']} €, "
            f"Higgsfield {spent(cfg, 'higgsfield'):.2f} / {b['monthly_higgsfield_eur']} €")
