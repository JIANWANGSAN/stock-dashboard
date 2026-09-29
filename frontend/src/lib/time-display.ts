export function beijingTime(value: string | null | undefined): string {
  if (!value) return '未注明';
  if (/^\d{14}$/.test(value)) return `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)} ${value.slice(8,10)}:${value.slice(10,12)}:${value.slice(12,14)} 北京时间`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) return value + '（原文时间）';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date) + ' 北京时间';
}

// Date-only contracts carry a day, not an instant. Compare calendar dates in
// Beijing time without inventing a source timezone or settlement hour.
export function closesWithin30Days(close: string, now = Date.now()): boolean {
  if (/^\d{4}-\d{2}-\d{2}$/.test(close)) {
    const parsed = Date.parse(`${close}T00:00:00Z`);
    if (!Number.isFinite(parsed) || new Date(parsed).toISOString().slice(0,10) !== close) return false;
    const today = new Date(now + 8*3600000).toISOString().slice(0,10);
    const days = (parsed - Date.parse(`${today}T00:00:00Z`)) / 86400000;
    return days >= 0 && days <= 30;
  }
  if (!/T.*(?:Z|[+-]\d{2}:?\d{2})$/.test(close)) return false;
  const stamp = Date.parse(close);
  return Number.isFinite(stamp) && stamp >= now && stamp <= now + 30*86400000;
}
