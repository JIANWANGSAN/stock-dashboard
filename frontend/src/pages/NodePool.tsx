import { useEffect, useMemo, useState } from "react";
import { Loader2, AlertCircle, RefreshCw, X } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { Dialog } from "@/components/workspace/Dialog";
import { api, type NodePoolData, type NodePoolNode, type NodePoolStock, type KlineData } from "@/lib/api";

const yi = (v: number | null) =>
  v == null ? "—" : `${v.toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 亿`;

const STATUS_STYLE: Record<string, string> = {
  "连板中": "text-danger border-danger/40 bg-danger/10",
  "首板": "text-muted-foreground border-border/50 bg-muted/40",
  "断板反包": "text-amber-500 border-amber-500/40 bg-amber-500/10",
  "已断板": "text-emerald-600 border-emerald-600/40 bg-emerald-600/10",
};

const TYPE_LABEL: Record<string, string> = {
  "最高标断板节点": "最高标断板",
  "突破节点": "突破",
  "穿越节点": "穿越",
};

/** A 股红涨绿跌；日K 同口径。 */
const UP = "#e11d48";
const DOWN = "#059669";

/** 触发票与节点票共有的属性 → 高亮（这是「同源信号」，不是推荐）。 */
type Shared = {
  /** 缩写里与触发票**共有的字母集合**（逐字母高亮，不是整块）。 */
  pinyinChars?: Set<string>;
  region?: boolean;
  industry?: boolean;
  concepts?: Set<string>;
};

function sharedWith(trigger: NodePoolNode["trigger"], s: NodePoolStock): Shared {
  const out: Shared = { concepts: new Set<string>() };
  if (!trigger) return out;
  // 缩写：逐字母求交集（大小写无关），交集非空才高亮对应字母
  if (s.pinyin && trigger.pinyin) {
    const tset = new Set(trigger.pinyin.toUpperCase());
    const common = new Set<string>();
    for (const ch of s.pinyin.toUpperCase()) if (tset.has(ch)) common.add(ch);
    if (common.size) out.pinyinChars = common;
  }
  if (s.region && trigger.region && s.region === trigger.region) out.region = true;
  if (s.industry && trigger.industry && s.industry === trigger.industry) out.industry = true;
  const tc = new Set(trigger.concepts ?? []);
  for (const c of s.concepts ?? []) if (tc.has(c)) out.concepts!.add(c);
  return out;
}

/** 标签小徽标：命中触发票同名属性时高亮（琥珀），否则常规灰。 */
function Tag({ text, hit, title }: { text: string; hit?: boolean; title?: string }) {
  return (
    <span
      title={title}
      className={
        hit
          ? "rounded border border-amber-500/60 bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-600"
          : "rounded border border-border/60 bg-muted/30 px-1.5 py-0.5 text-[10px] text-muted-foreground"
      }
    >
      {text}
    </span>
  );
}

/**
 * 拼音缩写徽标：**与触发票缩写共有的字母逐字高亮**（琥珀底+加粗），
 * 其余字母保持灰底。整块缩写相同的情况自然全字母高亮，无需另判。
 */
function PinyinTag({ text, common, self }: { text: string; common?: Set<string>; self?: boolean }) {
  // self=触发票自身：整块高亮（它就是基准）
  if (self) {
    return (
      <span
        title="拼音缩写（触发票）"
        className="rounded border border-amber-500/60 bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold tracking-wide text-amber-600"
      >
        {text}
      </span>
    );
  }
  const upper = new Set(text.toUpperCase());
  const hasCommon = !!common && common.size > 0;
  return (
    <span
      title={
        hasCommon
          ? `拼音缩写，与触发票共有字母：${[...common!].sort().join("")}`
          : "拼音缩写"
      }
      className="rounded border border-border/60 bg-muted/30 px-1.5 py-0.5 font-mono text-[10px] tracking-wide"
    >
      {[...text].map((ch, i) => {
        const on = hasCommon && upper.has(ch.toUpperCase()) && common!.has(ch.toUpperCase());
        return (
          <span
            key={`${ch}-${i}`}
            className={on ? "rounded bg-amber-500/30 font-bold text-amber-700" : "text-muted-foreground/70"}
          >
            {ch}
          </span>
        );
      })}
    </span>
  );
}

/** 缩写 / 地域 / 行业 / 概念 四件套，命中触发票同名属性时高亮（琥珀）。 */
function TagGroup({ sh, pinyin, region, industry, concepts, forceHit }: {
  sh?: Shared; pinyin?: string; region?: string; industry?: string; concepts?: string[]; forceHit?: boolean;
}) {
  const list = concepts ?? [];
  const hit = (k: string) => forceHit || !!sh?.concepts?.has(k);
  const any = !!(pinyin || region || industry || list.length);
  return (
    <span className="flex flex-wrap items-center gap-1">
      {pinyin && <PinyinTag text={pinyin} common={sh?.pinyinChars} self={forceHit} />}
      {region && <Tag text={region} hit={forceHit || sh?.region} title="所属地域" />}
      {industry && (
        <Tag text={industry} hit={forceHit || sh?.industry} title="所属行业" />
      )}
      {list.map((c) => (
        <Tag key={c} text={c} hit={hit(c)} title="所属概念" />
      ))}
      {!any && (
        <span className="text-xs text-muted-foreground/50">无标签</span>
      )}
    </span>
  );
}

/** 日K 蜡烛图（自绘 SVG，不引第三方图表库）。 */
function Candles({ rows }: { rows: { date: string; open: number; high: number; low: number; close: number; volume: number }[] }) {
  const W = 660, H = 260, PAD_B = 18;
  const hi = Math.max(...rows.map((r) => r.high));
  const lo = Math.min(...rows.map((r) => r.low));
  const span = hi - lo || 1;
  const n = rows.length;
  const bw = Math.max(2, Math.floor((W / n) * 0.62));
  const x = (i: number) => (W / n) * (i + 0.5);
  const y = (v: number) => PAD_B + (H - PAD_B * 2) * (1 - (v - lo) / span);
  const vmax = Math.max(...rows.map((r) => r.volume)) || 1;

  return (
    <svg viewBox={`0 0 ${W} ${H + 26}`} className="w-full" role="img" aria-label="日K走势">
      {rows.map((r, i) => {
        const up = r.close >= r.open;
        const c = up ? UP : DOWN;
        const top = y(Math.max(r.open, r.close));
        const bodyH = Math.max(1, Math.abs(y(r.open) - y(r.close)));
        return (
          <g key={r.date}>
            <line x1={x(i)} y1={y(r.high)} x2={x(i)} y2={y(r.low)} stroke={c} strokeWidth={1} />
            <rect x={x(i) - bw / 2} y={top} width={bw} height={bodyH} fill={up ? "none" : c} stroke={c} strokeWidth={1} />
            <rect
              x={x(i) - bw / 2}
              y={H + 4}
              width={bw}
              height={Math.max(1, (r.volume / vmax) * 18)}
              fill={c}
              opacity={0.45}
            />
          </g>
        );
      })}
      <text x={2} y={12} fontSize={10} fill="currentColor" opacity={0.6}>高 {hi.toFixed(2)}</text>
      <text x={2} y={H - 4} fontSize={10} fill="currentColor" opacity={0.6}>低 {lo.toFixed(2)}</text>
      <text x={W - 4} y={H + 24} fontSize={10} fill="currentColor" opacity={0.6} textAnchor="end">
        {rows[0]?.date} ~ {rows[n - 1]?.date}
      </text>
    </svg>
  );
}

/** 点击股票名弹出的日K 面板。 */
function KlineDialog({ code, name, onClose }: { code: string; name: string; onClose: () => void }) {
  const [data, setData] = useState<KlineData | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr(null);
    api
      .stockKline(code, 60)
      .then((d) => { if (alive) setData(d); })
      .catch((e: unknown) => { if (alive) setErr(e instanceof Error ? e.message : "日K读取失败"); });
    return () => { alive = false; };
  }, [code]);

  const rows = data?.kline ?? [];
  const last = rows[rows.length - 1];
  const prev = rows[rows.length - 2];
  const chg = last && prev ? ((last.close / prev.close - 1) * 100) : null;

  return (
    <Dialog titleId="kline-title" close={onClose} className="max-w-2xl">
      <div className="p-4">
        <div className="mb-3 flex flex-wrap items-baseline gap-2">
          <h2 id="kline-title" className="text-base font-bold">{name} 日K</h2>
          <span className="font-mono text-xs text-muted-foreground">{code}</span>
          {last && (
            <>
              <span className="font-mono text-sm font-bold" style={{ color: (chg ?? 0) >= 0 ? UP : DOWN }}>
                {last.close.toFixed(2)}
              </span>
              {chg != null && (
                <span className="font-mono text-xs" style={{ color: chg >= 0 ? UP : DOWN }}>
                  {chg >= 0 ? "+" : ""}{chg.toFixed(2)}%
                </span>
              )}
            </>
          )}
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭日K"
            className="ml-auto inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border/70 text-muted-foreground transition hover:border-primary/60 hover:text-primary"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {last && (
          <p className="-mt-2 mb-2 text-xs text-muted-foreground">
            {data?.days ?? rows.length} 个交易日 · 腾讯日K源
          </p>
        )}

        {!data && !err && (
          <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 日K加载中…
          </div>
        )}
        {err && <p role="alert" className="py-6 text-sm text-danger">日K读取失败：{err}</p>}
        {data && !data.available && (
          <p className="py-6 text-sm text-muted-foreground">{data.reason || "日K不可用"}</p>
        )}
        {data && data.available && rows.length > 0 && (
          <>
            <Candles rows={rows} />
            <div className="mt-2 max-h-40 overflow-y-auto rounded-lg border border-border/40">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-card">
                  <tr className="text-left text-muted-foreground">
                    <th className="px-2 py-1 font-medium">日期</th>
                    <th className="px-2 py-1 text-right font-medium">开</th>
                    <th className="px-2 py-1 text-right font-medium">高</th>
                    <th className="px-2 py-1 text-right font-medium">低</th>
                    <th className="px-2 py-1 text-right font-medium">收</th>
                    <th className="px-2 py-1 text-right font-medium">量(手)</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {[...rows].reverse().slice(0, 30).map((r) => (
                    <tr key={r.date} className="border-t border-border/30">
                      <td className="px-2 py-1 text-muted-foreground">{r.date}</td>
                      <td className="px-2 py-1 text-right">{r.open.toFixed(2)}</td>
                      <td className="px-2 py-1 text-right">{r.high.toFixed(2)}</td>
                      <td className="px-2 py-1 text-right">{r.low.toFixed(2)}</td>
                      <td className="px-2 py-1 text-right" style={{ color: r.close >= r.open ? UP : DOWN }}>
                        {r.close.toFixed(2)}
                      </td>
                      <td className="px-2 py-1 text-right text-muted-foreground">
                        {Math.round(r.volume).toLocaleString("zh-CN")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground/60">
              仅客观历史行情，不含买卖建议。红涨绿跌按 A 股惯例。
            </p>
          </>
        )}
      </div>
    </Dialog>
  );
}

function StockRow({ s, sh, onPick }: { s: NodePoolStock; sh: Shared; onPick: () => void }) {
  const st = STATUS_STYLE[s.status] || STATUS_STYLE["首板"];
  return (
    <tr className="border-b border-border/30">
      <td className="px-2 py-1.5">
        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
          <button
            type="button"
            onClick={onPick}
            title={`查看 ${s.name} 日K`}
            className="whitespace-nowrap font-medium underline-offset-2 hover:text-primary hover:underline"
          >
            {s.name}
          </button>
          <span className="whitespace-nowrap text-xs text-muted-foreground/50">{s.code}</span>
          {s.is_yizi && (
            <span className="whitespace-nowrap rounded bg-rose-500/15 px-1 text-[10px] text-rose-500">一字</span>
          )}
          <TagGroup
            sh={sh}
            pinyin={s.pinyin}
            region={s.region}
            industry={s.industry}
            concepts={s.concepts}
          />
        </div>
      </td>
      <td className="px-2 py-1.5 font-mono">{s.boards}板</td>
      <td className="px-2 py-1.5">
        <span className={`rounded border px-1.5 py-0.5 text-[11px] ${st}`}>{s.status}</span>
      </td>
      <td className="whitespace-nowrap px-2 py-1.5 font-mono text-muted-foreground">{yi(s.total_cap_yi)}</td>
      <td className="whitespace-nowrap px-2 py-1.5 font-mono text-muted-foreground">{s.first_seal || "—"}</td>
    </tr>
  );
}

function NodeCard({ n, onPick }: { n: NodePoolNode; onPick: (code: string, name: string) => void }) {
  const trig = n.trigger;
  return (
    <GlassCard className="p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
          {TYPE_LABEL[n.type] || n.type}
        </span>
        <span className="font-mono text-xs text-muted-foreground">{n.date}</span>
        {n.trigger_yin === true && (
          <span className="rounded-full bg-emerald-600/15 px-2 py-0.5 text-[11px] font-semibold text-emerald-600">
            减分
          </span>
        )}
        {n.top_related && (
          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-600">当前最高标血统</span>
        )}
        {trig && (
          <span className="ml-auto flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <span className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
              <button
                type="button"
                onClick={() => onPick(trig.code, trig.name)}
                title={`查看 ${trig.name} 日K`}
                className="whitespace-nowrap font-medium underline-offset-2 hover:text-primary hover:underline"
              >
                {trig.name}
              </button>
              <span className="whitespace-nowrap text-xs text-muted-foreground/60">{trig.code}</span>
              {typeof trig.today_lbc === "number" && (
                <span className="whitespace-nowrap text-xs text-muted-foreground">今日 {trig.today_lbc}板</span>
              )}
              {trig.is_yizi && (
                <span className="whitespace-nowrap rounded bg-rose-500/15 px-1 text-[10px] text-rose-500">一字</span>
              )}
              <TagGroup
                pinyin={trig.pinyin}
                region={trig.region}
                industry={trig.industry}
                concepts={trig.concepts}
                forceHit
              />
            </span>
          </span>
        )}
      </div>
      <p className="mb-2 text-xs text-muted-foreground">{n.desc}</p>
      {trig && (
        <p className="mb-2 text-xs text-muted-foreground">
          触发票市值：{yi(trig.total_cap_yi ?? null)}
          {n.volume_ratio != null && <>　断板倍量：{n.volume_ratio}×</>}
          {n.replaced?.length ? <>　甩下：{n.replaced.join("、")}</> : null}
        </p>
      )}
      <p className="mb-1.5 text-[11px] text-muted-foreground/70">
        缩写里与触发票<strong className="font-semibold text-amber-600">共有的字母</strong>逐字高亮（琥珀）· 地域/行业/概念相同者整块高亮 · 点击股票名看日K
        {!!n.broken_hidden && `　已隐藏已断板 ${n.broken_hidden} 只`}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
              <th className="px-2 py-1.5 font-medium">名称 / 缩写 · 地域 · 概念</th>
              <th className="px-2 py-1.5 font-medium">连板</th>
              <th className="px-2 py-1.5 font-medium">状态</th>
              <th className="px-2 py-1.5 font-medium">市值</th>
              <th className="px-2 py-1.5 font-medium">首封</th>
            </tr>
          </thead>
          <tbody>
            {n.stocks.map((s) => (
              <StockRow key={s.code} s={s} sh={sharedWith(trig, s)} onPick={() => onPick(s.code, s.name)} />
            ))}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}

export function NodePool() {
  const [data, setData] = useState<NodePoolData | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [kline, setKline] = useState<{ code: string; name: string } | null>(null);

  const load = (force = false) => {
    if (force) setRefreshing(true);
    api.nodePool(force)
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "节点票池读取失败"))
      .finally(() => {
        setLoaded(true);
        setRefreshing(false);
      });
  };
  useEffect(() => {
    load();
  }, []);

  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const pick = (code: string, name: string) => setKline({ code, name });

  return (
    <div>
      <PageHeader
        title="节点票池"
        subtitle="三类节点（最高标断板 / 突破 / 穿越）检测 + 当前最高标聚焦 · 已断板不显示 · 缩写共有字母/相同地域·行业·概念高亮 · 点击股票名看日K"
        actions={
          <button
            type="button"
            onClick={() => load(true)}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded-lg border border-primary/50 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-50"
          >
            <RefreshCw className={refreshing ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} /> 刷新
          </button>
        }
      />

      {data && data.available && (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            { label: "截至交易日", value: data.end_date || "—" },
            { label: "节点数", value: `${data.node_count ?? nodes.length}` },
            { label: "回看窗口", value: `${data.window_days ?? "—"} 交易日` },
            { label: "市值上限", value: `${data.cap_limit_yi ?? 200} 亿` },
          ].map((c) => (
            <GlassCard key={c.label} className="py-3 text-center">
              <div className="text-xs text-muted-foreground">{c.label}</div>
              <div className="mt-1 font-mono text-lg font-bold text-primary">{c.value}</div>
            </GlassCard>
          ))}
        </div>
      )}

      {!loaded ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
        </div>
      ) : error ? (
        <p role="alert" className="py-8 text-center text-sm text-danger">
          节点票池读取失败：{error}。请刷新重试。
        </p>
      ) : data && !data.available ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <AlertCircle className="h-4 w-4" /> {data.reason || "数据暂不可用"}
        </div>
      ) : nodes.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          近 {data?.keep_days ?? 10} 个交易日未检测到有票节点（已断板票不计入）
        </div>
      ) : (
        <div className="space-y-3">
          {nodes.map((n) => (
            <NodeCard key={n.id} n={n} onPick={pick} />
          ))}
        </div>
      )}

      {kline && <KlineDialog code={kline.code} name={kline.name} onClose={() => setKline(null)} />}

      <Disclaimer />
    </div>
  );
}