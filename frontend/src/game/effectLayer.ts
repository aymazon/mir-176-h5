// Placeholder for the W2 combat/drop effect layer. Exists now so world.ts
// keeps a stable layer stack (floor → entities → self → effects) before
// any effect content ships.

import { Container } from 'pixi.js';

export class EffectLayer extends Container {}
