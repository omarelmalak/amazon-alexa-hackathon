import { useCallback, useEffect, useRef, useState } from 'react'
import { probeTts, synthesizeTts } from '../api/agent'

type SpeechRecognitionLike = {
  lang: string
  interimResults: boolean
  maxAlternatives: number
  continuous: boolean
  start: () => void
  stop: () => void
  onstart: ((ev: Event) => void) | null
  onend: ((ev: Event) => void) | null
  onerror: ((ev: Event) => void) | null
  onresult: ((ev: { results?: ArrayLike<{ 0?: { transcript?: string } }> }) => void) | null
}

declare global {
  interface Window {
    SpeechRecognition?: new () => SpeechRecognitionLike
    webkitSpeechRecognition?: new () => SpeechRecognitionLike
  }
}

export function useVoice(onHeard: (text: string) => void) {
  const [listening, setListening] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [ttsReady, setTtsReady] = useState<boolean | null>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const listeningRef = useRef(false)
  const onHeardRef = useRef(onHeard)
  onHeardRef.current = onHeard

  useEffect(() => {
    void probeTts().then(setTtsReady)
    window.speechSynthesis?.getVoices()
  }, [])

  const stopSpeech = useCallback(() => {
    window.speechSynthesis?.cancel()
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.src = ''
      audioRef.current = null
    }
  }, [])

  const speakBrowser = useCallback((text: string) => {
    return new Promise<void>((resolve) => {
      if (!window.speechSynthesis) {
        resolve()
        return
      }
      window.speechSynthesis.cancel()
      const u = new SpeechSynthesisUtterance(text)
      u.rate = 1.02
      const voices = window.speechSynthesis.getVoices()
      const preferred =
        voices.find((v) => /Samantha|Google US English|Karen|Moira/i.test(v.name)) ||
        voices.find((v) => v.lang?.startsWith('en')) ||
        null
      if (preferred) u.voice = preferred
      u.onend = () => resolve()
      u.onerror = () => resolve()
      window.speechSynthesis.speak(u)
    })
  }, [])

  const speak = useCallback(
    async (text: string) => {
      if (!text.trim()) return
      setSpeaking(true)
      stopSpeech()
      try {
        const useEleven = ttsReady ?? (await probeTts())
        if (useEleven) {
          try {
            const blob = await synthesizeTts(text)
            const url = URL.createObjectURL(blob)
            await new Promise<void>((resolve, reject) => {
              const audio = new Audio(url)
              audioRef.current = audio
              audio.onended = () => {
                URL.revokeObjectURL(url)
                audioRef.current = null
                resolve()
              }
              audio.onerror = () => {
                URL.revokeObjectURL(url)
                audioRef.current = null
                reject(new Error('audio playback failed'))
              }
              void audio.play().catch(reject)
            })
            return
          } catch {
            /* fall through */
          }
        }
        await speakBrowser(text)
      } finally {
        setSpeaking(false)
      }
    },
    [speakBrowser, stopSpeech, ttsReady],
  )

  const getRecognition = useCallback(() => {
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!Ctor) return null
    if (recognitionRef.current) return recognitionRef.current
    const rec = new Ctor()
    rec.lang = 'en-US'
    rec.interimResults = false
    rec.maxAlternatives = 1
    rec.continuous = false
    rec.onstart = () => {
      listeningRef.current = true
      setListening(true)
    }
    rec.onend = () => {
      listeningRef.current = false
      setListening(false)
    }
    rec.onerror = () => {
      listeningRef.current = false
      setListening(false)
    }
    rec.onresult = (ev) => {
      const text = ev.results?.[0]?.[0]?.transcript?.trim()
      if (text) onHeardRef.current(text)
    }
    recognitionRef.current = rec
    return rec
  }, [])

  const stopListening = useCallback(() => {
    try {
      recognitionRef.current?.stop()
    } catch {
      /* ignore */
    }
    listeningRef.current = false
    setListening(false)
  }, [])

  const startListening = useCallback(() => {
    const rec = getRecognition()
    if (!rec) return false
    stopSpeech()
    // Restart cleanly — browsers throw if start() is called while active.
    if (listeningRef.current) {
      try {
        rec.stop()
      } catch {
        /* ignore */
      }
      listeningRef.current = false
      setListening(false)
    }
    try {
      rec.start()
      return true
    } catch {
      // Retry once after a beat (common right after TTS releases the audio device).
      window.setTimeout(() => {
        try {
          rec.start()
        } catch {
          /* give up */
        }
      }, 350)
      return false
    }
  }, [getRecognition, stopSpeech])

  return {
    listening,
    speaking,
    speak,
    stopSpeech,
    startListening,
    stopListening,
    speechSupported: Boolean(
      typeof window !== 'undefined' &&
        (window.SpeechRecognition || window.webkitSpeechRecognition),
    ),
  }
}
