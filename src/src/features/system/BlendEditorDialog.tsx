import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react';
import type { Blend, BlendModel, BlendStrategy } from '../../api/blends';

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const STRATEGIES: BlendStrategy[] = ['fallback', 'round-robin', 'fusion'];
const ACCENT = '#2f6f62';

const S = {
  overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60 } as CSSProperties,
  dialog: { width: 'min(560px, 94vw)', maxHeight: '90vh', overflow: 'auto', background: 'var(--panel, #1b1b1b)', color: 'inherit', border: '1px solid rgba(128,128,128,0.3)', borderRadius: 12, padding: 18 } as CSSProperties,
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 } as CSSProperties,
  label: { fontSize: 12, fontWeight: 600, opacity: 0.8, margin: '12px 0 4px' } as CSSProperties,
  input: { width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid rgba(128,128,128,0.32)', background: 'transparent', color: 'inherit', fontSize: 13 } as CSSProperties,
  btn: { padding: '6px 12px', borderRadius: 8, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer', fontSize: 13 } as CSSProperties,
  primary: { background: ACCENT, borderColor: ACCENT, color: '#fff' } as CSSProperties,
  iconbtn: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 24, borderRadius: 6, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer' } as CSSProperties,
  row: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' } as CSSProperties,
  muted: { fontSize: 12, opacity: 0.65 } as CSSProperties,
  err: { color: '#d06a52', fontSize: 12, marginTop: 8 } as CSSProperties,
  caveat: { fontSize: 12, opacity: 0.75, background: 'rgba(184,134,11,0.12)', border: '1px solid rgba(184,134,11,0.4)', borderRadius: 6, padding: '6px 8px', marginTop: 6 } as CSSProperties,
};

export function BlendEditorDialog({
  blend, loadModels, onSave, onClose,
}: {
  blend: Blend | null;
  loadModels: () => Promise<BlendModel[]>;
  onSave: (input: { name: string; models: string[]; strategy: BlendStrategy; judge_model: string | null; sticky_limit: number | null }) => Promise<void>;
  onClose: () => void;
}) {
  const [available, setAvailable] = useState<BlendModel[]>([]);
  const [name, setName] = useState(blend?.name ?? '');
  const [models, setModels] = useState<string[]>(blend?.models ?? []);
  const [strategy, setStrategy] = useState<BlendStrategy>(blend?.strategy ?? 'fallback');
  const [judge, setJudge] = useState<string>(blend?.judge_model ?? '');
  const [sticky, setSticky] = useState<string>(blend?.sticky_limit != null ? String(blend.sticky_limit) : '');
  const [addPick, setAddPick] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void loadModels().then(setAvailable).catch(() => setAvailable([])); }, [loadModels]);

  const unselected = useMemo(
    () => available.map((m) => m.id).filter((id) => !models.includes(id)),
    [available, models]);
  const nameValid = NAME_RE.test(name) && name.toLowerCase() !== 'auto';

  function move(index: number, dir: -1 | 1) {
    const target = index + dir;
    if (target < 0 || target >= models.length) return;
    const next = [...models];
    [next[index], next[target]] = [next[target], next[index]];
    setModels(next);
  }

  function canSave(): boolean {
    if (!nameValid || models.length < 1) return false;
    if (strategy === 'fusion' && (models.length < 2 || !judge)) return false;
    return true;
  }

  async function save() {
    setBusy(true); setError(null);
    try {
      await onSave({
        name,
        models,
        strategy,
        judge_model: strategy === 'fusion' ? (judge || null) : null,
        sticky_limit: strategy === 'round-robin' && sticky.trim() ? Number(sticky) : null,
      });
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save blend');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={S.overlay} onClick={onClose}>
      <div style={S.dialog} onClick={(e) => e.stopPropagation()}>
        <div style={S.head}>
          <strong>{blend ? 'Edit blend' : 'New blend'}</strong>
          <button style={S.iconbtn} onClick={onClose} aria-label="Close"><X size={15} /></button>
        </div>

        <div style={S.label}>Name</div>
        <input style={S.input} value={name} onChange={(e) => setName(e.target.value)} placeholder="research-blend" />
        {!nameValid && name.length > 0 && (
          <div style={S.muted}>Letters, digits, '.', '_', '-' (max 64); not "auto".</div>
        )}

        <div style={S.label}>Models (order = fallback order)</div>
        {models.length === 0 && <div style={S.muted}>No models yet — add at least one below.</div>}
        {models.map((id, index) => (
          <div style={S.row} key={id}>
            <span style={{ flex: 1, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{index + 1}. {id}</span>
            <button style={S.iconbtn} onClick={() => move(index, -1)} title="Up"><ArrowUp size={13} /></button>
            <button style={S.iconbtn} onClick={() => move(index, 1)} title="Down"><ArrowDown size={13} /></button>
            <button style={S.iconbtn} onClick={() => setModels(models.filter((m) => m !== id))} title="Remove"><X size={13} /></button>
          </div>
        ))}
        <div style={{ ...S.row, marginTop: 4 }}>
          <select style={{ ...S.input, flex: 1 }} value={addPick} onChange={(e) => setAddPick(e.target.value)}>
            <option value="">Add a model…</option>
            {unselected.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
          <button style={S.btn} disabled={!addPick || models.length >= 24}
            onClick={() => { if (addPick) { setModels([...models, addPick]); setAddPick(''); } }}>
            <Plus size={13} /> Add
          </button>
        </div>

        <div style={S.label}>Strategy</div>
        <div style={{ display: 'flex', gap: 12 }}>
          {STRATEGIES.map((value) => (
            <label key={value} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 13, cursor: 'pointer' }}>
              <input type="radio" name="strategy" checked={strategy === value} onChange={() => setStrategy(value)} />
              {value}
            </label>
          ))}
        </div>

        {strategy === 'round-robin' && (
          <>
            <div style={S.label}>Sticky limit (applies to all round-robin blends)</div>
            <input style={{ ...S.input, width: 120 }} type="number" min={1} value={sticky} onChange={(e) => setSticky(e.target.value)} />
          </>
        )}
        {strategy === 'fusion' && (
          <>
            <div style={S.label}>Judge model</div>
            <select style={S.input} value={judge} onChange={(e) => setJudge(e.target.value)}>
              <option value="">Choose a judge model…</option>
              {available.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
            </select>
            <div style={S.caveat}>Fusion runs every model in the blend on each request (higher cost); tools are disabled for fusion.</div>
          </>
        )}

        {error && <div style={S.err}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <button style={S.btn} onClick={onClose} disabled={busy}>Cancel</button>
          <button style={{ ...S.btn, ...S.primary }} onClick={() => void save()} disabled={busy || !canSave()}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
