import {
  IconBulb,
  IconCalendar,
  IconCheckSquare,
  IconClock,
  IconDoc,
  IconHome,
  IconList,
} from './icons'

const NAV = [
  { id: 'home', label: 'Home', Icon: IconHome },
  { id: 'history', label: 'History', Icon: IconClock },
  { id: 'ideas', label: 'Ideas', Icon: IconBulb },
  { id: 'calendar', label: 'Calendar', Icon: IconCalendar },
  { id: 'lists', label: 'Lists', Icon: IconList },
  { id: 'tasks', label: 'Tasks', Icon: IconCheckSquare },
  { id: 'notes', label: 'Notes', Icon: IconDoc },
] as const

type Props = {
  active?: string
  onSelect?: (id: string) => void
}

export function SideDock({ active = 'home', onSelect }: Props) {
  return (
    <nav className="side-dock" aria-label="Alexa navigation">
      {NAV.map(({ id, label, Icon }) => (
        <button
          key={id}
          type="button"
          className={`dock-btn${active === id ? ' active' : ''}`}
          aria-label={label}
          aria-current={active === id ? 'page' : undefined}
          onClick={() => onSelect?.(id)}
        >
          <Icon />
        </button>
      ))}
    </nav>
  )
}
