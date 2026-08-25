import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "物件資料オートメーション",
  description: "物件メールから一覧転記と資料生成までを自動化する",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <header className="site">
          <h1>物件資料オートメーション</h1>
          <nav>
            <a href="/">ダッシュボード</a>
            <a href="/properties">物件一覧</a>
          </nav>
        </header>
        <div className="container">{children}</div>
      </body>
    </html>
  );
}
