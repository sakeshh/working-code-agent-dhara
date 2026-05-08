'use client';

import { motion } from 'framer-motion';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { FaSignOutAlt } from 'react-icons/fa';
import Sidebar from '@/components/Sidebar';
import ChatWindow from '@/components/ChatWindow';
import AnimatedBackground from '@/components/AnimatedBackground';

export default function ChatPage() {
  const router = useRouter();

  useEffect(() => {
    window.scrollTo(0, 0);
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }, []);

  const handleSignOut = () => {
    if (typeof window !== 'undefined') {
      window.localStorage.removeItem('agentThreadId');
    }
    router.push('/');
  };

  return (
    <div className="relative z-[1] flex min-h-0 w-full flex-1 flex-row overflow-hidden bg-transparent text-zinc-900">
      <AnimatedBackground className="pointer-events-none" />

      <Sidebar />

      <main className="relative z-10 flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden self-stretch bg-white/60 backdrop-blur-sm max-lg:pt-14 max-lg:pl-14 max-lg:pr-14">
        <button
          type="button"
          onClick={handleSignOut}
          className="fixed right-4 top-1 z-50 flex max-w-[calc(100vw-2rem)] items-center gap-2 rounded-lg border border-black/10 bg-white/90 px-3 py-2 text-xs font-medium text-black/80 backdrop-blur transition-colors hover:bg-white"
          title="Sign out"
          aria-label="Sign out"
        >
          <FaSignOutAlt className="text-sm" />
          <span className="hidden sm:inline truncate">Sign out</span>
        </button>
        <ChatWindow />
      </main>
    </div>
  );
}
