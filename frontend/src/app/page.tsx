import Link from "next/link";
import { Assistant } from "./assistant";

export default function Home() {
  return (
    <main className="relative h-screen w-full">
      <Link
        href="/sources"
        className="absolute right-3 top-3 z-10 text-sm text-muted-foreground underline-offset-4 hover:underline"
      >
        データ管理
      </Link>
      <Assistant />
    </main>
  );
}
