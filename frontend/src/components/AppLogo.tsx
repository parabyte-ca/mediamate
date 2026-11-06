/** The MediaMate mark (public/logo.svg): a record whose centre is a play button. */
export default function AppLogo({ className = "h-8 w-8" }: { className?: string }) {
  return <img src="/logo.svg" alt="" aria-hidden width={64} height={64} className={`shrink-0 ${className}`} />;
}
