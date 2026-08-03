import { afterEach, describe, expect, it } from 'vitest';
import i18n from './i18n';

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
