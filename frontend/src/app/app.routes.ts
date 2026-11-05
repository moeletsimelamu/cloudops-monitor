import { Routes } from '@angular/router';

import { authGuard } from './core/auth.service';

export const routes: Routes = [
  {
    path: 'sign-in',
    title: 'Sign in · CloudOps Monitor',
    loadComponent: () => import('./features/sign-in').then((m) => m.SignInPage),
  },
  {
    path: '',
    canActivate: [authGuard],
    loadComponent: () => import('./shell').then((m) => m.Shell),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'overview' },
      {
        path: 'overview',
        title: 'Overview · CloudOps Monitor',
        data: { heading: 'Overview' },
        loadComponent: () => import('./features/overview').then((m) => m.OverviewPage),
      },
      {
        path: 'applications',
        title: 'Applications · CloudOps Monitor',
        data: { heading: 'Applications' },
        loadComponent: () => import('./features/applications').then((m) => m.ApplicationsPage),
      },
      {
        path: 'applications/new',
        title: 'Register application · CloudOps Monitor',
        data: { heading: 'Register application', hideRange: true },
        loadComponent: () => import('./features/application-form').then((m) => m.ApplicationForm),
      },
      {
        path: 'applications/:slug/edit',
        title: 'Edit application · CloudOps Monitor',
        data: { heading: 'Edit application', hideRange: true },
        loadComponent: () => import('./features/application-form').then((m) => m.ApplicationForm),
      },
      {
        path: 'applications/:slug',
        title: 'Application · CloudOps Monitor',
        data: { heading: 'Application' },
        loadComponent: () =>
          import('./features/application-detail').then((m) => m.ApplicationDetailPage),
      },
      {
        path: 'health',
        title: 'Health history · CloudOps Monitor',
        data: { heading: 'Health history' },
        loadComponent: () => import('./features/health').then((m) => m.HealthPage),
      },
      {
        path: 'deployments',
        title: 'Deployments · CloudOps Monitor',
        data: { heading: 'Deployments', hideRange: true },
        loadComponent: () => import('./features/deployments').then((m) => m.DeploymentsPage),
      },
      {
        path: 'logs',
        title: 'Logs · CloudOps Monitor',
        data: { heading: 'Logs' },
        loadComponent: () => import('./features/logs').then((m) => m.LogsPage),
      },
      {
        path: 'events',
        title: 'Events · CloudOps Monitor',
        data: { heading: 'Events' },
        loadComponent: () => import('./features/events').then((m) => m.EventsPage),
      },
      {
        path: 'system',
        title: 'System · CloudOps Monitor',
        data: { heading: 'System', hideRange: true },
        loadComponent: () => import('./features/system').then((m) => m.SystemPage),
      },
      {
        path: '**',
        title: 'Not found · CloudOps Monitor',
        data: { heading: 'Not found', hideRange: true },
        loadComponent: () => import('./features/not-found').then((m) => m.NotFoundPage),
      },
    ],
  },
];
