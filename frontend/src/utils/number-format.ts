const decimalFormatter = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 6 });
const scientificFormatter = new Intl.NumberFormat('en-GB', {
  notation: 'scientific',
  maximumSignificantDigits: 6,
});

export function formatValue(value: number): string {
  if (value === 0) return '0';
  if (!Number.isFinite(value)) return String(value);

  const magnitude = Math.abs(value);
  if (magnitude < 1e-6 || magnitude >= 1e12) {
    return scientificFormatter.format(value);
  }
  return decimalFormatter.format(value);
}
