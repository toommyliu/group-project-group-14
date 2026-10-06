export function searchable(text: string) {
  return text
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/[\s,]+/g, ' ')
    .trim()
    .toLocaleLowerCase();
}
