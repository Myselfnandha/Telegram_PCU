mod session;
mod safety;
mod stream;
mod upload;
mod download;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use axum::extract::{Json, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use base64::engine::general_purpose::STANDARD as BASE64_STD;
use base64::Engine;
use serde::{Deserialize, Serialize};
use tower_http::cors::CorsLayer;

use crate::safety::AntiBanPacer;
use crate::session::MultiAccountSessionPool;
use crate::stream::{StreamEngine, StreamPriority};
use crate::upload::UploadEngine;
use crate::download::DownloadEngine;

pub struct AppState {
    pub pool: MultiAccountSessionPool,
    pub pacer: Arc<AntiBanPacer>,
    pub stream_engine: Arc<StreamEngine>,
    pub upload_engine: Arc<UploadEngine>,
    pub download_engine: Arc<DownloadEngine>,
    pub request_counter: AtomicUsize,
}

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    engine: &'static str,
    active_accounts: usize,
    rate_limit_mbs: f64,
}

#[derive(Deserialize)]
struct RegisterSessionRequest {
    session_string: String,
    api_id: i32,
    account_idx: Option<usize>,
}

#[derive(Serialize)]
struct RegisterSessionResponse {
    success: bool,
    active_accounts: usize,
}

#[derive(Deserialize)]
struct FetchChunkRequest {
    dc_id: i32,
    doc_id: i64,
    access_hash: i64,
    file_reference_b64: String,
    offset: i64,
    limit: i32,
    priority: Option<String>,
    account_idx: Option<usize>,
}

#[derive(Deserialize)]
struct PrewarmRequest {
    dc_id: i32,
    doc_id: i64,
    access_hash: i64,
    file_reference_b64: String,
    file_size: i64,
}

#[derive(Deserialize)]
struct UploadPartRequest {
    file_id: i64,
    file_part: i32,
    file_total_parts: i32,
    bytes_b64: String,
    account_idx: Option<usize>,
}

#[derive(Deserialize)]
struct ReportFloodRequest {
    account_idx: usize,
    seconds: u64,
}

async fn health_handler(State(state): State<Arc<AppState>>) -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok",
        engine: "Rust MTProto (grammers)",
        active_accounts: state.pool.count().await,
        rate_limit_mbs: state.pacer.get_throughput_rate(),
    })
}

async fn register_session_handler(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<RegisterSessionRequest>,
) -> Result<Json<RegisterSessionResponse>, (StatusCode, String)> {
    let acc_idx = payload.account_idx.unwrap_or(0);
    let info = session::decode_telethon_string_session(&payload.session_string)
        .map_err(|e| (StatusCode::BAD_REQUEST, e))?;
    let home_dc = info.dc_id;

    let client = session::create_client_from_string(&payload.session_string, payload.api_id, acc_idx)
        .await
        .map_err(|e| (StatusCode::BAD_REQUEST, e))?;

    let total = if let Some(idx) = payload.account_idx {
        state.pool.set_client(idx, client).await
    } else {
        state.pool.add_client(client).await
    };
    state.stream_engine.mark_dc_authed(acc_idx, home_dc).await;
    Ok(Json(RegisterSessionResponse {
        success: true,
        active_accounts: total,
    }))
}

async fn fetch_chunk_handler(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<FetchChunkRequest>,
) -> Result<Response, (StatusCode, String)> {
    let file_ref = BASE64_STD
        .decode(&payload.file_reference_b64)
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("Invalid base64: {e}")))?;

    let count = state.pool.count().await;
    if count == 0 {
        return Err((StatusCode::SERVICE_UNAVAILABLE, "No Telegram sessions registered".to_string()));
    }

    let selected_idx = if let Some(idx) = payload.account_idx {
        if idx < count {
            idx
        } else {
            0
        }
    } else {
        let req_idx = state.request_counter.fetch_add(1, Ordering::Relaxed);
        let mut idx = req_idx % count;
        if state.pacer.is_account_cooling(idx).await && count > 1 {
            idx = (idx + 1) % count;
        }
        idx
    };

    if state.pacer.is_account_cooling(selected_idx).await {
        return Err((StatusCode::TOO_MANY_REQUESTS, format!("Account {selected_idx} is cooling down")));
    }

    let client = state.pool.get_client(selected_idx).await
        .ok_or((StatusCode::INTERNAL_SERVER_ERROR, "Client unavailable".to_string()))?;

    let prio = match payload.priority.as_deref() {
        Some("high") | Some("player") => StreamPriority::High,
        _ => StreamPriority::Bulk,
    };

    let bytes = state.stream_engine.fetch_chunk(
        &client,
        payload.dc_id,
        payload.doc_id,
        payload.access_hash,
        file_ref,
        payload.offset,
        payload.limit,
        prio,
        selected_idx,
    ).await.map_err(|e| {
        if e.contains("FLOOD_WAIT") || e.contains("FLOOD_PREMIUM_WAIT") || e.contains("code: 420") {
            (StatusCode::TOO_MANY_REQUESTS, e)
        } else if e.contains("FILE_REFERENCE_EXPIRED") {
            (StatusCode::BAD_REQUEST, e)
        } else {
            (StatusCode::BAD_GATEWAY, e)
        }
    })?;

    let resp = Response::builder()
        .status(StatusCode::OK)
        .header("Content-Type", "application/octet-stream")
        .header("Content-Length", bytes.len().to_string())
        .body(axum::body::Body::from(bytes))
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(resp)
}

async fn prewarm_handler(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<PrewarmRequest>,
) -> impl IntoResponse {
    let file_ref = match BASE64_STD.decode(&payload.file_reference_b64) {
        Ok(r) => r,
        Err(_) => return StatusCode::BAD_REQUEST,
    };

    let count = state.pool.count().await;
    if count == 0 {
        return StatusCode::SERVICE_UNAVAILABLE;
    }

    let req_idx = state.request_counter.fetch_add(1, Ordering::Relaxed);
    let selected_idx = req_idx % count;
    if let Some(client) = state.pool.get_client(selected_idx).await {
        state.stream_engine.prewarm_header_and_tail(
            &client,
            payload.dc_id,
            payload.doc_id,
            payload.access_hash,
            file_ref,
            payload.file_size,
            selected_idx,
        ).await;
        StatusCode::ACCEPTED
    } else {
        StatusCode::INTERNAL_SERVER_ERROR
    }
}

async fn upload_part_handler(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<UploadPartRequest>,
) -> Result<StatusCode, (StatusCode, String)> {
    let raw_bytes = BASE64_STD
        .decode(&payload.bytes_b64)
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("Invalid base64: {e}")))?;

    let count = state.pool.count().await;
    if count == 0 {
        return Err((StatusCode::SERVICE_UNAVAILABLE, "No Telegram sessions registered".to_string()));
    }

    let selected_idx = if let Some(idx) = payload.account_idx {
        if idx < count {
            idx
        } else {
            0
        }
    } else {
        0
    };

    let client = state.pool.get_client(selected_idx).await
        .ok_or((StatusCode::INTERNAL_SERVER_ERROR, "Client unavailable".to_string()))?;

    state.upload_engine.upload_part(
        &client,
        payload.file_id,
        payload.file_part,
        payload.file_total_parts,
        raw_bytes,
        selected_idx,
    ).await.map_err(|e| (StatusCode::BAD_GATEWAY, e))?;

    Ok(StatusCode::OK)
}

async fn report_flood_handler(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<ReportFloodRequest>,
) -> StatusCode {
    state.pacer.report_flood_wait(payload.account_idx, payload.seconds).await;
    StatusCode::OK
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let pacer = Arc::new(AntiBanPacer::new(24.0, 48.0));
    let stream_engine = Arc::new(StreamEngine::new(Arc::clone(&pacer), 32));
    let upload_engine = Arc::new(UploadEngine::new(Arc::clone(&pacer), 16));
    let download_engine = Arc::new(DownloadEngine::new(Arc::clone(&stream_engine)));

    let app_state = Arc::new(AppState {
        pool: MultiAccountSessionPool::new(),
        pacer,
        stream_engine,
        upload_engine,
        download_engine,
        request_counter: AtomicUsize::new(0),
    });

    let app = Router::new()
        .route("/health", get(health_handler))
        .route("/api/session/register", post(register_session_handler))
        .route("/api/stream/chunk", post(fetch_chunk_handler))
        .route("/api/stream/prewarm", post(prewarm_handler))
        .route("/api/upload/part", post(upload_part_handler))
        .route("/api/safety/report_flood", post(report_flood_handler))
        .layer(CorsLayer::permissive())
        .with_state(app_state);

    let addr = "127.0.0.1:8099";
    let listener = tokio::net::TcpListener::bind(addr).await.expect("Failed to bind to 127.0.0.1:8099");
    println!("tg-rust-core running on http://{addr}");
    axum::serve(listener, app).await.expect("Server failure");
}
