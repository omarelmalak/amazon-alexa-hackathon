import { useRef, type FormEvent } from 'react'
import { AlexaMark, IconMic, IconPaperclip, IconSend } from './icons'

type Props = {
  value: string
  onChange: (v: string) => void
  onSubmit: (text: string) => void
  onMic: () => void
  listening?: boolean
  busy?: boolean
  placeholder?: string
  autoFocus?: boolean
}

export function AskBar({
  value,
  onChange,
  onSubmit,
  onMic,
  listening,
  busy,
  placeholder = 'Ask Alexa',
  autoFocus,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const text = value.trim()
    if (!text || busy) return
    onSubmit(text)
  }

  return (
    <form className={`ask-bar${listening ? ' listening' : ''}`} onSubmit={handleSubmit}>
      <span className="ask-mark" aria-hidden>
        <AlexaMark size={28} />
      </span>
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        autoFocus={autoFocus}
        disabled={busy}
        aria-label="Ask Alexa"
      />
      <div className="ask-actions">
        <button type="button" className="icon-btn" aria-label="Attach" disabled>
          <IconPaperclip />
        </button>
        <button
          type="button"
          className={`icon-btn mic${listening ? ' on' : ''}`}
          aria-label={listening ? 'Listening' : 'Speak'}
          aria-pressed={listening}
          onClick={onMic}
          disabled={busy}
        >
          <IconMic />
        </button>
        <button
          type="submit"
          className="send-btn"
          aria-label="Send"
          disabled={busy || !value.trim()}
        >
          <IconSend />
        </button>
      </div>
    </form>
  )
}
