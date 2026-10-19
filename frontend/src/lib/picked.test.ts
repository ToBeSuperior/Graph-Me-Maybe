import { describe, expect, it } from 'vitest';
import { filesLink, groupState, toggled } from './picked';

describe('picking the files a question found', () => {
  it('turns one file, one hop or all of them on or off', () => {
    const all = new Set(['a', 'b', 'c']);
    expect([...toggled(all, ['b'], false)]).toEqual(['a', 'c']);
    expect([...toggled(new Set(['a']), ['b', 'c'], true)].sort()).toEqual(['a', 'b', 'c']);
    expect(toggled(all, ['a', 'b', 'c'], false).size).toBe(0);
  });

  it('says whether a hop is all, some or none picked', () => {
    expect(groupState(new Set(['a', 'b']), ['a', 'b'])).toBe('all');
    expect(groupState(new Set(['a']), ['a', 'b'])).toBe('some');
    expect(groupState(new Set(['c']), ['a', 'b'])).toBe('none');
    expect(groupState(new Set(), [])).toBe('none');
  });

  it('links the picked files, each with its folder, in the order shown', () => {
    const link = filesLink([{ id: 'a', folder: '1 found' }, { id: 'b', folder: '2 linked' }], 'why & how?');
    const query = new URL(link, 'http://x').searchParams;
    expect(new URL(link, 'http://x').pathname).toBe('/v1/documents/files.zip');
    expect(query.getAll('doc')).toEqual(['a', 'b']);
    expect(query.getAll('folder')).toEqual(['1 found', '2 linked']);
    expect(query.get('name')).toBe('why & how?');
  });
});
