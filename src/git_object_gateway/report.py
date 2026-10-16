"""Self-contained benchmark artifact with explicit local-workload boundaries."""

import html
import json
from pathlib import Path


def write_report(result: dict, output: Path) -> None:
    rows = []
    for name, values in result["strategies"].items():
        rows.append(
            f"<tr><th>{html.escape(name)}</th><td>{values['p50_ms']:.1f} ms</td>"
            f"<td>{values['p95_ms']:.1f} ms</td><td>{values['job_file_bytes']:,} B</td>"
            f"<td>{values['http_body_bytes'] if values['http_body_bytes'] is not None else 'not measured'}</td>"
            f"<td>{values['verified_runs']}/{values['runs']}</td></tr>"
        )
    ceiling = max(v["p95_ms"] for v in result["strategies"].values()) or 1
    bars = []
    for i, (name, values) in enumerate(result["strategies"].items()):
        y = i * 54 + 26
        width = 590 * values["p95_ms"] / ceiling
        color = "#82e8ca" if name.startswith("gateway") else "#95a9f6"
        bars.append(
            f'<text x="0" y="{y}" fill="#c3cfe1" font-size="13">{html.escape(name)}</text>'
            f'<rect x="220" y="{y - 15}" width="{width:.2f}" height="23" rx="4" fill="{color}"/>'
            f'<text x="{min(830, 230 + width):.2f}" y="{y + 1}" fill="#f2f5fa" font-size="12">{values["p95_ms"]:.1f}</text>'
        )
    document = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>Git Object Gateway · benchmark</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#eaf0fa;background:#0a1020}*{box-sizing:border-box}body{margin:0;padding:48px 24px}main{max-width:1100px;margin:auto}.eyebrow{color:#82e8ca;letter-spacing:.15em;text-transform:uppercase;font-size:12px;font-weight:700}h1{font-size:clamp(34px,6vw,60px);line-height:1.06;max-width:780px;letter-spacing:-.04em;margin:20px 0}p{color:#aebcd2;line-height:1.65;max-width:850px}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:32px 0}.card,section{background:#111b30;border:1px solid #24324b;border-radius:16px;padding:24px;min-width:0}.card strong{display:block;font-size:28px;color:#82e8ca;margin-bottom:8px}.card span{color:#aebcd2;font-size:13px}section{margin:20px 0}h2{font-size:19px;margin:0 0 20px}svg{width:100%;height:auto}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}td,th{text-align:left;padding:14px 12px;border-bottom:1px solid #24324b}th{color:#dce7fc}thead th{font-size:11px;text-transform:uppercase;color:#9aacc7}code{overflow-wrap:anywhere;color:#cad6ed}details{margin-top:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto;font-size:12px;color:#afc3df}footer{color:#879bb9;font-size:12px;margin-top:32px}@media(max-width:650px){body{padding:30px 16px}.cards{grid-template-columns:1fr}section{padding:18px}}</style>
<main><div class="eyebrow">Open source · MIT · reproducible evidence</div><h1>Read the files.<br>Skip the checkout.</h1><p>A read-only Git gateway inspired by server-side source-control ideas. This report compares complete, verified application snapshots on a deterministic binary-heavy repository.</p>
<div class="cards"><div class="card"><strong>COMMIT</strong><span>One immutable commit across every strategy</span></div><div class="card"><strong>RUNS</strong><span>Verified samples per strategy</span></div><div class="card"><strong>FILES</strong><span>Files selected under app/</span></div></div>
<section><h2>End-to-end p95 latency · milliseconds</h2><svg viewBox="0 0 900 290" role="img" aria-label="Latency comparison">BARS</svg><p>Local loopback HTTP versus file:// Git transport. OS caches are warm; this is not an internet-throughput or enterprise-scale benchmark.</p></section>
<section><h2>Correctness before speed</h2><div class="table"><table><thead><tr><th>Strategy</th><th>p50</th><th>p95</th><th>Job file bytes</th><th>HTTP body bytes</th><th>Verified</th></tr></thead><tbody>ROWS</tbody></table></div><p>Job bytes include the complete output directory and any per-job cache. A warm client uses a shared cache whose cost is disclosed below. Git transport bytes are not measured; HTTP body bytes exclude headers and TLS.</p></section>
<section><h2>What the numbers mean</h2><p>Both shallow and filtered sparse Git are real baselines. The archive baseline uses Git directly on the server filesystem and avoids transport entirely. The gateway is useful when callers need a language-neutral read API or reusable verified object cache. Git remains preferable for editing, history traversal and ordinary development.</p><details><summary>Environment, shared costs and methodology</summary><pre>DETAILS</pre></details></section><footer>Independent project. No affiliation with Anthropic; no Scion protocol compatibility. Generated from measured local samples.</footer></main></html>"""
    document = (
        document.replace("COMMIT", html.escape(result["commit"][:12]))
        .replace("RUNS", str(result["runs"]))
        .replace("FILES", str(result["selected_files"]))
    )
    document = (
        document.replace("BARS", "".join(bars))
        .replace("ROWS", "".join(rows))
        .replace(
            "DETAILS",
            html.escape(
                json.dumps({k: v for k, v in result.items() if k != "strategies"}, indent=2)
            ),
        )
    )
    output.write_text(document, encoding="utf-8")
