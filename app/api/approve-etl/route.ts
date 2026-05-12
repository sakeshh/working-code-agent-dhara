/**
 * Next.js API route — proxy to FastAPI /approve_etl_code
 *
 * POST /api/approve-etl
 * Body: { session_id?, user_reply }   (user_reply: "approve" | "cancel" | modification text)
 *
 * On approval  → returns { status: "success", generated_code, saved_files }
 * On cancel    → returns { status: "cancelled" }
 * On modify    → returns { status: "plan_updated", etl_plan } (call /api/generate-etl again to re-review)
 */
import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8000";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();

    const { session_id = "default", user_reply = "approve" } = body;

    const backendRes = await fetch(`${BACKEND_URL}/approve_etl_code`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(process.env.BACKEND_TOKEN
          ? { "X-Backend-Token": process.env.BACKEND_TOKEN }
          : {}),
      },
      body: JSON.stringify({ session_id, user_reply }),
    });

    const data = await backendRes.json();
    return NextResponse.json(data, { status: backendRes.status });
  } catch (err: any) {
    console.error("[approve-etl] proxy error:", err);
    return NextResponse.json(
      { error: err?.message ?? "Internal server error" },
      { status: 500 }
    );
  }
}
