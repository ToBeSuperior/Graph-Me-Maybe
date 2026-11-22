// A turn asked in a conversation: `POST /v1/threads/{id}/turns` as server-sent events, each stage
// shown as it comes (found, answered, checked) and the thread's saved turn put in its query once
// the server keeps it. A turn stopped is saved by the server with what it had.
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Answer, Schemas } from '../../api/client';
import { keyWanted } from '../../lib/key';
import { keys } from '../../lib/queries';

export type Found = Schemas['Found'];
export type Thread = Schemas['Thread'];
export type ThreadTurn = Schemas['ThreadTurn'];

export interface SseEvent { event: string; data: string }

/** The events whole in `buffer`, and what is left of it -- an event cut across two chunks waits
 * for the rest, and a CR ending a chunk waits for the LF that may follow it. */
export function sseEvents(buffer: string): { events: SseEvent[]; rest: string } {
  const held = buffer.endsWith('\r') ? '\r' : '';
  const blocks = buffer.slice(0, buffer.length - held.length).replace(/\r\n?/g, '\n').split('\n\n');
  const rest = (blocks.pop() ?? '') + held;
  const events = blocks.flatMap((block) => {
    let event = 'message';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
    }
    return data.length ? [{ event, data: data.join('\n') }] : [];
  });
  return { events, rest };
}

export type Stage = 'searching' | 'answering' | 'checking' | 'stopped' | 'failed';

/** The turn being asked, before the thread has it. `after` is how many turns the thread had
 * when it was asked: once the thread holds more, the saved one is shown instead. A failure with
 * no `error` is a reply that could not be read. */
export interface Live { question: string; found?: Found; answer?: Answer; stage: Stage; error?: string; after: number; exclude?: string[] }

export function useTurnStream(threadId: string) {
  const client = useQueryClient();
  const [live, setLive] = useState<Live | null>(null);
  const running = useRef<AbortController | null>(null);
  useEffect(() => () => running.current?.abort(), []);

  /** `question` asked; `exclude` leaves those documents out of this turn alone. */
  const ask = useCallback(async (question: string, { documents, stamp, exclude }:
    { documents?: number; stamp?: string | null; exclude?: string[] } = {}) => {
    running.current?.abort();
    const control = new AbortController();
    running.current = control;
    const after = client.getQueryData<Thread>(keys.thread(threadId))?.turns?.length ?? 0;
    const change = (next: Partial<Live>) => setLive((now) => (running.current === control ? { ...(now ?? { question, stage: 'searching', after, exclude }), ...next } : now));
    setLive({ question, stage: 'searching', after, exclude });
    try {
      const response = await fetch(`/v1/threads/${encodeURIComponent(threadId)}/turns`, {
        method: 'POST', signal: control.signal,
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ question, documents, stamp, exclude }),
      });
      if (!response.ok || !response.body) {
        if (response.status === 401) keyWanted();
        const detail = ((await response.json().catch(() => undefined)) as { detail?: unknown } | undefined)?.detail;
        change({ stage: 'failed', error: typeof detail === 'string' ? detail : response.statusText || String(response.status) });
        return;
      }
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = '';
      let ended = false; // `saved` or `error` said: the turn came to its end
      reading: for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const got = sseEvents(buffer + value);
        buffer = got.rest;
        for (const { event, data } of got.events) {
          let body;
          try {
            body = JSON.parse(data);
          } catch {
            void reader.cancel();
            change({ stage: 'failed', error: undefined });
            break reading; // the turn the server kept is read below
          }
          if (event === 'saved' || event === 'error') ended = true;
          // nothing found: no answer is written, so there is no stage after the search
          if (event === 'searched') change({ found: body as Found, ...((body as Found).sources.length ? { stage: 'answering' } : {}) });
          else if (event === 'answered') change({ answer: body as Answer, stage: 'checking' });
          else if (event === 'checked') setLive((now) => now?.answer ? { ...now, answer: { ...now.answer, ...body } } : now);
          else if (event === 'error') change({ stage: 'failed', error: (body as { message: string }).message });
          else if (event === 'saved') {
            client.setQueryData<Thread>(keys.thread(threadId), (thread) => thread && {
              ...thread, title: thread.title || (body as ThreadTurn).question, turns: [...(thread.turns ?? []), body as ThreadTurn] });
          }
        }
      }
      // The stream's end is the turn's end: the thread as the server keeps it now.
      await client.invalidateQueries({ queryKey: keys.thread(threadId) });
      void client.invalidateQueries({ queryKey: keys.threads });
      // A stream cut before its end leaves the turn shown as stopped, not gone.
      setLive((now) => (running.current !== control || !now || now.stage === 'failed' ? now : ended ? null : { ...now, stage: 'stopped' }));
    } catch (error) {
      if (control.signal.aborted) change({ stage: 'stopped' });
      else change({ stage: 'failed', error: String(error) });
    }
  }, [client, threadId]);

  /** Stopped where it is: the server keeps what was paid for. */
  const stop = useCallback(() => running.current?.abort(), []);
  return { live, ask, stop };
}
