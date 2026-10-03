"""Sledzenie aplikacji: co juz widziales, gdzie aplikowales, co z tego wyszlo.

Plik profile/tracker.json - lokalnie, poza repo, jak reszta profilu. To Twoja
historia szukania pracy, wiec zapis jest atomowy: przerwany w polowie (Ctrl+C,
zamkniete okno) nie moze zostawic uszkodzonego pliku i zjesc tygodni notatek.

Statusy ukladaja sie w lejek:

    zapisana -> aplikowalem -> rozmowa -> oferta
                            \\-> odmowa
    (dowolny etap)          --> nie dla mnie

Oferta, przy ktorej cos zrobiles, przestaje sie pokazywac w dopasowaniach -
inaczej co tydzien przegladalbys te same ogloszenia od nowa. Wyjatkiem sa
"zapisane": to lista do decyzji, wiec zostaja widoczne z oznaczeniem.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .profile import PROFILE_DIR

TRACKER_PATH = PROFILE_DIR / "tracker.json"

STATUSES = ["saved", "applied", "interview", "offer", "rejected", "dismissed"]

# Ktore statusy chowaja oferte z listy dopasowan.
HIDDEN_FROM_MATCHES = {"applied", "interview", "offer", "rejected", "dismissed"}

# Status dalej w lejku oznacza, ze aplikacja na pewno poszla - potrzebne do
# statystyk bez trzymania pelnej historii w kazdym liczeniu.
APPLIED_OR_LATER = {"applied", "interview", "offer", "rejected"}
RESPONDED = {"interview", "offer", "rejected"}

# Pola oferty zapamietywane przy zmianie statusu. Ogloszenie znika z agregatora
# po kilku tygodniach, a lejek ma pokazywac je dalej - z wlasnej kopii.
SNAPSHOT_FIELDS = ("title", "company", "url", "salary", "seniority")

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty() -> dict[str, Any]:
    return {"version": 1, "offers": {}, "seen": {}}


def load(path: Path = TRACKER_PATH) -> dict[str, Any]:
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Nie nadpisujemy po cichu pliku, ktorego nie umiemy przeczytac -
        # to moze byc jedyna kopia czyjejs historii aplikacji.
        raise RuntimeError(
            f"Nie moge odczytac {path}: {exc}. Plik zostal nietkniety - "
            "napraw go albo przenies, zanim zapiszesz cos nowego."
        ) from exc
    data.setdefault("offers", {})
    data.setdefault("seen", {})
    return data


def save(data: dict[str, Any], path: Path = TRACKER_PATH) -> None:
    """Zapis atomowy: najpierw plik tymczasowy, potem podmiana jednym ruchem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tracker-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def mark_seen(ids: list[str], path: Path = TRACKER_PATH) -> dict[str, str]:
    """Zapamietuje pierwsze pokazanie ofert. Zwraca {id: kiedy pierwszy raz}."""
    with _lock:
        data = load(path)
        seen = data["seen"]
        stamp = _now()
        changed = False
        for offer_id in ids:
            if offer_id not in seen:
                seen[offer_id] = stamp
                changed = True
        if changed:
            save(data, path)
        return {offer_id: seen[offer_id] for offer_id in ids}


def set_status(
    offer_id: str,
    status: str | None,
    offer: dict[str, Any] | None = None,
    note: str | None = None,
    path: Path = TRACKER_PATH,
) -> dict[str, Any] | None:
    """Ustawia status oferty. None usuwa ja z lejka (cofniecie decyzji)."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"nieznany status: {status!r}; dozwolone: {STATUSES}")

    with _lock:
        data = load(path)
        offers = data["offers"]

        if status is None:
            offers.pop(offer_id, None)
            save(data, path)
            return None

        entry = offers.get(offer_id) or {"history": []}
        for field in SNAPSHOT_FIELDS:
            # Kopia ogloszenia z chwili pierwszej decyzji; pozniejsze wywolania
            # bez danych oferty (np. sama zmiana notatki) jej nie kasuja.
            if offer and offer.get(field) not in (None, ""):
                entry[field] = offer[field]
        if status != entry.get("status"):
            entry["history"].append({"status": status, "at": _now()})
        entry["status"] = status
        if note is not None:
            entry["note"] = note.strip()
        offers[offer_id] = entry
        save(data, path)
        return entry


def set_note(offer_id: str, note: str, path: Path = TRACKER_PATH) -> dict[str, Any]:
    with _lock:
        data = load(path)
        entry = data["offers"].get(offer_id)
        if entry is None:
            raise KeyError(f"oferty {offer_id} nie ma w lejku")
        entry["note"] = note.strip()
        save(data, path)
        return entry


def first_applied(entry: dict[str, Any]) -> str | None:
    """Kiedy pierwszy raz oznaczono aplikacje - stad liczymy "dni bez odpowiedzi"."""
    for step in entry.get("history", []):
        if step.get("status") in APPLIED_OR_LATER:
            return step.get("at")
    return None


def stats(data: dict[str, Any]) -> dict[str, Any]:
    entries = list(data.get("offers", {}).values())
    by_status = {status: 0 for status in STATUSES}
    for entry in entries:
        if entry.get("status") in by_status:
            by_status[entry["status"]] += 1

    applied = sum(1 for e in entries if e.get("status") in APPLIED_OR_LATER)
    responded = sum(1 for e in entries if e.get("status") in RESPONDED)
    interviews = by_status["interview"] + by_status["offer"]

    # Ile aplikacji wisi bez odpowiedzi dluzej niz dwa tygodnie - to te,
    # przy ktorych warto sie przypomniec albo odpuscic.
    today = date.today()
    stale = 0
    for entry in entries:
        if entry.get("status") != "applied":
            continue
        if since := first_applied(entry):
            if (today - datetime.fromisoformat(since).date()).days > 14:
                stale += 1

    return {
        "by_status": by_status,
        "applied": applied,
        "responded": responded,
        "response_rate": round(100 * responded / applied, 1) if applied else None,
        "interviews": interviews,
        "offers": by_status["offer"],
        "stale": stale,
    }
