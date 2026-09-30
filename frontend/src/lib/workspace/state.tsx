import { sourceLabel } from '@/lib/ai-access';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { agentRequest, loadAgentConnection } from '@/lib/agent-api';
import { connectionLabel, subscriptionStatus, type ConnectionStatus } from './status';
const modeKey = 'astock-workspace-agent-v1';
export function readMode(): boolean { try { return localStorage.getItem(modeKey) === 'on'; } catch { return false; } }
type State = { status: ConnectionStatus; name: string; label: string; previouslyTested: boolean; enabled: boolean; error: string; refresh: () => void; toggle: () => void; connect: () => void; closeConnect: () => void; setupOpen: boolean };
const Context = createContext<State | null>(null);
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnectionStatus>('checking');
  const [name, setName] = useState('');
  const [previouslyTested, setPreviouslyTested] = useState(false);
  const [enabled, setEnabled] = useState(readMode);
  const [setupOpen, setSetupOpen] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const update = () => setRevision(x => x + 1);
    window.addEventListener('astock-connection-changed', update);
    window.addEventListener('storage', update);
    return () => { window.removeEventListener('astock-connection-changed', update); window.removeEventListener('storage', update); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const saved = loadAgentConnection(true);
    setPreviouslyTested(!!saved?.verifiedAt);
    setEnabled(readMode()); setError('');
    if (!saved) {
      try { setStatus(localStorage.getItem('astock-agent-connection') ? 'error' : 'missing'); }
      catch { setStatus('error'); }
      return;
    }
    setName(sourceLabel(saved));
    if (['claude', 'codebuddy'].includes(saved.provider)) {
      setStatus('checking');
      void agentRequest<{installed:boolean;authenticated:boolean;available:boolean}>(`/subscriptions/${saved.provider}`,undefined,controller.signal)
        .then(data=>{if(!controller.signal.aborted)setStatus(!data.installed?'uninstalled':!data.authenticated?'invalid':data.available?'authenticated':'error');})
        .catch(()=>{if(!controller.signal.aborted)setStatus('error');});
      return ()=>controller.abort();
    }
    if (saved.provider !== 'codex-private') { setStatus('saved'); return; }
    setStatus('checking');
    // Read-only account/catalog check. Never triggers a paid connection probe.
    void agentRequest<unknown>('/status', undefined, controller.signal)
      .then(data => { if (!controller.signal.aborted) setStatus(subscriptionStatus(data)); })
      .catch(() => { if (!controller.signal.aborted) setStatus('error'); });
    return () => controller.abort();
  }, [revision]);
  const toggle = () => {
    try { localStorage.setItem(modeKey, enabled ? 'off' : 'on'); setEnabled(!enabled); setError(''); }
    catch { setError('无法保存开关，请允许本地存储后重试。'); }
  };
  return <Context.Provider value={{ status, name, previouslyTested, label: connectionLabel(status, name, previouslyTested), enabled, error,
    refresh: () => setRevision(x => x + 1), toggle, connect: () => setSetupOpen(true), closeConnect: () => setSetupOpen(false), setupOpen }}>{children}</Context.Provider>;
}
export function useWorkspace() { const value = useContext(Context); if (!value) throw new Error('WorkspaceProvider missing'); return value; }
