import { describe, expect, it } from 'vitest';
import { reason } from './reasons';

const t = ((key: string) => `<${key}>`) as never;

describe('why a document failed, in the chosen language', () => {
  it('names each reason the server gives by its own word', () => {
    expect(reason(t, 'the file is locked by a password or rights protection -- open it in Office')).toBe('<whyProtected>');
    expect(reason(t, 'could not be read -- the file may be damaged or hold nothing readable (details in the server log)'))
      .toBe('<whyUnread>');
    expect(reason(t, 'the folder did not list in time -- not read')).toBe('<whyUnlisted>');
    expect(reason(t, 'no longer readable in Box')).toBe('<whyLostBox>');
  });
  it('says a reason it does not know as the server wrote it', () => {
    expect(reason(t, 'an invented reason')).toBe('an invented reason');
  });
});
