/**
 * @vitest-environment happy-dom
 *
 * The Field / FieldReadout primitive (2026-09-21 audit critic-04): the label
 * names the control by id, aria-describedby lists exactly the hint, notice
 * and error that are present, an error marks the control invalid, and the
 * polite notice region is mounted before anything is written into it.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Field, FieldReadout, type FieldProps } from './Field';

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

  it('lays hidden unit adornments around the control', () => {
    renderField({ label: 'Holdout %', suffix: '%' });
    const control = input().parentElement!;
    expect(control.className).toBe('field__control');
    expect([...control.children].map((node) => [node.tagName, node.textContent, node.getAttribute('aria-hidden')]))
      .toEqual([['INPUT', '', null], ['SPAN', '%', 'true']]);
    renderField({ label: 'Budget', prefix: '$' });
    expect([...input().parentElement!.children].map((node) => node.textContent)).toEqual(['$', '']);
  });
});

describe('FieldReadout', () => {
  it('renders read-only text as a labelled name/value pair, never as an input', () => {
    act(() => root.render(<FieldReadout label="Benefit-led subject" value={'Line one\nLine two'} />));
    const dd = document.querySelector('dd')!;
    expect(document.querySelectorAll('input, textarea')).toHaveLength(0);
    expect(dd.textContent).toBe('Line one\nLine two');
    expect(dd.className).toContain('field__readout');
    expect(byId(dd.getAttribute('aria-labelledby') ?? '')?.textContent).toBe('Benefit-led subject');
  });

  it('shows a muted dash for an empty value, announced as not set', () => {
    act(() => root.render(<FieldReadout label="Guidance-led subject" value="   " />));
    const dd = document.querySelector('dd')!;
    expect(dd.querySelector('[aria-hidden="true"]')?.textContent).toBe('—');
    expect(dd.querySelector('.sr-only')?.textContent).toBe('Not set');
  });
});
