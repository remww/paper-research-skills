#!/usr/bin/env python3
"""Run MinerU on one PDF without imposing a research project layout."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


TOOL_VERSION = "1.0.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_mineru(explicit: Path | None) -> str:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not candidate.is_file():
            raise ValueError(f"MinerU executable does not exist: {candidate}")
        return str(candidate)

    names = ("mineru.exe", "mineru") if sys.platform == "win32" else ("mineru",)
    for name in names:
        candidate = Path(sys.executable).resolve().parent / name
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("mineru")
    if found:
        return found
    raise ValueError("MinerU is not installed in the active Python environment or PATH")


def validate_pdf(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"PDF does not exist: {path}")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError(f"file does not have a PDF header: {path}")
    return path


def existing_markdown(output_dir: Path, stem: str) -> list[Path]:
    paper_dir = output_dir / stem
    return sorted(p.resolve() for p in paper_dir.rglob("*.md")) if paper_dir.is_dir() else []


def result_for_existing(pdf: Path, output_dir: Path, markdown: list[Path]) -> dict:
    return {
        "tool": "mineru-pdf",
        "tool_version": TOOL_VERSION,
        "status": "ok",
        "reused": True,
        "source": {
            "path": str(pdf),
            "bytes": pdf.stat().st_size,
            "sha256": sha256_file(pdf),
        },
        "output_dir": str(output_dir),
        "markdown": [str(path) for path in markdown],
        "image_count": sum(
            1 for path in markdown for image in (path.parent / "images").glob("*") if image.is_file()
        ),
    }


def convert(
    pdf: Path,
    output_dir: Path,
    mineru_bin: Path | None,
    backend: str,
    effort: str,
    image_analysis: bool,
    timeout: int,
    force: bool,
) -> dict:
    pdf = validate_pdf(pdf)
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prior = existing_markdown(output_dir, pdf.stem)
    if prior and not force:
        return result_for_existing(pdf, output_dir, prior)

    executable = find_mineru(mineru_bin)
    command = [
        executable,
        "-p", str(pdf),
        "-o", str(output_dir),
        "-b", backend,
        "--effort", effort,
        "--image-analysis", "true" if image_analysis else "false",
    ]
    started = dt.datetime.now(dt.timezone.utc)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "tool": "mineru-pdf",
            "tool_version": TOOL_VERSION,
            "status": "failed",
            "reason": f"timed out after {timeout}s",
            "command": command,
            "stdout_tail": (error.stdout or "").splitlines()[-10:] if isinstance(error.stdout, str) else [],
            "stderr_tail": (error.stderr or "").splitlines()[-10:] if isinstance(error.stderr, str) else [],
        }

    markdown = existing_markdown(output_dir, pdf.stem)
    if completed.returncode != 0 or not markdown:
        return {
            "tool": "mineru-pdf",
            "tool_version": TOOL_VERSION,
            "status": "failed",
            "reason": f"MinerU exited {completed.returncode}" if completed.returncode else "no Markdown produced",
            "command": command,
            "stdout_tail": completed.stdout.splitlines()[-10:],
            "stderr_tail": completed.stderr.splitlines()[-10:],
        }

    result = result_for_existing(pdf, output_dir, markdown)
    result.update({
        "reused": False,
        "converted_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": (dt.datetime.now(dt.timezone.utc) - started).total_seconds(),
        "backend": backend,
        "effort": effort,
        "image_analysis": image_analysis,
        "command": command,
    })
    return result


def write_record(result: dict, path: Path) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert one PDF with MinerU and leave MinerU's output layout unchanged."
    )
    parser.add_argument("pdf", type=Path, help="input PDF")
    parser.add_argument("--output-dir", type=Path, required=True, help="destination passed directly to MinerU")
    parser.add_argument("--mineru-bin", type=Path, help="explicit MinerU executable")
    parser.add_argument("--backend", default="hybrid-engine", help="MinerU backend")
    parser.add_argument("--effort", default="high", help="MinerU effort level")
    parser.add_argument(
        "--image-analysis",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="enable or disable MinerU image analysis",
    )
    parser.add_argument("--timeout", type=int, default=1800, help="timeout in seconds")
    parser.add_argument("--force", action="store_true", help="run again when Markdown already exists")
    parser.add_argument("--record", type=Path, help="optional JSON record path; nothing is recorded by default")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = convert(
            args.pdf,
            args.output_dir,
            args.mineru_bin,
            args.backend,
            args.effort,
            args.image_analysis,
            args.timeout,
            args.force,
        )
        if args.record:
            write_record(result, args.record)
            result["record_path"] = str(args.record.expanduser().resolve())
    except Exception as error:  # Keep command failures concise and machine-readable.
        print(json.dumps({
            "tool": "mineru-pdf",
            "tool_version": TOOL_VERSION,
            "status": "failed",
            "reason": f"{type(error).__name__}: {error}",
        }), file=sys.stderr)
        return 2

    stream = sys.stdout if result["status"] == "ok" else sys.stderr
    print(json.dumps(result, indent=2, ensure_ascii=False), file=stream)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
