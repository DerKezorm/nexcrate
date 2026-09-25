import { createContext } from 'react'

/** Zeigt unten einen kurzen Satz, der von selbst wieder geht. */
export const NoticeContext = createContext<(text: string) => void>(() => {})
