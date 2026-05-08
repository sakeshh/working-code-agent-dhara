'use client';

import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { FaBug, FaClipboard, FaCode, FaDatabase, FaExclamationTriangle, FaInfoCircle, FaWrench } from 'react-icons/fa';

type Severity = 'high' | 'medium' | 'low';

function normSeverity(s: any): Severity {
  const t = String(s || '').toLowerCase();
  if (t === 'high') return 'high';
  if (t === 'medium') return 'medium';
  return 'low';
}

function severityStyles(sev: Severity): string {
  if (sev === 'high') return 'bg-red-500/15 text-red-700 border-red-500/30';
  if (sev === 'medium') return 'bg-amber-500/15 text-amber-700 border-amber-500/30';
  return 'bg-blue-500/15 text-blue-700 border-blue-500/30';
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // ignore
  }
}

type Props = {
  result: any;
  userIntent?: string;
  enableTransformSuggestions?: boolean;
  enableDqRecommendations?: boolean;
  variant?: 'chat' | 'pipeline';
};

export default function ReportEnhancements({
  result,
  userIntent = '',
  enableTransformSuggestions = true,
  enableDqRecommendations = true,
  variant = 'chat',
}: Props) {
  const [dqRecLoading, setDqRecLoading] = useState(false);
  const [dqRec, setDqRec] = useState<any>(null);
  const [dqRecErr, setDqRecErr] = useState<string | null>(null);

  const [tfLoading, setTfLoading] = useState(false);
  const [tf, setTf] = useState<any>(null);
  const [tfErr, setTfErr] = useState<string | null>(null);

  const dqPayload = result?.data_quality ?? result?.data_quality_issues ?? null;
  const timings = result?.timings && typeof result.timings === "object" ? result.timings : null;
  const requestId = typeof result?.request_id === 'string' ? result.request_id : '';
  const extractionErrors = Array.isArray(result?.extraction_errors) ? result.extraction_errors : null;

  useEffect(() => {
    let alive = true;
    (async () => {
      // DQ recommendations
      if (!enableDqRecommendations) return;
      if (result?.dq_recommendations) {
        if (alive) setDqRec(result.dq_recommendations);
        return;
      }
      if (!dqPayload || typeof dqPayload !== 'object') return;
      setDqRecLoading(true);
      setDqRecErr(null);
      try {
        const res = await fetch('/api/dq-recommend', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data_quality: dqPayload, user_intent: userIntent }),
        });
        const j = await res.json().catch(() => null);
        if (!alive) return;
        if (!res.ok) throw new Error(j?.detail || 'Failed to generate recommendations');
        setDqRec(j?.recommendations ?? null);
      } catch (e: any) {
        if (!alive) return;
        setDqRecErr(e?.message || 'Failed to generate recommendations');
      } finally {
        if (alive) setDqRecLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enableDqRecommendations, JSON.stringify(Boolean(result?.dq_recommendations)), JSON.stringify(Boolean(dqPayload))]);

  useEffect(() => {
    let alive = true;
    (async () => {
      if (!enableTransformSuggestions) return;
      if (result?.transform_suggestions) {
        if (alive) setTf(result.transform_suggestions);
        return;
      }
      setTfLoading(true);
      setTfErr(null);
      try {
        const res = await fetch('/api/transform-suggest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ assessment_result: result }),
        });
        const j = await res.json().catch(() => null);
        if (!alive) return;
        if (!res.ok) throw new Error(j?.detail || 'Failed to generate transform suggestions');
        setTf(j?.suggestions ?? null);
      } catch (e: any) {
        if (!alive) return;
        setTfErr(e?.message || 'Failed to generate transform suggestions');
      } finally {
        if (alive) setTfLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [enableTransformSuggestions, result]);

  const recList: any[] = useMemo(() => {
    const r = dqRec?.recommendations;
    if (!Array.isArray(r)) return [];
    return [...r]
      .filter((x) => x && typeof x === 'object')
      .sort((a, b) => Number(a?.priority ?? 999) - Number(b?.priority ?? 999))
      .slice(0, 40);
  }, [dqRec]);

  const tfByAction = useMemo(() => {
    const src = tf?.sources;
    const out: Record<string, { action: string; count: number; items: any[] }> = {};
    const blocks: any[] = [];
    if (src && typeof src === 'object') {
      for (const [k, v] of Object.entries(src)) blocks.push({ source: k, val: v });
    } else {
      blocks.push({ source: 'result', val: tf });
    }
    for (const b of blocks) {
      const items = Array.isArray((b.val as any)?.suggested_transformations) ? (b.val as any).suggested_transformations : [];
      for (const it of items) {
        const action = String(it?.suggested_action || 'review_manually');
        if (!out[action]) out[action] = { action, count: 0, items: [] };
        out[action].count += 1;
        out[action].items.push({ ...it, _source: b.source });
      }
    }
    return Object.values(out).sort((a, b) => b.count - a.count).slice(0, 12);
  }, [tf]);

  const showDiagnostics = Boolean(requestId || timings || (extractionErrors && extractionErrors.length));

  return (
    <div className="space-y-4">
      {/* Cleaning recommendations */}
      {enableDqRecommendations ? (
        <div className="rounded-xl border border-black/10 bg-white/85 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2">
              <div className="rounded-lg bg-[#0070AD]/10 p-2 text-[#0070AD]">
                <FaWrench />
              </div>
              <div>
                <div className="text-[13px] font-semibold text-zinc-900">Cleaning recommendations</div>
                <div className="text-[12px] text-black/60">Prioritized actions to improve data quality</div>
              </div>
            </div>
            {variant === 'pipeline' ? (
              <div className="text-[11px] text-black/55 flex items-center gap-2">
                <FaInfoCircle />
                LLM-assisted when configured
              </div>
            ) : null}
          </div>

          {dqRecLoading ? (
            <div className="mt-3 text-[12px] text-black/60">Generating recommendations…</div>
          ) : dqRecErr ? (
            <div className="mt-3 text-[12px] text-red-700">{dqRecErr}</div>
          ) : recList.length === 0 ? (
            <div className="mt-3 text-[12px] text-black/60">No recommendations available.</div>
          ) : (
            <div className="mt-3 space-y-3">
              {recList.slice(0, 8).map((r, idx) => {
                const sev = normSeverity(r?.severity);
                return (
                  <div key={idx} className="rounded-xl border border-black/10 bg-white/80 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[12px] font-semibold text-zinc-900">#{Number(r?.priority ?? idx + 1)}</span>
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${severityStyles(sev)}`}
                        >
                          {sev.toUpperCase()}
                        </span>
                        <span className="text-[12px] font-medium text-black/70">
                          {r?.dataset ? String(r.dataset) : 'Global'}
                          {r?.column ? ` · ${String(r.column)}` : ''}
                        </span>
                        <span className="text-[11px] text-black/50">{String(r?.issue_type || '')}</span>
                      </div>
                    </div>
                    <div className="mt-2 text-[12px] text-black/80">
                      <span className="font-semibold">Fix:</span> {String(r?.suggested_fix || '')}
                    </div>
                    {r?.why_it_matters ? (
                      <div className="mt-1 text-[12px] text-black/60">
                        <span className="font-semibold">Why:</span> {String(r.why_it_matters)}
                      </div>
                    ) : null}
                    <div className="mt-2 flex flex-wrap gap-2">
                      {r?.example_sql ? (
                        <motion.button
                          type="button"
                          whileHover={{ scale: 1.02 }}
                          whileTap={{ scale: 0.98 }}
                          onClick={() => copy(String(r.example_sql))}
                          className="inline-flex items-center gap-2 rounded-lg border border-black/10 bg-white/85 px-3 py-1.5 text-[11px] font-semibold text-zinc-900 hover:bg-white hover:border-[#0070AD]/30"
                        >
                          <FaCode className="text-[#0070AD]" />
                          Copy SQL
                        </motion.button>
                      ) : null}
                      {r?.example_pandas ? (
                        <motion.button
                          type="button"
                          whileHover={{ scale: 1.02 }}
                          whileTap={{ scale: 0.98 }}
                          onClick={() => copy(String(r.example_pandas))}
                          className="inline-flex items-center gap-2 rounded-lg border border-black/10 bg-white/85 px-3 py-1.5 text-[11px] font-semibold text-zinc-900 hover:bg-white hover:border-[#0070AD]/30"
                        >
                          <FaClipboard className="text-[#0070AD]" />
                          Copy Pandas
                        </motion.button>
                      ) : null}
                    </div>
                  </div>
                );
              })}

              {recList.length > 8 ? (
                <details className="rounded-xl border border-black/10 bg-white/70 p-3">
                  <summary className="cursor-pointer select-none text-[12px] font-semibold text-zinc-900">
                    Show all recommendations ({recList.length})
                  </summary>
                  <pre className="mt-2 max-h-80 overflow-auto rounded-lg border border-black/10 bg-white/70 p-3 text-[11px] text-zinc-900">
                    {JSON.stringify(dqRec, null, 2)}
                  </pre>
                </details>
              ) : null}
            </div>
          )}
        </div>
      ) : null}

      {/* Transform suggestions */}
      {enableTransformSuggestions ? (
        <div className="rounded-xl border border-black/10 bg-white/85 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <div className="rounded-lg bg-[#12ABDB]/10 p-2 text-[#0070AD]">
              <FaDatabase />
            </div>
            <div>
              <div className="text-[13px] font-semibold text-zinc-900">Suggested transformations</div>
              <div className="text-[12px] text-black/60">Grouped by recommended action</div>
            </div>
          </div>
        </div>

        {tfLoading ? (
          <div className="mt-3 text-[12px] text-black/60">Generating suggestions…</div>
        ) : tfErr ? (
          <div className="mt-3 text-[12px] text-red-700">{tfErr}</div>
        ) : tfByAction.length === 0 ? (
          <div className="mt-3 text-[12px] text-black/60">No transform suggestions available.</div>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {tfByAction.map((g) => (
              <details key={g.action} className="rounded-xl border border-black/10 bg-white/80 p-3">
                <summary className="cursor-pointer select-none text-[12px] font-semibold text-zinc-900 flex items-center justify-between gap-3">
                  <span>{g.action}</span>
                  <span className="rounded-full border border-black/10 bg-black/5 px-2 py-0.5 text-[11px] font-semibold text-black/70">
                    {g.count}
                  </span>
                </summary>
                <div className="mt-2 space-y-2">
                  {g.items.slice(0, 8).map((it, i) => (
                    <div key={i} className="rounded-lg border border-black/10 bg-white/70 p-2 text-[11px] text-black/80">
                      <div className="font-semibold text-zinc-900">
                        {(it?._source ? `${it._source} · ` : '')}
                        {it?.dataset ?? 'global'}{it?.column ? ` · ${it.column}` : ''}
                      </div>
                      <div className="text-black/65">{String(it?.message || it?.issue_type || '')}</div>
                    </div>
                  ))}
                  {g.items.length > 8 ? (
                    <div className="text-[11px] text-black/55">…(+{g.items.length - 8} more)</div>
                  ) : null}
                </div>
              </details>
            ))}
          </div>
        )}
        </div>
      ) : null}

      {/* Advanced diagnostics */}
      {showDiagnostics ? (
        <details className="rounded-xl border border-black/10 bg-white/80 p-4">
          <summary className="cursor-pointer select-none text-[12px] font-semibold text-zinc-900 flex items-center gap-2">
            <FaBug className="text-black/60" />
            Advanced diagnostics
          </summary>
          <div className="mt-3 space-y-3">
            {requestId ? (
              <div className="rounded-lg border border-black/10 bg-white/70 p-3">
                <div className="text-[11px] font-semibold text-black/70">request_id</div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  <code className="text-[11px] text-zinc-900">{requestId}</code>
                  <button
                    type="button"
                    onClick={() => copy(requestId)}
                    className="rounded-md border border-black/10 bg-white/85 px-2 py-1 text-[11px] font-semibold text-zinc-900 hover:bg-white"
                  >
                    Copy
                  </button>
                </div>
              </div>
            ) : null}

            {timings ? (
              <div className="rounded-lg border border-black/10 bg-white/70 p-3">
                <div className="text-[11px] font-semibold text-black/70">timings</div>
                <pre className="mt-2 max-h-56 overflow-auto rounded-lg border border-black/10 bg-white/70 p-3 text-[11px] text-zinc-900">
                  {JSON.stringify(timings, null, 2)}
                </pre>
              </div>
            ) : null}

            {extractionErrors && extractionErrors.length ? (
              <div className="rounded-lg border border-black/10 bg-white/70 p-3">
                <div className="flex items-center gap-2 text-[11px] font-semibold text-black/70">
                  <FaExclamationTriangle className="text-amber-600" />
                  extraction_errors ({extractionErrors.length})
                </div>
                <pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-black/10 bg-white/70 p-3 text-[11px] text-zinc-900">
                  {JSON.stringify(extractionErrors, null, 2)}
                </pre>
              </div>
            ) : null}
          </div>
        </details>
      ) : null}
    </div>
  );
}

