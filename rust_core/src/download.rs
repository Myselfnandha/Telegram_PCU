use std::sync::Arc;
use grammers_client::Client;
use tokio::fs::File;
use tokio::io::AsyncWriteExt;
use crate::stream::{StreamEngine, StreamPriority};

pub struct DownloadEngine {
    stream_engine: Arc<StreamEngine>,
}

impl DownloadEngine {
    pub fn new(stream_engine: Arc<StreamEngine>) -> Self {
        Self { stream_engine }
    }

    pub async fn download_file_to_disk(
        &self,
        clients: &[Client],
        dc_id: i32,
        doc_id: i64,
        access_hash: i64,
        file_reference: Vec<u8>,
        file_size: i64,
        target_path: &str,
    ) -> Result<u64, String> {
        if clients.is_empty() {
            return Err("No active clients in pool".to_string());
        }

        let mut file = File::create(target_path).await.map_err(|e| format!("Failed to create file: {e}"))?;
        let chunk_size = 512 * 1024;
        let mut downloaded = 0i64;
        let mut part_idx = 0usize;

        while downloaded < file_size {
            let to_read = (file_size - downloaded).min(chunk_size as i64) as i32;
            let client = &clients[part_idx % clients.len()];
            let account_idx = part_idx % clients.len();

            let chunk = self.stream_engine.fetch_chunk(
                client,
                dc_id,
                doc_id,
                access_hash,
                file_reference.clone(),
                downloaded,
                to_read,
                StreamPriority::Bulk,
                account_idx,
            ).await?;

            file.write_all(&chunk).await.map_err(|e| format!("Disk write error: {e}"))?;
            downloaded += chunk.len() as i64;
            part_idx += 1;
        }

        file.flush().await.map_err(|e| format!("Flush error: {e}"))?;
        Ok(downloaded as u64)
    }
}
