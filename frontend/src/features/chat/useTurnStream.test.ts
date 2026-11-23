import { describe, expect, it } from 'vitest';
import { sseEvents } from './useTurnStream';

describe('server-sent events', () => {
  it('waits for the rest of an event cut across chunks', () => {
    const whole = 'event: searched\ndata: {"sources":[]}\n\nevent: answered\ndata: {"answer":"a"}\n\n';
    const first = sseEvents(whole.slice(0, 50));
    expect(first.events).toEqual([{ event: 'searched', data: '{"sources":[]}' }]);
    const second = sseEvents(first.rest + whole.slice(50));
    expect(second.events).toEqual([{ event: 'answered', data: '{"answer":"a"}' }]);
    expect(second.rest).toBe('');
  });

  it('reads CRLF lines and data written over several lines', () => {
    expect(sseEvents('event: error\r\ndata: {"message":\r\ndata: "x"}\r\n\r\n').events)
      .toEqual([{ event: 'error', data: '{"message":\n"x"}' }]);
  });
});

describe('a CR at the end of a chunk', () => {
  it('waits for the LF the next chunk may open with, so no event is cut early', () => {
    const first = sseEvents('event: searched\r');
    expect(first.events).toEqual([]);
    const second = sseEvents(`${first.rest}\ndata: {}\r\n\r\n`);
    expect(second.events).toEqual([{ event: 'searched', data: '{}' }]);
  });
});

import { QueryClient } from '@tanstack/react-query';
import { afterEach, vi } from 'vitest';
import { keys } from '../../lib/queries';
import { streamTurn, type Live } from './useTurnStream';

/** A reply that sends `chunks` and then, unless `close`, stays open until the request is aborted. */
function stubReply(chunks: string[], close = true) {
  vi.stubGlobal('fetch', (_url: string, init: RequestInit) => Promise.resolve(new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(new TextEncoder().encode(c));
      if (close) controller.close();
      else init.signal?.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError')));
    },
  }), { status: 200 })));
}

function recorder() {
  let state: Live | null = null;
  return { put: (update: (now: Live | null) => Live | null) => { state = update(state); }, now: () => state };
}

describe('a turn asked', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('stopped by the reader, still asks the thread again: the server kept what it had', async () => {
    stubReply(['event: searched\ndata: {"sources":[{"id":"a"}],"queries":[]}\n\n'], false);
    const client = new QueryClient();
    const spy = vi.spyOn(client, 'invalidateQueries');
    const control = new AbortController();
    const rec = recorder();
    const done = streamTurn(client, 't1', control, { question: 'What is the invented pressure?' }, rec.put);
    await new Promise((r) => setTimeout(r, 10));
    control.abort();
    await done;
    const asked = spy.mock.calls.map(([f]) => f?.queryKey);
    expect(asked).toContainEqual(keys.thread('t1'));
    expect(asked).toContainEqual(keys.threads);
    expect(rec.now()?.stage).toBe('stopped');
  });

  it('writes a late check through the same guard as every other event', async () => {
    stubReply([
      'event: answered\ndata: {"answer":"2 bar.","sources":[],"text_only":false}\n\n',
      'event: checked\ndata: {"checked":true,"claims":[]}\n\n',
    ]);
    const client = new QueryClient();
    const rec = recorder();
    await streamTurn(client, 't1', new AbortController(), { question: 'q' }, rec.put);
    expect(rec.now()?.answer?.checked).toBe(true);
    // a turn no longer current: its writes go nowhere, the check among them
    stubReply(['event: checked\ndata: {"checked":true}\n\n']);
    let writes = 0;
    await streamTurn(client, 't1', new AbortController(), { question: 'q' }, () => { writes += 1; });
    expect(writes).toBeGreaterThan(0); // the check was offered to the guard, not written past it
  });

  it('asks the thread again when it ends, even with no one left to show it', async () => {
    stubReply(['event: saved\ndata: {"question":"q"}\n\n']);
    const client = new QueryClient();
    const spy = vi.spyOn(client, 'invalidateQueries');
    await streamTurn(client, 't1', new AbortController(), { question: 'q' }, () => {});
    expect(spy).toHaveBeenCalledWith({ queryKey: keys.thread('t1') });
  });
});
