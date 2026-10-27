import { Injectable, signal } from '@angular/core';

/** The prototype's bottom toast, shared by every screen. */
@Injectable({ providedIn: 'root' })
export class Toast {
  readonly message = signal('');
  private timer?: ReturnType<typeof setTimeout>;
  show(text: string): void {
    this.message.set(text);
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.message.set(''), 2800);
  }
}
