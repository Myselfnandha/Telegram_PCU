use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

#[allow(dead_code)]
pub const OFFICIAL_DEVICE_MODEL: &str = "Desktop PC";
#[allow(dead_code)]
pub const OFFICIAL_SYSTEM_VERSION: &str = "Linux 6.x";
#[allow(dead_code)]
pub const OFFICIAL_APP_VERSION: &str = "5.0.1";
#[allow(dead_code)]
pub const OFFICIAL_LANG_CODE: &str = "en";

pub struct TokenBucket {
    capacity_mb: f64,
    min_rate_mbs: f64,
    max_rate_mbs: f64,
    state: Mutex<(f64, Instant, f64)>,
}

impl TokenBucket {
    pub fn new(initial_rate_mbs: f64, min_rate_mbs: f64, max_rate_mbs: f64, capacity_mb: f64) -> Self {
        Self {
            capacity_mb,
            min_rate_mbs,
            max_rate_mbs,
            state: Mutex::new((capacity_mb, Instant::now(), initial_rate_mbs)),
        }
    }

    pub async fn acquire(&self, size_bytes: usize, is_unlimited: bool) {
        if is_unlimited {
            return;
        }

        let needed_mb = (size_bytes as f64) / (1024.0 * 1024.0);
        loop {
            let wait_duration = {
                let mut state = self.state.lock().unwrap();
                let now = Instant::now();
                let elapsed = now.duration_since(state.1).as_secs_f64();
                state.1 = now;
                state.0 = (state.0 + elapsed * state.2).min(self.capacity_mb);

                if state.0 >= needed_mb {
                    state.0 -= needed_mb;
                    return;
                }

                let deficit = needed_mb - state.0;
                let wait_secs = deficit / state.2.max(1.0);
                Duration::from_secs_f64(wait_secs.clamp(0.001, 0.2))
            };

            tokio::time::sleep(wait_duration).await;
        }
    }

    pub fn apply_aimd_increase(&self) {
        let mut state = self.state.lock().unwrap();
        state.2 = (state.2 + 0.5).min(self.max_rate_mbs);
    }

    pub fn apply_aimd_decrease(&self) {
        let mut state = self.state.lock().unwrap();
        state.2 = (state.2 * 0.7).max(self.min_rate_mbs);
    }

    pub fn get_current_rate_mbs(&self) -> f64 {
        let state = self.state.lock().unwrap();
        state.2
    }
}

pub struct AntiBanPacer {
    token_bucket: TokenBucket,
    account_cooldowns: Arc<RwLock<HashMap<usize, Instant>>>,
    success_bytes: AtomicU64,
}

impl AntiBanPacer {
    pub fn new(initial_rate_mbs: f64, max_rate_mbs: f64) -> Self {
        Self {
            token_bucket: TokenBucket::new(initial_rate_mbs, 4.0, max_rate_mbs, 32.0),
            account_cooldowns: Arc::new(RwLock::new(HashMap::new())),
            success_bytes: AtomicU64::new(0),
        }
    }

    pub async fn wait_for_slot(&self, account_idx: usize, chunk_size: usize, is_unlimited: bool) {
        let cooldown = {
            let map = self.account_cooldowns.read().await;
            map.get(&account_idx).copied()
        };

        if let Some(until) = cooldown {
            let now = Instant::now();
            if now < until {
                let rem = until - now;
                tokio::time::sleep(rem).await;
            }
        }

        self.token_bucket.acquire(chunk_size, is_unlimited).await;
    }

    pub fn report_success(&self, bytes: usize) {
        let prev = self.success_bytes.fetch_add(bytes as u64, Ordering::Relaxed);
        if prev + (bytes as u64) >= 4 * 1024 * 1024 {
            self.success_bytes.store(0, Ordering::Relaxed);
            self.token_bucket.apply_aimd_increase();
        }
    }

    pub async fn report_flood_wait(&self, account_idx: usize, seconds: u64) {
        let until = Instant::now() + Duration::from_secs(seconds + 1);
        let mut map = self.account_cooldowns.write().await;
        map.insert(account_idx, until);
        self.success_bytes.store(0, Ordering::Relaxed);
        self.token_bucket.apply_aimd_decrease();
    }

    pub async fn is_account_cooling(&self, account_idx: usize) -> bool {
        let map = self.account_cooldowns.read().await;
        if let Some(&until) = map.get(&account_idx) {
            Instant::now() < until
        } else {
            false
        }
    }

    pub fn get_throughput_rate(&self) -> f64 {
        self.token_bucket.get_current_rate_mbs()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_token_bucket_initialization_and_rate() {
        let tb = TokenBucket::new(12.0, 4.0, 32.0, 4.0);
        assert!((tb.get_current_rate_mbs() - 12.0).abs() < 0.1);
        tb.apply_aimd_increase();
        assert!((tb.get_current_rate_mbs() - 12.5).abs() < 0.1);
        tb.apply_aimd_decrease();
        assert!(tb.get_current_rate_mbs() < 12.5);
    }

    #[tokio::test]
    async fn test_antiban_pacer_flood_cooldown() {
        let pacer = AntiBanPacer::new(10.0, 20.0);
        assert!(!pacer.is_account_cooling(0).await);
        pacer.report_flood_wait(0, 1).await;
        assert!(pacer.is_account_cooling(0).await);
        tokio::time::sleep(Duration::from_millis(2100)).await;
        assert!(!pacer.is_account_cooling(0).await);
    }

    #[tokio::test]
    async fn test_antiban_pacer_aimd_increase() {
        let pacer = AntiBanPacer::new(10.0, 20.0);
        let initial = pacer.get_throughput_rate();
        pacer.report_success(5 * 1024 * 1024);
        assert!(pacer.get_throughput_rate() > initial);
    }
}
