import { authHeaders } from "@/lib/api";
import { apiUrl } from "@/lib/base";

export type AgentConnection = { provider: string; model: string; baseURL: string; apiKey: string; verifiedAt?: number };
const key = "astock-agent-connection";
// Verification metadata is opt-in for the status display, never a model request field.
export function loadAgentConnection(includeVerification = false): AgentConnection | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value && ["openai", "mimo", "codex-private", "claude", "codebuddy", "api-compatible"].includes(value.provider)
      && typeof value.model === "string" && typeof value.baseURL === "string" && typeof value.apiKey === "string" ? {provider:value.provider,model:value.model,baseURL:value.baseURL,apiKey:value.apiKey,
        ...(includeVerification && typeof value.verifiedAt === 'number' && Number.isFinite(value.verifiedAt) && value.verifiedAt > 0 && value.verifiedAt <= Date.now() ? {verifiedAt:value.verifiedAt} : {})} : null;
  } catch { return null; }
}
export function saveAgentConnection(value: AgentConnection) { localStorage.setItem(key, JSON.stringify(value)); window.dispatchEvent(new Event("astock-connection-changed")); }
export class AgentRequestError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export async function agentRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(`/api/review-agent${path}`), {
    method: body === undefined ? "GET" : "POST", signal,
    headers: { ...authHeaders(), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new AgentRequestError(response.status === 401 ? "请在接入 AI 页面填写后端访问密钥（VR_API_KEY）" : (data.detail || data.error || "请求未完成"), response.status);
  return data as T;
}
