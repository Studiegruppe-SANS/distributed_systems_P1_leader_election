# laver graferne til rapporten for P1 Kubernetes ud fra load test og failover-målingerne
# graferne gemmes i results/kubernetes/plots/
#
#   python3 docker/plot_kubernetes.py

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")                   # ingen GUI, skriver kun til filer
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

RESULTS = Path(__file__).resolve().parent.parent / "results" / "kubernetes"
LOADTEST = RESULTS / "loadtest"
FAILOVER = RESULTS / "failover"
PLOTS = RESULTS / "plots"

# samme farver og stil som plot_results.py fra første projekt
BLUE, ORANGE = "#2a78d6", "#eb6834"          # første og anden load test-kørsel
AQUA, YELLOW, VIOLET = "#1baf7a", "#eda100", "#6250d6"  # faserne i failover
TEXT, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.set_axisbelow(True)


def title(ax, text):
    ax.set_title(text, color=TEXT, fontsize=11, loc="left", pad=12)


def block(ax, y, start, width, color, label, text_color="white"):
    # én blok i tidslinjen med teksten skrevet direkte på
    ax.barh(y, width, left=start, height=0.62, color=color, edgecolor=SURFACE, linewidth=2)
    ax.text(start + width / 2, y, label, ha="center", va="center", fontsize=9, color=text_color)


# figur 1: hvad sker der, når lederen forsvinder (gennemsnit af alle kørsler)
def plot_failover_timeline(summary: pd.DataFrame):
    detect = summary["first_election_s"].mean()
    elect = summary["election_time_s"].mean()
    total = summary["first_ok_s"].mean()
    switch = total - detect - elect

    # én søjle: tiden uden cookies delt op i de tre trin
    fig, ax = plt.subplots(figsize=(8, 2), facecolor=SURFACE)
    style(ax)
    ax.spines["left"].set_visible(False)
    block(ax, 0, 0, detect, AQUA, f"Detection\n{detect:.1f} s", TEXT)
    block(ax, 0, detect, elect, YELLOW, f"Election\n{elect:.1f} s", TEXT)
    block(ax, 0, detect + elect, switch, VIOLET, f"Service\nswitch\n{switch:.1f} s")
    ax.text(total + 0.15, 0, f"{total:.1f} s", ha="left", va="center", fontsize=10, color=TEXT)

    ax.set_yticks([])
    ax.set_xlim(0, total + 1.2)
    ax.set_xlabel("Website downtime (s)", color=TEXT)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    title(ax, f"Website downtime split into its parts (mean of {len(summary)} runs)")
    fig.tight_layout()
    fig.savefig(PLOTS / "failover_breakdown.png", dpi=200)
    plt.close(fig)


# figur 2: hvor længe brugeren var uden cookies i hver kørsel
def plot_failover_runs(summary: pd.DataFrame):
    downtime = summary["first_ok_s"].to_numpy()
    mean = downtime.mean()
    runs = [f"Run {i}" for i in range(1, len(downtime) + 1)]

    fig, ax = plt.subplots(figsize=(6.5, 3.6), facecolor=SURFACE)
    style(ax)
    ax.bar(runs, downtime, width=0.55, color=BLUE, edgecolor=SURFACE, linewidth=2)
    # værdierne står inde i søjlerne og middelværdien til højre, så intet overlapper
    for x, v in enumerate(downtime):
        ax.text(x, v / 2, f"{v:.1f} s", ha="center", va="center", fontsize=10, color="white")
    ax.axhline(mean, color=TEXT, linestyle="--", linewidth=1)
    ax.text(len(runs) - 0.35, mean + 0.15, f"mean {mean:.1f} s", ha="left", va="bottom", fontsize=9, color=TEXT)
    ax.set_xlim(-0.5, len(runs) + 0.5)
    ax.set_ylim(0, downtime.max() + 1)
    ax.set_ylabel("Downtime (s)", color=TEXT)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    title(ax, "Website downtime in each failover run")
    fig.tight_layout()
    fig.savefig(PLOTS / "failover_runs.png", dpi=200)
    plt.close(fig)


# figur 3: svartider i load testen
def plot_loadtest(summary: pd.DataFrame):
    stats = (("median_ms", "50 % of requests"), ("p95_ms", "95 % of requests"),
             ("p99_ms", "99 % of requests"), ("max_ms", "All requests"))
    x = np.arange(len(stats))
    width = 0.36

    fig, ax = plt.subplots(figsize=(7, 3.8), facecolor=SURFACE)
    style(ax)
    for i, ((_, run), color, load) in enumerate(zip(summary.iterrows(), (BLUE, ORANGE), ("Light", "Heavy"))):
        values = [run[key] for key, _ in stats]
        offset = (i - 0.5) * width
        label = f"{load} load: {run['requests']:,} requests, {run['concurrency']} at a time"
        ax.bar(x + offset, values, width, color=color, edgecolor=SURFACE, linewidth=2, label=label)
        for xi, v in zip(x + offset, values):
            ax.text(xi, v + 1.2, f"{v:.1f}", ha="center", va="bottom", fontsize=8.5, color=TEXT)
    ax.set_xticks(x, [name for _, name in stats])
    ax.tick_params(axis="x", labelsize=10, labelcolor=TEXT, length=0)
    ax.set_ylim(0, summary["max_ms"].max() * 1.35)
    ax.set_ylabel("Answered within (ms)", color=TEXT)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    title(ax, "Load test: response times of the leader")
    fig.tight_layout()
    fig.savefig(PLOTS / "loadtest_response_times.png", dpi=200)
    plt.close(fig)


def main():
    if not (LOADTEST / "summary.csv").exists() or not (FAILOVER / "summary.csv").exists():
        sys.exit(f"Mangler summary.csv i {LOADTEST} eller {FAILOVER}. Kør først make loadtest og make failover")
    PLOTS.mkdir(parents=True, exist_ok=True)
    for old in PLOTS.glob("*.png"):        # fjerner grafer fra tidligere versioner af scriptet
        old.unlink()
    failover = pd.read_csv(FAILOVER / "summary.csv")
    loadtest = pd.read_csv(LOADTEST / "summary.csv")
    plot_failover_timeline(failover)
    plot_failover_runs(failover)
    plot_loadtest(loadtest)

    # tal til tabellerne i rapporten
    print(loadtest[["requests", "concurrency", "ok", "failed", "throughput_rps",
                    "median_ms", "p95_ms", "p99_ms", "max_ms"]].to_string(index=False))
    cols = ["first_election_s", "election_time_s", "first_ok_s", "failed_requests"]
    print(failover[cols].agg(["mean", "std", "min", "max"]).round(2).to_string())
    print(f"Grafer gemt i {PLOTS}")


if __name__ == "__main__":
    main()
