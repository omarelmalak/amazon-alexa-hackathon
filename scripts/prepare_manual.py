#!/usr/bin/env python3
"""CLI: slice an IKEA PDF into diagrams/ + scaffold manifest.json."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infra.pdf_manual import prepare_product_from_pdf  # noqa: E402

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def main(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    product_id: str = typer.Option(..., "--product-id", help="e.g. kyrre"),
    display_name: str = typer.Option(..., "--display-name", help="e.g. KYRRE"),
    data_root: Path = typer.Option(
        ROOT / "data",
        "--data-root",
        help="Root folder for product packages",
    ),
    dpi: int = typer.Option(200, "--dpi"),
    no_trim: bool = typer.Option(False, "--no-trim", help="Skip whitespace crop"),
) -> None:
    product_dir = data_root / product_id.lower()
    manifest = prepare_product_from_pdf(
        pdf,
        product_dir,
        product_id=product_id.lower(),
        display_name=display_name,
        dpi=dpi,
        trim=not no_trim,
    )
    typer.echo(f"Wrote {product_dir / 'manifest.json'}")
    typer.echo(f"  BOM items: {len(manifest.get('bom', []))}")
    typer.echo(f"  Steps:     {len(manifest.get('steps', []))}")
    for step in manifest.get("steps", []):
        hw = ", ".join(
            f"{h['partId']}×{h['qty']}" for h in step.get("requiredHardware", [])
        )
        typer.echo(f"    step {step['number']}: {step.get('title')} [{hw}]")


if __name__ == "__main__":
    app()
