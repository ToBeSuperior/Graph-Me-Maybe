import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { lazy, StrictMode, Suspense, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, RouterProvider, useParams } from 'react-router-dom';
import { Toaster } from 'sonner';
import { AppShell } from './components/AppShell';
import { KeyGate } from './components/KeyGate';
import { TipProvider } from './components/ui';
import { DangerPage } from './features/manage/DangerPage';
import { SettingsPage } from './features/manage/SettingsPage';
import { ExportPage } from './features/manage/ExportPage';
import { LibraryPage } from './features/manage/LibraryPage';
import { ManageLayout } from './features/manage/ManageLayout';
import { AskPage } from './features/search/AskPage';
import { UploadProvider } from './features/upload/upload';
import { LangProvider } from './lib/i18n';
import { queryDefaults } from './lib/queries';
import { ThemeProvider, useTheme } from './lib/theme';
import './styles.css';

// The graph pages carry vis-network, the bulk of the app: loaded when one is first opened.
const DocumentPage = lazy(() => import('./features/manage/DocumentPage').then((m) => ({ default: m.DocumentPage })));
const MapPage = lazy(() => import('./features/manage/MapPage').then((m) => ({ default: m.MapPage })));
const later = (page: ReactNode) => <Suspense fallback={<div className="skeleton m-6 h-96" />}>{page}</Suspense>;

// Answers are kept until the graph changes (lib/queries.ts says when); a failure is retried
// once, not three times over a minute -- unless the server says to wait (Docker starting).
const client = new QueryClient({ defaultOptions: { queries: queryDefaults } });

// The app's own window (WebView2, WebKit) has no reload of its own: F5 and Ctrl/Cmd+R do it.
addEventListener('keydown', (event) => {
  if (event.key === 'F5' || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'r')) {
    event.preventDefault();
    location.reload();
  }
});

/** The old pages' links: `/?doc=<id>` opened a document, `/qfolder` the folders. */
function OldDocLink() {
  const doc = new URLSearchParams(location.search).get('doc');
  return doc ? <Navigate to={`/manage/docs/${encodeURIComponent(doc)}`} replace /> : <AskPage />;
}
function DocumentRoute() {
  const { id = '' } = useParams();
  return later(<DocumentPage key={id} id={id} />);
}

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <OldDocLink /> },
      { path: '/qfolder', element: <Navigate to={`/${location.search}`} replace /> },
      {
        path: '/manage',
        element: <ManageLayout />,
        children: [
          { index: true, element: <LibraryPage /> },
          { path: 'map', element: later(<MapPage />) },
          { path: 'docs/:id', element: <DocumentRoute /> },
          { path: 'export', element: <ExportPage /> },
          { path: 'settings', element: <SettingsPage /> },
          { path: 'danger', element: <DangerPage /> },
        ],
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);

function Toasts() {
  const { theme } = useTheme();
  return <Toaster theme={theme === 'auto' ? 'system' : theme} position="bottom-right" closeButton richColors />;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <LangProvider>
        <QueryClientProvider client={client}>
          <TipProvider>
            <UploadProvider>
              <RouterProvider router={router} />
              <KeyGate />
              <Toasts />
            </UploadProvider>
          </TipProvider>
        </QueryClientProvider>
      </LangProvider>
    </ThemeProvider>
  </StrictMode>,
);
