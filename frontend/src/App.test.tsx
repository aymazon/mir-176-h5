import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import App from './App';

describe('App', () => {
  it('renders the Mir 1.76 H5 title heading', () => {
    render(<App />);
    const heading = screen.getByRole('heading', { name: 'Mir 1.76 H5', level: 1 });
    expect(heading.tagName).toBe('H1');
    expect(heading.textContent).toContain('Mir 1.76 H5');
  });
});
