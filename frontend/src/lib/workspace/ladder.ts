export function filterYesterdayTier<T extends { boards?: number }>(rows: T[], minimum: number): T[] {
  return rows.filter(row => typeof row.boards === 'number' && row.boards >= minimum);
}
