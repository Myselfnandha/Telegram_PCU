use std::sync::Arc;
use grammers_client::Client;
use grammers_tl_types as tl;
use tokio::sync::Semaphore;
use crate::safety::AntiBanPacer;

pub struct UploadEngine {
    pacer: Arc<AntiBanPacer>,
    upload_semaphore: Arc<Semaphore>,
}

impl UploadEngine {
    pub fn new(pacer: Arc<AntiBanPacer>, max_concurrent_parts: usize) -> Self {
        Self {
            pacer,
            upload_semaphore: Arc::new(Semaphore::new(max_concurrent_parts)),
        }
    }

    pub async fn upload_part(
        &self,
        client: &Client,
        file_id: i64,
        file_part: i32,
        file_total_parts: i32,
        bytes: Vec<u8>,
        account_idx: usize,
    ) -> Result<bool, String> {
        let _permit = self.upload_semaphore.acquire().await.map_err(|e| e.to_string())?;
        self.pacer.wait_for_slot(account_idx, bytes.len(), false).await;

        let bytes_len = bytes.len();
        let req = tl::functions::upload::SaveBigFilePart {
            file_id,
            file_part,
            file_total_parts,
            bytes,
        };

        let res = tokio::time::timeout(std::time::Duration::from_secs(25), client.invoke(&req))
            .await
            .map_err(|_| "MTProto SaveBigFilePart timed out after 25s".to_string())?
            .map_err(|e| format!("MTProto SaveBigFilePart error: {e:?}"))?;
        self.pacer.report_success(bytes_len);
        Ok(res)
    }
}
