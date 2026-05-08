'use client';

import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { FaBroom, FaDownload, FaCheckCircle, FaThumbsUp, FaThumbsDown, FaExclamationTriangle } from 'react-icons/fa';

interface DataCleanerProps {
  files: string[];
  etlCode: string | null;
  assessmentData: any;
  userFeedback: Array<{ step: string; liked: boolean; comment?: string }>;
  onComplete: () => void;
  onFeedback: (liked: boolean, comment?: string) => void;
}

interface CleaningResult {
  fileName: string;
  originalRows: number;
  cleanedRows: number;
  duplicatesRemoved: number;
  missingValuesHandled: number;
  blobUrl: string;
}

export default function DataCleaner({ files, etlCode, assessmentData, userFeedback, onComplete, onFeedback }: DataCleanerProps) {
  const [showConfirmation, setShowConfirmation] = useState(true);
  const [cleaning, setCleaning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [currentFile, setCurrentFile] = useState('');
  const [results, setResults] = useState<CleaningResult[]>([]);
  const [learningFromFeedback, setLearningFromFeedback] = useState(false);

  const handleConfirm = () => {
    setShowConfirmation(false);
    startCleaning();
  };

  const startCleaning = async () => {
    setCleaning(true);
    
    if (userFeedback.some(f => !f.liked)) {
      setLearningFromFeedback(true);
      await new Promise(resolve => setTimeout(resolve, 2000));
      setLearningFromFeedback(false);
    }

    const cleaningResults: CleaningResult[] = [];

    for (let i = 0; i < files.length; i++) {
      setCurrentFile(files[i]);
      
      for (let p = 0; p <= 100; p += 5) {
        await new Promise(resolve => setTimeout(resolve, 50));
        setProgress(((i * 100 + p) / files.length));
      }

      const originalRows = Math.floor(Math.random() * 100000) + 10000;
      const duplicatesRemoved = Math.floor(Math.random() * 1000) + 100;
      const missingValuesHandled = Math.floor(Math.random() * 500) + 50;
      
      cleaningResults.push({
        fileName: files[i],
        originalRows,
        cleanedRows: originalRows - duplicatesRemoved,
        duplicatesRemoved,
        missingValuesHandled,
        blobUrl: `https://storage.blob.core.windows.net/cleaned-data/${files[i]}_cleaned_${Date.now()}.csv`
      });
    }

    setResults(cleaningResults);
    setCleaning(false);
  };

  const handleDownloadAll = () => {
    results.forEach(result => {
      console.log(`Downloading: ${result.blobUrl}`);
    });
    alert('All cleaned files downloaded! (In production, files would be downloaded from blob storage)');
  };

  const handleLike = () => {
    onFeedback(true);
    setTimeout(() => {
      onComplete();
    }, 1000);
  };

  const handleDislike = () => {
    const comment = prompt('What would you like us to improve in the data cleaning?');
    onFeedback(false, comment || undefined);
    
    setTimeout(() => {
      alert('Thank you for your feedback! We are learning from your input and will improve the cleaning process...');
      setResults([]);
      setCleaning(false);
      setShowConfirmation(true);
    }, 500);
  };

  if (showConfirmation) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="space-y-6"
      >
        <div className="text-center">
          <FaExclamationTriangle className="text-6xl text-yellow-500 mx-auto mb-4" />
          <h2 className="text-3xl font-bold text-zinc-900 mb-2">Are You Sure?</h2>
          <p className="text-black/60 mb-6">
            This will clean {files.length} file{files.length !== 1 ? 's' : ''} and remove duplicates and handle missing values.
            The original data will be preserved.
          </p>
        </div>

        <div className="bg-white/90 border border-black/10 rounded-lg p-6">
          <h3 className="font-semibold text-zinc-900 mb-4">What will be done:</h3>
          <ul className="space-y-2">
            <li className="flex items-center gap-3">
              <FaCheckCircle className="text-[#0070AD]" />
              <span className="text-black/80">Remove duplicate records</span>
            </li>
            <li className="flex items-center gap-3">
              <FaCheckCircle className="text-[#0070AD]" />
              <span className="text-black/80">Handle missing values (fill with median/mode)</span>
            </li>
            <li className="flex items-center gap-3">
              <FaCheckCircle className="text-[#0070AD]" />
              <span className="text-black/80">Standardize data types</span>
            </li>
            <li className="flex items-center gap-3">
              <FaCheckCircle className="text-[#0070AD]" />
              <span className="text-black/80">Save cleaned files to blob storage</span>
            </li>
          </ul>
        </div>

        <div className="flex gap-4">
          <button
            onClick={handleConfirm}
            className="flex-1 py-4 bg-gradient-to-r from-[#0070AD] to-[#12ABDB] text-white font-semibold rounded-lg hover:shadow-lg transition-all"
          >
            Yes, Proceed with Cleaning
          </button>
          <button
            onClick={onComplete}
            className="flex-1 py-4 bg-white/90 border border-black/10 text-black/80 font-semibold rounded-lg hover:bg-white transition-all"
          >
            Cancel
          </button>
        </div>
      </motion.div>
    );
  }

  if (cleaning) {
    return (
      <div className="space-y-6">
        <div className="text-center">
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
            className="inline-block"
          >
            <FaBroom className="text-6xl text-white/65 mx-auto mb-4" />
          </motion.div>
          <h2 className="text-3xl font-bold text-zinc-900 mb-2">Cleaning Data</h2>
          <p className="text-black/60">
            {learningFromFeedback ? 'Learning from your feedback...' : `Processing: ${currentFile}`}
          </p>
        </div>

        <div className="space-y-4">
          <div className="relative h-4 bg-black/10 rounded-full overflow-hidden">
            <motion.div
              className="h-full bg-gradient-to-r from-[#0070AD] to-[#12ABDB]"
              animate={{ width: `${progress}%` }}
              transition={{ duration: 0.3 }}
            />
          </div>
          <div className="text-center text-2xl font-bold text-[#0070AD]">{Math.floor(progress)}%</div>
        </div>

        {learningFromFeedback && (
          <div className="bg-white/90 border border-black/10 rounded-lg p-4">
            <div className="flex items-center gap-3">
              <motion.div
                animate={{ scale: [1, 1.2, 1] }}
                transition={{ duration: 1, repeat: Infinity }}
              >
                🧠
              </motion.div>
              <div>
                <div className="font-semibold text-zinc-900">AI Learning in Progress</div>
                <div className="text-sm text-black/60">
                  Adapting cleaning strategy based on your previous feedback...
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  if (results.length > 0) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-3xl font-bold text-zinc-900 mb-2">Cleaning Complete!</h2>
            <p className="text-black/60">Your data has been cleaned and saved to blob storage</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleLike}
              className="p-3 bg-[#0070AD]/10 text-[#0070AD] rounded-lg hover:bg-[#0070AD]/15 transition-colors"
            >
              <FaThumbsUp className="text-xl" />
            </button>
            <button
              onClick={handleDislike}
              className="p-3 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors"
            >
              <FaThumbsDown className="text-xl" />
            </button>
          </div>
        </div>

        {/* Results */}
        <div className="space-y-4">
          {results.map((result, idx) => (
            <motion.div
              key={result.fileName}
              className="border-2 border-[#0070AD]/50 bg-[#0070AD]/10 rounded-lg p-6"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.1 }}
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-xl font-bold text-white/90">{result.fileName}</h3>
                <FaCheckCircle className="text-3xl text-[#12ABDB]" />
              </div>

              <div className="grid grid-cols-4 gap-4 mb-4">
                <div className="bg-black/20 p-4 rounded-lg">
                  <div className="text-sm text-white/55">Original Rows</div>
                  <div className="text-2xl font-bold text-white/90">{result.originalRows.toLocaleString()}</div>
                </div>
                <div className="bg-black/20 p-4 rounded-lg">
                  <div className="text-sm text-white/55">Cleaned Rows</div>
                  <div className="text-2xl font-bold text-[#12ABDB]">{result.cleanedRows.toLocaleString()}</div>
                </div>
                <div className="bg-black/20 p-4 rounded-lg">
                  <div className="text-sm text-white/55">Duplicates Removed</div>
                  <div className="text-2xl font-bold text-red-400">{result.duplicatesRemoved.toLocaleString()}</div>
                </div>
                <div className="bg-black/20 p-4 rounded-lg">
                  <div className="text-sm text-white/55">Missing Values Fixed</div>
                  <div className="text-2xl font-bold text-amber-400">{result.missingValuesHandled.toLocaleString()}</div>
                </div>
              </div>

              <div className="bg-black/20 p-4 rounded-lg">
                <div className="text-sm text-white/55 mb-2">Blob Storage URL</div>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-sm text-white/90 bg-black/20 p-2 rounded border border-white/10 overflow-x-auto">
                    {result.blobUrl}
                  </code>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(result.blobUrl);
                      alert('URL copied to clipboard!');
                    }}
                    className="px-4 py-2 bg-[#0070AD] text-white rounded-lg hover:bg-[#12ABDB] transition-colors"
                  >
                    Copy
                  </button>
                </div>
              </div>
            </motion.div>
          ))}
        </div>

        {/* Actions */}
        <div className="flex gap-4">
          <button
            onClick={handleDownloadAll}
            className="flex-1 flex items-center justify-center gap-3 py-4 bg-gradient-to-r from-[#0070AD] to-[#12ABDB] text-white font-semibold rounded-lg hover:shadow-lg transition-all"
          >
            <FaDownload />
            Download All Cleaned Files
          </button>
          <button
            onClick={handleLike}
            className="px-8 py-4 bg-[#0070AD] text-white font-semibold rounded-lg hover:bg-[#12ABDB] transition-all"
          >
            Complete Pipeline
          </button>
        </div>
      </div>
    );
  }

  return null;
}
