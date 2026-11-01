import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-3 p-page text-center">
      <h1 className="text-2xl font-semibold text-primary">Page not found</h1>
      <p className="text-sm text-muted-foreground">
        The page you are looking for does not exist or you do not have access.
      </p>
      <Link href="/" className="text-sm font-medium text-primary hover:underline">
        Go to home
      </Link>
    </main>
  );
}
