'use client';

import { motion } from 'framer-motion';
import { FaExclamationTriangle, FaCheckCircle, FaPlus } from 'react-icons/fa';

export default function DataQuality() {
  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.1, ease: 'easeOut' }}
    >
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-black/55">
        Data Quality & Validation
      </h3>

      <div className="space-y-2">
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          className="flex w-full items-center gap-3 rounded-xl border border-black/10 bg-white/85 px-3 py-2.5 text-sm font-medium text-zinc-900 transition-all hover:border-[#0070AD]/25 hover:bg-white"
        >
          <motion.span whileHover={{ scale: 1.12, rotate: 5 }}>
            <FaExclamationTriangle className="text-amber-400" />
          </motion.span>
          View Anomalies
        </motion.button>

        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          className="flex w-full items-center gap-3 rounded-xl border border-black/10 bg-white/85 px-3 py-2.5 text-sm font-medium text-zinc-900 transition-all hover:border-[#0070AD]/25 hover:bg-white"
        >
          <motion.span whileHover={{ scale: 1.12, rotate: 5 }}>
            <FaCheckCircle className="text-[#0070AD]" />
          </motion.span>
          Apply Validation Rules
        </motion.button>

        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          className="flex w-full items-center gap-3 rounded-xl border border-[#0070AD]/40 bg-[#0070AD]/15 px-3 py-2.5 text-sm font-semibold text-[#0070AD] transition-all hover:bg-[#0070AD]/20"
        >
          <motion.span whileHover={{ scale: 1.12, rotate: 5 }}>
            <FaPlus />
          </motion.span>
          Add Custom Rule
        </motion.button>
      </div>
    </motion.div>
  );
}
