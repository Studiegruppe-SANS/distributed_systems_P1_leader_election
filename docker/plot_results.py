#!/usr/bin/env python3
"""
plot_results.py — laver grafer til rapporten ud fra en CSV fra `experiment.py sweep`.

Brug (kraever pandas og matplotlib: pip install -r requirements-dev.txt):
  python3 docker/plot_results.py                          seneste results/sweep-*.csv
  python3 docker/plot_results.py results/sweep-X.csv      en bestemt fil

Skriver til results/plots/:
  messages_vs_n.png   election-beskeder (ELECTION+OK+COORDINATOR) pr. scenarie, classic vs improved
  time_vs_n.png       election-tid pr. scenarie
  summary.csv         gennemsnit, min og max pr. (scenarie, algoritme, N) — tabellen bag graferne
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")                   # ingen GUI, skriv kun filer
import matplotlib.pyplot as plt         # noqa: E402
import pandas as pd                     # noqa: E402

RESULTS = Path(__file__).resolve().parent.parent / "results"
PLOTS = RESULTS / "plots"

# Faste farver pr. algoritme (valideret for farveblindhed); farven foelger algoritmen, ikke raekkefoelgen
COLORS = {"classic": "#2a78d6", "improved": "#eb6834"}
TEXT, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SCENARIO_TITLES = {
    "startup": "Opstart",
    "failover": "Failover (alle opdager)",
    "worst": "Værste tilfælde (node 0 starter)",
    "crash-middle": "Crash af ikke-leder",
    "crash-two": "Crash af de to højeste",
    "recover-leader": "Leder kommer tilbage",
}
# Teoretiske beskedtal for vaerste tilfaelde (samme formler som cmd_worst i experiment.py)
THEORY = {"classic": lambda n: n * (n - 1), "improved": lambda n: 3 * n - 3}


def latest_csv() -> Path:
    files = sorted(RESULTS.glob("sweep-*.csv"))
    if not files:
        sys.exit(f"Ingen sweep-*.csv i {RESULTS}. Koer foerst: python3 docker/experiment.py sweep")
    return files[-1]


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    return (df.groupby(["scenario", "algorithm", "n"])
              .agg(runs=("run", "count"),
                   correct=("correct", "mean"),
                   total_mean=("total", "mean"), total_min=("total", "min"), total_max=("total", "max"),
                   time_mean=("election_time_s", "mean"), time_min=("election_time_s", "min"),
                   time_max=("election_time_s", "max"))
              .reset_index())


def style(ax, title, ylabel):
    ax.set_title(title, color=TEXT, fontsize=11, loc="left")
    ax.set_xlabel("Antal noder (N)", color=MUTED, fontsize=9)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_facecolor(SURFACE)
    ax.set_ylim(0, max(ax.get_ylim()[1], 1))  # crash-middle er typisk 0 overalt


def plot(summary: pd.DataFrame, metric: str, ylabel: str, filename: str, theory: bool) -> Path:
    scenarios = [s for s in SCENARIO_TITLES if s in set(summary["scenario"])]
    cols = min(3, len(scenarios))
    rows = -(-len(scenarios) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 3.4 * rows), squeeze=False)

    for ax, scenario in zip(axes.flat, scenarios):
        data = summary[summary["scenario"] == scenario]
        drawn = False
        for algo, color in COLORS.items():
            d = data[data["algorithm"] == algo].dropna(subset=[f"{metric}_mean"]).sort_values("n")
            if d.empty:
                continue
            drawn = True
            # Skygge = min..max over koerslerne, linje = gennemsnit
            ax.fill_between(d["n"], d[f"{metric}_min"], d[f"{metric}_max"], color=color, alpha=0.15, linewidth=0)
            # Classic tegnes tykt nederst og improved tyndt ovenpaa. Er tallene ens,
            # ses classic som en blaa kant rundt om den orange linje.
            wide = algo == "classic"
            ax.plot(d["n"], d[f"{metric}_mean"], color=color, linewidth=4.5 if wide else 2,
                    marker="o", markersize=9 if wide else 5, zorder=2 if wide else 3,
                    markeredgecolor="white", markeredgewidth=1.5, label=algo)
            if theory and scenario == "worst":
                # Teori som tynd moerk stiplet linje oeverst, saa den ses selv naar maalingerne rammer den
                ax.plot(d["n"], [THEORY[algo](n) for n in d["n"]], color=TEXT, linewidth=1,
                        linestyle="--", zorder=4, label="teori: n(n-1) og 3n-3")
            # Direkte label ved sidste punkt, i tekstfarve (farven baeres af linjen)
            last = d.iloc[-1]
            value = last[f"{metric}_mean"]
            ax.annotate(f"{value:.0f}" if metric == "total" else f"{value:.1f}", (last["n"], last[f"{metric}_mean"]),
                        xytext=(8, 6 if wide else -6), textcoords="offset points", va="center", fontsize=8, color=TEXT)
        ax.set_xticks(sorted(data["n"].unique()))
        # Er tallene ens, ligger improved-linjen oven paa classic. Sig det, saa classic ikke ser ud til at mangle.
        per_algo = [tuple(data[data["algorithm"] == a].sort_values("n")[f"{metric}_mean"]) for a in COLORS]
        if drawn and len(per_algo[0]) and per_algo[0] == per_algo[1]:
            ax.text(0.02, 0.95, "classic og improved er ens", transform=ax.transAxes,
                    ha="left", va="top", fontsize=8, color=MUTED)
        if not drawn:
            ax.text(0.5, 0.5, "ingen election (forventet)", transform=ax.transAxes,
                    ha="center", va="center", fontsize=9, color=MUTED)
        style(ax, SCENARIO_TITLES[scenario], ylabel)
    for ax in axes.flat[len(scenarios):]:
        ax.set_visible(False)

    # Én faelles forklaring oeverst (stiplede linjer kun i 'Vaerste tilfaelde')
    handles = {}
    for ax in axes.flat:
        for h, lab in zip(*ax.get_legend_handles_labels()):
            handles.setdefault(lab, h)
    fig.legend(handles.values(), handles.keys(), loc="upper center", ncol=len(handles),
               frameon=False, fontsize=9, labelcolor=TEXT)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out = PLOTS / filename
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return out


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_csv()
    df = pd.read_csv(src)
    PLOTS.mkdir(parents=True, exist_ok=True)
    summary = summarize(df)
    summary.to_csv(PLOTS / "summary.csv", index=False)
    print(f"Laeste {len(df)} koersler fra {src}")
    print(plot(summary, "total", "Election-beskeder", "messages_vs_n.png", theory=True))
    print(plot(summary, "time", "Election-tid (s)", "time_vs_n.png", theory=False))
    print(PLOTS / "summary.csv")
    wrong = df[~df["correct"].astype(bool)]
    if len(wrong):
        print(f"\nADVARSEL: {len(wrong)} koersler valgte forkert leder eller blev ikke enige:")
        print(wrong[["scenario", "algorithm", "n", "run", "expected_leader", "leader", "agreement"]]
              .to_string(index=False))


if __name__ == "__main__":
    main()
