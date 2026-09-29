// 关注股票（自选股）—— 只存本地 localStorage，不上传、不进仓库。
// 行情复用 /api/quote；复盘时把关注股行情一并喂给用户自己的 AI。

const KEY = "vr-watchlist";

export function loadWatch(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v.filter((c) => /^\d{6}$/.test(c)) : [];
  } catch {
    return [];
  }
}

export function saveWatch(codes: string[]) {
  if (codes.length > 100 && codes.length >= loadWatch().length) throw new Error("自选最多100只，请先删减后再保存；已有列表未变更");
  try { localStorage.setItem(KEY, JSON.stringify(codes)); }
  catch { throw new Error("自选未保存：浏览器存储不可用或空间不足，请检查站点存储权限"); }
}

// 从任意文本里抽取 6 位 A 股代码（逗号 / 空格 / 换行 / 顿号分隔都行，方便一次粘贴一串）。
export function parseCodes(raw: string): string[] {
  const tokens = raw.split(/[^\d]+/).filter(Boolean);
  return Array.from(new Set(tokens.filter((t) => /^\d{6}$/.test(t))));
}

// 把用户输入的一串代码并入已有自选，返回去重后的新列表 + 实际新增数量。
export function addCodes(existing: string[], raw: string): { next: string[]; added: number } {
  const incoming = parseCodes(raw).filter((c) => !existing.includes(c));
  if (existing.length + incoming.length > 100) throw new Error("自选最多100只，请分批添加或先删减；本次未添加");
  return { next: [...existing, ...incoming], added: incoming.length };
}
