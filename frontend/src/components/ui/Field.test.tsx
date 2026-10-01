/**
 * @vitest-environment happy-dom
 *
 * The Field primitive (2026-09-21 audit critic-04): the label
 * names the control by id, aria-describedby lists exactly the hint, notice
 * and error that are present, an error marks the control invalid, and the
 * polite notice region is mounted before anything is written into it.
 *
 * deviation:field-affixes-readouts: the affixes, the unit description, the
 * hidden label and FieldReadout are opt-in, so a Field with none of them (Lead
 * Queue's range and saved-view fields) renders the markup it always did; and
 * Field.css's control states are scoped to `.field .form-input`.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads Field.css under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { Field, FieldReadout, type FieldProps } from './Field';

declare const process: { cwd(): string };

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
});

function renderField(props: Omit<FieldProps, 'children'>) {
  act(() => {
    root.render(
      <>
        <Field {...props}>{(control) => <input {...control} className="form-input" type="number" />}</Field>
        <Field label="Second">{(control) => <input {...control} className="form-input" />}</Field>
      </>,
    );
  });
}

const input = () => document.querySelector<HTMLInputElement>('input')!;
const byId = (id: string) => document.getElementById(id);
const describedBy = () => (input().getAttribute('aria-describedby') ?? '').split(' ').filter(Boolean);

describe('Field', () => {
  it('binds its label to the control with a unique id', () => {
    renderField({ label: 'Budget' });
    const [first, second] = [...document.querySelectorAll<HTMLInputElement>('input')];
    expect(first.id).not.toBe('');
    expect(first.id).not.toBe(second.id);
    expect(first.labels?.[0]?.textContent).toBe('Budget');
    expect(document.querySelector<HTMLLabelElement>('label')?.htmlFor).toBe(first.id);
    expect(first.getAttribute('aria-invalid')).toBeNull();
    expect(first.getAttribute('aria-describedby')).toBeNull();
  });

  it('describes the control by exactly the hint, notice and error that are present', () => {
    renderField({ label: 'Budget', hint: 'Whole dollars', notice: 'Capped at $10,000,000', error: 'Budget is required' });
    const ids = describedBy();
    expect(ids.map((id) => byId(id)?.textContent)).toEqual(['Whole dollars', 'Capped at $10,000,000', 'Budget is required']);
    renderField({ label: 'Budget', hint: 'Whole dollars' });
    expect(describedBy().map((id) => byId(id)?.textContent)).toEqual(['Whole dollars']);
  });

  it('marks the control invalid only while an error is shown', () => {
    renderField({ label: 'Budget', error: 'Budget is required' });
    expect(input().getAttribute('aria-invalid')).toBe('true');
    renderField({ label: 'Budget', error: null });
    expect(input().getAttribute('aria-invalid')).toBeNull();
    expect(document.querySelector('.field__error')).toBeNull();
  });

  it('keeps one polite status region mounted, empty and hidden until a notice arrives', () => {
    renderField({ label: 'Holdout %' });
    const region = input().closest('.field')!.querySelector<HTMLElement>('[role="status"]')!;
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.textContent).toBe('');
    expect(region.classList.contains('sr-only')).toBe(true);
    renderField({ label: 'Holdout %', notice: 'Capped at 50%' });
    const same = input().closest('.field')!.querySelector<HTMLElement>('[role="status"]');
    expect(same).toBe(region);
    expect(region.textContent).toBe('Capped at 50%');
    expect(region.classList.contains('sr-only')).toBe(false);
    renderField({ label: 'Holdout %', notice: null });
    expect(region.isConnected).toBe(true);
    expect(region.textContent).toBe('');
  });

  it('shows a prefix or suffix beside the control, hidden from assistive tech, and the unit as its description', () => {
    act(() => {
      root.render(
        <>
          <Field label="Budget" prefix="$" unit="US dollars">{(control) => <input {...control} className="form-input" />}</Field>
          <Field label="Holdout %" suffix="%" unit="percent" notice="Capped at 50%">
            {(control) => <input {...control} className="form-input" />}
          </Field>
        </>,
      );
    });
    const [budget, holdout] = [...document.querySelectorAll<HTMLInputElement>('input')];
    for (const [input, affix, before] of [[budget, '$', true], [holdout, '%', false]] as const) {
      const wrapper = input.parentElement!;
      expect(wrapper.className).toBe('field__control');
      const children = [...wrapper.children];
      expect(children.map((node) => node.tagName)).toEqual(before ? ['SPAN', 'INPUT'] : ['INPUT', 'SPAN']);
      const affixNode = children.find((node) => node !== input)!;
      expect([affixNode.className, affixNode.textContent, affixNode.getAttribute('aria-hidden')]).toEqual(['field__affix', affix, 'true']);
      // The accessible name is the label alone; the unit is a description.
      expect(input.labels?.[0]?.textContent).toBe(input === budget ? 'Budget' : 'Holdout %');
    }
    const described = (input: HTMLInputElement) => (input.getAttribute('aria-describedby') ?? '').split(' ').map((id) => byId(id));
    expect(described(budget).map((node) => [node?.textContent, node?.className])).toEqual([['US dollars', 'sr-only']]);
    expect(described(holdout).map((node) => node?.textContent)).toEqual(['percent', 'Capped at 50%']);
  });

  it('keeps a hidden label as the control name', () => {
    act(() => {
      root.render(
        <Field label="Message variant for Summit IL refi" labelHidden>
          {(control) => <select {...control}><option>A</option></select>}
        </Field>,
      );
    });
    const label = document.querySelector('label')!;
    expect(label.className).toBe('field__label sr-only');
    expect(document.querySelector('select')!.labels?.[0]).toBe(label);
    expect(document.querySelector('.field__control')).toBeNull();
  });

  it('renders the markup it always did when no new prop is passed (Lead Queue range and saved-view fields)', () => {
    act(() => {
      root.render(
        <Field label="View name" hint="Up to 80 characters" error="Name taken">
          {(control) => <input {...control} className="form-input" type="text" data-testid="lead-queue-saved-views-name" />}
        </Field>,
      );
    });
    const html = document.getElementById('root')!.innerHTML.replace(/_r_[0-9a-z]+_/g, 'ID');
    expect(html).toBe(
      '<div class="field">'
        + '<label class="field__label" for="IDcontrol">View name</label>'
        + '<input id="IDcontrol" aria-describedby="IDhint IDerror" aria-invalid="true" class="form-input" data-testid="lead-queue-saved-views-name" type="text">'
        + '<p class="field__hint" id="IDhint">Up to 80 characters</p>'
        + '<p class="field__notice sr-only" id="IDnotice" role="status" aria-live="polite"></p>'
        + '<p class="field__error" id="IDerror">Name taken</p>'
        + '</div>',
    );
  });

  it('FieldReadout shows a value as labelled text, never as an input, and says when it is not set', () => {
    act(() => {
      root.render(
        <>
          <FieldReadout label="Benefit-led subject" value="Review your options" className="campaign-setup__field" />
          <FieldReadout label="Benefit-led message" value={'Line one\nLine two'} multiline />
          <FieldReadout label="Guidance-led subject" value="  " empty="Not set. Apply a recommendation to fill it." />
        </>,
      );
    });
    expect(document.querySelectorAll('input, textarea')).toHaveLength(0);
    const groups = [...document.querySelectorAll<HTMLElement>('[role="group"]')];
    expect(groups.map((group) => byId(group.getAttribute('aria-labelledby')!)?.textContent)).toEqual([
      'Benefit-led subject',
      'Benefit-led message',
      'Guidance-led subject',
    ]);
    expect(groups[0].className).toBe('field campaign-setup__field');
    expect(groups.map((group) => group.querySelector('.field__label')?.tagName)).toEqual(['SPAN', 'SPAN', 'SPAN']);
    const values = groups.map((group) => group.querySelector<HTMLElement>('.field__value')!);
    expect(values.map((node) => [node.tagName, node.className, node.textContent])).toEqual([
      ['DIV', 'field__value field__readout', 'Review your options'],
      ['DIV', 'field__value field__readout field__readout--multiline', 'Line one\nLine two'],
      ['DIV', 'field__value field__readout field__readout--empty', 'Not set. Apply a recommendation to fill it.'],
    ]);
  });
});

describe('Field.css control states (check correction 4)', () => {
  const css = readFileSync(join(process.cwd(), 'src', 'components', 'ui', 'Field.css'), 'utf-8') as string;
  const STATE = /:(read-only|disabled|user-invalid)\b/;
  /** Every selector (comma-split, comments stripped) that names a control state. */
  const selectors = css
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('}')
    .map((rule) => rule.split('{')[0] ?? '')
    .flatMap((list) => list.split(','))
    .map((selector) => selector.trim())
    .filter((selector) => STATE.test(selector));

  it('scopes every state rule to a Field-wrapped .form-input: no bare control, no unscoped state', () => {
    expect(selectors.length).toBeGreaterThanOrEqual(3);
    for (const selector of selectors) {
      expect(selector).toMatch(/^\.field \.form-input(?::read-only|:disabled|:user-invalid|:not\(:disabled\))+$/);
    }
  });

  for (const state of [':read-only', ':disabled', ':user-invalid']) {
    it(`has a ${state} rule on .field .form-input`, () => {
      expect(selectors.some((selector) => selector.startsWith(`.field .form-input${state}`)), state).toBe(true);
    });
  }

  it('paints user-invalid with the danger token', () => {
    expect(css).toMatch(/\.field \.form-input:user-invalid \{\s*border-color: var\(--status-danger-line-strong\);\s*\}/);
  });
});

