// The keys and model the demo runs with, put in here instead of in .env by hand. A key is shown
// only as set or not -- the demo never sends one back -- and a new one is tried before it is kept.
import { useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { api, call, ApiError } from '../../api/client';
import { Button, Input, Spinner } from '../../components/ui';
import { useT } from '../../lib/i18n';
import { keys, useSettings } from '../../lib/queries';
import { PageHeader } from './ManageLayout';

export function SettingsPage() {
  const t = useT();
  const client = useQueryClient();
  const { data } = useSettings();
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const changed = Object.values(values).some((v) => v.trim());
  const fields = data?.fields ?? [];
  // A provider chosen here reads its own key: shown at once, so the switch and the key are one
  // save -- otherwise a Claude key had no field until a save without it went through.
  const keyFields = fields.flatMap((f) => {
    const env = f.choices?.[values[f.env] || f.value || ''];
    return env && !fields.some((g) => g.env === env)
      ? [{ env, secret: true, set: false, value: null, hint: null, choices: null }] : [];
  });
  // Who answers and who embeds come first, then the keys they read, each saying what for.
  const chosen = (env: string) => {
    const f = fields.find((g) => g.env === env);
    return f?.choices?.[values[env] || f.value || ''];
  };
  const roles = (env: string) => [
    ...(chosen('GRAPHME_PROVIDER') === env ? [t('keyForChat')] : []),
    ...(chosen('GRAPHME_EMBEDDING_PROVIDER') === env ? [t('keyForSearch')] : []),
  ];
  const ordered = [...fields.filter((f) => f.choices), ...keyFields,
    ...fields.filter((f) => !f.choices)];
  const choose = (env: string, value: string, current: string | null | undefined) => {
    const next = { ...values };
    if (value === current) delete next[env]; else next[env] = value;
    setValues(next);
  };

  const save = async () => {
    setSaving(true);
    setSaid(null);
    try {
      const now = await call(api.PUT('/v1/settings', { body: { values } }));
      client.setQueryData(keys.settings, now);
      setValues({});
      setSaid({ ok: true, text: t('settingsSaved') });
    } catch (error) {
      setSaid({ ok: false, text: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <PageHeader title={t('keysTitle')} description={t('keysBody')} />
      <div className="grid max-w-2xl gap-5 p-6">
        {data && (data.problems.length ? (
          <ul className="space-y-1 rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm" aria-label={t('notReady')}>
            {data.problems.map((p) => (
              <li key={p} className="flex gap-2"><TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" />{p}</li>
            ))}
          </ul>
        ) : (
          <p className="flex items-center gap-2 text-sm text-muted"><CheckCircle2 className="size-4 text-ok" />{t('settingsReady')}</p>
        ))}
        <form className="grid gap-4" autoComplete="off" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          {ordered.map((f) => (
            <label key={f.env} className="grid gap-1">
              <span className="flex items-baseline gap-2 text-sm font-medium">
                {t('settingName', f.env)}
                {roles(f.env).map((role) => <span key={role} className="rounded bg-accent/10 px-1.5 text-2xs font-normal text-accent">{role}</span>)}
                {f.secret && <span className="text-xs font-normal text-muted">{f.set ? t('keySet', f.hint ?? null) : t('keyUnset')}</span>}
              </span>
              {f.choices ? (
                <select
                  className="h-8 w-full rounded-md border border-line bg-panel px-2 text-sm text-fg focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
                  value={values[f.env] ?? f.value ?? ''}
                  onChange={(e) => choose(f.env, e.target.value, f.value)}
                >
                  {Object.keys(f.choices).map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              ) : (
                <Input
                  type={f.secret ? 'password' : 'text'} spellCheck={false}
                  autoComplete={f.secret ? 'new-password' : 'off'}
                  placeholder={f.secret ? (f.set ? t('keyReplace') : f.env) : (f.value ?? f.env)}
                  value={values[f.env] ?? ''}
                  onChange={(e) => setValues({ ...values, [f.env]: e.target.value })}
                />
              )}
              <span className="text-xs leading-5 text-faint">{t('settingNote', f.env)} <code>{f.env}</code></span>
            </label>
          ))}
          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" disabled={!changed || saving}>
              {saving && <Spinner className="size-3.5" />}{t('saveSettings')}
            </Button>
            {said && <span role="status" className={said.ok ? 'text-sm text-ok' : 'text-sm text-danger-fg'}>{said.text}</span>}
          </div>
        </form>
      </div>
    </div>
  );
}
