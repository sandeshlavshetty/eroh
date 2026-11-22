"""Scenario-based economic model for the EV charging + swapping network.

This is NOT a backtest. It is a transparent, assumption-driven calculation that
turns the design levers of the framework into reliability, loss and EBITDA
numbers for three weather/market regimes and five clusters. Every input below is
an explicit assumption (see README, Section 7) and can be changed.

Run:  python3 src/scenario_model.py
Writes: assets/charts/*.svg and output/results.json
"""

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHARTS = os.path.join(ROOT, "assets", "charts")
OUT = os.path.join(ROOT, "output")
os.makedirs(CHARTS, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

NAVY, TEAL, AMBER, GREY, RED, LIGHT = "#0B2545", "#13838F", "#E9A23B", "#8D99AE", "#C8553D", "#E8EEF2"
plt.rcParams.update({
    "font.family": ["Helvetica", "Arial", "DejaVu Sans"],
    "font.size": 10,
    "axes.edgecolor": "#B8C2CC",
    "axes.labelcolor": NAVY,
    "xtick.color": NAVY,
    "ytick.color": NAVY,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "svg.fonttype": "none",
})

# --------------------------------------------------------------------------
# 1. Network-level assumptions (INR, per day unless stated)
# --------------------------------------------------------------------------
TARIFF = 19.0             # blended INR/kWh (DC ~17, swap-equivalent ~22)
VAR_COST = 2.6            # INR/kWh delivered: host share, payments, variable O&M
DEG_BASE, DEG_OPT = 1.00, 0.85   # INR/kWh delivered battery wear
FIXED_CR_DAY = 2.65       # INR crore/day: leases, staff, fixed DISCOM charges, overheads
AI_RUN_CR_YR = 35.0       # INR crore/yr: platform, team, data, extra pack logistics
AI_CAPEX_CR = 40.0        # INR crore one-time build and integration
CR = 1e7

# Scenario: weight (share of days), forecast demand MWh/day, and policy outcomes.
# reliability, loss, unit energy cost (INR/kWh of input energy), tariff multiplier
SCENARIOS = {
    "Normal day": dict(w=0.65, D=6000,
                       base=dict(R=0.800, L=0.097, C=6.60, P=1.00),
                       opt=dict(R=0.910, L=0.077, C=5.95, P=1.01)),
    "Heatwave + IEX spike": dict(w=0.20, D=6900,
                                 base=dict(R=0.725, L=0.110, C=8.40, P=1.00),
                                 opt=dict(R=0.875, L=0.086, C=7.30, P=1.06)),
    "Monsoon RE lull": dict(w=0.15, D=5800,
                            base=dict(R=0.770, L=0.101, C=7.40, P=1.00),
                            opt=dict(R=0.895, L=0.079, C=6.50, P=1.02)),
}


def day_pnl(D, R, L, C, P, deg, fixed_cr):
    delivered = D * R * 1000                 # kWh
    energy_in = delivered / (1 - L)
    rev = delivered * TARIFF * P
    cost_energy = energy_in * C
    cost_var = delivered * VAR_COST
    cost_deg = delivered * deg
    ebitda = rev - cost_energy - cost_var - cost_deg - fixed_cr * CR
    return dict(delivered_mwh=delivered / 1000, input_mwh=energy_in / 1000,
                rev_cr=rev / CR, energy_cr=cost_energy / CR, var_cr=cost_var / CR,
                deg_cr=cost_deg / CR, ebitda_cr=ebitda / CR, margin=ebitda / rev)


def run(realisation=1.0, cost_shock=1.0, tariff_shock=1.0, loss_floor=None):
    """realisation scales how much of each base->opt improvement is achieved."""
    res = {}
    for name, s in SCENARIOS.items():
        b, o = s["base"], s["opt"]
        mix = {k: b[k] + realisation * (o[k] - b[k]) for k in b}
        if loss_floor is not None:
            mix["L"] = max(mix["L"], loss_floor)
        base = day_pnl(s["D"], b["R"], b["L"], b["C"] * cost_shock, b["P"] * tariff_shock,
                       DEG_BASE, FIXED_CR_DAY)
        opt = day_pnl(s["D"], mix["R"], mix["L"], mix["C"] * cost_shock, mix["P"] * tariff_shock,
                      DEG_BASE + realisation * (DEG_OPT - DEG_BASE),
                      FIXED_CR_DAY + AI_RUN_CR_YR / 365)
        res[name] = dict(w=s["w"], D=s["D"], base=base, opt=opt,
                         R_base=b["R"], R_opt=mix["R"], L_base=b["L"], L_opt=mix["L"])
    return res


def annual(res, key):
    days = {n: r["w"] * 365 for n, r in res.items()}
    rev = sum(r[key]["rev_cr"] * days[n] for n, r in res.items())
    ebitda = sum(r[key]["ebitda_cr"] * days[n] for n, r in res.items())
    deliv = sum(r[key]["delivered_mwh"] * days[n] for n, r in res.items())
    demand = sum(r["D"] * days[n] for n, r in res.items())
    inp = sum(r[key]["input_mwh"] * days[n] for n, r in res.items())
    return dict(rev_cr=rev, ebitda_cr=ebitda, margin=ebitda / rev,
                reliability=deliv / demand, loss=1 - deliv / inp,
                delivered_gwh=deliv / 1000)


base_case = run()
A_base, A_opt = annual(base_case, "base"), annual(base_case, "opt")
conservative = run(realisation=0.5)
C_opt = annual(conservative, "opt")

# --------------------------------------------------------------------------
# 2. Cluster check: share of demand, energy cost index, fixed-cost index
# --------------------------------------------------------------------------
CLUSTERS = {
    "Delhi NCR":  dict(share=0.28, cost=1.06, fixed=1.00, heat=1.30),
    "Mumbai MMR": dict(share=0.22, cost=1.05, fixed=1.12, heat=0.80),
    "Bengaluru":  dict(share=0.20, cost=0.95, fixed=0.95, heat=0.60),
    "Hyderabad":  dict(share=0.16, cost=0.97, fixed=0.92, heat=1.10),
    "Chennai":    dict(share=0.14, cost=0.98, fixed=0.98, heat=1.00),
}


def cluster_margins(realisation=1.0):
    out = {}
    for c, p in CLUSTERS.items():
        tot = {"base": [0, 0], "opt": [0, 0]}
        for name, s in SCENARIOS.items():
            w = s["w"]
            if name.startswith("Heatwave"):
                w = s["w"] * p["heat"]
            for key in ("base", "opt"):
                pol = s[key] if key == "base" else {
                    k: s["base"][k] + realisation * (s["opt"][k] - s["base"][k]) for k in s["base"]}
                fixed = FIXED_CR_DAY * p["share"] * p["fixed"]
                deg = DEG_BASE
                if key == "opt":
                    fixed += AI_RUN_CR_YR / 365 * p["share"]
                    deg = DEG_BASE + realisation * (DEG_OPT - DEG_BASE)
                r = day_pnl(s["D"] * p["share"], pol["R"], pol["L"], pol["C"] * p["cost"],
                            pol["P"], deg, fixed)
                tot[key][0] += r["rev_cr"] * w
                tot[key][1] += r["ebitda_cr"] * w
        out[c] = {k: v[1] / v[0] for k, v in tot.items()}
    return out


clusters = cluster_margins()
clusters_cons = cluster_margins(0.5)

# --------------------------------------------------------------------------
# 3. Sensitivity on optimised annual EBITDA margin
# --------------------------------------------------------------------------
sens = {
    "Only 50% of gains realised": annual(run(realisation=0.5), "opt")["margin"],
    "Power cost +20%": annual(run(cost_shock=1.2), "opt")["margin"],
    "Average tariff -5%": annual(run(tariff_shock=0.95), "opt")["margin"],
    "Losses stuck at 8.5%": annual(run(loss_floor=0.085), "opt")["margin"],
    "Power cost -10%": annual(run(cost_shock=0.9), "opt")["margin"],
}

# --------------------------------------------------------------------------
# 4. Illustrative one-day dispatch for a 250 MW cluster (rule-based view of
#    what the MILP does: charge in cheap hours, discharge in dear hours,
#    only when spread * efficiency beats wear cost)
# --------------------------------------------------------------------------
hours = np.arange(24)
acos = 7.3
discom = np.where((hours >= 9) & (hours < 16), 0.7 * acos, 1.3 * acos)
iex = np.array([3.9, 3.7, 3.6, 3.6, 3.8, 4.2, 4.8, 4.6, 3.6, 2.9, 2.5, 2.4,
                2.4, 2.5, 2.8, 3.6, 5.2, 7.8, 10.0, 10.0, 9.4, 7.2, 5.6, 4.6]) + 1.5
price = np.minimum(discom, iex)
demand_profile = np.array([0.35, 0.28, 0.22, 0.20, 0.22, 0.32, 0.50, 0.68, 0.80, 0.78, 0.74, 0.72,
                           0.70, 0.70, 0.72, 0.78, 0.88, 0.98, 1.00, 0.96, 0.88, 0.74, 0.58, 0.45])
load = demand_profile * 120  # MW at a large cluster
solar = np.clip(np.sin((hours - 6) / 12 * np.pi), 0, None) * 55
cap_e, cap_p, eta, wear = 50.0, 12.5, 0.90, 1.3   # MWh, MW, round-trip, INR/kWh
soc, socs, bess = 0.2 * cap_e, [], np.zeros(24)
order_cheap = np.argsort(price)
cheap_set = set(order_cheap[:6])
dear_set = set(np.argsort(-price)[:5])
spread_ok = price[list(dear_set)].mean() * eta - price[list(cheap_set)].mean() > wear
for h in hours:
    if spread_ok and h in cheap_set and soc < 0.95 * cap_e:
        p = min(cap_p, (0.95 * cap_e - soc))
        bess[h] = -p
        soc += p * np.sqrt(eta)
    elif spread_ok and h in dear_set and soc > 0.10 * cap_e:
        p = min(cap_p, (soc - 0.10 * cap_e) * np.sqrt(eta))
        bess[h] = p
        soc -= p / np.sqrt(eta)
    socs.append(soc)

# --------------------------------------------------------------------------
# 5. Charts
# --------------------------------------------------------------------------

def save(fig, name):
    fig.savefig(os.path.join(CHARTS, name), bbox_inches="tight")
    plt.close(fig)


# 5a. Reliability bridge 78 -> 89.7+
steps = [("Today", 78.0, "total"),
         ("Pack stock-outs\n(JIT + P90 buffer)", 3.6, "step"),
         ("Energy shortfall\n(prob. bids + storage)", 3.4, "step"),
         ("Queue abandonment\n(price steering)", 2.0, "step"),
         ("Grid-cap hours\n(BESS peak shaving)", 1.6, "step"),
         ("Charger downtime\n(predictive alerts)", 1.4, "step")]
fig, ax = plt.subplots(figsize=(8.6, 3.6))
run_total, xs = 0, []
for i, (lab, v, kind) in enumerate(steps):
    if kind == "total":
        ax.bar(i, v, color=GREY, width=0.6)
        ax.text(i, v + 0.3, f"{v:.1f}%", ha="center", color=NAVY, fontweight="bold")
        run_total = v
    else:
        ax.bar(i, v, bottom=run_total, color=TEAL, width=0.6)
        ax.text(i, run_total + v + 0.3, f"+{v:.1f}", ha="center", color=NAVY)
        run_total += v
ax.bar(len(steps), run_total, color=AMBER, width=0.6)
ax.text(len(steps), run_total + 0.3, f"{run_total:.1f}%", ha="center", color=NAVY, fontweight="bold")
ax.axhline(89.7, color=RED, ls="--", lw=1)
ax.text(len(steps) + 0.45, 89.9, "target 89.7%", color=RED, fontsize=9, ha="right", va="bottom")
ax.set_xticks(range(len(steps) + 1), [s[0] for s in steps] + ["Design\nestimate"], fontsize=8)
ax.set_ylim(70, 93)
ax.set_ylabel("Service reliability (%)")
save(fig, "fig_reliability_bridge.svg")

# 5b. Loss waterfall 10 -> 8
lsteps = [("Today", 10.0), ("Charger module\nstaging", -0.6), ("Just-in-time pack\ncharging (idle loss)", -0.5),
          ("Cycle BESS only\nwhen it pays", -0.4), ("Feeder load\nbalancing", -0.3),
          ("Standby + thermal\nmanagement", -0.3)]
fig, ax = plt.subplots(figsize=(8.6, 3.4))
cur = 0
for i, (lab, v) in enumerate(lsteps):
    if i == 0:
        ax.bar(i, v, color=GREY, width=0.6)
        ax.text(i, v + 0.1, f"{v:.1f}%", ha="center", fontweight="bold", color=NAVY)
        cur = v
    else:
        ax.bar(i, -v, bottom=cur + v, color=TEAL, width=0.6)
        ax.text(i, cur + 0.1, f"{v:.1f}", ha="center", color=NAVY)
        cur += v
ax.bar(len(lsteps), cur, color=AMBER, width=0.6)
ax.text(len(lsteps), cur + 0.1, f"{cur:.1f}%", ha="center", fontweight="bold", color=NAVY)
ax.axhline(8.0, color=RED, ls="--", lw=1)
ax.set_xticks(range(len(lsteps) + 1), [s[0] for s in lsteps] + ["Design\nestimate"], fontsize=8)
ax.set_ylim(6, 10.8)
ax.set_ylabel("Storage + conversion loss (%)")
save(fig, "fig_loss_waterfall.svg")

# 5c. EBITDA by scenario
names = list(SCENARIOS)
fig, ax = plt.subplots(figsize=(8.6, 3.4))
x = np.arange(len(names) + 1)
bm = [base_case[n]["base"]["margin"] * 100 for n in names] + [A_base["margin"] * 100]
om = [base_case[n]["opt"]["margin"] * 100 for n in names] + [A_opt["margin"] * 100]
cm = [conservative[n]["opt"]["margin"] * 100 for n in names] + [C_opt["margin"] * 100]
ax.bar(x - 0.27, bm, 0.26, color=GREY, label="Today (rule-based)")
ax.bar(x, cm, 0.26, color="#86C5CB", label="AI framework, 50% of gains")
ax.bar(x + 0.27, om, 0.26, color=TEAL, label="AI framework, design case")
for xi, v in zip(x, bm):
    ax.text(xi - 0.27, v + 0.5, f"{v:.1f}", ha="center", fontsize=8)
for xi, v in zip(x, cm):
    ax.text(xi, v + 0.5, f"{v:.1f}", ha="center", fontsize=8)
for xi, v in zip(x, om):
    ax.text(xi + 0.27, v + 0.5, f"{v:.1f}", ha="center", fontsize=8, fontweight="bold")
ax.axhline(12, color=RED, ls="--", lw=1)
ax.text(3.45, 12.4, "12% floor", color=RED, fontsize=9, ha="right")
ax.set_xticks(x, [f"{n}\n({int(SCENARIOS[n]['w']*100)}% of days)" for n in names] + ["Full year\n(weighted)"], fontsize=8.5)
ax.set_ylabel("EBITDA margin (%)")
ax.legend(frameon=False, fontsize=8, loc="upper center", ncol=3, bbox_to_anchor=(0.5, -0.2))
save(fig, "fig_ebitda_scenarios.svg")

# 5d. Cluster margins
fig, ax = plt.subplots(figsize=(8.6, 3.0))
cn = list(CLUSTERS)
x = np.arange(len(cn))
ax.bar(x - 0.27, [clusters[c]["base"] * 100 for c in cn], 0.26, color=GREY, label="Today")
ax.bar(x, [clusters_cons[c]["opt"] * 100 for c in cn], 0.26, color="#86C5CB", label="50% of gains")
ax.bar(x + 0.27, [clusters[c]["opt"] * 100 for c in cn], 0.26, color=TEAL, label="Design case")
for xi, c in zip(x, cn):
    ax.text(xi - 0.27, clusters[c]["base"] * 100 + 0.5, f"{clusters[c]['base']*100:.1f}", ha="center", fontsize=8)
    ax.text(xi, clusters_cons[c]["opt"] * 100 + 0.5, f"{clusters_cons[c]['opt']*100:.1f}", ha="center", fontsize=8)
    ax.text(xi + 0.27, clusters[c]["opt"] * 100 + 0.5, f"{clusters[c]['opt']*100:.1f}", ha="center", fontsize=8, fontweight="bold")
ax.axhline(12, color=RED, ls="--", lw=1)
ax.set_xticks(x, cn)
ax.set_ylabel("Annual EBITDA margin (%)")
ax.legend(frameon=False, fontsize=8, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12))
save(fig, "fig_cluster_margins.svg")

# 5e. Sensitivity tornado
fig, ax = plt.subplots(figsize=(8.6, 2.8))
labels = list(sens)
vals = [sens[k] * 100 for k in labels]
ref = A_opt["margin"] * 100
order = np.argsort([abs(v - ref) for v in vals])
for i, idx in enumerate(order):
    v = vals[idx]
    ax.barh(i, v - ref, left=ref, color=TEAL if v >= ref else AMBER, height=0.55)
    ax.text(v + (0.3 if v >= ref else -0.3), i, f"{v:.1f}%", va="center",
            ha="left" if v >= ref else "right", fontsize=8.5)
ax.axvline(ref, color=NAVY, lw=1)
ax.axvline(12, color=RED, ls="--", lw=1)
ax.text(12.2, len(labels) - 0.4, "12% floor", color=RED, fontsize=8.5)
ax.set_yticks(range(len(labels)), [labels[i] for i in order], fontsize=8.5)
ax.set_xlabel(f"Full-year EBITDA margin (%) — design case = {ref:.1f}%")
ax.set_xlim(9, ref + 5)
save(fig, "fig_sensitivity.svg")

# 5f. Illustrative dispatch day
fig, ax1 = plt.subplots(figsize=(8.6, 3.4))
ax1.fill_between(hours, 0, solar, step="mid", color=AMBER, alpha=0.25, label="Solar / cheap window")
ax1.plot(hours, load, color=NAVY, lw=2, label="Charging + swap load (MW)")
ax1.bar(hours, bess, color=[TEAL if b > 0 else GREY for b in bess], width=0.7, label="Battery: + discharge / - charge (MW)")
ax1.set_xlabel("Hour of day (IST)")
ax1.set_ylabel("MW")
ax1.set_xticks(range(0, 24, 2))
ax2 = ax1.twinx()
ax2.step(hours, price, where="mid", color=RED, lw=1.4, label="Cheapest available power (INR/kWh)")
ax2.set_ylabel("INR/kWh", color=RED)
ax2.spines["right"].set_visible(True)
ax2.tick_params(axis="y", colors=RED)
ax2.set_ylim(0, 14)
h1, l1 = ax1.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax1.legend(h1 + h2, l1 + l2, frameon=False, fontsize=8, loc="upper center", ncol=2, bbox_to_anchor=(0.5, -0.18))
ax1.set_ylim(-20, 140)
save(fig, "fig_dispatch_day.svg")

# --------------------------------------------------------------------------
# 6. Save numbers
# --------------------------------------------------------------------------
payback_months = AI_CAPEX_CR / ((A_opt["ebitda_cr"] - A_base["ebitda_cr"]) / 12)
payback_cons = AI_CAPEX_CR / ((C_opt["ebitda_cr"] - A_base["ebitda_cr"]) / 12)
results = dict(annual_base=A_base, annual_opt=A_opt, annual_conservative=C_opt,
               scenarios={n: {k: v for k, v in r.items()} for n, r in base_case.items()},
               clusters=clusters, clusters_conservative=clusters_cons, sensitivity=sens,
               uplift_cr=A_opt["ebitda_cr"] - A_base["ebitda_cr"],
               uplift_cons_cr=C_opt["ebitda_cr"] - A_base["ebitda_cr"],
               payback_months=payback_months, payback_months_conservative=payback_cons,
               dispatch=dict(price=price.round(2).tolist(), bess=bess.round(2).tolist(),
                             spread_ok=bool(spread_ok)))
with open(os.path.join(OUT, "results.json"), "w") as f:
    json.dump(results, f, indent=2, default=float)

print(json.dumps({"annual_base": A_base, "annual_opt": A_opt, "annual_cons": C_opt,
                  "uplift_cr": results["uplift_cr"], "uplift_cons_cr": results["uplift_cons_cr"],
                  "payback_m": payback_months, "payback_cons_m": payback_cons,
                  "clusters": clusters, "clusters_cons": clusters_cons, "sens": sens}, indent=1, default=float))
for n, r in base_case.items():
    print(n, {k: round(r[k]["margin"] * 100, 1) for k in ("base", "opt")},
          round(conservative[n]["opt"]["margin"] * 100, 1),
          "deliv", round(r["base"]["delivered_mwh"]), round(r["opt"]["delivered_mwh"]))
