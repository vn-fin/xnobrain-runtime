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
const STRATEGIES: { value: BlendStrategy; label: string; description: string }[] = [
  { value: 'fallback', label: 'Fallback', description: 'Tries models in order and moves to the next one when a model is unavailable or fails.' },
  { value: 'round-robin', label: 'Round robin', description: 'Rotates requests across the selected models to spread usage.' },
  { value: 'fusion', label: 'Fusion', description: 'Runs every selected model and uses a judge model to combine the answers. Highest cost; tools are disabled.' },
  { value: 'smart-route', label: 'Smart route', description: 'Classifies task difficulty and sends it to the first suitable model in the matching tier.' },
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
  label: { display: 'block', fontSize: 12, fontWeight: 600, opacity: 0.8, margin: '12px 0 4px' } as CSSProperties,
  input: { width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid rgba(128,128,128,0.32)', background: 'transparent', color: 'inherit', fontSize: 13 } as CSSProperties,
  btn: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 5, padding: '6px 12px', borderRadius: 8, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer', fontSize: 13 } as CSSProperties,
  primary: { background: ACCENT, borderColor: ACCENT, color: '#fff' } as CSSProperties,
  iconbtn: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 26, height: 24, borderRadius: 6, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer' } as CSSProperties,
  row: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' } as CSSProperties,
  muted: { fontSize: 12, opacity: 0.65 } as CSSProperties,
  required: { color: '#d06a52' } as CSSProperties,
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
  const stickyNumber = Number(sticky);
  const stickyValid = !sticky.trim()
    || (Number.isInteger(stickyNumber) && stickyNumber >= 1 && stickyNumber <= 1000);
  const validationIssues = (() => {
    const issues: string[] = [];
    if (!name.trim()) issues.push('Name is required.');
    else if (!nameValid) issues.push('Enter a valid name.');
    if (strategy === 'smart-route') {
      for (const tier of TIERS) {
        if (groups[tier].length === 0) issues.push(`${TIER_COPY[tier].title} requires at least one model.`);
      }
      if (routedIds.length > 24) issues.push('Smart route supports at most 24 tier assignments.');
    } else {
      if (strategy === 'fusion') {
        if (models.length < 2) issues.push('Fusion requires at least two models.');
        if (!judge) issues.push('Judge model is required for Fusion.');
      } else if (models.length === 0) issues.push('Add at least one model.');
    }
    if (strategy === 'round-robin' && !stickyValid) {
      issues.push('Sticky limit must be a whole number from 1 to 1000.');
    }
    return issues;
  })();

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

  async function save() {
    if (validationIssues.length > 0) return;
    setBusy(true); setError(null);
    try {
      const smart = strategy === 'smart-route' ? smartRoute() : null;
      const selectedModels = smart
        ? [...new Set(TIERS.flatMap((tier) => smart[tier].map((row) => row.model)))]
        : models;
      await onSave({
        name,
        models: selectedModels,
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
        <div style={S.muted}>Fields marked with <span style={S.required}>*</span> are required.</div>

        <label style={S.label} htmlFor="blend-name">Name <span style={S.required}>*</span></label>
        <input id="blend-name" required aria-invalid={!nameValid} style={S.input} value={name} onChange={(event) => setName(event.target.value)} placeholder="research-blend" />
        {!name.trim()
          ? <div style={S.err}>Name is required.</div>
          : !nameValid && <div style={S.err}>Use letters, digits, '.', '_', or '-' (max 64); the name cannot be "auto".</div>}

        <div style={S.label}>Blend mode <span style={S.required}>*</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8 }}>
          {STRATEGIES.map(({ value, label, description }) => (
            <label key={value} style={{ display: 'flex', alignItems: 'flex-start', gap: 7, padding: 9, border: strategy === value ? `1px solid ${ACCENT}` : '1px solid rgba(128,128,128,0.24)', borderRadius: 8, cursor: 'pointer' }}>
              <input required aria-label={label} type="radio" name="strategy" checked={strategy === value} onChange={() => setStrategy(value)} />
              <span>
                <strong style={{ display: 'block', fontSize: 13 }}>{label}</strong>
                <span style={S.muted}>{description}</span>
              </span>
            </label>
          ))}
        </div>

        {strategy === 'smart-route' ? (
          <>
            <div style={{ ...S.caveat, borderColor: 'rgba(47,111,98,0.5)', background: 'rgba(47,111,98,0.1)' }}>
              Smart Route adds a classification step, so responses may be slightly slower. It can save cost by routing quick and normal tasks to less expensive models.
            </div>
            {TIERS.map((tier) => {
              const copy = TIER_COPY[tier];
              const tierIds = new Set(groups[tier].map((row) => row.model));
              const choices = available.filter((model) => !tierIds.has(model.id));
              return (
                <div style={S.tier} key={tier}>
                  <div style={{ padding: '8px 10px', borderLeft: `4px solid ${copy.tone}`, background: 'rgba(128,128,128,0.06)' }}>
                    <strong style={{ fontSize: 13 }}>{copy.title}</strong> <span style={S.required}>*</span>
                    <div style={S.muted}>{copy.help}</div>
                  </div>
                  <div style={{ padding: '4px 10px 8px' }}>
                    {groups[tier].length === 0 && <div style={{ ...S.err, padding: '8px 0' }}>At least one model is required.</div>}
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
                        aria-label={`Add model to ${tier} tasks`}
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

            <div style={S.label}>If the task is unclear <span style={S.required}>*</span></div>
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
            <div style={S.label}>Models <span style={S.required}>*</span>{strategy === 'fallback' ? ' (order = fallback order)' : ''}</div>
            {strategy !== 'fusion' && models.length === 0 && <div style={S.err}>At least one model is required.</div>}
            {strategy === 'fusion' && models.length < 2 && <div style={S.err}>Fusion requires at least two models.</div>}
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
              <select aria-label="Add blend model" style={{ ...S.input, flex: 1 }} value={addPick} onChange={(event) => setAddPick(event.target.value)}>
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
            <input aria-label="Sticky limit" style={{ ...S.input, width: 120 }} type="number" min={1} max={1000} value={sticky} onChange={(event) => setSticky(event.target.value)} />
            {!stickyValid && <div style={S.err}>Enter a whole number from 1 to 1000, or leave it empty.</div>}
          </>
        )}
        {strategy === 'fusion' && (
          <>
            <div style={S.label}>Judge model <span style={S.required}>*</span></div>
            <select required aria-label="Judge model" aria-invalid={!judge} style={S.input} value={judge} onChange={(event) => setJudge(event.target.value)}>
              <option value="">Choose a judge model…</option>
              {available.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}
            </select>
            {!judge && <div style={S.err}>Judge model is required for Fusion.</div>}
            <div style={S.caveat}>Fusion runs every model in the blend on each request (higher cost); tools are disabled for fusion.</div>
          </>
        )}

        {error && <div style={S.err}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <button style={S.btn} onClick={onClose} disabled={busy}>Cancel</button>
          <button style={{ ...S.btn, ...S.primary }} onClick={() => void save()}
            title={validationIssues.join(' ')} disabled={busy || validationIssues.length > 0}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
