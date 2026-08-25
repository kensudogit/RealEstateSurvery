/**
 * サーバ起動時に一度だけ走る。
 *
 * API_BASE_URL の設定漏れは、リクエストして初めて 502 で分かる。それだと
 * 「デプロイは成功しているのに画面が真っ赤」の状態でしばらく気づけない。
 * 起動時にデプロイログへ出しておけば、公開前に気づける。
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const base = process.env.API_BASE_URL;
  const inProduction = process.env.NODE_ENV === "production";

  if (!base) {
    console.warn(
      "[frontend] API_BASE_URL が未設定です。既定の http://localhost:8000 へ中継します。" +
        (inProduction
          ? " 公開環境ではバックエンド API の URL を設定してください。"
          : ""),
    );
    return;
  }

  if (inProduction && /^https?:\/\/(localhost|127\.0\.0\.1)/.test(base)) {
    console.warn(
      `[frontend] API_BASE_URL が ${base} になっています。` +
        "公開環境では同じコンテナに API がいないため、画面からの通信は失敗します。",
    );
    return;
  }

  console.log(`[frontend] API の中継先: ${base}`);

  // 起動時点で疎通も見ておく。落ちている場合でも起動自体は妨げない
  // （API が後から立ち上がる構成もあるため）。
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    const response = await fetch(`${base}/health`, { signal: controller.signal });
    clearTimeout(timer);
    console.log(
      response.ok
        ? "[frontend] API へ疎通できました"
        : `[frontend] API が ${response.status} を返しました（起動は続行します）`,
    );
  } catch (error) {
    console.warn(
      `[frontend] API へ疎通できません: ${(error as Error).message}` +
        "（起動は続行します。API が後から立ち上がる場合は問題ありません）",
    );
  }
}
