import {
  AlertCircle,
  AlertTriangle,
  Check,
  ChevronDown,
  Circle,
  Info,
  LoaderCircle,
  RotateCw,
  X,
} from 'lucide-react'
import {
  type ButtonHTMLAttributes,
  type PropsWithChildren,
  type ReactNode,
  useEffect,
  useId,
  useRef,
} from 'react'
import type { CheckStatus, StructuredError } from './types'

export type ButtonTone = 'primary' | 'secondary' | 'quiet' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: ButtonTone
  busy?: boolean
  icon?: ReactNode
}

export function Button({
  tone = 'secondary',
  busy = false,
  icon,
  disabled,
  className = '',
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`mc-button mc-button--${tone} ${className}`}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {busy ? <LoaderCircle className="mc-spin" aria-hidden="true" /> : icon}
      <span>{children}</span>
    </button>
  )
}

export function StatusBadge({
  tone,
  children,
}: PropsWithChildren<{ tone: 'success' | 'warning' | 'error' | 'neutral' | 'info' }>) {
  return <span className={`mc-badge mc-badge--${tone}`}>{children}</span>
}

export function CheckMark({ status }: { status: CheckStatus }) {
  const Icon = status === 'pass' ? Check : status === 'warning' ? AlertTriangle : status === 'fail' ? AlertCircle : Circle
  return <span className={`mc-check-mark mc-check-mark--${status}`}><Icon aria-hidden="true" /></span>
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string
  title: string
  description: string
  actions?: ReactNode
}) {
  return (
    <header className="mc-section-heading">
      <div>
        {eyebrow ? <span className="mc-eyebrow">{eyebrow}</span> : null}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="mc-section-heading__actions">{actions}</div> : null}
    </header>
  )
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="mc-empty">
      {icon ? <span className="mc-empty__icon">{icon}</span> : null}
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  )
}

export function Metric({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="mc-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </div>
  )
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  const bounded = Math.min(100, Math.max(0, value))
  return (
    <div className="mc-progress" aria-label={label} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(bounded)}>
      <span style={{ width: `${bounded}%` }} />
    </div>
  )
}

export function AdvancedDetails({ summary = 'Advanced details', children, open = false }: PropsWithChildren<{ summary?: string; open?: boolean }>) {
  return (
    <details className="mc-details" open={open || undefined}>
      <summary><ChevronDown aria-hidden="true" />{summary}</summary>
      <div className="mc-details__body">{children}</div>
    </details>
  )
}

export function Notice({
  tone = 'info',
  title,
  children,
}: PropsWithChildren<{ tone?: 'info' | 'success' | 'warning' | 'error'; title?: string }>) {
  const Icon = tone === 'success' ? Check : tone === 'warning' ? AlertTriangle : tone === 'error' ? AlertCircle : Info
  return (
    <div className={`mc-notice mc-notice--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <Icon aria-hidden="true" />
      <div>{title ? <strong>{title}</strong> : null}{children}</div>
    </div>
  )
}

export function ErrorCard({
  error,
  onRetry,
  onDismiss,
  retryLabel = 'Try again',
}: {
  error: StructuredError
  onRetry?: () => void
  onDismiss?: () => void
  retryLabel?: string
}) {
  return (
    <article className="mc-error-card" role="alert">
      <div className="mc-error-card__icon"><AlertCircle aria-hidden="true" /></div>
      <div className="mc-error-card__content">
        <span className="mc-eyebrow">{error.code.replace(/_/g, ' ')}</span>
        <h2>{error.title}</h2>
        <p>{error.summary}</p>
        {error.likelyCause ? <p className="mc-muted"><strong>Likely cause:</strong> {error.likelyCause}</p> : null}
        {error.recommendedAction ? <p><strong>Recommended:</strong> {error.recommendedAction}</p> : null}
        <div className="mc-button-row">
          {onRetry && error.retryable ? <Button icon={<RotateCw aria-hidden="true" />} onClick={onRetry}>{retryLabel}</Button> : null}
          {onDismiss ? <Button tone="quiet" onClick={onDismiss}>Dismiss</Button> : null}
        </div>
        {error.technicalDetails || error.relatedPath ? (
          <AdvancedDetails summary="Technical details">
            {error.relatedPath ? <p><strong>Related path</strong><br /><code>{error.relatedPath}</code></p> : null}
            {error.technicalDetails ? <pre>{error.technicalDetails}</pre> : null}
          </AdvancedDetails>
        ) : null}
      </div>
    </article>
  )
}

export function Modal({
  open,
  title,
  description,
  children,
  footer,
  onClose,
  closeLabel = 'Close dialog',
}: PropsWithChildren<{
  open: boolean
  title: string
  description?: string
  footer: ReactNode
  onClose: () => void
  closeLabel?: string
}>) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      ))
      const firstItem = focusable[0]
      const lastItem = focusable[focusable.length - 1]
      if (!firstItem || !lastItem) return
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault()
        lastItem.focus()
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault()
        firstItem.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previous?.focus()
    }
  }, [onClose, open])

  if (!open) return null
  return (
    <div className="mc-modal-backdrop" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose()
    }}>
      <div
        ref={dialogRef}
        className="mc-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
      >
        <div className="mc-modal__heading">
          <div>
            <span className="mc-eyebrow">Confirmation required</span>
            <h2 id={titleId}>{title}</h2>
            {description ? <p id={descriptionId}>{description}</p> : null}
          </div>
          <button ref={closeRef} className="mc-icon-button" aria-label={closeLabel} onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </div>
        <div className="mc-modal__body">{children}</div>
        <div className="mc-modal__footer">{footer}</div>
      </div>
    </div>
  )
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="mc-skeleton" aria-label="Loading" role="status">
      {Array.from({ length: lines }, (_, index) => <span key={index} />)}
    </div>
  )
}
