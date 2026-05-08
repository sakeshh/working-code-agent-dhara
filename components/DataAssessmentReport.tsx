'use client';

import { useMemo, useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { FaChartBar, FaExclamationTriangle, FaCheckCircle, FaThumbsUp, FaThumbsDown, FaWrench } from 'react-icons/fa';
import ReportEnhancements from '@/components/ReportEnhancements';
import { sourceRootLabel } from '@/lib/assessmentDisplay';

interface DataAssessmentReportProps {
  files: string[];
  database: string;
  includeTransformSuggestions?: boolean;
  onIncludeTransformSuggestionsChange?: (v: boolean) => void;
  includeDqRecommendations?: boolean;
  onIncludeDqRecommendationsChange?: (v: boolean) => void;
  onComplete: (data: any) => void;
  onFeedback: (liked: boolean, comment?: string) => void;
}

type Severity = 'high' | 'medium' | 'low';

type BackendAssessment = {
  datasets?: Record<
    string,
    {
      row_count?: number;
      column_count?: number;
      source_root?: string;
      columns?: Record<
        string,
        {
          dtype?: string;
          null_percentage?: number;
          unique_count?: number;
          semantic_type?: string;
          candidate_primary_key?: boolean;
        }
      >;
    }
  >;
  relationships?: Array<{
    dataset_a?: string;
    column_a?: string;
    dataset_b?: string;
    column_b?: string;
    cardinality?: string;
    overlap_count?: number;
  }>;
  data_quality_issues?: {
    datasets?: Record<
      string,
      {
        summary?: {
          issue_count?: number;
          high_severity?: number;
          medium_severity?: number;
          low_severity?: number;
        };
        issues?: Array<{
          severity?: Severity | string;
          type?: string;
          column?: string;
          count?: number;
          message?: string;
          recommendation?: string;
        }>;
      }
    >;
    global_issues?: Record<string, any>;
  };
};

type UiDatasetSummary = {
  name: string;
  sourceLabel: string;
  rows: number;
  cols: number;
  issues: number;
  high: number;
  med: number;
  low: number;
};

function normalizeSeverity(s: any): Severity {
  const t = String(s || '').toLowerCase();
  if (t === 'high') return 'high';
  if (t === 'medium') return 'medium';
  return 'low';
}

export default function DataAssessmentReport({
  files,
  database,
  includeTransformSuggestions = true,
  onIncludeTransformSuggestionsChange,
  includeDqRecommendations = true,
  onIncludeDqRecommendationsChange,
  onComplete,
  onFeedback,
}: DataAssessmentReportProps) {
  const [assessing, setAssessing] = useState(true);
  const [progress, setProgress] = useState(0);
  const [assessment, setAssessment] = useState<BackendAssessment | null>(null);
  const [reportMarkdown, setReportMarkdown] = useState<string | null>(null);
  const [reportHtml, setReportHtml] = useState<string | null>(null);
  const [showFeedback, setShowFeedback] = useState(false);
  const [transformEnabled, setTransformEnabled] = useState<boolean>(Boolean(includeTransformSuggestions));
  const [transformSuggestions, setTransformSuggestions] = useState<any>(null);
  const [transformLoading, setTransformLoading] = useState(false);
  const [dqRecEnabled, setDqRecEnabled] = useState<boolean>(Boolean(includeDqRecommendations));

  const summaries: UiDatasetSummary[] = useMemo(() => {
    const datasets = assessment?.datasets || {};
    const dq = assessment?.data_quality_issues?.datasets || {};
    return Object.entries(datasets).map(([name, meta]) => {
      const summ = dq?.[name]?.summary || {};
      return {
        name,
        sourceLabel: sourceRootLabel(meta?.source_root),
        rows: Number(meta?.row_count ?? 0) || 0,
        cols: Number(meta?.column_count ?? 0) || 0,
        issues: Number(summ?.issue_count ?? 0) || 0,
        high: Number(summ?.high_severity ?? 0) || 0,
        med: Number(summ?.medium_severity ?? 0) || 0,
        low: Number(summ?.low_severity ?? 0) || 0,
      };
    });
  }, [assessment]);

  useEffect(() => {
    runAssessment();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep local state in sync with parent toggle if provided
  useEffect(() => {
    setTransformEnabled(Boolean(includeTransformSuggestions));
  }, [includeTransformSuggestions]);

  useEffect(() => {
    setDqRecEnabled(Boolean(includeDqRecommendations));
  }, [includeDqRecommendations]);

  const getSessionId = () => {
    if (typeof window === 'undefined') return 'default';
    return window.localStorage.getItem('dharaSessionId') || 'default';
  };

  const parseSourceToken = (token: string): { kind: 'sql' | 'blob' | 'streams' | 'unknown'; absIndex: number } => {
    const parts = String(token || '').split(':');
    const kind = (parts[1] as any) || 'unknown';
    const absIndex = Number(parts[2] || 0);
    return { kind, absIndex: Number.isFinite(absIndex) ? absIndex : 0 };
  };

  const normalizeType = (t: any): string => {
    const s = String(t || '').toLowerCase();
    if (s.includes('azure_blob')) return 'azure_blob';
    if (s.includes('filesystem')) return 'filesystem';
    if (s.includes('database')) return 'database';
    return s || 'unknown';
  };

  const runAssessment = async () => {
    setAssessing(true);
    setProgress(5);
    setTransformSuggestions(null);
    try {
      const sid = getSessionId();
      const src = parseSourceToken(database);
      const sourcesRes = await fetch('/api/sources');
      const sourcesJson = await sourcesRes.json().catch(() => null);
      const locs = Array.isArray(sourcesJson?.locations) ? sourcesJson.locations : [];
      const absList = locs.map((l: any) => ({ index: Number(l?.index ?? 0), type: normalizeType(l?.type) }));

      const relIndex = (type: string, absIndex: number) => {
        const only = absList
          .filter((x: { index: number; type: string }) => x.type === type)
          .map((x: { index: number; type: string }) => x.index);
        const pos = only.indexOf(absIndex);
        return pos >= 0 ? pos : 0;
      };

      // Store deterministic selection context for the backend session.
      const context: any = {};
      if (src.kind === 'sql') {
        context.last_table_list = files; // best effort for selection UX
        context.selected_tables = files;
        context.selected_db_location_index = relIndex('database', src.absIndex);
      } else if (src.kind === 'blob') {
        context.last_blob_list = files;
        context.selected_blob_files = files;
        context.selected_blob_location_index = relIndex('azure_blob', src.absIndex);
      } else if (src.kind === 'streams') {
        context.last_local_file_list = files;
        context.selected_local_files = files;
        context.selected_fs_location_index = relIndex('filesystem', src.absIndex);
      }

      setProgress(25);
      await fetch('/api/session-context', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, context }),
      });

      setProgress(45);
      const cmd =
        src.kind === 'sql'
          ? 'assess selected tables'
          : src.kind === 'blob'
            ? 'assess selected files'
            : src.kind === 'streams'
              ? 'assess selected local files'
              : 'help';

      const chatRes = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId: sid, messages: [{ role: 'user', content: cmd }] }),
      });
      const chatJson = await chatRes.json().catch(() => null);
      const payload = chatJson?.payload ?? null;
      const result: BackendAssessment | null = payload?.result ?? payload ?? null;
      setAssessment(result);
      setReportMarkdown(typeof payload?.report_markdown === 'string' ? payload.report_markdown : null);
      setReportHtml(typeof payload?.report_html === 'string' ? payload.report_html : null);

      let suggestionsOut: any = null;
      if (transformEnabled && result) {
        setTransformLoading(true);
        try {
          const tr = await fetch('/api/transform-suggest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ assessment_result: result }),
          });
          const trJson = await tr.json().catch(() => null);
          suggestionsOut = trJson?.suggestions ?? null;
          setTransformSuggestions(suggestionsOut);
        } catch {
          suggestionsOut = null;
          setTransformSuggestions(null);
        } finally {
          setTransformLoading(false);
        }
      }

      setProgress(90);
      onComplete({
        result,
        report_markdown: payload?.report_markdown,
        report_html: payload?.report_html,
        report_files: payload?.report_files,
        transform_suggestions: suggestionsOut,
      });
    } catch {
      setAssessment(null);
      setReportMarkdown(null);
      setReportHtml(null);
      onComplete(null);
    } finally {
      setProgress(100);
      setAssessing(false);
    }
  };

  const handleLike = () => {
    onFeedback(true);
    setShowFeedback(false);
  };

  const handleDislike = () => {
    const comment = prompt('What would you like us to improve?');
    onFeedback(false, comment || undefined);
    setShowFeedback(false);
    
    setTimeout(() => {
      alert('Thank you for your feedback! Re-assessing data with improvements...');
      setAssessing(true);
      setProgress(0);
      runAssessment();
    }, 500);
  };

  if (assessing) {
    return (
      <div className="space-y-6">
        <div className="text-center">
          <h2 className="text-3xl font-bold text-zinc-900 mb-2">Analyzing Data</h2>
          <p className="text-black/60">Please wait while we assess your data quality</p>
        </div>

        <div className="space-y-4">
          <div className="relative h-4 bg-black/10 rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-gradient-to-r from-[#0070AD] to-[#12ABDB]"
              initial={{ width: 0 }}
              animate={{ width: `${progress}%` }}
              transition={{ duration: 0.3 }}
            />
          </div>
          <div className="text-center text-2xl font-bold text-[#0070AD]">{progress}%</div>
        </div>

        <div className="grid grid-cols-3 gap-4">
          {['Scanning Data', 'Detecting Duplicates', 'Analyzing Quality'].map((task, idx) => (
            <motion.div
              key={task}
              className="p-4 bg-white/90 border border-black/10 rounded-lg text-center"
              initial={{ opacity: 0.3 }}
              animate={{ opacity: progress > idx * 30 ? 1 : 0.3 }}
            >
              <div className="text-sm font-medium text-black/80">{task}</div>
            </motion.div>
          ))}
        </div>
      </div>
    );
  }

  const title = files.length === 1 ? `Assessment Report of ${files[0]}` : 'Assessment Report';

  const relationships = Array.isArray(assessment?.relationships) ? assessment!.relationships! : [];
  const globalIssues = assessment?.data_quality_issues?.global_issues || null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-3xl font-bold text-zinc-900 mb-2">{title}</h2>
          <p className="text-black/60">Datasets summary, relationships, and issues</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleLike}
            className="p-3 bg-[#0070AD]/10 text-[#0070AD] rounded-lg hover:bg-[#0070AD]/15 transition-colors"
            title="Like this assessment"
          >
            <FaThumbsUp className="text-xl" />
          </button>
          <button
            onClick={handleDislike}
            className="p-3 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors"
            title="Dislike this assessment"
          >
            <FaThumbsDown className="text-xl" />
          </button>
        </div>
      </div>

      {/* Transform suggestions toggle */}
      <div className="border border-black/10 rounded-lg p-4 bg-white/90 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-[#0070AD]/10 text-[#0070AD]">
            <FaWrench />
          </div>
          <div>
            <div className="font-semibold text-zinc-900">Include transformation suggestions</div>
            <div className="text-sm text-black/60">Generates suggested cleaning/transform actions from detected issues</div>
          </div>
        </div>
        <motion.button
          type="button"
          onClick={() => {
            const next = !transformEnabled;
            setTransformEnabled(next);
            if (onIncludeTransformSuggestionsChange) onIncludeTransformSuggestionsChange(next);
          }}
          className={`px-4 py-2 rounded-xl border text-sm font-semibold transition-colors ${
            transformEnabled
              ? 'border-[#0070AD]/50 bg-[#0070AD]/10 text-[#0070AD] hover:bg-[#0070AD]/15'
              : 'border-black/10 bg-white/80 text-black/60 hover:bg-white'
          }`}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          {transformEnabled ? 'On' : 'Off'}
        </motion.button>
      </div>

      {/* DQ recommendations toggle */}
      <div className="border border-black/10 rounded-lg p-4 bg-white/90 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-[#0070AD]/10 text-[#0070AD]">
            <FaWrench />
          </div>
          <div>
            <div className="font-semibold text-zinc-900">Generate cleaning recommendations (LLM)</div>
            <div className="text-sm text-black/60">Creates a prioritized plan to clean/fix issues (falls back if LLM not configured)</div>
          </div>
        </div>
        <motion.button
          type="button"
          onClick={() => {
            const next = !dqRecEnabled;
            setDqRecEnabled(next);
            if (onIncludeDqRecommendationsChange) onIncludeDqRecommendationsChange(next);
          }}
          className={`px-4 py-2 rounded-xl border text-sm font-semibold transition-colors ${
            dqRecEnabled
              ? 'border-[#0070AD]/50 bg-[#0070AD]/10 text-[#0070AD] hover:bg-[#0070AD]/15'
              : 'border-black/10 bg-white/80 text-black/60 hover:bg-white'
          }`}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          {dqRecEnabled ? 'On' : 'Off'}
        </motion.button>
      </div>

      {/* Datasets summary */}
      <div className="border border-black/10 rounded-lg p-6 bg-white/90">
        <div className="flex items-center gap-3 mb-4">
          <FaChartBar className="text-[#0070AD]" />
          <h3 className="text-xl font-bold text-zinc-900">Datasets (summary)</h3>
        </div>

        {summaries.length === 0 ? (
          <div className="text-sm text-black/60">No datasets found in assessment output.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-black/10">
                  <th className="text-left py-3 px-2 text-[#0070AD]">Dataset</th>
                  <th className="text-left py-3 px-2 text-[#0070AD]">Source</th>
                  <th className="text-right py-3 px-2 text-[#0070AD]">Rows</th>
                  <th className="text-right py-3 px-2 text-[#0070AD]">Cols</th>
                  <th className="text-right py-3 px-2 text-[#0070AD]">Issues</th>
                  <th className="text-right py-3 px-2 text-[#0070AD]">High</th>
                  <th className="text-right py-3 px-2 text-[#0070AD]">Med</th>
                  <th className="text-right py-3 px-2 text-[#0070AD]">Low</th>
                </tr>
              </thead>
              <tbody>
                {summaries.map((r) => (
                  <tr key={r.name} className="border-b border-black/5">
                    <td className="py-2 px-2 font-medium text-zinc-900">{r.name}</td>
                    <td className="py-2 px-2 text-black/70">{r.sourceLabel}</td>
                    <td className="py-2 px-2 text-right text-black/70">{r.rows.toLocaleString()}</td>
                    <td className="py-2 px-2 text-right text-black/70">{r.cols.toLocaleString()}</td>
                    <td className="py-2 px-2 text-right text-black/70">{r.issues.toLocaleString()}</td>
                    <td className="py-2 px-2 text-right text-red-500">{r.high.toLocaleString()}</td>
                    <td className="py-2 px-2 text-right text-amber-500">{r.med.toLocaleString()}</td>
                    <td className="py-2 px-2 text-right text-blue-600">{r.low.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Best UX panels: cleaning recommendations + transforms + advanced diagnostics */}
      {assessment ? (
        <ReportEnhancements
          result={{
            ...assessment,
            // allow showing the already-generated transform suggestions, if present
            transform_suggestions: transformSuggestions ? { sources: { result: transformSuggestions } } : undefined,
          }}
          userIntent={title}
          enableDqRecommendations={dqRecEnabled}
          enableTransformSuggestions={transformEnabled}
          variant="pipeline"
        />
      ) : null}

      {/* Relationships */}
      <div className="border border-black/10 rounded-lg p-6 bg-white/90">
        <div className="flex items-center gap-3 mb-4">
          <FaCheckCircle className="text-[#0070AD]" />
          <h3 className="text-xl font-bold text-zinc-900">Relationships</h3>
        </div>
        {relationships.length === 0 ? (
          <div className="text-sm text-black/60">No relationships found.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-black/10">
                  <th className="text-left py-3 px-2 text-[#0070AD]">Dataset A</th>
                  <th className="text-left py-3 px-2 text-[#0070AD]">Column A</th>
                  <th className="text-left py-3 px-2 text-[#0070AD]">Dataset B</th>
                  <th className="text-left py-3 px-2 text-[#0070AD]">Column B</th>
                  <th className="text-left py-3 px-2 text-[#0070AD]">Cardinality</th>
                  <th className="text-right py-3 px-2 text-[#0070AD]">Shared keys</th>
                </tr>
              </thead>
              <tbody>
                {relationships.map((r, i) => (
                  <tr key={`${r.dataset_a ?? ''}-${r.column_a ?? ''}-${i}`} className="border-b border-black/5">
                    <td className="py-2 px-2 text-zinc-900">{r.dataset_a}</td>
                    <td className="py-2 px-2 text-black/70">{r.column_a}</td>
                    <td className="py-2 px-2 text-zinc-900">{r.dataset_b}</td>
                    <td className="py-2 px-2 text-black/70">{r.column_b}</td>
                    <td className="py-2 px-2 text-black/70">{r.cardinality}</td>
                    <td className="py-2 px-2 text-right text-black/70">{(r.overlap_count ?? 0).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Global issues */}
      <div className="border border-black/10 rounded-lg p-6 bg-white/90">
        <div className="flex items-center gap-3 mb-4">
          <FaExclamationTriangle className="text-amber-500" />
          <h3 className="text-xl font-bold text-zinc-900">Global issues</h3>
        </div>
        {!globalIssues ? (
          <div className="text-sm text-black/60">No global issues returned.</div>
        ) : (
          <pre className="text-xs text-zinc-900 whitespace-pre-wrap font-mono bg-black/5 border border-black/10 rounded-lg p-4 overflow-auto max-h-80">
            {JSON.stringify(globalIssues, null, 2)}
          </pre>
        )}
      </div>

      {/* Backend report previews (optional) */}
      {(reportMarkdown || reportHtml) && (
        <div className="border border-black/10 rounded-lg p-6 bg-white/90 space-y-3">
          <h3 className="text-xl font-bold text-zinc-900">Generated report artifacts</h3>
          {reportMarkdown && (
            <details className="bg-white/80 border border-black/10 rounded-lg p-4">
              <summary className="cursor-pointer font-semibold text-zinc-900">Markdown (preview)</summary>
              <pre className="mt-3 text-xs text-zinc-900 whitespace-pre-wrap font-mono">{reportMarkdown}</pre>
            </details>
          )}
          {reportHtml && (
            <details className="bg-white/80 border border-black/10 rounded-lg p-4">
              <summary className="cursor-pointer font-semibold text-zinc-900">HTML (preview)</summary>
              <pre className="mt-3 text-xs text-zinc-900 whitespace-pre-wrap font-mono">{reportHtml.slice(0, 4000)}{reportHtml.length > 4000 ? '\n…(truncated preview)…' : ''}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
