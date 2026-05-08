'use client';

import { useState } from 'react';
import { motion } from 'framer-motion';
import { FaPlay, FaStop } from 'react-icons/fa';

export default function DataOrchestration() {
  const [schedule, setSchedule] = useState('batch');
  const [isRunning, setIsRunning] = useState(false);

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.2, ease: 'easeOut' }}
    >
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-black/55">
        Adaptive Data Orchestration
      </h3>

      <div>
        <label className="mb-2 block text-xs font-medium text-black/55">Schedule Selector</label>
        <div className="relative">
          <select
            value={schedule}
            onChange={(e) => setSchedule(e.target.value)}
            className="w-full appearance-none rounded-xl border border-black/10 bg-white/90 px-3 py-2 text-sm text-zinc-900 outline-none focus:border-[#0070AD]/40 focus:ring-2 focus:ring-[#0070AD]/20"
          >
            <option value="batch">Batch</option>
            <option value="near-realtime">Near Real-Time</option>
            <option value="streaming">Streaming</option>
          </select>
          <div className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-black/40">
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
      </div>

      <div className="space-y-2">
        <motion.button
          whileHover={{ scale: isRunning ? 1 : 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={() => setIsRunning(true)}
          disabled={isRunning}
          className={`flex w-full items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-semibold transition-all ${
            isRunning
              ? 'cursor-not-allowed border border-black/10 bg-white/70 text-black/35'
              : 'border border-[#0070AD]/40 bg-[#0070AD]/10 text-[#0070AD] hover:bg-[#0070AD]/15'
          }`}
        >
          <motion.span whileHover={{ scale: isRunning ? 1 : 1.12, rotate: isRunning ? 0 : 5 }}>
            <FaPlay />
          </motion.span>
          Start Pipeline
        </motion.button>

        <motion.button
          whileHover={{ scale: !isRunning ? 1 : 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={() => setIsRunning(false)}
          disabled={!isRunning}
          className={`flex w-full items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-semibold transition-all ${
            !isRunning
              ? 'cursor-not-allowed border border-black/10 bg-white/70 text-black/35'
              : 'border border-black/10 bg-white/85 text-zinc-900 hover:bg-white'
          }`}
        >
          <motion.span whileHover={{ scale: 1.12, rotate: 5 }}>
            <FaStop />
          </motion.span>
          Stop Pipeline
        </motion.button>
      </div>

      <div className="mt-4">
        <label className="mb-2 block text-xs font-medium text-black/55">Pipeline Logs</label>
        <div className="h-32 overflow-y-auto rounded-xl border border-black/10 bg-white/90 p-3 font-mono text-xs text-[#0070AD]/90">
          {isRunning ? (
            <div className="space-y-1">
              <div>[{new Date().toLocaleTimeString()}] Pipeline started...</div>
              <div>[{new Date().toLocaleTimeString()}] Loading data source...</div>
              <div>[{new Date().toLocaleTimeString()}] Applying transformations...</div>
              <div className="animate-pulse">[{new Date().toLocaleTimeString()}] Processing...</div>
            </div>
          ) : (
            <div className="text-black/45">No active pipeline. Click Start Pipeline to begin.</div>
          )}
        </div>
      </div>
    </motion.div>
  );
}
