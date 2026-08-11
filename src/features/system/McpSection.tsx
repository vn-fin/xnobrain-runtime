import { useEffect, useMemo, useRef, useState } from 'react';
import { Braces, RefreshCw, Save } from 'lucide-react';
import { agentsApi } from '../../api/agents';
import type { Agent } from '../../types';

const EMPTY_CONFIG = '{}';
const STDIO_EXAMPLE = JSON.stringify({
  filesystem: {
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem', '/path/to/folder'],
  },
}, null, 2);
const HTTP_EXAMPLE = JSON.stringify({
  docs: {
    url: 'https://your-mcp-server.example.com/mcp',
    headers: { Authorization: '${MCP_API_KEY}' },
  },
}, null, 2);

function containsRedaction(value: unknown): boolean {
  if (value === '***') return true;
  if (Array.isArray(value)) return value.some(containsRedaction);
  if (value && typeof value === 'object') {
    return Object.values(value as Record<string, unknown>).some(containsRedaction);
  }
  return false;
}

export function McpSection({ agents }: { agents: Agent[] }) {
  const [agentId, setAgentId] = useState(agents[0]?.id ?? '');
  const [editor, setEditor] = useState(EMPTY_CONFIG);
  const [status, setStatus] = useState<'idle' | 'loading' | 'saving'>('idle');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const loadRequest = useRef<{ id: string; request: ReturnType<typeof agentsApi.getMcp> }>();

  useEffect(() => {
    if (agents.some((agent) => agent.id === agentId)) return;
    setAgentId(agents[0]?.id ?? '');
  }, [agentId, agents]);

  const load = async (id: string) => {
    if (!id) {
      setEditor(EMPTY_CONFIG);
      return;
    }
    setStatus('loading');
    setError('');
    setSaved(false);
    const existing = loadRequest.current;
    const request = existing?.id === id ? existing.request : agentsApi.getMcp(id);
    if (!existing || existing.id !== id) loadRequest.current = { id, request };
    try {
      const config = await request;
      setEditor(JSON.stringify(config.servers ?? {}, null, 2));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load MCP configuration.');
    } finally {
      if (loadRequest.current?.request === request) loadRequest.current = undefined;
      setStatus('idle');
    }
  };

  useEffect(() => { void load(agentId); }, [agentId]);

  const serverCount = useMemo(() => {
    try {
      const parsed = JSON.parse(editor) as unknown;
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? Object.keys(parsed).length
        : 0;
    } catch {
      return 0;
    }
  }, [editor]);

  const save = async () => {
    setError('');
    setSaved(false);
    let servers: unknown;
    try {
      servers = JSON.parse(editor);
    } catch {
      setError('MCP servers must be valid JSON.');
      return;
    }
    if (!servers || typeof servers !== 'object' || Array.isArray(servers)) {
      setError('MCP servers must be a JSON object keyed by server name.');
      return;
    }
    if (containsRedaction(servers)) {
      setError('Replace redacted values (***) with ${ENV_VAR} references before saving.');
      return;
    }
    setStatus('saving');
    try {
      const config = await agentsApi.updateMcp(
        agentId,
        { servers: servers as Record<string, Record<string, unknown>> },
      );
      setEditor(JSON.stringify(config.servers ?? {}, null, 2));
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save MCP configuration.');
    } finally {
      setStatus('idle');
    }
  };

  const useExample = (example: string) => {
    setEditor(example);
    setSaved(false);
    setError('');
  };

  if (agents.length === 0) {
    return <div className="system-empty">Create an agent before configuring MCP servers.</div>;
  }

  return (
    <section className="system-card mcp-settings-card">
      <div className="system-card-title">
        <Braces size={18} />
        <div>
          <strong>Agent MCP servers</strong>
          <small>Saved to this agent's native config.yaml under mcp_servers.</small>
        </div>
        <span className="conn-badge">{serverCount} server{serverCount === 1 ? '' : 's'}</span>
      </div>
      <label className="mcp-agent-picker">
        Agent
        <select value={agentId} onChange={(event) => setAgentId(event.target.value)}>
          {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.title} ({agent.id})</option>)}
        </select>
      </label>
      <div className="mcp-editor-head">
        <div><strong>mcp_servers</strong><small>Choose one transport per server: local command or remote URL.</small></div>
        <button className="conn-btn ghost" disabled={status !== 'idle'} onClick={() => void load(agentId)}><RefreshCw size={14} />Reload</button>
      </div>
      <details className="mcp-help">
        <summary>What should I enter here?</summary>
        <div className="mcp-help-body">
          <p><code>command</code> + <code>args</code> starts a local stdio server. Use this when the MCP package is installed on the same machine.</p>
          <p><code>url</code> connects to a remote HTTP/SSE server. Put credentials in <code>headers</code> using an environment reference such as <code>${'{MCP_API_KEY}'}</code>.</p>
          <p><code>tools</code> is optional; omit it to expose all tools, or use <code>{'{"include":["search"]}'}</code> to limit the server.</p>
          <div className="mcp-example-actions">
            <button className="conn-btn ghost" onClick={() => useExample(STDIO_EXAMPLE)}>Insert stdio example</button>
            <button className="conn-btn ghost" onClick={() => useExample(HTTP_EXAMPLE)}>Insert HTTPS example</button>
          </div>
        </div>
      </details>
      <textarea
        className="mcp-json-editor"
        aria-label="MCP servers JSON"
        spellCheck={false}
        value={editor}
        onChange={(event) => { setEditor(event.target.value); setSaved(false); }}
      />
      <div className="mcp-settings-foot">
        <p>Keep credentials in profile environment variables and reference them as <code>${'{MCP_API_KEY}'}</code>. Secret values are never returned by the API.</p>
        <button className="conn-btn primary" disabled={!agentId || status !== 'idle'} onClick={() => void save()}><Save size={14} />{status === 'saving' ? 'Saving…' : 'Save MCP'}</button>
      </div>
      {status === 'loading' && <div className="system-note">Loading MCP configuration…</div>}
      {error && <div className="system-error" role="alert">{error}</div>}
      {saved && <div className="system-success" role="status">MCP configuration saved. New agent runs will use the updated servers.</div>}
    </section>
  );
}
