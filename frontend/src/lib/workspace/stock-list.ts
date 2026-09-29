export function mergeStockCodes(holdings: {code: string}[], watch: string[]) {
  return [...new Set([...holdings.map(row => row.code), ...watch])];
}
