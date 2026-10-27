import { DatePipe, JsonPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { Api } from '../core/api';

@Component({
  selector: 'bc-audit',
  imports: [DatePipe, JsonPipe],
  template: `
    <div class="wrap">
      <h1 class="v-title">Audit log</h1>
      <p class="sub">Sign-ins, uploads and the requests sent to the AI model (after redaction), newest first.</p>
      <div class="card" style="padding:4px 8px;margin-top:16px"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Item</th><th>Details</th></tr></thead>
        <tbody>
        @for (a of rows(); track a.id) {
          <tr><td style="white-space:nowrap">{{ a.at | date: 'dd MMM yyyy, HH:mm:ss' }}</td><td>{{ a.actor }}</td><td>{{ a.action }}</td>
            <td class="small">{{ a.entity }} {{ a.entityId?.slice(0, 8) }}</td><td class="small muted">{{ a.detail | json }}</td></tr>
        }
        </tbody>
      </table></div></div>
    </div>`,
})
export class AuditPage {
  private api = inject(Api);
  rows = signal<{ id: number; at: string; actor: string; action: string; entity: string | null; entityId: string | null; detail: unknown }[]>([]);
  constructor() { this.api.audit().subscribe(r => this.rows.set(r)); }
}
