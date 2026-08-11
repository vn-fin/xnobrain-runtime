import { afterEach, describe, expect, it } from 'vitest';
import i18n from './i18n';
import en from './locales/en.json';
import vi from './locales/vi.json';

function flatten(value: Record<string, unknown>, prefix = '', result: Record<string, string> = {}) {
  Object.entries(value).forEach(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof item === 'string') result[path] = item;
    else flatten(item as Record<string, unknown>, path, result);
  });
  return result;
}

function placeholders(value: string) {
  return [...value.matchAll(/{{\s*([^}]+?)\s*}}/g)].map((match) => match[1]).sort();
}

describe('document language', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('tracks the selected application language on the root element', async () => {
    await i18n.changeLanguage('vi');
    expect(document.documentElement.lang).toBe('vi');

    await i18n.changeLanguage('zh-CN');
    expect(document.documentElement.lang).toBe('zh');
  });
});

describe('Vietnamese translations', () => {
  it('covers every English key with matching interpolation placeholders', () => {
    const english = flatten(en);
    const vietnamese = flatten(vi);

    expect(Object.keys(vietnamese).sort()).toEqual(Object.keys(english).sort());
    Object.entries(english).forEach(([key, value]) => {
      expect(placeholders(vietnamese[key]), key).toEqual(placeholders(value));
    });
  });
});
