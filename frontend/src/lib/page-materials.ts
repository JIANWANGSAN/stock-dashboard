export function pageMaterials(blocks: Record<string, unknown>): string {
  return "仅依据下列页面材料；各块按自身交易日/时点解释，不混用分母。null或空列表表示当前页面未提供，不等于零。摘录不含完整市场或历史，不补造资料。\n" +
    "回答用中文字段名称，不展示接口键名；金额缺少单位时不要引用该金额。只比较材料中的明确数值，不从开盘价、日内高点推断触板发生时刻；不从单日数据推断资金关注增强。控制在约400字，先事实后缺口。\n" +
    "字段读法：市场概览sentiment来自乐咕乐股，zt/dt为其涨停/跌停，zt_real/dt_real为其自定义真实涨停/跌停，active是源站算法，不能推出成交或资金活跃。已收盘情绪来自东财池，zt_count=涨停家数，dt_count=跌停家数，zb_count=炸板未回封家数，不是涨跌停并存。两个来源计数不一致时分别呈现，不挑一个当统一真值。sectors.net/inflow/outflow的资金类别未明确，不能称主力净流入或据此认定承接；只有单日值不能判断放量缩量。\n" +
    Object.entries(blocks).slice(0,9).map(([name, data]) => {
      const text = JSON.stringify(data) ?? "未提供";
      return `${name}：${text.slice(0,700)}${text.length > 700 ? "\n[本块仅提供前700字符；其余未提供，不推断缺失内容]" : ""}`;
    }).join("\n");
}
