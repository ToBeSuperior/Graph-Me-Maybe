// The server's API (`/v1`), typed from its own contract (schema.d.ts, made by `npm run api` from
// `graphme serve --openapi`). Every call goes through `call` (or `sent`, for what the contract
// cannot type): an answer, or an ApiError saying in the server's words why there is none.
import createClient from 'openapi-fetch';
import { keyWanted } from '../lib/key';
import type { components, paths } from './schema';

export type Schemas = components['schemas'];
export type LibraryItem = Schemas['LibraryItem'];
export type Pages = Schemas['Pages'];
export type PageInfo = Schemas['PageInfo'];
export type BuildState = Schemas['BuildState'];
export type CorpusMap = Schemas['CorpusMap'];
export type DocumentGraph = Schemas['DocumentGraph'];
export type GraphNode = Schemas['GraphNode'];
export type Source = Schemas['Source'];
export type Answer = Schemas['Answer'];
export type ExportJob = Schemas['ExportJob'];
export type Job = BuildState | ExportJob;

export const api = createClient<paths>({ baseUrl: '' });

/** An answer the server says to wait for (Docker starting, `Retry-After`) is asked again until
 * it comes; any other failure once. */
export const isWaiting = (error: unknown) => error instanceof ApiError && error.waiting;

export const retrying = (failures: number, error: unknown) => isWaiting(error) || failures < 1;

export const retryDelay = (failures: number, error: unknown) =>
  isWaiting(error) ? 3000 : Math.min(1000 * 2 ** failures, 30000);

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly waiting = false) { super(message); }
}

/** The answer's data, or an ApiError with the server's `detail` (or the status when it says none). */
export async function call<T>(request: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let got;
  try {
    got = await request;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(String(error), 0);
  }
  if (got.data !== undefined && got.response.ok) return got.data;
  if (got.response.status === 401) keyWanted(); // the page asks for the server's key
  const detail = (got.error as { detail?: unknown } | undefined)?.detail;
  throw new ApiError(typeof detail === 'string' ? detail : got.response.statusText || String(got.response.status),
    got.response.status, got.response.headers.has('Retry-After'));
}

/** A request the contract does not type -- an upload's form, the demo's samples -- answered as
 * `call` answers. */
export async function sent<T>(path: string, init: RequestInit): Promise<T> {
  return call(fetch(path, init).then(async (response) => {
    const body: unknown = await response.json().catch(() => undefined);
    return response.ok ? { data: body as T, response } : { error: body, response };
  }));
}

/** The demo's samples (`POST /v1/samples`): a route the demo adds, so not in the server's
 * contract; offered only when `GET /v1/settings` says `samples`. */
export interface Samples { files: string[]; build: BuildState }

const isBuild = (job: Job): job is BuildState => ['build', 'read', 'embed'].includes(job.kind);
/** The newest job in the build's slot and in the export's (`GET /v1/jobs` is newest first), or
 * null before there is one. */
export const lastBuild = (jobs: Job[]) => jobs.find(isBuild) ?? null;
export const lastExport = (jobs: Job[]) => jobs.find((job): job is ExportJob => !isBuild(job)) ?? null;

/** Where a file's rendered page is, and a node's stored crop on it. */
export const pageImage = (file: string, index: number) => `/v1/files/${file}/pages/${index}/image`;
export const nodeCrop = (file: string, index: number, node: string) =>
  `/v1/files/${file}/pages/${index}/crop?node=${encodeURIComponent(node)}`;

const BUILDING = ['reading', 'linking', 'embedding'];
export const isBuilding = (b?: BuildState | null) => !!b && BUILDING.includes(b.state);
