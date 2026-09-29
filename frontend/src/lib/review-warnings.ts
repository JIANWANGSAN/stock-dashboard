/** Describe persisted report gaps without confusing them with current AI health. */
export function reviewWarningMessages(warnings: string[]): string[] {
  const missingKey = (w: string) => /(?:没配问财接口密钥|未配置|缺少?)\s*IWENCAI_API_KEY/.test(w);
  const missingWencai = warnings.some(missingKey);
  const messages = warnings.flatMap(w => {
    if (missingKey(w)) return ['生成这份报告时未配置问财数据接入，因此缺少涨停题材与原因；相关题材分析不完整。'];
    // Only coalesce the exact legacy summary when its specific diagnosis is present.
    if (missingWencai && w === '题材串：数据缺失，本次复盘少了这一路') return [];
    return [w];
  });
  return [...new Set(messages)];
}
