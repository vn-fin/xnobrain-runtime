import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react';
import type {
  Blend,
  BlendCreateInput,
  BlendModel,
  BlendStrategy,
  ReasoningLevel,
  SmartRouteConfig,
  SmartRouteModel,
} from '../../api/blends';

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const STRATEGIES: { value: BlendStrategy; label: string }[] = [
  { value: 'fallback', label: 'Fallback' },
  { value: 'round-robin', label: 'Round robin' },
  { value: 'fusion', label: 'Fusion' },
  { value: 'smart-route', label: 'Smart route' },
];
const TIERS = ['quick', 'normal', 'difficult'] as const;
type Tier = (typeof TIERS)[number];
type RouteGroups = Record<Tier, SmartRouteModel[]>;

const TIER_COPY: Record<Tier, { title: string; help: string; tone: string }> = {
  quick: { title: 'Quick tasks', help: 'Short answers, summaries, formatting', tone: '#3e8e70' },
  normal: { title: 'Normal tasks', help: 'Writing, analysis, everyday coding', tone: '#b8860b' },
  difficult: { title: 'Difficult tasks', help: 'Architecture, advanced coding, deep analysis', tone: '#a65353' },
};
const REASONING: { value: ReasoningLevel; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'low', label: 'Fast' },
  { value: 'medium', label: 'Balanced' },
  { value: 'high', label: 'Deep' },
];
const ACCENT = '#2f6f62';

const S = {
  overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60 } as CSSProperties,
  dialog: { width: 'min(860px, 96vw)', maxHeight: '92vh', overflow: 'auto', background: 'var(--panel, #1b1b1b)', color: 'inherit', border: '1px solid rgba(128,128,128,0.3)', borderRadius: 12, padding: 18 } as CSSProperties,
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 } as CSSProperties,
  label: { fontSize: 12, fontWeight: 600, opacity: 0.8, margin: '12px 0 4px' } as CSSProperties,
  input: { width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid rgba(128,128,128,0.32)', background: 'transparent', color: 'inherit', fontSize: 13 } as CSSProperties,
  btn: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 5, padding: '6px 12px', borderRadius: 8, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer', fontSize: 13 } as CSSProperties,
  primary: { background: ACCENT, borderColor: ACCENT, color: '#fff' } as CSSProperties,
  iconbtn: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 24, borderRadius: 6, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer' } as CSSProperties,
  row: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' } as CSSProperties,
  muted: { fontSize: 12, opacity: 0.65 } as CSSProperties,
  err: { color: '#d06a52', fontSize: 12, marginTop: 8 } as CSSProperties,
  caveat: { fontSize: 12, opacity: 0.75, background: 'rgba(184,134,11,0.12)', border: '1px solid rgba(184,134,11,0.4)', borderRadius: 6, padding: '6px 8px', marginTop: 6 } as CSSProperties,
  tier: { border: '1px solid rgba(128,128,128,0.24)', borderRadius: 9, overflow: 'hidden', marginTop: 10 } as CSSProperties,
};

function initialGroups(blend: Blend | null): RouteGroups {
  const smart = blend?.smart_route;
  return {
    quick: smart?.quick.map((row) => ({ ...row })) ?? [],
    normal: smart?.normal.map((row) => ({ ...row })) ?? [],
    difficult: smart?.difficult.map((row) => ({ ...row })) ?? [],
  };
}

function formatContext(tokens?: number | null): string {
  if (!tokens) return 'Unknown';
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toFixed(1))}M`;
  return `${Math.round(tokens / 1_000)}K`;
}

export function BlendEditorDialog({
  blend, loadModels, onSave, onClose,
}: {
  blend: Blend | null;
  loadModels: () => Promise<BlendModel[]>;
  onSave: (input: BlendCreateInput) => Promise<void>;
  onClose: () => void;
}) {
  const [available, setAvailable] = useState<BlendModel[]>([]);
  const [name, setName] = useState(blend?.name ?? '');
  const [models, setModels] = useState<string[]>(blend?.models ?? []);
  const [strategy, setStrategy] = useState<BlendStrategy>(blend?.strategy ?? 'fallback');
  const [judge, setJudge] = useState(blend?.judge_model ?? '');
  const [sticky, setSticky] = useState(blend?.sticky_limit != null ? String(blend.sticky_limit) : '');
  const [groups, setGroups] = useState<RouteGroups>(() => initialGroups(blend));
  const [uncertainTier, setUncertainTier] = useState<'normal' | 'difficult'>(blend?.smart_route?.uncertain_tier ?? 'difficult');
  const [addPick, setAddPick] = useState('');
  const [tierPicks, setTierPicks] = useState<Record<Tier, string>>({ quick: '', normal: '', difficult: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void loadModels().then(setAvailable).catch(() => setAvailable([])); }, [loadModels]);

  const modelById = useMemo(() => new Map(available.map((model) => [model.id, model])), [available]);
  const routedIds = useMemo(() => TIERS.flatMap((tier) => groups[tier].map((row) => row.model)), [groups]);
  const unselected = useMemo(
    () => available.map((model) => model.id).filter((id) => !models.includes(id)),
    [available, models]);
  const routeContexts = TIERS.flatMap((tier) => groups[tier].map(
    (row) => modelById.get(row.model)?.context_length ?? row.context_length,
  )).filter((value): value is number => Boolean(value));
  const guaranteedContext = routeContexts.length === routedIds.length && routeContexts.length > 0
    ? Math.min(...routeContexts) : null;
  const maximumContext = routeContexts.length > 0 ? Math.max(...routeContexts) : null;
  const nameValid = NAME_RE.test(name) && name.toLowerCase() !== 'auto';

  function moveModel(index: number, dir: -1 | 1) {
    const target = index + dir;
    if (target < 0 || target >= models.length) return;
    const next = [...models];
    [next[index], next[target]] = [next[target], next[index]];
    setModels(next);
  }

  function updateTier(tier: Tier, next: SmartRouteModel[]) {
    setGroups((current) => ({ ...current, [tier]: next }));
  }

  function moveTierModel(tier: Tier, index: number, dir: -1 | 1) {
    const rows = groups[tier];
    const target = index + dir;
    if (target < 0 || target >= rows.length) return;
    const next = [...rows];
    [next[index], next[target]] = [next[target], next[index]];
    updateTier(tier, next);
  }

  function addTierModel(tier: Tier) {
    const id = tierPicks[tier];
    if (!id) return;
    updateTier(tier, [...groups[tier], { model: id, reasoning: 'auto' }]);
    setTierPicks((current) => ({ ...current, [tier]: '' }));
  }

  function smartRoute(): SmartRouteConfig {
    const clean = (tier: Tier) => groups[tier].map(({ model, reasoning }) => ({ model, reasoning }));
    return { quick: clean('quick'), normal: clean('normal'), difficult: clean('difficult'), uncertain_tier: uncertainTier };
  }

  function canSave(): boolean {
    if (!nameValid) return false;
    if (strategy === 'smart-route') return TIERS.every((tier) => groups[tier].length > 0) && routedIds.length <= 24;
    if (models.length < 1) return false;
    if (strategy === 'fusion' && (models.length < 2 || !judge)) return false;
    return true;
  }

  async function save() {
    setBusy(true); setError(null);
    try {
      const smart = strategy === 'smart-route' ? smartRoute() : null;
      await onSave({
        name,
        models: smart ? TIERS.flatMap((tier) => smart[tier].map((row) => row.model)) : models,
        strategy,
        judge_model: strategy === 'fusion' ? (judge || null) : null,
        sticky_limit: strategy === 'round-robin' && sticky.trim() ? Number(sticky) : null,
        smart_route: smart,
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
      <div style={S.dialog} role="dialog" aria-modal="true" aria-label={blend ? 'Edit blend' : 'New blend'} onClick={(event) => event.stopPropagation()}>
        <div style={S.head}>
          <strong>{blend ? 'Edit blend' : 'New blend'}</strong>
          <button style={S.iconbtn} onClick={onClose} aria-label="Close"><X size={15} /></button>
        </div>

        <div style={S.label}>Name</div>
        <input style={S.input} value={name} onChange={(event) => setName(event.target.value)} placeholder="research-blend" />
        {!nameValid && name.length > 0 && <div style={S.muted}>Letters, digits, '.', '_', '-' (max 64); not "auto".</div>}

        <div style={S.label}>How should this blend choose a model?</div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {STRATEGIES.map(({ value, label }) => (
            <label key={value} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 13, cursor: 'pointer' }}>
              <input type="radio" name="strategy" checked={strategy === value} onChange={() => setStrategy(value)} />
              {label}
            </label>
          ))}
        </div>

        {strategy === 'smart-route' ? (
          <>
            <div style={{ ...S.caveat, borderColor: 'rgba(47,111,98,0.5)', background: 'rgba(47,111,98,0.1)' }}>
              Smart route reads the task and chooses the first suitable model from one of the three groups. No thresholds to configure.
            </div>
            {TIERS.map((tier) => {
              const copy = TIER_COPY[tier];
              const choices = available.filter((model) => !routedIds.includes(model.id));
              return (
                <div style={S.tier} key={tier}>
                  <div style={{ padding: '8px 10px', borderLeft: `4px solid ${copy.tone}`, background: 'rgba(128,128,128,0.06)' }}>
                    <strong style={{ fontSize: 13 }}>{copy.title}</strong>
                    <div style={S.muted}>{copy.help}</div>
                  </div>
                  <div style={{ padding: '4px 10px 8px' }}>
                    {groups[tier].length === 0 && <div style={{ ...S.muted, padding: '8px 0' }}>Add at least one model.</div>}
                    {groups[tier].map((row, index) => {
                      const metadata = modelById.get(row.model);
                      const supported = metadata?.reasoning_levels ?? [];
                      return (
                        <div style={{ ...S.row, borderBottom: '1px solid rgba(128,128,128,0.12)' }} key={row.model}>
                          <span style={{ width: 22, ...S.muted }}>{index + 1}</span>
                          <span style={{ flex: 1, minWidth: 140, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis' }}>{row.model}</span>
                          <span style={{ width: 72, fontSize: 12 }} title="Model context length">{formatContext(metadata?.context_length ?? row.context_length)}</span>
                          <select
                            style={{ ...S.input, width: 112 }}
                            aria-label={`${row.model} thinking`}
                            value={row.reasoning}
                            onChange={(event) => updateTier(tier, groups[tier].map((item, rowIndex) => rowIndex === index
                              ? { ...item, reasoning: event.target.value as ReasoningLevel } : item))}
                          >
                            {REASONING.map((option) => (
                              <option key={option.value} value={option.value}
                                disabled={option.value !== 'auto' && supported.length > 0 && !supported.includes(option.value)}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                          <button style={S.iconbtn} onClick={() => moveTierModel(tier, index, -1)} title="Move up"><ArrowUp size={13} /></button>
                          <button style={S.iconbtn} onClick={() => moveTierModel(tier, index, 1)} title="Move down"><ArrowDown size={13} /></button>
                          <button style={S.iconbtn} onClick={() => updateTier(tier, groups[tier].filter((_, rowIndex) => rowIndex !== index))} title="Remove"><X size={13} /></button>
                        </div>
                      );
                    })}
                    <div style={{ ...S.row, marginTop: 5 }}>
                      <select style={{ ...S.input, flex: 1 }} value={tierPicks[tier]}
                        onChange={(event) => setTierPicks((current) => ({ ...current, [tier]: event.target.value }))}>
                        <option value="">Add a model…</option>
                        {choices.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}
                      </select>
                      <button style={S.btn} disabled={!tierPicks[tier] || routedIds.length >= 24} onClick={() => addTierModel(tier)}>
                        <Plus size={13} /> Add
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}

            <div style={S.label}>If the task is unclear</div>
            <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
              <label><input type="radio" checked={uncertainTier === 'difficult'} onChange={() => setUncertainTier('difficult')} /> Use Difficult (recommended)</label>
              <label><input type="radio" checked={uncertainTier === 'normal'} onChange={() => setUncertainTier('normal')} /> Use Normal</label>
            </div>
            <div style={{ ...S.muted, marginTop: 8 }}>
              Context across selected models: guaranteed {formatContext(guaranteedContext)} · largest {formatContext(maximumContext)}
            </div>
          </>
        ) : (
          <>
            <div style={S.label}>Models (order = fallback order)</div>
            {models.length === 0 && <div style={S.muted}>No models yet — add at least one below.</div>}
            {models.map((id, index) => (
              <div style={S.row} key={id}>
                <span style={{ flex: 1, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{index + 1}. {id}</span>
                <span style={{ width: 70, fontSize: 12 }}>{formatContext(modelById.get(id)?.context_length)}</span>
                <button style={S.iconbtn} onClick={() => moveModel(index, -1)} title="Up"><ArrowUp size={13} /></button>
                <button style={S.iconbtn} onClick={() => moveModel(index, 1)} title="Down"><ArrowDown size={13} /></button>
                <button style={S.iconbtn} onClick={() => setModels(models.filter((model) => model !== id))} title="Remove"><X size={13} /></button>
              </div>
            ))}
            <div style={{ ...S.row, marginTop: 4 }}>
              <select style={{ ...S.input, flex: 1 }} value={addPick} onChange={(event) => setAddPick(event.target.value)}>
                <option value="">Add a model…</option>
                {unselected.map((id) => <option key={id} value={id}>{id}</option>)}
              </select>
              <button style={S.btn} disabled={!addPick || models.length >= 24}
                onClick={() => { if (addPick) { setModels([...models, addPick]); setAddPick(''); } }}>
                <Plus size={13} /> Add
              </button>
            </div>
          </>
        )}

        {strategy === 'round-robin' && (
          <>
            <div style={S.label}>Sticky limit (applies to all round-robin blends)</div>
            <input style={{ ...S.input, width: 120 }} type="number" min={1} value={sticky} onChange={(event) => setSticky(event.target.value)} />
          </>
        )}
        {strategy === 'fusion' && (
          <>
            <div style={S.label}>Judge model</div>
            <select style={S.input} value={judge} onChange={(event) => setJudge(event.target.value)}>
              <option value="">Choose a judge model…</option>
              {available.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}
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
