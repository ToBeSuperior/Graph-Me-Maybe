// A rendered page that fits its frame, with what is pointed at on it boxed: the wheel zooms it
// once the frame was clicked (until the pointer leaves -- scrolling past otherwise zoomed it),
// double-click or the buttons zoom too, and a drag moves it. The page is laid out at its own
// aspect inside the frame, and the marks are placed in the page's own pixels over it, so they
// move and scale with it. A new set of marks brings the passage they mark into view.
import { Maximize, Minus, Plus } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { cn } from '../lib/format';
import { useT } from '../lib/i18n';
import { Button } from './ui';

const MAX = 8;
/** How large a mark is shown when it is brought into view: as much of the frame as this, and
 * never closer than AUTO_MAX, so the page around it stays readable as context. */
const MARK_FILL = 0.6;
const AUTO_MAX = 2.5;

export interface Mark {
  /** left, top, right, bottom in the rendered page's pixels. */
  box: [number, number, number, number];
  /** The one being read, or others of the same kind. */
  strong?: boolean;
}

export function PageViewer({ src, alt, width, height, marks = [], className }: {
  src: string; alt: string; width: number; height: number; marks?: Mark[]; className?: string;
}) {
  const t = useT();
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [zoom, setZoom] = useState({ scale: 1, x: 0, y: 0 });
  // Which picture has drawn: a new page's marks wait for it, not for the one before.
  const [loadedSrc, setLoadedSrc] = useState('');
  const loaded = loadedSrc === src;
  const armed = useRef(false);
  const drag = useRef<{ x: number; y: number } | null>(null);

  useLayoutEffect(() => {
    const el = frame.current!;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  // Where the page sits in the frame at scale 1: as large as fits, centred.
  const fit = size.w && size.h ? Math.min(size.w / width, size.h / height) : 0;
  const page = { left: (size.w - width * fit) / 2, top: (size.h - height * fit) / 2, w: width * fit, h: height * fit };

  // A new page opens fitted; marks given, the strong one is brought into view instead.
  const markKey = marks.map((m) => `${m.box.join(',')}${m.strong ? '*' : ''}`).join('|');
  useEffect(() => {
    // The passage being read framed whole: the box around all of its marks.
    const strong = marks.some((m) => m.strong) ? marks.filter((m) => m.strong) : marks;
    if (!strong.length || !fit) { setZoom({ scale: 1, x: 0, y: 0 }); return; }
    const l = Math.min(...strong.map((m) => m.box[0]));
    const tp = Math.min(...strong.map((m) => m.box[1]));
    const r = Math.max(...strong.map((m) => m.box[2]));
    const b = Math.max(...strong.map((m) => m.box[3]));
    const w = (r - l) * fit;
    const h = (b - tp) * fit;
    const scale = Math.min(AUTO_MAX, Math.max(1, Math.min((size.w * MARK_FILL) / Math.max(w, 1), (size.h * MARK_FILL) / Math.max(h, 1))));
    // The mark's centre, in frame pixels at scale 1, put in the middle of the frame.
    const cx = page.left + ((l + r) / 2) * fit;
    const cy = page.top + ((tp + b) / 2) * fit;
    setZoom(scale === 1 ? { scale: 1, x: 0, y: 0 } : { scale, x: size.w / 2 - cx * scale, y: size.h / 2 - cy * scale });
    // Only when the page or the marks change, not as the reader zooms.
  // Once the frame is measured (fit > 0), not again on every resize the reader zoomed through.
  }, [src, markKey, fit > 0]);

  const zoomBy = (factor: number, at?: { clientX: number; clientY: number }) => {
    const box = frame.current!.getBoundingClientRect();
    const px = at ? at.clientX - box.left : box.width / 2;
    const py = at ? at.clientY - box.top : box.height / 2;
    setZoom((z) => {
      const scale = Math.min(MAX, Math.max(1, z.scale * factor));
      if (scale === 1) return { scale: 1, x: 0, y: 0 };
      const k = scale / z.scale;
      return { scale, x: px - (px - z.x) * k, y: py - (py - z.y) * k };
    });
  };

  useEffect(() => {
    const el = frame.current!;
    const wheel = (e: WheelEvent) => {
      if (!armed.current) return;
      e.preventDefault();
      zoomBy(Math.exp(-e.deltaY * 0.0015), e);
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  }, []);

  return (
    <div
      ref={frame}
      className={cn('relative overflow-hidden rounded-lg border border-line bg-subtle select-none',
        zoom.scale > 1 ? 'cursor-grab active:cursor-grabbing' : 'cursor-zoom-in', className)}
      style={{ touchAction: 'none' }}
      onPointerDown={(e) => {
        armed.current = true;
        if ((e.target as HTMLElement).closest('[data-zoom]')) return;
        drag.current = { x: e.clientX - zoom.x, y: e.clientY - zoom.y };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!drag.current || zoom.scale === 1) return;
        const { x, y } = drag.current;
        setZoom((z) => ({ ...z, x: e.clientX - x, y: e.clientY - y }));
      }}
      onPointerUp={() => { drag.current = null; }}
      onPointerCancel={() => { drag.current = null; }}
      onPointerLeave={() => { armed.current = false; }}
      onDoubleClick={(e) => {
        if ((e.target as HTMLElement).closest('[data-zoom]')) return;
        if (zoom.scale > 1) setZoom({ scale: 1, x: 0, y: 0 }); else zoomBy(2.5, e);
      }}
    >
      {!loaded && <div className="skeleton absolute inset-3" />}
      <div className="absolute inset-0" style={{ transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.scale})`, transformOrigin: '0 0' }}>
        {fit > 0 && (
          <div className="absolute" style={{ left: page.left, top: page.top, width: page.w, height: page.h }}>
            <img src={src} alt={alt} draggable={false} onLoad={() => setLoadedSrc(src)} className="absolute inset-0 h-full w-full bg-white" />
            {loaded && marks.map((m, i) => {
              const [l, tp, r, b] = m.box;
              return (
                <div key={i} aria-hidden
                  // Multiplied like a highlighter -- the page is white in either theme -- and drawn
                  // hugging its unit: side by side cells' marks overlap otherwise and hide the words.
                  className={cn('pointer-events-none absolute rounded-[3px] mix-blend-multiply',
                    m.strong ? 'bg-mark/45 outline-1 outline-accent' : 'bg-mark/25')}
                  style={{ left: l * fit - 1, top: tp * fit - 1, width: (r - l) * fit + 2, height: (b - tp) * fit + 2 }} />
              );
            })}
          </div>
        )}
      </div>
      <div data-zoom className="absolute right-2 bottom-2 flex gap-1 rounded-lg border border-line bg-panel/90 p-0.5 shadow-sm backdrop-blur">
        <Button variant="ghost" size="icon-sm" aria-label={t('zoomIn')} onClick={() => zoomBy(1.4)}><Plus className="size-3.5" /></Button>
        <Button variant="ghost" size="icon-sm" aria-label={t('zoomOut')} onClick={() => zoomBy(1 / 1.4)}><Minus className="size-3.5" /></Button>
        <Button variant="ghost" size="icon-sm" aria-label={t('zoomFit')} onClick={() => setZoom({ scale: 1, x: 0, y: 0 })}><Maximize className="size-3.5" /></Button>
      </div>
    </div>
  );
}
