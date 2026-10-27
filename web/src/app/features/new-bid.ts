import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Api, errorText } from '../core/api';
import { Icons } from '../core/icons';
import { RFP_DOC_KINDS, guessRfpKind } from '../core/models';

interface Picked { file: File; kind: string; }

@Component({
  selector: 'bc-new-bid',
  imports: [FormsModule, Icons],
  template: `
    <div class="wrap" style="max-width:820px">
      <h1 class="v-title">Analyse an RFP</h1>
      <p class="sub">Upload all documents of the tender: the RFP volumes, annexures, and any corrigenda, addenda or pre-bid
        clarifications. They are read and indexed on this server; only the relevant sections are sent, redacted, for
        extraction. Documents issued later can be added on the analysis screen.</p>
      <div class="field" style="margin-top:18px"><label>Name (optional)</label>
        <input class="inp" [(ngModel)]="title" placeholder="For example: State grievance portal SI"></div>
      <div class="drop" [class.hot]="hot()" role="button" tabindex="0" aria-label="Choose tender documents"
           (click)="picker.click()" (keydown.enter)="picker.click()" (keydown.space)="$event.preventDefault(); picker.click()"
           (dragover)="$event.preventDefault(); hot.set(true)" (dragleave)="hot.set(false)" (drop)="drop($event)">
        <lucide-icon name="upload"></lucide-icon><b>Drop the tender documents here</b>PDF or Word, including scanned copies
      </div>
      <input #picker type="file" multiple hidden accept=".pdf,.doc,.docx" (change)="pick(picker)">
      @if (files().length) {
        <div class="card" style="padding:4px 8px;margin:12px 0"><table class="tbl"><tr><th>Document</th><th>Type</th><th></th></tr>
          @for (p of files(); track $index) {
            <tr><td>{{ p.file.name }} <span class="muted small">({{ (p.file.size / 1048576).toFixed(1) }} MB)</span></td>
              <td><select class="inp" style="width:240px" [(ngModel)]="p.kind" [attr.aria-label]="'Type of ' + p.file.name">
                @for (k of kinds; track k[0]) { <option [value]="k[0]">{{ k[1] }}</option> }</select></td>
              <td style="text-align:right"><button class="ib" (click)="remove($index)" aria-label="Remove"><lucide-icon name="x"></lucide-icon></button></td></tr>
          }
        </table></div>
      }
      @if (error()) { <div class="notice warn"><lucide-icon name="info"></lucide-icon><div>{{ error() }}</div></div> }
      <button class="btn primary" [disabled]="!files().length || busy()" (click)="start()"><lucide-icon name="file-search"></lucide-icon>{{ busy() ? 'Uploading…' : 'Analyse RFP' }}</button>
    </div>`,
})
export class NewBidPage {
  private api = inject(Api);
  private router = inject(Router);
  kinds = Object.entries(RFP_DOC_KINDS);
  title = '';
  files = signal<Picked[]>([]);
  hot = signal(false);
  busy = signal(false);
  error = signal('');

  private add(list: FileList | null | undefined): void {
    this.files.update(f => [...f, ...[...(list ?? [])].map(file => ({ file, kind: guessRfpKind(file.name) }))]);
  }
  pick(input: HTMLInputElement): void { this.add(input.files); input.value = ''; }
  drop(e: DragEvent): void { e.preventDefault(); this.hot.set(false); this.add(e.dataTransfer?.files); }
  remove(i: number): void { this.files.update(f => f.filter((_, j) => j !== i)); }

  start(): void {
    this.busy.set(true);
    this.api.createBid(this.files().map(p => p.file), this.files().map(p => p.kind), this.title).subscribe({
      next: r => this.router.navigate(['/bids', r.bid.id]),
      error: e => { this.error.set(errorText(e)); this.busy.set(false); },
    });
  }
}
