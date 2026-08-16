// Flow router: no JWT → AuthScreen; JWT → CharSelect; active character → GameView.

import { useCallback, useState } from 'react';

import { getJwt, type CharacterOut } from './api/rest';
import AuthScreen from './ui/AuthScreen';
import CharSelect from './ui/CharSelect';
import GameView from './ui/GameView';

type Screen =
  | { readonly kind: 'auth' }
  | { readonly kind: 'chars'; readonly notice?: string }
  | { readonly kind: 'game'; readonly character: CharacterOut };

export default function App() {
  const [screen, setScreen] = useState<Screen>(() =>
    getJwt() !== null ? { kind: 'chars' } : { kind: 'auth' },
  );

  const handleAuthenticated = useCallback(() => setScreen({ kind: 'chars' }), []);
  const handleEnter = useCallback(
    (character: CharacterOut) => setScreen({ kind: 'game', character }),
    [],
  );
  const handleExit = useCallback(
    (reason?: string) => setScreen({ kind: 'chars', notice: reason }),
    [],
  );

  switch (screen.kind) {
    case 'auth':
      return <AuthScreen onAuthenticated={handleAuthenticated} />;
    case 'chars':
      return <CharSelect notice={screen.notice} onEnter={handleEnter} />;
    case 'game':
      return (
        <GameView
          key={screen.character.id}
          character={screen.character}
          onExit={handleExit}
        />
      );
  }
}
