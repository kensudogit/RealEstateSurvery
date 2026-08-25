/**
 * バックエンド API へのリバースプロキシ。
 *
 * NEXT_PUBLIC_ 接頭辞の変数はビルド時にバンドルへ焼き込まれる。つまり
 * API の URL をビルド時に決め打ちする必要があり、設定を忘れると
 * localhost が埋まったまま公開されて「動いているのに全部 Failed to fetch」
 * という一番分かりにくい壊れ方をする。実際にそれを踏んだ。
 *
 * ブラウザからは常に同一オリジンの /api/backend/... を叩き、その先を
 * サーバ側の API_BASE_URL（実行時に読む）へ中継する。これで
 *
 *   - ビルドし直さずに接続先を変えられる
 *   - CORS の設定が不要になる（同一オリジンなので）
 *   - 設定漏れが 502 と明確なメッセージで表面化する
 *
 * SSE（ジョブ進捗）も通すため、レスポンスの body はそのまま流す。
 */

import { type NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://localhost:8000";

// ホップバイホップのヘッダは中継しない。転送すると接続が壊れる。
const HOP_BY_HOP = [
  "connection", "keep-alive", "transfer-encoding", "upgrade",
  "proxy-authenticate", "proxy-authorization", "te", "trailer",
  "host", "content-length",
];

const STRIPPED_REQUEST = new Set(HOP_BY_HOP);

// レスポンスでは content-encoding も落とす。
//
// Node の fetch は gzip / br のレスポンスを自動で展開して body を返すが、
// headers には content-encoding が残ったままになる。それをそのまま
// 転送すると、ブラウザは展開済みのデータをもう一度展開しようとして失敗し、
// 本文が空になる。デプロイ先のエッジが一定サイズ以上だけ圧縮するため、
// 「小さい応答は通るのに大きい応答だけ空になる」という分かりにくい形で出た。
const STRIPPED_RESPONSE = new Set([...HOP_BY_HOP, "content-encoding"]);

function filterHeaders(source: Headers, stripped: Set<string>): Headers {
  const result = new Headers();
  source.forEach((value, key) => {
    if (!stripped.has(key.toLowerCase())) result.append(key, value);
  });
  return result;
}

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const search = request.nextUrl.search;
  const target = `${API_BASE_URL}/${path.join("/")}${search}`;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: filterHeaders(request.headers, STRIPPED_REQUEST),
      body: request.method === "GET" || request.method === "HEAD"
        ? undefined
        : await request.arrayBuffer(),
      redirect: "manual",
      cache: "no-store",
      // SSE は開きっぱなしになるので、Next の既定タイムアウトに任せる
      // （duplex は Node の fetch で body を持つ場合に必要）
      ...(request.method === "GET" || request.method === "HEAD"
        ? {}
        : { duplex: "half" }),
    } as RequestInit);
  } catch (error) {
    // 接続先が間違っている／API が落ちている場合はここに来る。
    // 画面側で原因が読めるよう、素の fetch エラーではなく理由を返す。
    return Response.json(
      {
        detail: "バックエンド API へ接続できません",
        target: API_BASE_URL,
        cause: (error as Error).message,
        hint: "フロントエンドのサービスに API_BASE_URL を設定してください",
      },
      { status: 502 },
    );
  }

  const headers = filterHeaders(upstream.headers, STRIPPED_RESPONSE);
  // このレスポンスを確かに中継したことを示す印。デプロイ先で本文が
  // 空になるなどしたとき、プロキシを通ったのか手前で握られたのかを
  // ヘッダだけで切り分けられる。接続先はホストのみ（認証情報は出さない）。
  headers.set("x-proxy-upstream-status", String(upstream.status));
  try {
    headers.set("x-proxy-target", new URL(API_BASE_URL).host);
  } catch {
    headers.set("x-proxy-target", "invalid");
  }
  // SSE を途中でバッファさせない（プロキシによっては必要）
  if (headers.get("content-type")?.includes("text/event-stream")) {
    headers.set("cache-control", "no-cache, no-transform");
    headers.set("x-accel-buffering", "no");
  }

  return new Response(upstream.body, { status: upstream.status, headers });
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}

export async function PATCH(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}

export async function PUT(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}
