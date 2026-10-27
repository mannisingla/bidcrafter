import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { EMPTY, catchError, filter, switchMap, timer } from 'rxjs';
import { Api } from './core/api';
import { AuthService } from './core/auth';
import { CurrentBid } from './core/current-bid';
import { Icons } from './core/icons';
import { Toast } from './core/toast';
import { LibraryDoc } from './core/models';
import { daysUntil } from './features/shared';

interface Alert { lvl: 'warn' | 'bad'; ic: string; text: string; link: unknown[]; query?: Record<string, string>; action: string; }

/** Application shell: the prototype's top bar, document tab, alerts bell and left tool rail. */
@Component({
  selector: 'bc-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Icons],
  template: `
    @if (auth.user()) {
      <header class="topbar">
        <a class="brand" routerLink="/" style="text-decoration:none;color:inherit">
          <div class="brand-mark"><lucide-icon name="file-text"></lucide-icon></div>
          <b>BidCraft</b><span>Bid Response Crafter</span>
        </a>
        @if (bid.bid(); as b) {
          <a class="doc-tab" [routerLink]="['/bids', b.id]" style="text-decoration:none;color:inherit"><lucide-icon name="file-text"></lucide-icon><span>Technical bid, RFP {{ b.rfpRef || b.title }}</span></a>
        }
        <div class="grow"></div>
        <button class="ib alert-btn" (click)="alertsOpen.set(!alertsOpen())" aria-label="Alerts" title="Alerts">
          <lucide-icon name="bell"></lucide-icon>@if (alerts().length) { <span class="badge">{{ alerts().length }}</span> }
        </button>
        <span class="small muted" style="margin:0 4px">{{ auth.user()?.fullName }}</span>
        <button class="btn sm quiet" (click)="logout()">Sign out</button>
        <button class="btn primary" [disabled]="!bid.hasDraft()" (click)="exportPdf()"><lucide-icon name="printer"></lucide-icon><span>Export PDF</span></button>
      </header>
      @if (alertsOpen()) {
        <div class="alerts-pop">
          <div class="ah"><b>Alerts</b><span class="muted small">{{ alerts().length }} open</span></div>
          @for (a of alerts(); track a.text) {
            <div class="al" [class.warn]="a.lvl === 'warn'" [class.bad]="a.lvl === 'bad'"><lucide-icon [name]="a.ic"></lucide-icon>
              <div><span [innerHTML]="a.text"></span><div class="act"><button class="btn sm" (click)="go(a)">{{ a.action }}</button></div></div></div>
          } @empty { <div class="al">Nothing needs your attention.</div> }
        </div>
      }
      <div class="app">
        <nav class="rail" id="rail" aria-label="Main">
          <button routerLink="/" routerLinkActive="on" [routerLinkActiveOptions]="{ exact: true }"><lucide-icon name="layout-dashboard"></lucide-icon>Home</button>
          <button routerLink="/library" routerLinkActive="on"><lucide-icon name="library"></lucide-icon>Library</button>
          <button routerLink="/bids" [class.on]="rfpOn()"><lucide-icon name="file-search"></lucide-icon>RFP analysis</button>
          <button [routerLink]="bid.bid() ? ['/bids', bid.bid()!.id, 'response'] : ['/bids']" routerLinkActive="on"><lucide-icon name="clipboard-check"></lucide-icon>Bid response</button>
          <button [routerLink]="bid.bid() ? ['/bids', bid.bid()!.id, 'builder'] : ['/bids/new']" routerLinkActive="on"><lucide-icon name="layers"></lucide-icon>Bid builder</button>
          <button [routerLink]="bid.bid() ? ['/bids', bid.bid()!.id, 'preview'] : ['/bids/new']" routerLinkActive="on"><lucide-icon name="pen-tool"></lucide-icon>Preview &amp; sign</button>
          @if (auth.canReview()) { <button routerLink="/audit" routerLinkActive="on"><lucide-icon name="scroll-text"></lucide-icon>Audit log</button> }
        </nav>
        <main id="view"><router-outlet /></main>
      </div>
    } @else {
      <router-outlet />
    }
    <div class="toast" [class.show]="toast.message()">{{ toast.message() }}</div>
  `,
})
export class App {
  protected auth = inject(AuthService);
  protected bid = inject(CurrentBid);
  protected toast = inject(Toast);
  private api = inject(Api);
  private router = inject(Router);
  protected alertsOpen = signal(false);
  private expiring = signal<LibraryDoc[]>([]);
  private url = signal(this.router.url);
  /** RFP analysis covers the list of RFPs, a new one and one RFP's analysis; its bid response, builder and preview have their own buttons. */
  protected rfpOn = computed(() => /^\/bids(\/[^/?#]+)?(?:[?#].*)?$/.test(this.url()));

  protected alerts = computed<Alert[]>(() => {
    const out: Alert[] = [];
    for (const d of this.expiring()) {
      const days = daysUntil(d.validUntil!);
      out.push({
        lvl: days < 0 ? 'bad' : 'warn', ic: days < 0 ? 'flag' : 'clock',
        text: days < 0 ? `<b>${d.title}</b> expired on ${d.validUntil}.` : `<b>${d.title}</b> expires in ${days} days. Upload the renewed certificate when available.`,
        link: ['/library'], query: { tab: 'statutory' }, action: 'Open in library',
      });
    }
    return out;
  });

  constructor() {
    const destroy = inject(DestroyRef);
    timer(0, 60_000).pipe(
      filter(() => !!this.auth.user()),
      switchMap(() => this.api.library('statutory').pipe(catchError(() => EMPTY))),
      takeUntilDestroyed(destroy),
    ).subscribe(docs => this.expiring.set(docs.filter(d => d.validUntil && daysUntil(d.validUntil) < 120)));
    this.router.events.pipe(filter(e => e instanceof NavigationEnd), takeUntilDestroyed(destroy)).subscribe(() => {
      this.alertsOpen.set(false);
      this.url.set(this.router.url);
    });
  }

  go(a: Alert): void { this.alertsOpen.set(false); this.router.navigate(a.link as string[], { queryParams: a.query }); }
  // a fresh value each time, so the export dialog also opens when the preview is already showing
  exportPdf(): void { const b = this.bid.bid(); if (b) this.router.navigate(['/bids', b.id, 'preview'], { queryParams: { export: Date.now() } }); }
  logout(): void { this.auth.clear(); this.bid.set(null); this.router.navigate(['/login']); }
}
