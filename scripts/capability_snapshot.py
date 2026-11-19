"""Persist a capability snapshot so "what can I generate with?" is instant.

The live tool registry stays the source of truth, but probing it costs a
full dependency/health sweep (HyperFrames doctor alone can take 20s). This
script runs that sweep once and writes a cached rollup:

    workspace/capabilities/capability_snapshot.md    human/agent-readable
    workspace/capabilities/capability_snapshot.json  machine-readable + env fingerprint

Usage:
    python scripts/capability_snapshot.py            # probe + write snapshot
    python scripts/capability_snapshot.py --show     # print saved markdown (no probe)
    python scripts/capability_snapshot.py --check    # freshness report (no probe)

Refresh after editing .env, installing a local server, or when --check
reports stale. Production preflight still uses the live registry.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT_DIR = REPO_ROOT / "workspace" / "capabilities"
SNAPSHOT_JSON = "capability_snapshot.json"
SNAPSHOT_MD = "capability_snapshot.md"
FRESH_DAYS = 7

# Rendered in detail ahead of everything else; the rest collapse into a table.
_PRIMARY_CAPS = ("image_generation", "video_generation")

_CAP_TITLES = {
    "image_generation": "Image generation",
    "video_generation": "Video generation",
    "tts": "Text-to-speech",
    "music_generation": "Music generation",
}

# Env vars named in install_instructions. Suffix-anchored so uppercase prose
# ("API", "GPU", "REST") does not get mistaken for a variable.
_ENV_TOKEN_RE = re.compile(
    r"\b[A-Z][A-Z0-9_]*(?:"
    r"_KEY|_TOKEN|_SECRET|_SECRETKEY|_ACCESSKEY|_CREDENTIALS|"
    r"_ENDPOINT|_URL|_DEPLOYMENT|_MODEL|_PROJECT|_LOCATION"
    r")\b"
)


def _now() -> datetime:
    return datetime.now().astimezone()


def _reconfigure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _cell(value: Any) -> str:
    text = str(value or "").replace("|", "/").replace("\n", " ").strip()
    return text or "-"


def _cap_title(cap: str) -> str:
    return _CAP_TITLES.get(cap, cap.replace("_", " ").capitalize())


def _env_vars(info: dict[str, Any]) -> list[str]:
    found: set[str] = set()
    for dep in info.get("dependencies") or []:
        if isinstance(dep, str) and dep.startswith("env:"):
            name = dep[4:].strip()
            if name:
                found.add(name)
    offer = info.get("setup_offer") or {}
    if isinstance(offer, dict) and offer.get("env_var"):
        found.add(str(offer["env_var"]))
    instructions = info.get("install_instructions") or ""
    found.update(_ENV_TOKEN_RE.findall(instructions))
    return sorted(found)


def _models(info: dict[str, Any]) -> list[str]:
    matrix = info.get("provider_matrix") or {}
    return sorted(matrix.keys()) if isinstance(matrix, dict) else []


def _tool_record(info: dict[str, Any]) -> dict[str, Any]:
    best_for = list(info.get("best_for") or [])
    return {
        "name": info.get("name"),
        "provider": info.get("provider"),
        "runtime": info.get("runtime"),
        "best_for": best_for,
        "models": _models(info),
        "env": _env_vars(info),
        "install": _first_line(info.get("install_instructions") or ""),
        "deprecated": any(str(item).startswith("DEPRECATED") for item in best_for),
    }


def build_snapshot() -> dict[str, Any]:
    from tools.tool_registry import registry

    registry.discover()
    envelope = registry.support_envelope()

    caps: dict[str, Any] = {}
    env_keys: set[str] = set()
    for info in envelope.values():
        if info.get("provider") == "selector":
            continue
        cap = info.get("capability") or "other"
        bucket = caps.setdefault(
            cap, {"configured": 0, "total": 0, "available": [], "unavailable": []}
        )
        record = _tool_record(info)
        env_keys.update(record["env"])
        bucket["total"] += 1
        if info.get("status") == "available":
            bucket["configured"] += 1
            bucket["available"].append(record)
        else:
            bucket["unavailable"].append(record)

    for bucket in caps.values():
        for key in ("available", "unavailable"):
            bucket[key].sort(key=lambda rec: (rec["provider"] or "", rec["name"] or ""))

    video_compose = envelope.get("video_compose") or {}
    runtimes = {
        name: bool(ok) for name, ok in (video_compose.get("render_engines") or {}).items()
    }
    hyperframes = envelope.get("hyperframes_compose") or {}
    warnings = [
        f"hyperframes: {reason}"
        for reason in ((hyperframes.get("hyperframes_runtime") or {}).get("reasons") or [])
    ]

    return {
        "schema_version": 1,
        "generated_at": _now().isoformat(timespec="seconds"),
        "fresh_for_days": FRESH_DAYS,
        "composition_runtimes": runtimes,
        "runtime_warnings": warnings,
        "capabilities": dict(sorted(caps.items())),
        "env_fingerprint": {
            key: ("set" if os.environ.get(key) else "unset") for key in sorted(env_keys)
        },
    }


def _render_cap_section(
    cap: str,
    data: dict[str, Any],
    lines: list[str],
    fingerprint: dict[str, str],
) -> None:
    lines.append(f"## {_cap_title(cap)} — {data['configured']}/{data['total']} configured")
    lines.append("")

    if data["available"]:
        lines.append("| Tool | Provider | Runtime | Models | Best for |")
        lines.append("|---|---|---|---|---|")
        for rec in data["available"]:
            models = ", ".join(rec["models"][:4]) or "-"
            best = "; ".join(rec["best_for"][:2])
            lines.append(
                f"| `{rec['name']}` | {rec['provider']} | {rec['runtime']} "
                f"| {_cell(models)} | {_cell(best)} |"
            )
    else:
        lines.append("Nothing configured.")

    if data["unavailable"]:
        groups: dict[tuple[str, ...], list[str]] = {}
        local: list[dict[str, Any]] = []
        other: list[dict[str, Any]] = []
        deprecated: list[str] = []
        for rec in data["unavailable"]:
            if rec["deprecated"]:
                deprecated.append(rec["name"])
            elif rec["env"]:
                groups.setdefault(tuple(rec["env"]), []).append(rec["name"])
            elif rec["runtime"] in ("local", "local_gpu", "hybrid"):
                local.append(rec)
            else:
                other.append(rec)

        lines.append("")
        lines.append("Unlocks:")
        for envs, names in sorted(groups.items(), key=lambda item: (len(item[0]), item[0])):
            joined = " + ".join(
                f"**{var}**" + (" (set)" if fingerprint.get(var) == "set" else "")
                for var in envs
            )
            lines.append(f"- {joined} → {', '.join(names)}")
        for rec in local:
            detail = _cell((rec["install"] or "no install hint").rstrip(":"))
            lines.append(f"- local: `{rec['name']}` ({rec['runtime']}) — {detail}")
        for rec in other:
            lines.append(f"- `{rec['name']}` — {_cell(rec['install'] or 'no install hint')}")
        if deprecated:
            lines.append(f"- deprecated (ignored): {', '.join(deprecated)}")
    lines.append("")


def _render_md(snapshot: dict[str, Any]) -> str:
    generated = datetime.fromisoformat(snapshot["generated_at"])
    until = generated + timedelta(days=snapshot.get("fresh_for_days", FRESH_DAYS))
    lines = [
        "# Capability snapshot",
        "",
        f"Generated {generated.strftime('%Y-%m-%d %H:%M')} · refresh by "
        f"{until.strftime('%Y-%m-%d')} · regenerate with "
        "`python scripts/capability_snapshot.py`",
        "",
        "> Cached rollup of the live tool registry. Re-run after editing `.env`",
        "> or starting/stopping a local server. Production preflight still uses",
        "> `provider_menu_summary()`.",
        "",
        "## Composition runtimes",
        "",
    ]
    for name, ok in (snapshot.get("composition_runtimes") or {}).items():
        lines.append(f"- {name}: {'yes' if ok else 'no'}")
    for warning in snapshot.get("runtime_warnings") or []:
        lines.append(f"- warning: {warning}")
    lines.append("")

    caps = snapshot.get("capabilities") or {}
    fingerprint = snapshot.get("env_fingerprint") or {}
    for cap in _PRIMARY_CAPS:
        if cap in caps:
            _render_cap_section(cap, caps[cap], lines, fingerprint)

    others = [cap for cap in caps if cap not in _PRIMARY_CAPS]
    if others:
        lines.append("## Other capabilities")
        lines.append("")
        lines.append("| Capability | Configured | Total |")
        lines.append("|---|---|---|")
        for cap in others:
            data = caps[cap]
            lines.append(f"| {_cap_title(cap)} (`{cap}`) | {data['configured']} | {data['total']} |")
        lines.append("")

    fingerprint = snapshot.get("env_fingerprint") or {}
    set_vars = [key for key, value in fingerprint.items() if value == "set"]
    unset_vars = [key for key, value in fingerprint.items() if value != "set"]
    lines.append("## Environment fingerprint")
    lines.append("")
    lines.append("- set: " + (", ".join(set_vars) if set_vars else "(none)"))
    lines.append("- unset: " + (", ".join(unset_vars) if unset_vars else "(none)"))
    lines.append("")
    return "\n".join(lines)


def write_snapshot(snapshot: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / SNAPSHOT_JSON
    md_path = out_dir / SNAPSHOT_MD
    with open(json_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(snapshot, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    with open(md_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_render_md(snapshot))
    return json_path, md_path


def check_snapshot(out_dir: Path) -> int:
    json_path = out_dir / SNAPSHOT_JSON
    if not json_path.is_file():
        print(f"missing: {json_path}")
        print("refresh: python scripts/capability_snapshot.py")
        return 1

    with open(json_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)

    from tools.tool_registry import ToolRegistry

    ToolRegistry._load_dotenv()

    generated = datetime.fromisoformat(snapshot["generated_at"])
    age = _now() - generated
    limit = timedelta(days=snapshot.get("fresh_for_days", FRESH_DAYS))
    reasons: list[str] = []
    if age > limit:
        reasons.append(f"older than {limit.days} days (age {age.days}d)")

    changed: list[str] = []
    for key, expected in (snapshot.get("env_fingerprint") or {}).items():
        current = "set" if os.environ.get(key) else "unset"
        if current != expected:
            changed.append(f"{key}: {expected} -> {current}")
    if changed:
        reasons.append("env changed: " + "; ".join(changed))

    print(f"snapshot: {json_path}")
    print(f"generated: {generated.strftime('%Y-%m-%d %H:%M')} (age {age.days}d {age.seconds // 3600}h)")
    if reasons:
        print("status: stale — " + " | ".join(reasons))
        print("refresh: python scripts/capability_snapshot.py")
        return 1
    print("status: fresh")
    return 0


def main(argv: list[str] | None = None) -> int:
    _reconfigure_streams()
    parser = argparse.ArgumentParser(
        description="Cache the tool registry's capability rollup for fast answers."
    )
    parser.add_argument("--show", action="store_true", help="print saved markdown (no probe)")
    parser.add_argument("--check", action="store_true", help="report freshness (no probe)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    if args.show:
        md_path = args.out_dir / SNAPSHOT_MD
        if not md_path.is_file():
            print(f"error: no snapshot at {md_path}", file=sys.stderr)
            print("refresh: python scripts/capability_snapshot.py", file=sys.stderr)
            return 2
        print(md_path.read_text(encoding="utf-8"))
        return 0
    if args.check:
        return check_snapshot(args.out_dir)

    snapshot = build_snapshot()
    json_path, md_path = write_snapshot(snapshot, args.out_dir)
    print(f"snapshot written: {md_path}")
    print(f"                  {json_path}")
    for cap in _PRIMARY_CAPS:
        data = snapshot["capabilities"].get(cap)
        if data:
            print(f"{_cap_title(cap)}: {data['configured']}/{data['total']} configured")
    for warning in snapshot.get("runtime_warnings") or []:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
