"""Zbieranie danych: przechodzi macierz zapytan i zapisuje surowy wynik.

Zasada: ten modul NICZEGO nie interpretuje. Zapisuje to, co powiedzial serwer,
w formie append-only. Cala analiza dzieje sie pozniej, na dysku. Dzieki temu
zmiana pomyslu na metryke nie wymaga ponownego odpytywania serwera - a
historycznych danych i tak nie da sie odtworzyc, bo oferty znikaja.

Uzycie:
    python -m radar.collect                      # pelny przebieg
    python -m radar.collect --seniorities junior,mid
    python -m radar.collect --groups frontend,backend --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .mcp_client import EldoradoClient
from .queries import DEFAULT_WINDOW, SENIORITIES, TECHNOLOGIES, Query, build_matrix

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOB_ID_RE = re.compile(r"/praca/(\d+)-")


def job_id(url: str) -> str | None:
    """Wyciaga stabilne ID oferty z URL-a (/praca/440489-slug -> '440489')."""
    match = JOB_ID_RE.search(url or "")
    return match.group(1) if match else None


@dataclass
class RunStats:
    queries_ok: int = 0
    queries_failed: int = 0
    jobs_seen: int = 0
    unique_jobs: int = 0
    failures: list[str] = field(default_factory=list)


class SnapshotWriter:
    """Zapisuje jeden przebieg do data/snapshots/<data>/."""

    def __init__(self, root: Path, run_date: date) -> None:
        self.dir = root / "snapshots" / run_date.isoformat()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._measurements = (self.dir / "measurements.jsonl").open("a", encoding="utf-8")
        self._jobs = (self.dir / "jobs.jsonl").open("a", encoding="utf-8")
        self._seen_jobs: set[str] = set()

    def write_measurement(self, query: Query, payload: dict[str, Any]) -> None:
        record = {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "kind": query.kind,
            "label": query.label,
            "group": query.group,
            "seniority": query.seniority,
            "total_results": payload.get("total_results", 0),
            "shown_results": payload.get("shown_results", 0),
            "arguments": query.arguments,
        }
        self._measurements.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._measurements.flush()

    def write_jobs(self, jobs: Iterable[dict[str, Any]]) -> tuple[int, int]:
        """Zapisuje oferty, pomijajac te juz widziane w tym przebiegu."""
        total = new = 0
        for job in jobs:
            total += 1
            identifier = job_id(job.get("url", "")) or job.get("url", "")
            if not identifier or identifier in self._seen_jobs:
                continue
            self._seen_jobs.add(identifier)
            new += 1
            self._jobs.write(json.dumps({"id": identifier, **job}, ensure_ascii=False) + "\n")
        self._jobs.flush()
        return total, new

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        (self.dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def close(self) -> None:
        self._measurements.close()
        self._jobs.close()

    @property
    def unique_jobs(self) -> int:
        return len(self._seen_jobs)


def run(
    matrix: list[Query],
    *,
    data_dir: Path = DATA_DIR,
    window: str = DEFAULT_WINDOW,
    interval: float = 1.2,
    verbose: bool = True,
) -> RunStats:
    stats = RunStats()
    writer = SnapshotWriter(data_dir, date.today())
    started = datetime.now(timezone.utc)

    try:
        with EldoradoClient(min_interval=interval) as client:
            if verbose:
                name = client.server_info.get("name", "?")
                print(f"Polaczono z MCP: {name} | zapytan do wykonania: {len(matrix)}")

            for index, query in enumerate(matrix, start=1):
                try:
                    payload = client.search_jobs(**query.arguments)
                except Exception as exc:  # noqa: BLE001 - jedna zla komorka nie psuje przebiegu
                    stats.queries_failed += 1
                    stats.failures.append(f"{query.kind}/{query.label}/{query.seniority}: {exc}")
                    if verbose:
                        print(f"  [{index}/{len(matrix)}] BLAD {query.label}: {exc}", file=sys.stderr)
                    continue

                writer.write_measurement(query, payload)
                seen, _ = writer.write_jobs(payload.get("jobs", []))
                stats.queries_ok += 1
                stats.jobs_seen += seen

                if verbose:
                    total = payload.get("total_results", 0)
                    level = query.seniority or "-"
                    print(f"  [{index}/{len(matrix)}] {query.label:<18} {level:<8} {total:>6}")
    finally:
        stats.unique_jobs = writer.unique_jobs
        writer.write_manifest(
            {
                "started_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "window": window,
                "queries_total": len(matrix),
                "queries_ok": stats.queries_ok,
                "queries_failed": stats.queries_failed,
                "unique_jobs": stats.unique_jobs,
                "failures": stats.failures,
            }
        )
        writer.close()

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zbiera snapshot rynku pracy IT.")
    parser.add_argument("--window", default=DEFAULT_WINDOW, help="okno czasowe: 24h/3d/7d/14d/30d")
    parser.add_argument("--seniorities", default=",".join(SENIORITIES))
    parser.add_argument("--groups", default="", help="ogranicz do grup technologii, np. frontend,backend")
    parser.add_argument("--interval", type=float, default=1.2, help="min. odstep miedzy zapytaniami [s]")
    parser.add_argument("--dry-run", action="store_true", help="tylko pokaz, co byloby odpytane")
    args = parser.parse_args(argv)

    seniorities = [s.strip() for s in args.seniorities.split(",") if s.strip()]
    technologies = TECHNOLOGIES
    if args.groups:
        wanted = {g.strip() for g in args.groups.split(",") if g.strip()}
        technologies = {k: v for k, v in TECHNOLOGIES.items() if k in wanted}
        if not technologies:
            parser.error(f"nieznane grupy: {sorted(wanted)}; dostepne: {sorted(TECHNOLOGIES)}")

    matrix = build_matrix(window=args.window, seniorities=seniorities, technologies=technologies)

    if args.dry_run:
        print(f"{len(matrix)} zapytan, szacowany czas: {len(matrix) * args.interval / 60:.1f} min")
        for query in matrix[:10]:
            print(f"  {query.kind:<10} {query.label:<18} {query.seniority or '-'}")
        print("  ...")
        return 0

    stats = run(matrix, window=args.window, interval=args.interval)
    print(
        f"\nGotowe: {stats.queries_ok} ok, {stats.queries_failed} bledow, "
        f"{stats.unique_jobs} unikalnych ofert."
    )
    return 1 if stats.queries_failed > len(matrix) // 4 else 0


if __name__ == "__main__":
    raise SystemExit(main())
