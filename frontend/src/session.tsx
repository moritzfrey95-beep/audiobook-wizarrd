import { createContext, useContext } from 'react'

export interface Me { id: number; user: string | null; role: 'admin' | 'user'; langs: ('en' | 'de')[] }
export const MeContext = createContext<Me>({ id: 1, user: null, role: 'admin', langs: ['en', 'de'] })
/** Der angemeldete Benutzer. Benutzer ohne Administratorrolle sehen weder «Prüfen» noch «Aufgaben» und wählen keine Treffer von Hand. */
export const useMe = () => useContext(MeContext)
export const useAdmin = () => useContext(MeContext).role === 'admin'
/** Sprachen mit eingerichteter Bibliothek; Sprachwahl erscheint nur bei mehreren. */
export const useLangs = () => useContext(MeContext).langs
