"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Maximize2, Minus, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  getDomainGraph,
  listDomains,
  type DomainGraph,
  type DomainSummary,
  type GraphEdge,
  type GraphNode,
} from "@/lib/knowledge-tree-api";

const FIELD = "rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[13px]";
const ICON_BTN =
  "inline-flex h-7 w-7 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]";

const R = 46; // node radius
const COL = 170; // horizontal spacing
const ROW = 150; // vertical spacing inside a band
const TOP_PAD = 20;
const FALLBACK_COLOR = "#9aa5b1";
// One colour per relation type, so many kinds of links stay readable.
const REL_PALETTE = ["#e4572e", "#2e86ab", "#3bb273", "#9c6ade", "#f0a202", "#d1495b", "#00a6a6", "#8d6346", "#5c80bc", "#c05299"];

interface Placed extends GraphNode {
  x: number;
  y: number;
}

/**
 * Rows by relation direction: a node sits one row below the nodes it points
 * to (TYPE_OF / PART_OF point up), then each row is ordered by its
 * neighbours' positions to cut crossings.
 */
function layout(nodes: GraphNode[], edges: GraphEdge[]): { placed: Placed[]; bands: { y: number; h: number }[]; width: number } {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const up = new Map<string, string[]>();
  const nbrs = new Map<string, string[]>();
  for (const e of edges) {
    const a = byId.get(e.source_id);
    const b = byId.get(e.target_id);
    if (!a || !b) continue;
    nbrs.set(a.id, [...(nbrs.get(a.id) ?? []), b.id]);
    nbrs.set(b.id, [...(nbrs.get(b.id) ?? []), a.id]);
    up.set(a.id, [...(up.get(a.id) ?? []), b.id]);
  }
  const rank = new Map<string, number>();
  const visit = (id: string, stack: Set<string>): number => {
    if (rank.has(id)) return rank.get(id)!;
    if (stack.has(id)) return 0; // a cycle: break it here
    stack.add(id);
    const r = Math.max(-1, ...(up.get(id) ?? []).map((t) => visit(t, stack))) + 1;
    stack.delete(id);
    rank.set(id, r);
    return r;
  };
  nodes.forEach((n) => visit(n.id, new Set()));

  const placed = new Map<string, Placed>();
  const bands: { y: number; h: number }[] = [];
  let top = 0;
  let width = 0;
  {
    const rows: GraphNode[][] = [];
    for (const n of nodes) (rows[rank.get(n.id) ?? 0] ??= []).push(n);
    const filled = rows.filter(Boolean);
    const h = Math.max(1, filled.length) * ROW + TOP_PAD;
    filled.forEach((row, i) => {
      row.sort((a, b) => a.title.localeCompare(b.title));
      row.forEach((n, j) => placed.set(n.id, { ...n, x: j * COL + COL / 2, y: top + TOP_PAD + i * ROW + R }));
    });
    // Two sweeps: order each row by the mean x of its already placed neighbours.
    for (let pass = 0; pass < 2; pass++) {
      for (const row of filled) {
        const key = (n: GraphNode) => {
          const xs = (nbrs.get(n.id) ?? []).map((id) => placed.get(id)?.x).filter((x): x is number => x !== undefined);
          return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : placed.get(n.id)!.x;
        };
        const order = [...row].sort((a, b) => key(a) - key(b));
        order.forEach((n, j) => {
          placed.get(n.id)!.x = j * COL + COL / 2;
        });
      }
    }
    for (const row of filled) width = Math.max(width, row.length * COL);
    bands.push({ y: top, h });
    top += h;
  }
  // Centre each row in the widest one.
  const rowsByY = new Map<number, Placed[]>();
  for (const p of placed.values()) rowsByY.set(p.y, [...(rowsByY.get(p.y) ?? []), p]);
  for (const row of rowsByY.values()) {
    const shift = (width - row.length * COL) / 2;
    row.forEach((p) => (p.x += shift));
  }
  return { placed: [...placed.values()], bands, width: Math.max(width, COL * 3) };
}

function wrap(text: string, max = 15): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if ((cur + " " + w).trim().length > max && cur) {
      lines.push(cur);
      cur = w;
    } else cur = (cur + " " + w).trim();
  }
  if (cur) lines.push(cur);
  if (lines.length > 2) return [lines[0], `${lines[1].slice(0, max - 1)}…`];
  return lines;
}

function fmtProps(props: Record<string, unknown>) {
  const entries = Object.entries(props);
  return entries.length ? entries.map(([k, v]) => `${k}:${String(v)}`).join(", ") : "";
}

/** The main knowledge graph of one domain, for R&D and administrators to see at a glance. */
export default function GraphDiagram() {
  const { t } = useTranslation();
  const [domains, setDomains] = useState<DomainSummary[] | null>(null);
  const [domainId, setDomainId] = useState("");
  const [graph, setGraph] = useState<DomainGraph | null>(null);
  // Relation types switched off in the legend (the outline's PART_OF starts hidden when other links exist).
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState({ x: 16, y: 16, k: 0.8 });
  const [error, setError] = useState("");
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    listDomains()
      .then((d) => {
        if (!alive) return;
        setDomains(d);
        if (d.length) setDomainId((cur) => cur || d[0].id);
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!domainId) return;
    let alive = true;
    getDomainGraph(domainId)
      .then((g) => {
        if (!alive) return;
        setGraph(g);
        setSelectedId(null);
        // A plain outline has no other relations: show its structure then.
        setHidden(g.edges.some((e) => !e.implicit) ? new Set([g.schema.implicit_relation]) : new Set());
        setView({ x: 16, y: 16, k: 0.8 });
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [domainId]);

  const relTypes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of graph?.edges ?? []) counts.set(e.type, (counts.get(e.type) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [graph]);
  const relColor = useMemo(() => {
    const names = (graph?.schema.relations ?? []).map((r) => r.name);
    return (type: string) => {
      const i = names.indexOf(type);
      return REL_PALETTE[(i < 0 ? names.length : i) % REL_PALETTE.length];
    };
  }, [graph]);
  const edges = useMemo(
    () => (graph ? graph.edges.filter((e) => !hidden.has(e.type)) : []),
    [graph, hidden],
  );
  const { placed, bands, width } = useMemo(
    // Every relation (outline included) decides the rows, so hiding PART_OF never reshuffles.
    () => (graph ? layout(graph.nodes, graph.edges) : { placed: [], bands: [], width: 0 }),
    [graph],
  );
  const pos = useMemo(() => new Map(placed.map((p) => [p.id, p])), [placed]);
  const colors = useMemo(
    () => new Map((graph?.schema.labels ?? []).map((l) => [l.name, l.color || FALLBACK_COLOR])),
    [graph],
  );
  const height = bands.reduce((a, b) => a + b.h, 0);
  const selected = selectedId ? pos.get(selectedId) ?? null : null;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      setView((v) => {
        const k = Math.min(2.5, Math.max(0.15, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
        return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [graph]);

  const zoom = (f: number) => setView((v) => ({ ...v, k: Math.min(2.5, Math.max(0.15, v.k * f)) }));
  const fit = () => {
    const el = box.current;
    if (!el || !width) return;
    const k = Math.min(1.5, Math.max(0.15, Math.min(el.clientWidth / (width + 40), el.clientHeight / (height + 40))));
    setView({ x: 12, y: 12, k });
  };

  if (error) return <p className="text-[13px] text-[var(--destructive)]">{error}</p>;
  if (domains === null) return <Loader2 className="h-4 w-4 animate-spin text-[var(--muted-foreground)]" />;
  if (domains.length === 0) return <p className="text-[13px] text-[var(--muted-foreground)]">{t("No domains yet.")}</p>;

  const edgeOf = (e: GraphEdge) => {
    const a = pos.get(e.source_id);
    const b = pos.get(e.target_id);
    if (!a || !b) return null;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const x1 = a.x + (dx / len) * R;
    const y1 = a.y + (dy / len) * R;
    const x2 = b.x - (dx / len) * (R + 6);
    const y2 = b.y - (dy / len) * (R + 6);
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const props = fmtProps(e.properties);
    const active = selectedId === e.source_id || selectedId === e.target_id;
    return (
      <g key={e.id} opacity={selectedId && !active ? 0.25 : 1}>
        <line
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke={relColor(e.type)}
          strokeOpacity={e.implicit ? 0.55 : 0.9}
          strokeWidth={e.implicit ? 1.2 : 1.8}
          strokeDasharray={e.implicit ? "4 4" : undefined}
          markerEnd={`url(#kg-arrow-${e.type})`}
        />
        <text x={mx} y={my - 4} fontSize={11} fontWeight={600} textAnchor="middle" fill={relColor(e.type)} stroke="var(--background)" strokeWidth={4} paintOrder="stroke">
          {e.type}
        </text>
        {props && (
          <text x={mx} y={my + 10} fontSize={10} textAnchor="middle" fill="var(--muted-foreground)" stroke="var(--background)" strokeWidth={4} paintOrder="stroke">
            {props}
          </text>
        )}
      </g>
    );
  };

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <select className={FIELD} value={domainId} onChange={(e) => setDomainId(e.target.value)}>
          {domains.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} ({d.nodes})
            </option>
          ))}
        </select>
        <div className="flex flex-wrap gap-1" role="group" aria-label={t("Relation types")}>
          {relTypes.map(([type, count]) => {
            const on = !hidden.has(type);
            return (
              <button
                key={type}
                type="button"
                aria-pressed={on}
                title={on ? t("Hide {{type}} links", { type }) : t("Show {{type}} links", { type })}
                onClick={() =>
                  setHidden((prev) => {
                    const next = new Set(prev);
                    if (next.has(type)) next.delete(type);
                    else next.add(type);
                    return next;
                  })
                }
                className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[11px] transition ${
                  on ? "border-[var(--border)] bg-[var(--card)] text-[var(--foreground)]" : "border-dashed border-[var(--border)] text-[var(--muted-foreground)] opacity-60"
                }`}
              >
                <span className="inline-block h-2 w-3 rounded-sm" style={{ background: relColor(type) }} />
                {type} <span className="text-[var(--muted-foreground)]">{count}</span>
              </button>
            );
          })}
        </div>
        <div className="ml-auto flex gap-1">
          <button type="button" className={ICON_BTN} aria-label={t("Zoom out")} onClick={() => zoom(1 / 1.2)}>
            <Minus size={14} />
          </button>
          <button type="button" className={ICON_BTN} aria-label={t("Zoom in")} onClick={() => zoom(1.2)}>
            <Plus size={14} />
          </button>
          <button type="button" className={ICON_BTN} aria-label={t("Fit to view")} onClick={fit}>
            <Maximize2 size={14} />
          </button>
        </div>
      </div>
      <div
        ref={box}
        className="relative h-[65vh] min-h-[360px] cursor-grab overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--background)] active:cursor-grabbing"
        onPointerDown={(e) => {
          if ((e.target as Element).closest("[data-node]")) return;
          drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
          (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (d) setView((v) => ({ ...v, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y }));
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onClick={(e) => {
          if (!(e.target as Element).closest("[data-node]")) setSelectedId(null);
        }}
      >
        {!graph ? (
          <Loader2 className="m-4 h-4 w-4 animate-spin text-[var(--muted-foreground)]" />
        ) : (
          <svg width="100%" height="100%" role="img" aria-label={t("Knowledge graph diagram")}>
            <defs>
              {relTypes.map(([type]) => (
                <marker key={type} id={`kg-arrow-${type}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill={relColor(type)} />
                </marker>
              ))}
            </defs>
            <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
              {edges.map(edgeOf)}
              {placed.map((n) => {
                const lines = wrap(n.title);
                const active = selectedId === n.id;
                return (
                  <g
                    key={n.id}
                    data-node
                    className="cursor-pointer"
                    transform={`translate(${n.x} ${n.y})`}
                    onClick={() => setSelectedId(active ? null : n.id)}
                  >
                    <title>{`${n.title} (${n.label})`}</title>
                    <circle r={R} fill={colors.get(n.label) ?? FALLBACK_COLOR} stroke={active ? "var(--primary)" : "#00000055"} strokeWidth={active ? 3 : 1.2} />
                    <text y={-10 - (lines.length - 1) * 6} fontSize={11} fontWeight={700} fontStyle="italic" textAnchor="middle" fill="#1d1d1f">
                      {n.label}
                    </text>
                    {lines.map((line, i) => (
                      <text key={i} y={6 + i * 13 - (lines.length - 1) * 6} fontSize={11} textAnchor="middle" fill="#1d1d1f">
                        {line}
                      </text>
                    ))}
                  </g>
                );
              })}
            </g>
          </svg>
        )}
      </div>
      {selected && graph && (
        <div className="mt-3 rounded-xl border border-[var(--border)] p-3 text-[13px]">
          <div className="font-medium">
            {selected.title} <span className="font-normal text-[var(--muted-foreground)]">· {selected.label}</span>
          </div>
          {fmtProps(selected.properties) && (
            <p className="mt-1 text-[12px] text-[var(--muted-foreground)]">{fmtProps(selected.properties)}</p>
          )}
          {selected.summary && <p className="mt-1 text-[12px]">{selected.summary}</p>}
          <ul className="mt-2 space-y-0.5 font-mono text-[11px]">
            {graph.edges
              .filter((e) => e.source_id === selected.id || e.target_id === selected.id)
              .map((e) => (
                <li key={e.id} className={e.implicit ? "text-[var(--muted-foreground)]" : ""}>
                  ({pos.get(e.source_id)?.title}) -[<span style={{ color: relColor(e.type) }}>{e.type}</span>]-&gt; ({pos.get(e.target_id)?.title})
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}
