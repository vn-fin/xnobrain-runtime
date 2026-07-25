import { useState, type CSSProperties } from 'react';
import { Layers, Pencil, Plus, Trash2 } from 'lucide-react';
import { useBlends } from '../../hooks/useBlends';
import type { Blend } from '../../api/blends';
import { BlendEditorDialog } from './BlendEditorDialog';

const S = {
  bar: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 } as CSSProperties,
  btn: { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 8, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer', fontSize: 13 } as CSSProperties,
  primary: { background: '#2f6f62', borderColor: '#2f6f62', color: '#fff' } as CSSProperties,
  row: { display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', border: '1px solid rgba(128,128,128,0.22)', borderRadius: 10, background: 'rgba(128,128,128,0.05)', marginBottom: 6 } as CSSProperties,
  chip: { fontSize: 10.5, padding: '1px 7px', borderRadius: 6, border: '1px solid rgba(128,128,128,0.35)', opacity: 0.85 } as CSSProperties,
  system: { fontSize: 10.5, padding: '1px 7px', borderRadius: 6, border: '1px solid #b8860b', color: '#b8860b' } as CSSProperties,
  iconbtn: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 26, borderRadius: 6, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer' } as CSSProperties,
  muted: { fontSize: 12.5, opacity: 0.65 } as CSSProperties,
  banner: { padding: '10px 12px', border: '1px solid rgba(180,70,47,0.5)', borderRadius: 8, background: 'rgba(180,70,47,0.08)', marginBottom: 10, fontSize: 13 } as CSSProperties,
};

export function BlendsSection() {
  const state = useBlends();
  const [editing, setEditing] = useState<Blend | null>(null);
  const [creating, setCreating] = useState(false);

  return (
    <div>
      <div style={S.bar}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Layers size={16} />
          <strong style={{ fontSize: 14 }}>Model Blends</strong>
        </div>
        <button style={{ ...S.btn, ...S.primary }} disabled={state.unavailable} onClick={() => setCreating(true)}>
          <Plus size={14} /> New blend
        </button>
      </div>
      <p style={{ ...S.muted, marginTop: 0 }}>
        A blend is a named group of models that behaves as one model — 9router routes
        each request through it (fallback, round-robin, or fusion).
      </p>

      {state.unavailable && <div style={S.banner}>9router is not reachable. Blends are unavailable.</div>}
      {state.status === 'error' && !state.unavailable && <div style={S.banner}>{state.error}</div>}
      {state.status === 'loading' && state.blends.length === 0 && <div style={S.muted}>Loading blends…</div>}
      {state.status === 'ready' && state.blends.length === 0 && (
        <div style={S.muted}>No blends yet.</div>
      )}

      {state.blends.map((blend) => (
        <div style={S.row} key={blend.id}>
          <strong style={{ fontSize: 13.5 }}>{blend.display_name}</strong>
          {blend.system
            ? <span style={S.system}>System</span>
            : <span style={S.chip}>{blend.strategy}</span>}
          <span style={S.muted}>{blend.models.length} model{blend.models.length === 1 ? '' : 's'}</span>
          <span style={{ flex: 1 }} />
          <button style={S.iconbtn} disabled={blend.read_only} title="Edit" onClick={() => setEditing(blend)}>
            <Pencil size={13} />
          </button>
          <button style={S.iconbtn} disabled={blend.read_only} title="Delete"
            onClick={() => {
              if (window.confirm(`Delete the blend "${blend.name}"?`)) void state.deleteBlend(blend.id);
            }}>
            <Trash2 size={13} />
          </button>
        </div>
      ))}

      {(creating || editing) && (
        <BlendEditorDialog
          blend={editing}
          loadModels={state.availableModels}
          onSave={async (input) => {
            if (editing) await state.updateBlend(editing.id, input);
            else await state.createBlend(input);
          }}
          onClose={() => { setCreating(false); setEditing(null); }}
        />
      )}
    </div>
  );
}
