import { inject } from '@angular/core';
import { Router, Routes } from '@angular/router';
import { AuthService, authGuard } from './core/auth';

export const routes: Routes = [
  { path: 'login', loadComponent: () => import('./features/login').then(m => m.LoginPage) },
  {
    path: '',
    canActivate: [authGuard],
    children: [
      { path: '', loadComponent: () => import('./features/home').then(m => m.HomePage) },
      { path: 'library', loadComponent: () => import('./features/library').then(m => m.LibraryPage) },
      { path: 'library/projects/:id', loadComponent: () => import('./features/project').then(m => m.ProjectPage) },
      { path: 'library/people/:id', loadComponent: () => import('./features/person').then(m => m.PersonPage) },
      { path: 'bids', loadComponent: () => import('./features/bids-list').then(m => m.BidsListPage) },
      { path: 'bids/new', loadComponent: () => import('./features/new-bid').then(m => m.NewBidPage) },
      { path: 'bids/:id', loadComponent: () => import('./features/bid-analysis').then(m => m.BidAnalysisPage) },
      { path: 'bids/:id/response', loadComponent: () => import('./features/bid-response').then(m => m.BidResponsePage) },
      { path: 'bids/:id/builder', loadComponent: () => import('./features/builder').then(m => m.BuilderPage) },
      { path: 'bids/:id/preview', loadComponent: () => import('./features/preview').then(m => m.PreviewPage) },
      { path: 'audit', canActivate: [() => inject(AuthService).canReview() || inject(Router).parseUrl('/')],
        loadComponent: () => import('./features/audit').then(m => m.AuditPage) },
    ],
  },
  { path: '**', redirectTo: '' },
];
