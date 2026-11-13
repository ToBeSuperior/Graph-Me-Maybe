// Mail as a source: an account is connected with its server and app password (IMAP), counted,
// and only then asked about -- read all, or only the last months -- before one message is read.
// Each account is read into a workspace of its own. The password goes in the request and nowhere
// else: the page keeps it only while it is typed, and the server never answers it.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { api, call, ApiError } from '../../api/client';
import type { components } from '../../api/schema';
import { Button, Input, Spinner } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { keys } from '../../lib/queries';
import { PageHeader } from './ManageLayout';

type Account = components['schemas']['MailAccount'];

// The same servers as `IMAP_PRESETS` (adapters/sources/imap_mail.py); the server exposes no route
// for them. DaouOffice and Hiworks take the company's own host, so they offer none.
const PRESETS: { id: string; name: string; host: string }[] = [
  { id: 'naverworks', name: 'NAVER WORKS', host: 'imap.worksmobile.com' },
  { id: 'naver', name: 'Naver', host: 'imap.naver.com' },
  { id: 'daum', name: 'Daum', host: 'imap.daum.net' },
  { id: 'zoho', name: 'Zoho', host: 'imap.zoho.com' },
  { id: 'gmail', name: 'Gmail', host: 'imap.gmail.com' },
  { id: 'daou', name: 'DaouOffice', host: '' },
  { id: 'hiworks', name: 'Hiworks', host: '' },
];

const size = (bytes: number) => bytes >= 1e9 ? `${Math.round(bytes / 1e8) / 10} GB`
  : bytes >= 1e6 ? `${Math.round(bytes / 1e6)} MB` : `${Math.round(bytes / 1e3)} KB`;

/** The date `months` months before today, as the server takes it (a day, UTC). */
export function monthsAgo(months: number, now = new Date()) {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - months, now.getUTCDate()));
  return d.toISOString().slice(0, 10);
}

export function MailPage() {
  const t = useT();
  // The Google sign-in's address while the browser signs in: its account lands on the server.
  const [signing, setSigning] = useState<{ url: string; had: number } | null>(null);
  const accounts = useQuery({ queryKey: keys.mail, queryFn: () => call(api.GET('/v1/mail/accounts')),
    // Followed while an account is being read, and while a Google sign-in is under way.
    refetchInterval: (q) => (q.state.data?.some((a) => a.state === 'reading')
      || (signing && (q.state.data?.length ?? 0) <= signing.had) ? 2000 : false) });
  return (
    <div>
      <PageHeader title={t('mailTitle')} description={t('mailBody')} />
      <div className="grid max-w-2xl gap-4 p-6 text-sm">
        {!accounts.data ? <Spinner /> : accounts.data.map((a) => <AccountCard key={a.id} account={a} />)}
        {accounts.data?.length === 0 && <p className="text-muted">{t('mailNone')}</p>}
        <GoogleConnect signing={signing && (accounts.data?.length ?? 0) <= signing.had ? signing.url : null}
          onStarted={(url) => setSigning({ url, had: accounts.data?.length ?? 0 })} />
        <p className="text-muted">{t('mailImapHow')}</p>
        <ConnectForm />
      </div>
    </div>
  );
}

/** Gmail by a Google sign-in in the browser: no app password. The server opens the browser;
 * the link is there when it could not. */
function GoogleConnect({ signing, onStarted }: { signing: string | null; onStarted: (url: string) => void }) {
  const t = useT();
  const [said, setSaid] = useState<string | null>(null);
  const start = async () => {
    setSaid(null);
    try {
      onStarted((await call(api.POST('/v1/mail/google/connect'))).url);
    } catch (error) {
      setSaid(error instanceof ApiError ? error.message : String(error));
    }
  };
  return (
    <section className="grid gap-2 rounded-xl border border-line bg-panel p-4" aria-label="Gmail">
      <p className="text-muted">{t('mailGoogleHow')}</p>
      <div><Button variant="primary" onClick={() => void start()}>{t('mailGoogle')}</Button></div>
      {signing && (
        <p role="status" className="text-muted">{t('mailGoogleOpened')}{' '}
          <a href={signing} target="_blank" rel="noreferrer" className="underline">{t('driveOpenLink')}</a></p>
      )}
      {said && <p role="alert" className="text-danger-fg">{said}</p>}
    </section>
  );
}

function ConnectForm() {
  const t = useT();
  const client = useQueryClient();
  const [preset, setPreset] = useState(PRESETS[0].id);
  const [host, setHost] = useState(PRESETS[0].host);
  const [port, setPort] = useState('993');
  const [address, setAddress] = useState('');
  const [password, setPassword] = useState('');
  const connect = useMutation({
    mutationFn: () => call(api.POST('/v1/mail/accounts', { body: { host, port: Number(port), address, password } })),
    onSuccess: async () => {
      setPassword('');
      await Promise.all([client.invalidateQueries({ queryKey: keys.mail }), client.invalidateQueries({ queryKey: keys.workspaces })]);
    },
  });
  const choose = (id: string) => {
    setPreset(id);
    setHost(PRESETS.find((p) => p.id === id)?.host ?? '');
    setPort('993');
  };
  const submit = (event: FormEvent) => { event.preventDefault(); connect.mutate(); };
  const fixed = !!PRESETS.find((p) => p.id === preset)?.host;
  return (
    <form onSubmit={submit} className="grid gap-3 rounded-xl border border-line bg-panel p-4" aria-label={t('mailConnect')}>
      <label className="grid gap-1">{t('mailPreset')}
        <select value={preset} onChange={(e) => choose(e.target.value)}
          className="h-8 rounded-md border border-line bg-panel px-2 text-sm text-fg">
          {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          <option value="">{t('mailOther')}</option>
        </select>
      </label>
      <div className="grid grid-cols-[minmax(0,1fr)_6rem] gap-2">
        <label className="grid gap-1">{t('mailHost')}
          <Input value={host} onChange={(e) => setHost(e.target.value)} readOnly={fixed} required /></label>
        <label className="grid gap-1">{t('mailPort')}
          <Input type="number" value={port} onChange={(e) => setPort(e.target.value)} readOnly={fixed} required /></label>
      </div>
      <label className="grid gap-1">{t('mailAddress')}
        <Input type="email" value={address} onChange={(e) => setAddress(e.target.value)} autoComplete="username" required /></label>
      <label className="grid gap-1">{t('mailPassword')}
        <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" required /></label>
      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" busy={connect.isPending}>{t('mailConnect')}</Button>
        {connect.error && <p role="alert" className="text-danger-fg">{connect.error instanceof ApiError ? connect.error.message : String(connect.error)}</p>}
      </div>
    </form>
  );
}

function AccountCard({ account }: { account: Account }) {
  const t = useT();
  const client = useQueryClient();
  const [months, setMonths] = useState('12');
  const [leaving, setLeaving] = useState(false);
  const [typed, setTyped] = useState('');
  const [said, setSaid] = useState<string | null>(null);
  const act = async (run: () => Promise<unknown>) => {
    setSaid(null);
    try {
      await run();
      await Promise.all([client.invalidateQueries({ queryKey: keys.mail }), client.invalidateQueries({ queryKey: keys.workspaces })]);
    } catch (error) {
      setSaid(error instanceof ApiError ? error.message : String(error));
    }
  };
  const read = (since: string | null) => act(async () => {
    client.setQueryData(keys.build, await call(api.POST('/v1/mail/accounts/{account}/read', { params: { path: { account: account.id } }, body: { since } })));
  });
  const earlier = () => act(async () => {
    client.setQueryData(keys.build, await call(api.POST('/v1/mail/accounts/{account}/earlier', { params: { path: { account: account.id } } })));
  });
  const leave = (workspace: 'delete' | 'keep') => act(() => call(api.DELETE('/v1/mail/accounts/{account}', {
    params: { path: { account: account.id }, query: { workspace } } })));
  const n = Math.max(1, Math.floor(Number(months)) || 1);
  const count = account.count;
  return (
    <section className="grid gap-2 rounded-xl border border-line bg-panel p-4" aria-label={account.address}>
      <h2 className="font-semibold">{account.address}</h2>
      {account.state === 'counted' && count && (
        <>
          <p data-testid="mail-question">{count.attachment_bytes == null
            ? t('mailCountedGmail', count.messages.toLocaleString('en-US'), count.oldest?.slice(0, 7) ?? '—',
              count.attachments.toLocaleString('en-US'))
            : t('mailCounted', count.messages.toLocaleString('en-US'), count.oldest?.slice(0, 7) ?? '—',
              count.attachments.toLocaleString('en-US'), size(count.attachment_bytes))}</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" onClick={() => read(null)}>{t('mailReadAll')}</Button>
            <Input type="number" min={1} value={months} onChange={(e) => setMonths(e.target.value)}
              aria-label={t('mailMonths')} className="w-16" />
            <Button onClick={() => read(monthsAgo(n))}>{t('mailLastMonths', n)}</Button>
            <Button variant="ghost" onClick={() => leave('delete')}>{t('mailCancel')}</Button>
          </div>
        </>
      )}
      {account.state === 'reading' && <p role="status" className="flex items-center gap-2 text-muted"><Spinner className="size-3" />{t('mailReading')}</p>}
      {account.state === 'current' && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-muted">{t('mailCurrent', account.read_since ?? null)}</p>
          {account.read_since && <Button size="sm" onClick={earlier}>{t('mailEarlier')}</Button>}
        </div>
      )}
      {account.state === 'error' && <p role="alert" className="text-danger-fg">{account.error}</p>}
      {said && <p role="alert" className="text-danger-fg">{said}</p>}
      <div className="flex flex-wrap gap-2">
        {!leaving ? <Button variant="ghost" size="sm" onClick={() => setLeaving(true)}>{t('mailDisconnect')}</Button> : (
          <>
            <Button size="sm" onClick={() => leave('keep')}>{t('mailDisconnectKeep')}</Button>
            <label className="flex flex-wrap items-center gap-2" htmlFor={`delete-${account.id}`}>{t('mailDeleteType', account.address)}
              <Input id={`delete-${account.id}`} value={typed} onChange={(e) => setTyped(e.target.value)} className="w-56" autoComplete="off" />
            </label>
            <Button variant="danger" size="sm" disabled={typed.trim() !== account.address} onClick={() => leave('delete')}>
              {t('mailDisconnectDelete')}
            </Button>
          </>
        )}
      </div>
    </section>
  );
}
