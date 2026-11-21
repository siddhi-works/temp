import Link from "next/link";
import Logo from "./Logo";

export default function Header({ who, wide = false }: { who?: string | null; wide?: boolean }) {
  return (
    <header className="top">
      <div className={wide ? "wrap-wide" : "wrap"}>
        <Link className="brand" href="/">
          <Logo />
          Saathi Campus
        </Link>
        {who ? <span className="who">{who}</span> : null}
      </div>
    </header>
  );
}
