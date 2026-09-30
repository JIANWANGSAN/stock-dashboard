import { useEffect, useState } from "react";
import { Loader2, AlertCircle, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { api, type NodePoolData, type NodePoolNode, type NodePoolStock } from "@/lib/api";

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

function StockRow({ s }: { s: NodePoolStock }) {
  const st = STATUS_STYLE[s.status] || STATUS_STYLE["首板"];
  return (
    <tr className="border-b border-border/30">
      <td className="whitespace-nowrap px-2 py-1.5">
        <span className="font-medium">{s.name}</span>{" "}
        <span className="text-xs text-muted-foreground/50">{s.code}</span>
        {s.is_yizi && (
          <span className="ml-1 rounded bg-rose-500/15 px-1 text-[10px] text-rose-500">一字</span>
        )}
      </td>
      <td className="px-2 py-1.5 font-mono">{s.boards}板</td>
      <td className="px-2 py-1.5">
        <span className={`rounded border px-1.5 py-0.5 text-[11px] ${st}`}>{s.status}</span>
      </td>
      <td className="whitespace-nowrap px-2 py-1.5 font-mono text-muted-foreground">{yi(s.total_cap_yi)}</td>
      <td className="whitespace-nowrap px-2 py-1.5 font-mono text-muted-foreground">{s.first_seal || "—"}</td>
      <td className="px-2 py-1.5 text-xs text-muted-foreground">{s.sector || "—"}</td>
    </tr>
  );
}

function NodeCard({ n }: { n: NodePoolNode }) {
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
          <span className="ml-auto text-sm">
            <span className="font-medium">{trig.name}</span>{" "}
            <span className="text-xs text-muted-foreground/60">{trig.code}</span>
            {typeof trig.today_lbc === "number" && (
              <span className="ml-1 text-xs text-muted-foreground">今日 {trig.today_lbc}板</span>
            )}
            {trig.is_yizi && (
              <span className="ml-1 rounded bg-rose-500/15 px-1 text-[10px] text-rose-500">一字</span>
            )}
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
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
              <th className="px-2 py-1.5 font-medium">名称</th>
              <th className="px-2 py-1.5 font-medium">连板</th>
              <th className="px-2 py-1.5 font-medium">状态</th>
              <th className="px-2 py-1.5 font-medium">市值</th>
              <th className="px-2 py-1.5 font-medium">首封</th>
              <th className="px-2 py-1.5 font-medium">行业</th>
            </tr>
          </thead>
          <tbody>
            {n.stocks.map((s) => <StockRow key={s.code} s={s} />)}
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

  const nodes = data?.nodes ?? [];

  return (
    <div>
      <PageHeader
        title="节点票池"
        subtitle="三类节点（最高标断板 / 突破 / 穿越）检测 + 当前最高标聚焦 · 创业板保留、剔除北交所/科创板/ST、市值≤200亿"
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
          近 {data?.keep_days ?? 10} 个交易日未检测到节点（非交易日或数据未更新）
        </div>
      ) : (
        <div className="space-y-3">
          {nodes.map((n) => (
            <NodeCard key={n.id} n={n} />
          ))}
        </div>
      )}

      <Disclaimer />
    </div>
  );
}
