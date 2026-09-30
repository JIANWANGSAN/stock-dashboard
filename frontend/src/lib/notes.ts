// 研究记录（沉淀）—— 把 AI 复盘 / 今日要点 / 问 AI 的结果存本地，形成个人投研记录。
// 只存本地 localStorage，不上传、不进仓库。对应投研框架第 7 层「沉淀」。

export interface Note {
  id: string;
  kind: string;   // 复盘 / 今日要点 / 问AI
  title: string;  // 如「每日复盘 2026-07-04」「AI 算力 今日要点」「问 AI · 600519」
  content: string; // markdown 正文
  ts: number;      // 保存时间戳(ms)
}

const KEY = "vr-notes";
// 不静默删除旧研究记录；配额不足时原记录保留，保存调用方显示错误。

export function loadNotes(): Note[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    if (!Array.isArray(v) || v.some(n => !n || typeof n.id !== "string" || typeof n.title !== "string" || typeof n.content !== "string" || typeof n.kind !== "string" || !Number.isFinite(n.ts))) throw new Error("研究记录格式不正确");
    return v;
  } catch {
    throw new Error("无法读取本机研究记录，原始数据未修改，请检查浏览器存储权限");
  }
}

function persist(notes: Note[]) {
  try { localStorage.setItem(KEY, JSON.stringify(notes)); }
  catch { throw new Error("本机存储空间不足或禁止保存，原记录未改动，请先复制本次结果"); }
}

// 新记录置顶。返回更新后的完整列表。
export function addNote(kind: string, title: string, content: string): Note[] {
  const note: Note = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    kind,
    title,
    content,
    ts: Date.now(),
  };
  const next = [note, ...loadNotes()];
  persist(next);
  return next;
}

export function deleteNote(id: string): Note[] {
  const next = loadNotes().filter((n) => n.id !== id);
  persist(next);
  return next;
}
