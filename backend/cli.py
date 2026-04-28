#!/usr/bin/env python3
"""
PSX CLI — standalone signal runner (no server required).
Run: python cli.py [--mock] [--interval N] [--symbol ENGRO,HBL]

Press Ctrl+C to exit.
"""

import argparse
import asyncio
import os
import sys

# Ensure package is importable
sys.path.insert(0, os.path.dirname(__file__))

from rich.console import Console
from rich.table import Table
from rich import box
from rich.live import Live

from app.scraper.psx_scraper import PSXScraper
from app.strategy.signal_engine import SignalEngine

console = Console()

SIGNAL_STYLE = {
    "BUY":        "[bold green]▲ BUY[/]",
    "SELL":       "[bold red]▼ SELL[/]",
    "HOLD":       "[dim]● HOLD[/]",
    "FORCE_SELL": "[bold bright_red]✖ FORCE SELL[/]",
}


def make_table(signals: list[dict]) -> Table:
    table = Table(
        title="[bold cyan]PSX Smart Signal System[/]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Symbol",   style="bold", width=10)
    table.add_column("Sector",   width=16)
    table.add_column("Price",    justify="right", width=10)
    table.add_column("Chg%",     justify="right", width=8)
    table.add_column("Volume",   justify="right", width=12)
    table.add_column("RSI",      justify="right", width=6)
    table.add_column("Signal",   justify="center", width=14)
    table.add_column("Sources",  width=30)

    for s in sorted(signals, key=lambda x: x["symbol"]):
        chg_pct = s.get("change_pct", 0)
        chg_str = f"[green]+{chg_pct:.2f}%[/]" if chg_pct >= 0 else f"[red]{chg_pct:.2f}%[/]"
        rsi_val = s.get("rsi")
        rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "—"
        signal  = s.get("signal", "HOLD")
        sig_str = SIGNAL_STYLE.get(signal, signal)
        changed = " [yellow]⚡[/]" if s.get("signal_changed") else ""

        table.add_row(
            s["symbol"] + changed,
            s.get("sector", "—"),
            f"{s.get('current', 0):.2f}",
            chg_str,
            f"{s.get('volume', 0):,}",
            rsi_str,
            sig_str,
            ", ".join(s.get("signal_sources", [])) or "—",
        )
    return table


async def run(args):
    scraper = PSXScraper()
    engine  = SignalEngine()

    if args.mock:
        scraper.enable_mock()
        console.print("[yellow]⚠ Running in MOCK mode[/]")

    filter_syms = set(s.upper() for s in args.symbol.split(",")) if args.symbol else set()

    console.print(f"[cyan]Polling every {args.interval}s — Ctrl+C to stop[/]\n")

    with Live(console=console, refresh_per_second=1) as live:
        while True:
            try:
                stocks  = await scraper.fetch()
                signals = engine.process(stocks)

                if filter_syms:
                    signals = [s for s in signals if s["symbol"] in filter_syms]

                live.update(make_table(signals))

            except KeyboardInterrupt:
                break
            except Exception as exc:
                console.print(f"[red]Error: {exc}[/]")

            try:
                await asyncio.sleep(args.interval)
            except asyncio.CancelledError:
                break

    await scraper.close()
    console.print("\n[cyan]Goodbye.[/]")


def main():
    parser = argparse.ArgumentParser(description="PSX Trading Signal CLI")
    parser.add_argument("--mock",     action="store_true", help="Use mock data")
    parser.add_argument("--interval", type=int, default=15, help="Poll interval in seconds")
    parser.add_argument("--symbol",   type=str, default="",  help="Comma-separated symbols to filter")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
