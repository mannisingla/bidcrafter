import { Component, input } from '@angular/core';
import { BidSettings } from './models';

type Sign = NonNullable<BidSettings['sign']>;

/** Stamp image, signature image and caption, exactly where the server puts them on each page. */
@Component({
  selector: 'bc-sign-block',
  template: `
    <div [class]="'pg-sign ' + (sign().position === 'left' ? 'left' : 'right')">
      @if (sign().stamp && stampImage()) { <img class="stamp" [src]="stampImage()" alt=""> }
      @if (sign().enabled) {
        <div class="sig">@if (image()) { <img [src]="image()" alt=""> }</div>
        <div class="cap">{{ sign().caption }}</div>
      }
    </div>`,
  styles: ':host{display:contents}',
})
export class SignBlock {
  readonly sign = input.required<Sign>();
  readonly image = input<string | null>(null);
  readonly stampImage = input<string | null>(null);
}

/** Header, page number and signature drawn over a rendered PDF page (794 x 1123 px space). An enclosed library
 * document ('docs') gets only the page number, signature and stamp, as in the built PDF. */
@Component({
  selector: 'bc-page-decor',
  imports: [SignBlock],
  template: `
    <div class="page decor">
      @if (headerOn() && kind() !== 'cover' && kind() !== 'docs') { <div class="pg-head"><span>{{ headerLeft() }}</span><span>{{ headerRight() }}</span></div> }
      @if (label()) { <div [class]="'pg-num ' + numberPos()">{{ label() }}</div> }
      @if (signed()) { <bc-sign-block [sign]="sign()" [image]="image()" [stampImage]="stampImage()" /> }
    </div>`,
  styles: ':host{position:absolute;top:0;left:0;pointer-events:none} .decor{background:transparent!important}',
})
export class PageDecor {
  readonly kind = input('text');
  readonly label = input<string | null>(null);
  readonly numberPos = input('center');
  readonly headerOn = input(true);
  readonly headerLeft = input('');
  readonly headerRight = input('');
  readonly signed = input(false);
  readonly sign = input.required<Sign>();
  readonly image = input<string | null>(null);
  readonly stampImage = input<string | null>(null);
}
