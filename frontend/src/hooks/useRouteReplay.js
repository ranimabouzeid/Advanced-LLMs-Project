import { useEffect, useRef, useState } from 'react';

const STEP_MS = 220;

function prefersReducedMotion() {
  return typeof window !== 'undefined' &&
    Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

/**
 * Plays a visual-only replay of committed delivery legs, one robot leg at a
 * time. Server state stays authoritative throughout; this only produces
 * overlay positions for WarehouseGrid to draw on top of the committed map.
 */
export function useRouteReplay() {
  const [positions, setPositions] = useState(null); // { [robotId]: {x,y} } | null
  const [playing, setPlaying] = useState(false);
  const cancelRef = useRef(null);

  function stop() {
    if (cancelRef.current) cancelRef.current();
    cancelRef.current = null;
    setPlaying(false);
    setPositions(null);
  }

  function play(legs) {
    stop();
    if (!legs?.length || prefersReducedMotion()) return;
    let cancelled = false;
    cancelRef.current = () => { cancelled = true; };
    setPlaying(true);
    (async () => {
      for (const { robotId, route } of legs) {
        for (const point of route) {
          if (cancelled) return;
          setPositions(prev => ({ ...prev, [robotId]: point }));
          await new Promise(resolve => setTimeout(resolve, STEP_MS));
        }
      }
      if (!cancelled) { setPlaying(false); setPositions(null); }
    })();
  }

  useEffect(() => () => { if (cancelRef.current) cancelRef.current(); }, []);

  return { positions, playing, play, skip: stop };
}
