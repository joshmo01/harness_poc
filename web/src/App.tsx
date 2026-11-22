import {useCallback, useEffect, useRef, useState} from 'react';
import {A2uiSurface} from '@a2ui/react';
import {MessageProcessor} from '@a2ui/web_core';
import {lendingCatalog} from './catalog';

type Persona = {id: string; name: string; role: string; branch: string};
type Step = {kind: string; title: string; detail: Record<string, any>};
type Turn = {who: 'user' | 'agent' | 'system'; text: string; surfaces: string[]; steps: Step[]; ctx?: any};
type RecordRow = Record<string, any>;

const PROMPTS: Record<string, string[]> = {
  underwriter: ['Assess case 1042', 'Analyse the bank statement for case 1042', 'Approve case 1042'],
  credit_manager: ['Assess case 1042', 'Approve case 1042', 'Assess case 1017'],
  field_officer: ['Assess case 1033', 'Assess case 1042', 'Log a promise to pay of 12000 for case 1033'],
};

async function* sse(url: string, body: unknown) {
  const res = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const {value, done} = await reader.read();
    if (done) break;
    buf += dec.decode(value, {stream: true});
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const ev = /event: (.*)/.exec(chunk)?.[1] ?? 'message';
      const data = /data: (.*)/s.exec(chunk)?.[1];
      if (data) yield {ev, data: JSON.parse(data)};
    }
  }
}

function md(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith('**') ? <b key={i}>{p.slice(2, -2)}</b> : p));
}

export default function App() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [persona, setPersona] = useState('underwriter');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<'trace' | 'record' | 'registry'>('trace');
  const [records, setRecords] = useState<RecordRow[]>([]);
  const [registry, setRegistry] = useState<any>(null);
  const [tamper, setTamper] = useState(false);
  const [toast, setToast] = useState<{ok: boolean; text: string} | null>(null);
  const [, force] = useState(0);
  const personaRef = useRef(persona);
  personaRef.current = persona;
  const endRef = useRef<HTMLDivElement>(null);

  const refreshAudit = useCallback(async () => {
    setRecords(await (await fetch('/api/records')).json());
    setRegistry(await (await fetch('/api/registry')).json());
  }, []);

  const [processor] = useState(
    () =>
      new MessageProcessor([lendingCatalog as any], async (action: any) => {
        const r = await (
          await fetch('/api/action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({persona: personaRef.current, action}),
          })
        ).json();
        setToast({ok: r.ok, text: r.ok ? r.message : `Denied: ${r.reason_code} · ${r.message}`});
        setTurns(t => [...t, {who: 'system', text: r.ok ? `✅ ${r.message}` : `⛔ ${r.message}`, surfaces: [], steps: []}]);
        refreshAudit();
      }),
  );

  useEffect(() => {
    fetch('/api/personas').then(r => r.json()).then(setPersonas);
    refreshAudit();
    const subs = [processor.onSurfaceCreated(() => force(x => x + 1)), processor.onSurfaceDeleted(() => force(x => x + 1))];
    return () => subs.forEach(s => s.unsubscribe());
  }, [processor, refreshAudit]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    endRef.current?.scrollIntoView({block: 'end'});
  }, [turns]);

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setInput('');
    setTab('trace');
    setTurns(t => [...t, {who: 'user', text, surfaces: [], steps: []}, {who: 'agent', text: '', surfaces: [], steps: []}]);
    const patch = (f: (t: Turn) => Turn) => setTurns(ts => [...ts.slice(0, -1), f(ts[ts.length - 1])]);
    try {
      for await (const {ev, data} of sse('/api/chat', {persona, message: text})) {
        if (ev === 'step') patch(t => ({...t, steps: [...t.steps, data]}));
        else if (ev === 'text') patch(t => ({...t, text: (t.text ? t.text + '\n\n' : '') + data.text}));
        else if (ev === 'context') patch(t => ({...t, ctx: data}));
        else if (ev === 'a2ui') {
          processor.processMessages(data.messages);
          const ids = data.messages.filter((m: any) => m.createSurface).map((m: any) => m.createSurface.surfaceId);
          patch(t => ({...t, surfaces: [...t.surfaces, ...ids]}));
        }
      }
    } finally {
      setBusy(false);
      refreshAudit();
    }
  };

  const toggleTamper = async () => {
    const r = await (
      await fetch('/api/admin/tamper', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: !tamper})})
    ).json();
    setTamper(r.enabled);
    setTurns(t => [
      ...t,
      {
        who: 'system',
        text: r.enabled
          ? '🧪 Demo: the bureau MCP server now advertises a changed tool description (with an injected instruction).'
          : '🧪 Demo: bureau tool description restored.',
        surfaces: [],
        steps: [],
      },
    ]);
    refreshAudit();
  };

  const resetDemo = async () => {
    await fetch('/api/admin/reset', {method: 'POST'});
    setTamper(false);
    setTurns([]);
    refreshAudit();
  };

  const last = [...turns].reverse().find(t => t.who === 'agent');
  const me = personas.find(p => p.id === persona);

  return (
    <div className="app">
      <header>
        <div className="brand">
          Yutrix <span>· Governed lending agent POC</span>
        </div>
        <div className="personas">
          {personas.map(p => (
            <button key={p.id} className={p.id === persona ? 'on' : ''} onClick={() => setPersona(p.id)}>
              {p.name}
              <small>{p.branch}</small>
            </button>
          ))}
        </div>
        <label className="tamper">
          <input type="checkbox" checked={tamper} onChange={toggleTamper} /> Tamper bureau tool
        </label>
        <button className="reset" onClick={resetDemo} title="Regenerate synthetic data and start a new decision record">
          Reset demo
        </button>
      </header>

      <main>
        <section className="chat">
          {turns.length === 0 && (
            <div className="empty">
              <h3>Scripted mode · synthetic data</h3>
              <p>
                You are <b>{me?.name}</b>. Every tool call passes the gateway (registry, pins, Cedar) and lands in the
                decision record. Try a prompt below.
              </p>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className={`turn ${t.who}`}>
              {t.text && <div className="bubble">{t.text.split('\n\n').map((p, j) => <p key={j}>{md(p)}</p>)}</div>}
              {t.who === 'agent' && !t.text && busy && i === turns.length - 1 && <div className="bubble muted">Working…</div>}
              {t.surfaces.map(id => {
                const s = processor.model.surfacesMap.get(id);
                return s ? (
                  <div key={id} className="surface">
                    <A2uiSurface surface={s as any} />
                  </div>
                ) : null;
              })}
            </div>
          ))}
          <div ref={endRef} />
          <div className="composer">
            <div className="chips">
              {(PROMPTS[persona] ?? []).map(p => (
                <button key={p} onClick={() => send(p)} disabled={busy}>
                  {p}
                </button>
              ))}
            </div>
            <form onSubmit={e => (e.preventDefault(), send(input))}>
              <input value={input} onChange={e => setInput(e.target.value)} placeholder="Ask the agent about a case…" />
              <button disabled={busy}>Send</button>
            </form>
          </div>
        </section>

        <aside>
          <nav>
            {(['trace', 'record', 'registry'] as const).map(k => (
              <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>
                {k === 'trace' ? 'Agent trace' : k === 'record' ? 'Decision record' : 'Registry & pins'}
              </button>
            ))}
          </nav>

          {tab === 'trace' && (
            <div className="panel">
              {last?.ctx && (
                <div className="ctx">
                  <b>Agent context</b>: 3 meta-tools + {last.ctx.schemas_loaded} of {last.ctx.tools_available} tool schemas
                  loaded ({last.ctx.meta_tool_chars + last.ctx.loaded_schema_chars} chars)
                </div>
              )}
              {(last?.steps ?? []).map((s, i) => (
                <div key={i} className={`step ${s.kind}`}>
                  <div className="step-title">
                    <span className="kind">{s.kind}</span> {s.title}
                  </div>
                  {s.kind === 'code' ? (
                    <details>
                      <summary>
                        script · {s.detail.output_chars} chars returned · SDK {s.detail.sdk?.join(', ')}
                      </summary>
                      <pre>{s.detail.script}</pre>
                    </details>
                  ) : (
                    <pre>{JSON.stringify(s.detail, null, 1)}</pre>
                  )}
                </div>
              ))}
              {!last && <p className="muted">Steps the agent takes appear here.</p>}
            </div>
          )}

          {tab === 'record' && (
            <div className="panel">
              <button className="refresh" onClick={refreshAudit}>
                Refresh
              </button>
              {records.map(r => (
                <div key={r.id} className={`rec-row ${r.kind} ${r.decision === 'DENY' || r.decision === 'HIDDEN' ? 'bad' : ''}`}>
                  <div>
                    <span className="kind">{r.kind}</span> {r.tool ?? ''} <b>{r.decision}</b>{' '}
                    {r.reason && <code>{r.reason}</code>}
                  </div>
                  <div className="muted small">
                    {r.ts?.slice(11, 19)} · case {r.case_id ?? '—'} · {r.actor ?? '—'} ({r.role ?? '—'}) · {r.agent ?? ''}{' '}
                    {r.via ? `· via ${r.via}` : ''} · trace {r.trace_id}
                  </div>
                </div>
              ))}
            </div>
          )}

          {tab === 'registry' && registry && (
            <div className="panel">
              <p className="muted small">Cedar policies: {registry.policies.join(', ')}</p>
              <table>
                <tbody>
                  {Object.entries(registry.registry.tools).map(([name, t]: any) => (
                    <tr key={name}>
                      <td>
                        <code>{name}</code>
                      </td>
                      <td>{t.tier}</td>
                      <td className="small">{t.allowed_agents.join(', ')}</td>
                      <td className={registry.pin_status[name] === 'ok' ? 'ok' : 'bad'}>{registry.pin_status[name]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </aside>
      </main>
      {toast && (
        <div className={`toast ${toast.ok ? 'ok' : 'bad'}`} onClick={() => setToast(null)}>
          {toast.text}
        </div>
      )}
    </div>
  );
}
