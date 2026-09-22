import { useEffect, useRef, useState } from 'react';

/**
 * Tracks "seconds since first seen in this UI" per id. There are no server
 * timestamps, so this is a client-side convenience timer, not an authoritative
 * queue wait time; it resets on reload. Returns a lookup function.
 */
export function useElapsed(ids) {
  const seenAt = useRef(new Map());
  const [, tick] = useState(0);

  useEffect(() => {
    const now = Date.now();
    let added = false;
    for (const id of ids) {
      if (!seenAt.current.has(id)) { seenAt.current.set(id, now); added = true; }
    }
    if (added) tick(t => t + 1);
  }, [ids]);

  useEffect(() => {
    const timer = setInterval(() => tick(t => t + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  return function elapsedSeconds(id) {
    const start = seenAt.current.get(id);
    return start ? Math.max(0, Math.round((Date.now() - start) / 1000)) : 0;
  };
}
