"""Podsumowanie przebiegu do zakladki Summary w GitHub Actions.

Wypisuje na stdout markdown, ktory workflow dopisuje do $GITHUB_STEP_SUMMARY.
Chodzi o to, zeby po cotygodniowym przebiegu nie trzeba bylo otwierac logow,
zeby sprawdzic, czy cos sie nie posypalo.

Uruchamiany z `if: always()`, wiec musi zniesc kazdy stan: brak manifestu
(przebieg padl przed zapisem), manifest niepelny, manifest uszkodzony.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    day = datetime.now(timezone.utc).date().isoformat()
    manifest_path = ROOT / "data" / "snapshots" / day / "manifest.json"

    print("### Przebieg radaru\n")

    if not manifest_path.exists():
        print(f"Brak `{manifest_path.relative_to(ROOT)}` — przebieg nie doszedł do zapisu.")
        print("\nSzczegóły w logach kroku „Zbierz snapshot”.")
        return 0

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Manifest jest nieczytelny: `{exc}`")
        return 0

    ok = manifest.get("queries_ok", 0)
    failed = manifest.get("queries_failed", 0)
    unique = manifest.get("unique_jobs", 0)
    failures = manifest.get("failures") or []

    print(f"- Zapytania: **{ok}** OK, **{failed}** błędów")
    print(f"- Unikalnych ofert w próbie: **{unique}**")

    # Korpus nie jest commitowany, wiec to jedyne miejsce, gdzie widac
    # jego rozmiar - a nagly spadek znaczy, ze cos sie zmienilo po stronie
    # serwera i warto na to spojrzec.
    if unique and unique < 1000:
        print(f"- ⚠️ Próba mniejsza niż zwykle ({unique}); wcześniejsze przebiegi dawały ~2500.")

    if failures:
        print(f"\n<details><summary>Nieudane zapytania ({len(failures)})</summary>\n")
        for failure in failures[:25]:
            print(f"- `{failure}`")
        if len(failures) > 25:
            print(f"- …i {len(failures) - 25} więcej")
        print("\n</details>")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
