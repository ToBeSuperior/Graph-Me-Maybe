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
