import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { User } from './models';

const KEY = 'bidcraft.session';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly session = signal<{ token: string; user: User } | null>(this.restore());
  readonly user = computed(() => this.session()?.user ?? null);
  readonly token = computed(() => this.session()?.token ?? null);
  readonly canReview = computed(() => ['admin', 'reviewer'].includes(this.user()?.role ?? ''));

  set(token: string, user: User): void {
    sessionStorage.setItem(KEY, JSON.stringify({ token, user }));
    this.session.set({ token, user });
  }

  clear(): void {
    sessionStorage.removeItem(KEY);
    this.session.set(null);
  }

  private restore() {
    try {
      const raw = sessionStorage.getItem(KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const token = auth.token();
  const authed = token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;
  return next(authed).pipe(
    catchError((err: HttpErrorResponse) => {
      if (err.status === 401 && !req.url.endsWith('/auth/login')) {
        auth.clear();
        router.navigate(['/login']);
      }
      return throwError(() => err);
    }),
  );
};

export const authGuard: CanActivateFn = () =>
  inject(AuthService).user() ? true : inject(Router).createUrlTree(['/login']);
