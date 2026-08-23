import { useState, useEffect } from "react";

const C = {
  gold: "#FFD700", green: "#00FF41", red: "#FF2222",
  blue: "#00BFFF", cyan: "#00FFD0", amber: "#FFB000", dim: "#003300",
  bg: "#000814", frame: "#3D1C00", border: "#8B4513",
  panel: "#000D1A", grid: "#001A33",
};

const px = (extra = {}) => ({
  fontFamily: "'Courier New', monospace",
  imageRendering: "pixelated",
  ...extra,
});

function Blink({ on: active, color = C.green, offColor = "#003300", size = 10 }) {
  const [on, setOn] = useState(true);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setOn(p => !p), 600);
    return () => clearInterval(t);
  }, [active]);
  return <span style={{ color: active ? (on ? color : offColor) : C.red, fontSize: size }}>■</span>;
}

function PixelChart({ data }) {
  const max = Math.max(...data), min = Math.min(...data);
  const range = max - min || 1;
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: "2px", height: "72px", background: "#00040E", border: `1px solid ${C.grid}`, padding: "4px 4px 0", position: "relative" }}>
      {[25, 50, 75].map(pct => (
        <div key={pct} style={{ position: "absolute", left: 0, right: 0, top: `${pct}%`, borderTop: `1px solid #001A33`, zIndex: 0 }} />
      ))}
      {data.map((v, i) => {
        const h = Math.max(4, ((v - min) / range) * 64);
        const up = i === 0 || v >= data[i - 1];
        return (
          <div key={i} style={{
            flex: 1, height: h, zIndex: 1,
            background: up ? "#002200" : "#220000",
            borderTop: `2px solid ${up ? C.green : C.red}`,
          }} />
        );
      })}
    </div>
  );
}

function BotMascot({ active }) {
  const col = active ? C.green : "#333";
  const eye = active ? C.blue : "#222";
  return (
    <div style={{ ...px(), fontSize: 0, lineHeight: 0, display: "inline-block" }}>
      {[
        ["  ██████  ", C.amber],
        [" █      █ ", C.amber],
        [" █ ◉  ◉ █ ", eye],
        [" █  ██  █ ", C.amber],
        [" █      █ ", C.amber],
        ["  ██████  ", C.amber],
        ["   █  █   ", col],
        [" ████████ ", col],
        [" █  ██  █ ", col],
        [" █      █ ", col],
        [" █      █ ", col],
        ["  ██  ██  ", col],
      ].map(([row, c], i) => (
        <div key={i} style={{ display: "block", height: 7 }}>
          {row.split("").map((ch, j) => (
            <span key={j} style={{
              display: "inline-block", width: 7, height: 7,
              background: ch === "█" || ch === "◉" ? c : "transparent",
              boxShadow: (ch === "◉" && active) ? `0 0 4px ${C.blue}` : "none"
            }} />
          ))}
        </div>
      ))}
    </div>
  );
}

// ── Two live XAUUSD variants, sharing one gold price feed ──────────────────
const VARIANTS = ["cwider", "mix_a"];
const LABEL  = { cwider: "C_WIDER", mix_a: "MIX_A" };
const ACCENT = { cwider: C.gold,    mix_a: C.cyan };
const MAGIC  = { cwider: 555003,    mix_a: 555033 };
const MAXPOS = { cwider: 2,         mix_a: 1 };
const EXIT   = { cwider: "SL 2.5x / TP 5.0x", mix_a: "SL 2.5x / TP 7.0x" };

const INIT_BOTS = {
  cwider: { running: true, killed: false, pnl: 14.66, pos: 0, trades: 3, spread: 0.26 },
  mix_a:  { running: true, killed: false, pnl: 0.00,  pos: 0, trades: 0, spread: 0.00 },
};

export default function PixelDashboard() {
  const [bots, setBots] = useState(INIT_BOTS);
  const [price, setPrice] = useState(3327.45);
  const [chart, setChart] = useState(() =>
    [...Array(30)].map((_, i) => 3310 + Math.sin(i * 0.45) * 12 + i * 0.6)
  );
  const [time, setTime] = useState(new Date());
  const [selected, setSelected] = useState("cwider");

  useEffect(() => {
    const t = setInterval(() => {
      setTime(new Date());
      setChart(prev => {
        const last = prev[prev.length - 1];
        const next = Math.max(3280, Math.min(3380, last + (Math.random() - 0.47) * 3));
        return [...prev.slice(1), next];
      });
      setPrice(p => +(p + (Math.random() - 0.5) * 0.5).toFixed(2));
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const totalPnl = Object.values(bots).reduce((s, b) => s + b.pnl, 0);
  const totalPos = Object.entries(bots).reduce((s, [k, b]) => s + b.pos, 0);
  const totalMax = VARIANTS.reduce((s, k) => s + MAXPOS[k], 0);
  const anyRunning = VARIANTS.some(k => bots[k].running);
  const allRunning = VARIANTS.every(k => bots[k].running);

  const toggle = v => setBots(p => ({ ...p, [v]: { ...p[v], running: !p[v].running } }));
  const killToggle = v => setBots(p => ({ ...p, [v]: { ...p[v], killed: !p[v].killed } }));
  const startAll = () => setBots(p => Object.fromEntries(VARIANTS.map(k => [k, { ...p[k], running: true, killed: false }])));
  const killAll  = () => setBots(p => Object.fromEntries(VARIANTS.map(k => [k, { ...p[k], killed: true }])));

  const pnlCol = v => v > 0 ? C.green : v < 0 ? C.red : "#334433";
  const timeStr = time.toUTCString().slice(5, 25);

  return (
    <div style={{ ...px(), background: "#0A0400", minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>

      {/* ── OUTER FRAME (suitcase) ─────────────────────────────────── */}
      <div style={{
        background: "#2A1000",
        borderRadius: 14,
        padding: "10px 12px 12px",
        border: "6px solid #6B3311",
        boxShadow: "inset 0 2px 6px rgba(255,180,0,0.15), 0 0 60px rgba(0,0,0,0.9), 0 4px 0 #1A0800",
        maxWidth: 860,
        width: "100%",
      }}>

        {/* Handle */}
        <div style={{ textAlign: "center", marginBottom: 8 }}>
          <div style={{ display: "inline-block", background: "#4A2200", border: "3px solid #6B3311", borderRadius: 8, padding: "3px 36px", height: 10 }} />
        </div>

        {/* ── SCREEN ─────────────────────────────────────────────────── */}
        <div style={{
          background: C.bg,
          borderRadius: 6,
          border: "3px solid #140700",
          boxShadow: "inset 0 0 40px rgba(0,255,65,0.04), inset 0 0 80px rgba(0,0,0,0.8)",
          overflow: "hidden",
          position: "relative",
        }}>

          {/* Scanlines */}
          <div style={{
            position: "absolute", inset: 0, pointerEvents: "none", zIndex: 20,
            backgroundImage: "repeating-linear-gradient(0deg, rgba(0,0,0,0.12) 0, rgba(0,0,0,0.12) 1px, transparent 1px, transparent 3px)",
          }} />
          {/* CRT glow */}
          <div style={{
            position: "absolute", inset: 0, pointerEvents: "none", zIndex: 19,
            background: "radial-gradient(ellipse at 50% 40%, rgba(0,40,20,0.15) 0%, transparent 70%)",
          }} />

          <div style={{ padding: "12px 14px", position: "relative", zIndex: 1 }}>

            {/* ── STATUS BAR ─────────────────────────────────────────── */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, borderBottom: `1px solid #002211`, paddingBottom: 6 }}>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <Blink on={anyRunning} size={10} />
                <span style={{ ...px(), color: C.green, fontSize: 9 }}>FOREX BOT SYSTEM v2.1</span>
              </div>
              <span style={{ ...px(), color: "#005522", fontSize: 9 }}>{timeStr} UTC</span>
              <span style={{ ...px(), color: C.amber, fontSize: 9 }}>
                {Object.values(bots).filter(b => b.running).length}/2 ACTIVE
              </span>
            </div>

            {/* ── HEADER METRICS ──────────────────────────────────────── */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 6, marginBottom: 10 }}>
              {[
                { label: "EQUITY",    val: "$9,892.65",                                          color: C.gold },
                { label: "TODAY P&L", val: `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`, color: pnlCol(totalPnl) },
                { label: "OPEN POS",  val: `${totalPos} / ${totalMax}`,                           color: "#60A5FA" },
                { label: "SYMBOL",    val: "XAUUSDm",                                             color: "#34D399" },
              ].map(({ label, val, color }) => (
                <div key={label} style={{ background: C.panel, border: `1px solid ${C.grid}`, padding: "6px 8px", textAlign: "center" }}>
                  <div style={{ ...px(), color: "#224422", fontSize: 7, marginBottom: 3 }}>{label}</div>
                  <div style={{ ...px(), color, fontSize: 11, textShadow: `0 0 8px ${color}55` }}>{val}</div>
                </div>
              ))}
            </div>

            {/* ── MAIN GRID ───────────────────────────────────────────── */}
            <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 8 }}>

              {/* ── LEFT COLUMN ─────────────────────────────────────── */}
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>

                {/* Chart */}
                <div style={{ background: C.panel, border: `1px solid ${C.grid}`, padding: "7px 8px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                    <span style={{ ...px(), color: "#224422", fontSize: 8 }}>◼ MARKET ◼ VOLUME</span>
                    <span style={{ ...px(), color: C.gold, fontSize: 8 }}>XAUUSDm  {price.toFixed(2)}</span>
                  </div>
                  <PixelChart data={chart} />
                  <div style={{ display: "flex", justifyContent: "space-between", marginTop: 3 }}>
                    {["LO", "25", "MID", "75", "HI"].map(l => (
                      <span key={l} style={{ ...px(), color: "#113322", fontSize: 7 }}>{l}</span>
                    ))}
                  </div>
                </div>

                {/* Variant rows */}
                {VARIANTS.map(v => {
                  const b = bots[v];
                  const ac = ACCENT[v];
                  return (
                    <div key={v} onClick={() => setSelected(v)}
                      style={{
                        background: selected === v ? "#001A10" : C.panel,
                        border: `1px solid ${selected === v ? ac + "55" : C.grid}`,
                        borderLeft: `4px solid ${b.running ? ac : "#220000"}`,
                        padding: "7px 8px",
                        cursor: "pointer",
                        transition: "all 0.1s",
                      }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <Blink on={b.running} color={ac} size={9} />
                        <span style={{ ...px(), color: ac, fontSize: 10, width: 58 }}>{LABEL[v]}</span>
                        <span style={{ ...px(), color: "#224433", fontSize: 7, flex: 1 }}>
                          {EXIT[v]} · #{MAGIC[v]} · MAX:{MAXPOS[v]}
                        </span>
                        <span style={{ ...px(), color: pnlCol(b.pnl), fontSize: 12, textShadow: `0 0 6px ${pnlCol(b.pnl)}55` }}>
                          {b.pnl >= 0 ? "+" : ""}${b.pnl.toFixed(2)}
                        </span>
                        <button onClick={e => { e.stopPropagation(); toggle(v); }} style={{
                          ...px(), padding: "3px 7px", border: "none", cursor: "pointer",
                          background: b.running ? "#330000" : "#003300",
                          color: b.running ? C.red : C.green,
                          fontSize: 9, letterSpacing: 1,
                          boxShadow: b.running ? `0 0 6px ${C.red}33` : `0 0 6px ${C.green}33`,
                        }}>
                          {b.running ? "■STOP" : "▶START"}
                        </button>
                        <button onClick={e => { e.stopPropagation(); killToggle(v); }} style={{
                          ...px(), padding: "3px 5px", border: "none", cursor: "pointer",
                          background: b.killed ? "#332200" : "#001122",
                          color: b.killed ? C.gold : "#223333",
                          fontSize: 9,
                        }}>
                          {b.killed ? "🔒" : "🔓"}
                        </button>
                      </div>
                      <div style={{ display: "flex", gap: 10, marginTop: 5, marginLeft: 15 }}>
                        <span style={{ ...px(), color: "#224422", fontSize: 7 }}>POS {b.pos}/{MAXPOS[v]}</span>
                        <span style={{ ...px(), color: "#224422", fontSize: 7 }}>TRADES {b.trades}</span>
                        <span style={{ ...px(), color: "#224422", fontSize: 7 }}>
                          SPREAD {b.spread > 0 ? `$${b.spread.toFixed(2)}` : "—"}
                        </span>
                      </div>
                      {b.killed && (
                        <div style={{ ...px(), color: C.amber, fontSize: 7, marginTop: 3, marginLeft: 15 }}>
                          ▲ KILL-SWITCH ON — NO NEW ENTRIES
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* ── RIGHT COLUMN ─────────────────────────────────────── */}
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>

                {/* Price board */}
                <div style={{ background: C.panel, border: `1px solid ${C.grid}`, padding: "8px 10px" }}>
                  <div style={{ ...px(), color: "#224422", fontSize: 8, textAlign: "center", marginBottom: 8, borderBottom: `1px solid ${C.grid}`, paddingBottom: 4 }}>
                    ── PRICE BOARD ──
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 6px", background: "#001A10" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                      <div style={{ width: 5, height: 5, background: C.gold }} />
                      <span style={{ ...px(), color: "#336644", fontSize: 8 }}>GOLD</span>
                    </div>
                    <span style={{ ...px(), color: C.gold, fontSize: 13, textShadow: `0 0 8px ${C.gold}44` }}>
                      {price.toFixed(2)}
                    </span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, padding: "0 6px" }}>
                    <span style={{ ...px(), color: "#224422", fontSize: 7 }}>ENTRY SIGNAL</span>
                    <span style={{ ...px(), color: C.blue, fontSize: 8 }}>SHARED</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, padding: "0 6px" }}>
                    <span style={{ ...px(), color: "#224422", fontSize: 7 }}>VARIANTS</span>
                    <span style={{ ...px(), color: C.cyan, fontSize: 8 }}>2 LIVE</span>
                  </div>
                </div>

                {/* Bot mascot */}
                <div style={{
                  background: C.panel, border: `1px solid ${C.grid}`,
                  padding: "12px 8px", flex: 1,
                  display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8
                }}>
                  <BotMascot active={anyRunning} />
                  <div style={{ textAlign: "center" }}>
                    <div style={{ ...px(), color: C.green, fontSize: 9, textShadow: `0 0 6px ${C.green}66` }}>
                      {allRunning ? "ALL SYSTEMS GO" : anyRunning ? "PARTIAL ACTIVE" : "OFFLINE"}
                    </div>
                    <div style={{ ...px(), color: "#224422", fontSize: 7, marginTop: 3 }}>
                      BOT v2.1 · DEMO MODE
                    </div>
                  </div>
                </div>

                {/* Controls */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                  <button onClick={startAll} style={{
                    ...px(), padding: "10px 0", border: "2px solid #00AA22",
                    background: "#001A00", color: C.green, cursor: "pointer",
                    fontSize: 11, letterSpacing: 2,
                    boxShadow: `0 0 12px ${C.green}44`, textShadow: `0 0 6px ${C.green}`,
                  }}>
                    ▶ START ALL
                  </button>
                  <button onClick={killAll} style={{
                    ...px(), padding: "10px 0", border: "2px solid #AA0000",
                    background: "#1A0000", color: C.red, cursor: "pointer",
                    fontSize: 11, letterSpacing: 2,
                    boxShadow: `0 0 12px ${C.red}44`, textShadow: `0 0 6px ${C.red}`,
                  }}>
                    ■ KILL ALL
                  </button>
                </div>

              </div>
            </div>

            {/* ── FOOTER ─────────────────────────────────────────────── */}
            <div style={{ marginTop: 8, background: C.panel, border: `1px solid ${C.grid}`, padding: "4px 10px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ ...px(), color: "#224422", fontSize: 7 }}>DEMO · Exness-MT5Trial6 · Account 413879493 · RISK 0.30%/trade</span>
              <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                <Blink on={true} color={C.green} size={7} />
                <span style={{ ...px(), color: "#224422", fontSize: 7 }}>LIVE DATA</span>
              </div>
            </div>

          </div>
        </div>

        {/* Bottom frame detail */}
        <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 20px 0", alignItems: "center" }}>
          {["◈", "━━━━━━━━━━━━━━━━━━━━━━━━━━━", "◈"].map((s, i) => (
            <span key={i} style={{ color: "#4A2200", fontSize: i === 1 ? 8 : 14 }}>{s}</span>
          ))}
        </div>
      </div>
    </div>
  );
}
