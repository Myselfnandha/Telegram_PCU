use std::collections::HashMap;
use std::sync::Arc;
use bytes::Bytes;
use grammers_client::Client;
use grammers_tl_types as tl;
use tokio::sync::{RwLock, Semaphore};

use crate::safety::AntiBanPacer;

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum StreamPriority {
    High,
    Bulk,
}

#[derive(Hash, PartialEq, Eq, Clone, Debug)]
pub struct ChunkKey {
    pub dc_id: i32,
    pub doc_id: i64,
    pub offset: i64,
    pub limit: i32,
}

pub struct ChunkRingCache {
    entries: RwLock<HashMap<ChunkKey, Bytes>>,
    keys: RwLock<Vec<ChunkKey>>,
    max_entries: usize,
}

impl ChunkRingCache {
    pub fn new(max_entries: usize) -> Self {
        Self {
            entries: RwLock::new(HashMap::with_capacity(max_entries)),
            keys: RwLock::new(Vec::with_capacity(max_entries)),
            max_entries,
        }
    }

    pub async fn get(&self, key: &ChunkKey) -> Option<Bytes> {
        let map = self.entries.read().await;
        map.get(key).cloned()
    }

    pub async fn insert(&self, key: ChunkKey, data: Bytes) {
        let mut map = self.entries.write().await;
        let mut keys = self.keys.write().await;

        if map.len() >= self.max_entries {
            if !keys.is_empty() {
                let oldest = keys.remove(0);
                map.remove(&oldest);
            }
        }
        keys.push(key.clone());
        map.insert(key, data);
    }
}

pub struct StreamEngine {
    cache: Arc<ChunkRingCache>,
    bulk_semaphore: Arc<Semaphore>,
    account_semaphores: Arc<RwLock<HashMap<usize, Arc<Semaphore>>>>,
    pacer: Arc<AntiBanPacer>,
    authed_dcs: Arc<RwLock<std::collections::HashSet<(usize, i32)>>>,
    auth_lock: Arc<tokio::sync::Mutex<()>>,
}

impl StreamEngine {
    pub fn new(pacer: Arc<AntiBanPacer>, max_bulk_connections: usize) -> Self {
        Self {
            cache: Arc::new(ChunkRingCache::new(128)),
            bulk_semaphore: Arc::new(Semaphore::new(max_bulk_connections)),
            account_semaphores: Arc::new(RwLock::new(HashMap::new())),
            pacer,
            authed_dcs: Arc::new(RwLock::new(std::collections::HashSet::new())),
            auth_lock: Arc::new(tokio::sync::Mutex::new(())),
        }
    }

    async fn get_account_semaphore(&self, account_idx: usize) -> Arc<Semaphore> {
        {
            let map = self.account_semaphores.read().await;
            if let Some(sem) = map.get(&account_idx) {
                return Arc::clone(sem);
            }
        }
        let mut map = self.account_semaphores.write().await;
        Arc::clone(map.entry(account_idx).or_insert_with(|| Arc::new(Semaphore::new(6))))
    }

    async fn copy_auth_to_dc(&self, client: &Client, dc_id: i32, account_idx: usize) -> Result<(), String> {
        let exp = tl::functions::auth::ExportAuthorization { dc_id };
        let res = match tokio::time::timeout(std::time::Duration::from_secs(12), client.invoke(&exp)).await {
            Ok(Ok(r)) => r,
            Ok(Err(e)) => {
                let err_str = format!("{e:?}");
                if err_str.contains("DC_ID_INVALID") {
                    return Ok(());
                }
                if let Some(pos) = err_str.find("FLOOD_WAIT").or_else(|| err_str.find("FLOOD_PREMIUM_WAIT")) {
                    let sub = &err_str[pos..];
                    if let Some(v_pos) = sub.find("Some(") {
                        let num_str = sub[v_pos + 5..].chars().take_while(|c| c.is_ascii_digit()).collect::<String>();
                        if let Ok(secs) = num_str.parse::<u64>() {
                            self.pacer.report_flood_wait(account_idx, secs).await;
                        }
                    }
                }
                return Err(format!("ExportAuthorization error: {e:?}"));
            }
            Err(_) => {
                return Err("ExportAuthorization timed out after 12s".to_string());
            }
        };
        match res {
            tl::enums::auth::ExportedAuthorization::Authorization(auth) => {
                let imp = tl::functions::auth::ImportAuthorization {
                    id: auth.id,
                    bytes: auth.bytes,
                };
                match tokio::time::timeout(std::time::Duration::from_secs(12), client.invoke_in_dc(dc_id, &imp)).await {
                    Ok(Ok(_)) => Ok(()),
                    Ok(Err(e)) => {
                        let err_str = format!("{e:?}");
                        if err_str.contains("DC_ID_INVALID") {
                            Ok(())
                        } else {
                            Err(format!("ImportAuthorization error: {e:?}"))
                        }
                    }
                    Err(_) => Err("ImportAuthorization timed out after 12s".to_string()),
                }
            }
        }
    }

    pub async fn mark_dc_authed(&self, account_idx: usize, dc_id: i32) {
        let mut set = self.authed_dcs.write().await;
        set.insert((account_idx, dc_id));
    }

    pub async fn clear_authed_dcs(&self, account_idx: Option<usize>) {
        let mut set = self.authed_dcs.write().await;
        if let Some(idx) = account_idx {
            set.retain(|(acc, _)| *acc != idx);
        } else {
            set.clear();
        }
    }

    async fn fetch_single_subchunk(
        &self,
        client: &Client,
        dc_id: i32,
        doc_id: i64,
        access_hash: i64,
        file_reference: &[u8],
        offset: i64,
        limit: i32,
        priority: StreamPriority,
        account_idx: usize,
    ) -> Result<Bytes, String> {
        let _account_permit = self.get_account_semaphore(account_idx).await.acquire_owned().await.map_err(|e| e.to_string())?;

        let _permit = if priority == StreamPriority::Bulk {
            Some(self.bulk_semaphore.acquire().await.map_err(|e| e.to_string())?)
        } else {
            None
        };

        self.pacer.wait_for_slot(account_idx, limit as usize, priority == StreamPriority::High).await;

        let req = tl::functions::upload::GetFile {
            precise: true,
            cdn_supported: false,
            location: tl::enums::InputFileLocation::InputDocumentFileLocation(tl::types::InputDocumentFileLocation {
                id: doc_id,
                access_hash,
                file_reference: file_reference.to_vec(),
                thumb_size: String::new(),
            }),
            offset,
            limit,
        };

        if !self.authed_dcs.read().await.contains(&(account_idx, dc_id)) {
            let _guard = self.auth_lock.lock().await;
            if !self.authed_dcs.read().await.contains(&(account_idx, dc_id)) {
                if let Err(e) = self.copy_auth_to_dc(client, dc_id, account_idx).await {
                    return Err(e);
                }
                self.authed_dcs.write().await.insert((account_idx, dc_id));
            }
        }

        let invoke_fut = client.invoke_in_dc(dc_id, &req);
        let invoke_res = tokio::time::timeout(std::time::Duration::from_secs(20), invoke_fut)
            .await
            .map_err(|_| "MTProto GetFile timed out after 20s".to_string())?;

        let result = match invoke_res {
            Ok(file) => file,
            Err(e) => {
                let err_str = format!("{e:?}");
                if err_str.contains("AUTH_KEY_UNREGISTERED") {
                    let _guard = self.auth_lock.lock().await;
                    if let Err(e_copy) = self.copy_auth_to_dc(client, dc_id, account_idx).await {
                        return Err(e_copy);
                    }
                    self.authed_dcs.write().await.insert((account_idx, dc_id));
                    drop(_guard);
                    tokio::time::timeout(std::time::Duration::from_secs(20), client.invoke_in_dc(dc_id, &req))
                        .await
                        .map_err(|_| "MTProto GetFile retry timed out after 20s".to_string())?
                        .map_err(|e2| format!("MTProto GetFile retry error: {e2:?}"))?
                } else if err_str.contains("FLOOD_WAIT") || err_str.contains("FLOOD_PREMIUM_WAIT") {
                    let mut wait_secs = 2u64;
                    if let Some(pos) = err_str.find("Some(") {
                        let num_str = err_str[pos + 5..].chars().take_while(|c| c.is_ascii_digit()).collect::<String>();
                        if let Ok(secs) = num_str.parse::<u64>() {
                            wait_secs = secs;
                        }
                    }
                    self.pacer.report_flood_wait(account_idx, wait_secs).await;
                    return Err(format!("FLOOD_WAIT_{wait_secs}: {err_str}"));
                } else {
                    return Err(format!("MTProto GetFile error: {e:?}"));
                }
            }
        };

        match result {
            tl::enums::upload::File::File(file) => {
                Ok(Bytes::from(file.bytes))
            }
            tl::enums::upload::File::CdnRedirect(_) => {
                Err("CDN redirects not supported for this media".to_string())
            }
        }
    }

    pub async fn fetch_chunk(
        &self,
        client: &Client,
        dc_id: i32,
        doc_id: i64,
        access_hash: i64,
        file_reference: Vec<u8>,
        offset: i64,
        limit: i32,
        priority: StreamPriority,
        account_idx: usize,
    ) -> Result<Bytes, String> {
        let key = ChunkKey {
            dc_id,
            doc_id,
            offset,
            limit,
        };

        if let Some(cached) = self.cache.get(&key).await {
            return Ok(cached);
        }

        let max_sub_chunk = 512 * 1024;
        let result = if limit <= max_sub_chunk {
            self.fetch_single_subchunk(
                client,
                dc_id,
                doc_id,
                access_hash,
                &file_reference,
                offset,
                limit,
                priority,
                account_idx,
            ).await?
        } else {
            let mut sub_tasks = Vec::new();
            let mut cur_offset = offset;
            let mut remaining = limit;
            while remaining > 0 {
                let cur_limit = remaining.min(max_sub_chunk);
                sub_tasks.push(self.fetch_single_subchunk(
                    client,
                    dc_id,
                    doc_id,
                    access_hash,
                    &file_reference,
                    cur_offset,
                    cur_limit,
                    priority,
                    account_idx,
                ));
                cur_offset += cur_limit as i64;
                remaining -= cur_limit;
            }
            let sub_results = futures::future::try_join_all(sub_tasks).await?;
            let mut combined = bytes::BytesMut::with_capacity(limit as usize);
            for sub in sub_results {
                combined.extend_from_slice(&sub);
            }
            combined.freeze()
        };

        self.pacer.report_success(result.len());
        self.cache.insert(key, result.clone()).await;
        Ok(result)
    }

    pub async fn prewarm_header_and_tail(
        &self,
        client: &Client,
        dc_id: i32,
        doc_id: i64,
        access_hash: i64,
        file_reference: Vec<u8>,
        file_size: i64,
        account_idx: usize,
    ) {
        let chunk_size = 512 * 1024;
        let c1 = client.clone();
        let ref1 = file_reference.clone();
        let s = self.clone_ref();

        tokio::spawn(async move {
            let _ = s.fetch_chunk(&c1, dc_id, doc_id, access_hash, ref1, 0, chunk_size, StreamPriority::High, account_idx).await;
        });

        if file_size > chunk_size as i64 {
            let tail_offset = (file_size - chunk_size as i64).max(0);
            let c2 = client.clone();
            let ref2 = file_reference;
            let s2 = self.clone_ref();

            tokio::spawn(async move {
                let _ = s2.fetch_chunk(&c2, dc_id, doc_id, access_hash, ref2, tail_offset, chunk_size, StreamPriority::High, account_idx).await;
            });
        }
    }

    fn clone_ref(&self) -> Self {
        Self {
            cache: Arc::clone(&self.cache),
            bulk_semaphore: Arc::clone(&self.bulk_semaphore),
            account_semaphores: Arc::clone(&self.account_semaphores),
            pacer: Arc::clone(&self.pacer),
            authed_dcs: Arc::clone(&self.authed_dcs),
            auth_lock: Arc::clone(&self.auth_lock),
        }
    }
}
