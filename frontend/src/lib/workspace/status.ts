export type ConnectionStatus = 'checking' | 'missing' | 'saved' | 'authenticated' | 'invalid' | 'uninstalled' | 'error';
/** Read-only status cannot establish a successful paid model connection. */
export function subscriptionStatus(value: unknown): ConnectionStatus {
  if (!value || typeof value !== 'object') return 'error';
  const data = value as Record<string, unknown>;
  if (typeof data.installed !== 'boolean' || typeof data.subscription_ready !== 'boolean' || typeof data.models_error !== 'string') return 'error';
  if (!data.installed) return 'uninstalled';
  if (!data.subscription_ready) return 'invalid';
  if (data.models_error) return 'error';
  return 'authenticated';
}
export function connectionLabel(status: ConnectionStatus, name: string, previouslyTested = false): string {
  if (status === 'checking') return '正在检测 AI 接入…';
  if (status === 'missing') return '未接入AI，请设置';
  if (status === 'error') return '无法确认 AI 状态，请检查设置';
  if (status === 'uninstalled') return '产品引擎未就绪，请检查安装';
  if (status === 'invalid') return `${name} 未登录或登录失效，请设置`;
  if (previouslyTested) return `${name}（此前连接实测通过）`;
  if (status === 'saved') return `已保存AI：${name}（待实测）`;
  return `已登录：${name}（连接待实测）`;
}
