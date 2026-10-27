import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Api, errorText } from '../core/api';
import { AuthService } from '../core/auth';

@Component({
  selector: 'bc-login',
  imports: [FormsModule],
  template: `
    <div class="box card">
      <div class="row"><span class="mark">B</span><div><b>BidCraft</b><div class="muted small">Bid Response Crafter</div></div></div>
      <form (ngSubmit)="submit()">
        <div class="field"><label for="e">Email</label><input id="e" class="inp" type="email" name="email" [(ngModel)]="email" autocomplete="username" required></div>
        <div class="field"><label for="p">Password</label><input id="p" class="inp" type="password" name="password" [(ngModel)]="password" autocomplete="current-password" required></div>
        @if (error()) { <div class="notice bad">{{ error() }}</div> }
        <button class="btn primary" type="submit" [disabled]="busy()">Sign in</button>
      </form>
    </div>`,
  styles: `
    :host { display: grid; place-items: center; min-height: 100vh; padding: 20px; }
    .box { width: 360px; max-width: 100%; display: flex; flex-direction: column; gap: 20px; }
    .mark { width: 36px; height: 36px; border-radius: 8px; background: var(--accent); color: #fff; display: grid; place-items: center; font-weight: 700; }
    form .btn { width: 100%; justify-content: center; }
  `,
})
export class LoginPage {
  private api = inject(Api);
  private auth = inject(AuthService);
  private router = inject(Router);
  email = '';
  password = '';
  busy = signal(false);
  error = signal('');

  submit(): void {
    this.busy.set(true);
    this.error.set('');
    this.api.login(this.email, this.password).subscribe({
      next: r => { this.auth.set(r.token, r.user); this.router.navigate(['/']); },
      error: e => { this.error.set(errorText(e)); this.busy.set(false); },
    });
  }
}
