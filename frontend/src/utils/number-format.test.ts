import { describe, expect, it } from 'vitest';
import { formatValue } from './number-format';

describe('formatValue', () => {
  it('keeps sub-millienergy values distinguishable from zero', () => {
    expect(formatValue(0.0005)).toBe('0.0005');
  });

  it('uses scientific notation for very small positive and negative values', () => {
    expect(formatValue(0.0000005)).toMatch(/5E-7/);
    expect(formatValue(-0.0000005)).toMatch(/-5E-7/);
  });

  it('renders true zero as zero without a negative sign', () => {
    expect(formatValue(0)).toBe('0');
    expect(formatValue(-0)).toBe('0');
  });
});
