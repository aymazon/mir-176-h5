import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import App from './App';

describe('App', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('shows the auth screen when no JWT is stored', () => {
    render(<App />);
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading.textContent).toContain('Mir 1.76 H5');
    expect(screen.getByRole('tab', { name: '登录' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '注册' })).toBeTruthy();
  });

  it('shows the character select screen when a JWT is stored', () => {
    localStorage.setItem('mir176_jwt', 'test-jwt');
    render(<App />);
    expect(screen.getByRole('heading', { name: '角色选择' })).toBeTruthy();
  });
});
