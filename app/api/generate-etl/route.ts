/**
 * Next.js API route — proxy to FastAPI /generate_etl_code
 *
 * POST /api/generate-etl
 * Body: { session_id?, engine?, target?, target_path?, message? }
 *
 * Returns the ETL plan (status: "plan_ready") for human review.
 * The frontend then calls /api/approve-etl to generate actual code.
 */
import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8000";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();

    const {
      session_id = "default",
      engine = "python",
      target = "local_file",
      target_path,
      message = "generate etl code",
    } = body;

    const backendRes = await fetch(`${BACKEND_URL}/generate_etl_code`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(process.env.BACKEND_TOKEN
          ? { "X-Backend-Token": process.env.BACKEND_TOKEN }
          : {}),
      },
      body: JSON.stringify({ session_id, engine, target, target_path, message }),
    });

    const data = await backendRes.json();
    return NextResponse.json(data, { status: backendRes.status });
  } catch (err: any) {
    console.error("[generate-etl] proxy error:", err);
    return NextResponse.json(
      { error: err?.message ?? "Internal server error" },
      { status: 500 }
    );
  }
}
