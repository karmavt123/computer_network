"""Build the generated assets of the report from measured data.

* figures/bench_udp.pdf  - transfer time and datagrams vs. loss rate
* figures/cwnd_trace.pdf - congestion window over time (two loss rates)
* tables/bench.tex       - the benchmark table
* snippets/*.py          - code excerpts cut from the source by name
* generated/facts.tex    - numbers quoted in the text (test count, ...)
* figures/seq_*.pdf      - sequence diagrams from diagrams/*.mmd (needs mmdc)

Run from the project root after ``python tools/bench_udp.py --out report/bench_udp.json``::

    python report/make_figures.py
"""

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "report"
LATEX = REPORT / "latex"
INK, MUTED, GRID = "#14212B", "#5F6B7A", "#E3E8EE"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]   # categorical slots 1-3 (validated palette)

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8.5, "axes.edgecolor": MUTED,
    "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "legend.frameon": False, "pdf.fonttype": 42,
})


def bench_figures(results):
    """Transfer time and datagram breakdown versus loss rate."""
    loss = [r["loss"] * 100 for r in results]
    labels = ["{:g} %".format(x) for x in loss]
    fig, (left, right) = plt.subplots(1, 2, figsize=(6.4, 2.5))

    left.plot(labels, [r["duration"] for r in results], color=SERIES[0], linewidth=2,
              marker="o", markersize=5)
    for x, r in zip(labels, results):
        left.annotate("{:.2f} s".format(r["duration"]), (x, r["duration"]),
                      textcoords="offset points", xytext=(0, 6), ha="center",
                      fontsize=7.5, color=INK)
    left.set_title("Time to send 2 MiB", fontsize=9, color=INK, loc="left")
    left.set_xlabel("simulated loss, each direction")
    left.set_ylabel("seconds (median of 3)")
    left.set_ylim(0, max(r["duration"] for r in results) * 1.2)

    first = [r["chunks"] for r in results]
    resent = [r["retransmissions"] for r in results]
    right.bar(labels, first, color=SERIES[0], width=0.6, label="first transmissions")
    right.bar(labels, resent, bottom=first, color=SERIES[1], width=0.6,
              label="retransmissions", edgecolor="white", linewidth=1)
    for x, f, r in zip(labels, first, resent):
        right.annotate("+{:.0f}".format(r), (x, f + r), textcoords="offset points",
                       xytext=(0, 3), ha="center", fontsize=7.5, color=INK)
    right.set_title("DATA datagrams sent", fontsize=9, color=INK, loc="left")
    right.set_xlabel("simulated loss, each direction")
    right.set_ylim(0, max(f + r for f, r in zip(first, resent)) * 1.15)
    right.legend(loc="upper left", fontsize=7.5)
    fig.tight_layout()
    fig.savefig(LATEX / "figures" / "bench_udp.pdf")
    plt.close(fig)


def cwnd_figure(results):
    """Congestion window over time for two loss rates."""
    fig, ax = plt.subplots(figsize=(6.4, 2.4))
    chosen = [r for r in results if r["loss"] in (0.0, 0.05, 0.1)]
    for color, row in zip(SERIES, chosen):
        trace = row["cwnd_trace"]
        times = [t for t, _ in trace]
        values = [w for _, w in trace]
        ax.step(times, values, where="post", color=color, linewidth=1.4,
                label="loss {:g} %".format(row["loss"] * 100))
        ax.annotate("loss {:g} %".format(row["loss"] * 100), (times[-1], values[-1]),
                    textcoords="offset points", xytext=(4, 0), fontsize=7.5, color=INK,
                    va="center")
    ax.set_xlabel("time since the first DATA datagram (s)")
    ax.set_ylabel("cwnd (chunks)")
    ax.set_ylim(0, 70)
    ax.legend(loc="upper right", fontsize=7.5)
    fig.tight_layout()
    fig.savefig(LATEX / "figures" / "cwnd_trace.pdf")
    plt.close(fig)


def bench_table(results):
    """LaTeX table of the benchmark."""
    rows = []
    for r in results:
        rows.append("{:g}\\,\\% & {:.2f} & {:,.0f} & {:.0f} & {:.0f} & {:.0f} & {:.0f} & {} \\\\".format(
            r["loss"] * 100, r["duration"], r["throughput_kib_s"], r["datagrams_sent"],
            r["retransmissions"], r["fast_retransmits"], r["timeouts"],
            "yes" if r["intact"] else "\\textbf{no}"))
    text = (
        "\\begin{table}[H]\\centering\\small\n"
        "\\caption{UDP file transfer of a 2\\,MiB file (" + str(results[0]["chunks"]) +
        " chunks) on loopback, median of " + str(results[0]["runs"]) + " runs}\\label{tab:bench}\n"
        "\\begin{tabular}{@{}rrrrrrrc@{}}\n\\toprule\n"
        "\\textbf{Loss} & \\textbf{Time (s)} & \\textbf{KiB/s} & \\textbf{Datagrams} & "
        "\\textbf{Resent} & \\textbf{Fast} & \\textbf{Timeouts} & \\textbf{SHA-256 ok} \\\\\n"
        "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n")
    (LATEX / "tables" / "bench.tex").write_text(text, encoding="utf-8")


SNIPPETS = {
    # file, qualified name, output file
    "frame_codec.py": ("chat/protocol.py", ["encode_frame", "FrameBuffer"]),
    "encrypt_chat.py": ("chat/protocol.py", ["chat_aad", "encrypt_chat"]),
    "cubic.py": ("chat/udp_files.py", ["CubicWindow.on_ack", "CubicWindow.on_loss"]),
    "rtt.py": ("chat/udp_files.py", ["RttEstimator"]),
    "send_window.py": ("chat/udp_files.py", ["ReliableUdpFiles._send_window"]),
    "handle_data.py": ("chat/udp_files.py", ["ReliableUdpFiles._handle_data"]),
    "peer_loop.py": ("chat/peer.py", ["PeerServer._loop", "PeerServer.handle",
                                      "_Connection._on_readable"]),
    "fan_out.py": ("chat/peer.py", ["ChatNode._fan_out"]),
    "dispatch_async.py": ("daemon/httpadapter.py", ["HttpAdapter.dispatch_async"]),
    "tracker_register.py": ("chat/tracker.py", ["Tracker.register"]),
    "proxy_policy.py": ("daemon/proxy.py", ["resolve_routing_policy"]),
}


def _find(tree, dotted):
    node = tree
    for part in dotted.split("."):
        node = next(n for n in ast.iter_child_nodes(node)
                    if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                    and n.name == part)
    return node


def snippets():
    """Cut functions/classes out of the source so listings never go stale."""
    out = LATEX / "snippets"
    out.mkdir(exist_ok=True)
    for name, (source, parts) in SNIPPETS.items():
        lines = (ROOT / source).read_text(encoding="utf-8").splitlines()
        tree = ast.parse("\n".join(lines))
        pieces = []
        for dotted in parts:
            node = _find(tree, dotted)
            start = node.lineno - 1 - len(node.decorator_list)
            block = lines[start:node.end_lineno]
            indent = min(len(l) - len(l.lstrip()) for l in block if l.strip())
            pieces.append("\n".join(l[indent:] for l in block))
        (out / name).write_text("\n\n".join(pieces) + "\n", encoding="utf-8")


def facts(results):
    """Numbers quoted in the text."""
    tests = 0
    for path in (ROOT / "tests").glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            own = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
            base_names = [b.id for b in cls.bases if isinstance(b, ast.Name)]
            inherited = 0
            for base in base_names:
                parent = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == base), None)
                if parent is not None:
                    inherited = len([n for n in parent.body if isinstance(n, ast.FunctionDef)
                                     and n.name.startswith("test")])
            tests += len(own) + inherited
    by_loss = {r["loss"]: r for r in results}
    lines = [
        "\\newcommand{\\FactTests}{%d}" % tests,
        "\\newcommand{\\FactBenchZero}{%.2f}" % by_loss[0.0]["duration"],
        "\\newcommand{\\FactBenchTen}{%.2f}" % by_loss[0.1]["duration"],
        "\\newcommand{\\FactBenchTwenty}{%.1f}" % by_loss[0.2]["duration"],
        "\\newcommand{\\FactKibZero}{%s}" % "{:,.0f}".format(by_loss[0.0]["throughput_kib_s"]),
        "\\newcommand{\\FactChunks}{%d}" % results[0]["chunks"],
    ]
    (LATEX / "generated" / "facts.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tests


def sequence_diagrams():
    """Render diagrams/*.mmd to PDF with the Mermaid CLI if it is installed."""
    mmdc = shutil.which("mmdc")
    if not mmdc:
        print("mmdc not found: keeping the existing sequence diagram PDFs")
        return
    for source in sorted((REPORT / "diagrams").glob("*.mmd")):
        target = LATEX / "figures" / (source.stem + ".pdf")
        subprocess.run([mmdc, "-q", "-i", str(source), "-o", str(target), "-c",
                        str(REPORT / "diagrams" / "mermaid-config.json"), "--pdfFit",
                        "-b", "white"], check=True)


def main():
    results = json.loads((REPORT / "bench_udp.json").read_text())
    for folder in ("figures", "tables", "snippets", "generated"):
        (LATEX / folder).mkdir(parents=True, exist_ok=True)
    bench_figures(results)
    cwnd_figure(results)
    bench_table(results)
    snippets()
    count = facts(results)
    sequence_diagrams()
    print("assets written; {} test methods".format(count))


if __name__ == "__main__":
    sys.exit(main())
