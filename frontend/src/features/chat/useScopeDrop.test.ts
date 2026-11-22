import { describe, expect, it } from 'vitest';
import { uploadedDocuments } from './useScopeDrop';

describe('the documents dropped files became', () => {
  it('are found by what the files hold, never by their names', () => {
    const documents = [
      { id: 'mine', label: 'Invented memo.docx', content_hash: 'abc123def4567890', mentions: 0 },
      { id: 'namesake', label: 'Invented memo.docx', content_hash: 'ffff000011112222', mentions: 0 },
      { id: 'unhashed', label: 'Other.docx', mentions: 0 },
    ];
    expect(uploadedDocuments(documents, ['abc123def4'])).toEqual(['mine']);
    expect(uploadedDocuments(documents, ['0000000000'])).toEqual([]);
  });
});
