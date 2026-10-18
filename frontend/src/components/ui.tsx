// The app's few building blocks, one look everywhere: buttons, fields, badges, a spinner,
// tooltips, dialogs and menus (Radix, for focus and keyboard), and the empty state.
import * as DialogPrimitive from '@radix-ui/react-dialog';
import * as MenuPrimitive from '@radix-ui/react-dropdown-menu';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { Loader2, X } from 'lucide-react';
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '../lib/format';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'danger-soft';
type Size = 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm';

const VARIANT: Record<Variant, string> = {
  primary: 'bg-accent text-on-accent hover:bg-accent-hover shadow-sm',
  secondary: 'bg-panel text-fg border border-line hover:bg-hover hover:border-line-strong',
  ghost: 'text-muted hover:text-fg hover:bg-hover',
  danger: 'bg-danger text-white hover:opacity-90 shadow-sm',
  'danger-soft': 'text-danger-fg border border-danger-line hover:bg-danger-soft',
};
const SIZE: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5 rounded-md',
  md: 'h-8 px-3 text-sm gap-2 rounded-md',
  lg: 'h-10 px-4 text-sm gap-2 rounded-lg',
  icon: 'h-8 w-8 rounded-md',
  'icon-sm': 'h-7 w-7 rounded-md',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  busy?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', size = 'md', busy, className, children, disabled, ...rest }, ref) => (
    <button
      ref={ref}
      disabled={disabled || busy}
      className={cn('inline-flex shrink-0 items-center justify-center font-medium whitespace-nowrap transition-colors',
        'disabled:opacity-50', VARIANT[variant], SIZE[size], className)}
      {...rest}
    >
      {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...rest }, ref) => (
  <input
    ref={ref}
    className={cn('h-8 w-full rounded-md border border-line bg-panel px-2.5 text-sm text-fg placeholder:text-faint',
      'focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20', className)}
    {...rest}
  />
));
Input.displayName = 'Input';

export function Badge({ className, children, tone = 'neutral' }: {
  className?: string; children: ReactNode; tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'danger';
}) {
  const tones = {
    neutral: 'bg-surface text-muted',
    accent: 'bg-accent-soft text-accent-fg',
    ok: 'bg-ok/12 text-ok',
    warn: 'bg-warn/12 text-warn',
    danger: 'bg-danger-soft text-danger-fg',
  };
  return (
    <span className={cn('inline-flex h-5 shrink-0 items-center gap-1 rounded px-1.5 text-2xs font-medium whitespace-nowrap',
      tones[tone], className)}>
      {children}
    </span>
  );
}

export const Spinner = ({ className }: { className?: string }) => (
  <Loader2 className={cn('size-4 animate-spin text-faint', className)} aria-hidden />
);

export const Kbd = ({ children }: { children: ReactNode }) => (
  <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-line bg-subtle px-1 font-mono text-2xs text-muted">
    {children}
  </kbd>
);

export function Tip({ label, children, side = 'top' }: { label: ReactNode; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  return (
    <TooltipPrimitive.Root delayDuration={300}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content side={side} sideOffset={6}
          className="z-50 max-w-80 rounded-md bg-fg px-2 py-1 text-xs text-bg shadow-pop">
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
export const TipProvider = TooltipPrimitive.Provider;

// Dialog --------------------------------------------------------------------------------------

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;

export function DialogContent({ title, description, children, className, closeLabel }: {
  title: ReactNode; description?: ReactNode; children?: ReactNode; className?: string; closeLabel: string;
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[1px]" />
      <DialogPrimitive.Content
        className={cn('fixed top-1/2 left-1/2 z-50 max-h-[calc(100vh-48px)] w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
          'overflow-y-auto rounded-xl border border-line bg-panel p-5 shadow-pop focus:outline-none', className)}
      >
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <DialogPrimitive.Title className="text-lg font-semibold">{title}</DialogPrimitive.Title>
            {description && <DialogPrimitive.Description className="mt-1 text-sm text-muted">{description}</DialogPrimitive.Description>}
          </div>
          <DialogPrimitive.Close asChild>
            <Button variant="ghost" size="icon-sm" aria-label={closeLabel}><X className="size-4" /></Button>
          </DialogPrimitive.Close>
        </div>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

/** A panel sliding in from the side -- the preview on a narrow screen. */
export function Sheet({ open, onOpenChange, title, closeLabel, children }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: ReactNode; closeLabel: string; children: ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <DialogPrimitive.Content className="fixed inset-y-0 right-0 z-50 flex w-[min(640px,100vw)] flex-col border-l border-line bg-panel shadow-pop focus:outline-none">
          <div className="flex h-12 items-center justify-between gap-2 border-b border-line px-3">
            <DialogPrimitive.Title className="truncate text-sm font-semibold">{title}</DialogPrimitive.Title>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label={closeLabel}><X className="size-4" /></Button>
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

// Menu ----------------------------------------------------------------------------------------

export const Menu = MenuPrimitive.Root;
export const MenuTrigger = MenuPrimitive.Trigger;
export const MenuLabel = ({ children }: { children: ReactNode }) => (
  <MenuPrimitive.Label className="px-2 pt-1.5 pb-1 text-2xs font-medium tracking-wide text-faint uppercase">{children}</MenuPrimitive.Label>
);
export const MenuSeparator = () => <MenuPrimitive.Separator className="my-1 h-px bg-line" />;
export const MenuRadioGroup = MenuPrimitive.RadioGroup;

export function MenuContent({ children, align = 'end' }: { children: ReactNode; align?: 'start' | 'end' }) {
  return (
    <MenuPrimitive.Portal>
      <MenuPrimitive.Content align={align} sideOffset={6}
        className="z-50 min-w-44 rounded-lg border border-line bg-panel p-1 shadow-pop">
        {children}
      </MenuPrimitive.Content>
    </MenuPrimitive.Portal>
  );
}

export function MenuItem({ onSelect, children }: { onSelect: () => void; children: ReactNode }) {
  return (
    <MenuPrimitive.Item onSelect={onSelect}
      className="flex h-8 max-w-80 cursor-pointer items-center truncate rounded-md px-2 text-sm outline-none select-none data-[highlighted]:bg-hover">
      {children}
    </MenuPrimitive.Item>
  );
}

export function MenuRadio({ value, children }: { value: string; children: ReactNode }) {
  return (
    <MenuPrimitive.RadioItem value={value}
      className="relative flex h-8 cursor-pointer items-center rounded-md pr-2 pl-7 text-sm outline-none select-none data-[highlighted]:bg-hover">
      <MenuPrimitive.ItemIndicator className="absolute left-2.5 size-1.5 rounded-full bg-accent" />
      {children}
    </MenuPrimitive.RadioItem>
  );
}

// States --------------------------------------------------------------------------------------

export function Empty({ icon, title, children, className }: {
  icon?: ReactNode; title: ReactNode; children?: ReactNode; className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 px-6 py-12 text-center', className)}>
      {icon && <div className="mb-1 grid size-10 place-items-center rounded-xl bg-surface text-muted">{icon}</div>}
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="max-w-sm text-sm text-muted">{children}</div>}
    </div>
  );
}

export const Skeleton = ({ className }: { className?: string }) => <div className={cn('skeleton h-4', className)} />;
