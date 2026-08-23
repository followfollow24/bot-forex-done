import MetaTrader5 as mt5
from datetime import datetime, timedelta

mt5.initialize()
names = {555053: "adx20tp7", 555083: "adx18tp7", 555103: "regime22", 666000: "btc_cons", 666010: "btc_aggr"}
sl_tp_expected = {
    "adx20tp7": (3.0, 7.0), "adx18tp7": (3.0, 7.0), "regime22": (3.0, 7.0),
    "btc_cons": (4.0, 12.0), "btc_aggr": (2.5, 7.5),
}

deals = mt5.history_deals_get(datetime.now() - timedelta(days=60), datetime.now())
by_magic = {}
for d in deals or []:
    if d.entry == 1 and d.magic in names:
        by_magic.setdefault(d.magic, []).append(d)

print("=== PART 1: per-bot win rate (last 60 days, closed trades) ===")
for m, ds in by_magic.items():
    ds.sort(key=lambda x: x.time)
    pnls = [(x.profit + x.swap + x.commission) for x in ds]
    wins = sum(1 for p in pnls if p > 0)
    n = len(pnls)
    print(f"{names[m]:<10} n={n:>4} wins={wins:>3} winrate={wins/n*100:5.1f}%")

print()
print("=== PART 2: SL/TP execution accuracy (open positions right now) ===")
positions = mt5.positions_get() or []
if not positions:
    print("No open positions right now.")
for p in positions:
    name = names.get(p.magic, "?")
    if name == "?":
        continue
    sl_mult, tp_mult = sl_tp_expected[name]
    sl_dist = abs(p.price_open - p.sl)
    tp_dist = abs(p.tp - p.price_open)
    actual_ratio = tp_dist / sl_dist if sl_dist > 0 else 0
    expected_ratio = tp_mult / sl_mult
    print(f"{name:<10} ticket={p.ticket} entry={p.price_open} sl_dist={sl_dist:.3f} tp_dist={tp_dist:.3f} "
          f"actual_TP:SL={actual_ratio:.2f} expected={expected_ratio:.2f} "
          f"{'OK' if abs(actual_ratio-expected_ratio)<0.05 else 'MISMATCH!'}")

print()
print("=== PART 3: recent CLOSED trade SL/TP sanity (last 10 per bot) ===")
for m, ds in by_magic.items():
    name = names[m]
    sl_mult, tp_mult = sl_tp_expected[name]
    expected_ratio = tp_mult / sl_mult
    recent = ds[-10:]
    print(f"-- {name} (expected TP:SL ratio = {expected_ratio:.2f}) --")
    for d in recent:
        orders = mt5.history_orders_get(position=d.position_id)
        if not orders:
            continue
        open_order = min(orders, key=lambda o: o.time_setup)
        sl = open_order.sl
        tp = open_order.tp
        entry_px = open_order.price_open if open_order.price_open else d.price
        if sl and tp and entry_px:
            sl_dist = abs(entry_px - sl)
            tp_dist = abs(tp - entry_px)
            ratio = tp_dist / sl_dist if sl_dist > 0 else 0
            flag = "OK" if abs(ratio - expected_ratio) < 0.1 else "MISMATCH!"
            print(f"   pos={d.position_id} entry={entry_px:.3f} sl_dist={sl_dist:.3f} tp_dist={tp_dist:.3f} "
                  f"ratio={ratio:.2f} {flag}")
