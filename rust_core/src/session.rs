use std::net::{Ipv4Addr, SocketAddrV4, SocketAddrV6};
use std::sync::Arc;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::engine::general_purpose::URL_SAFE;
use base64::Engine;
use grammers_client::Client;
use grammers_mtsender::SenderPool;
use grammers_session::storages::SqliteSession;
use grammers_session::types::DcOption;
use grammers_session::Session;

#[derive(Clone, Debug)]
pub struct TelethonSessionInfo {
    pub dc_id: i32,
    pub ipv4: SocketAddrV4,
    pub auth_key: [u8; 256],
}

pub fn decode_telethon_string_session(raw: &str) -> Result<TelethonSessionInfo, String> {
    let clean = raw.trim();
    if clean.is_empty() {
        return Err("Empty session string".to_string());
    }
    if !clean.starts_with('1') {
        return Err("Unsupported session version; expected 1".to_string());
    }
    let encoded = &clean[1..];
    let bytes = URL_SAFE_NO_PAD
        .decode(encoded)
        .or_else(|_| URL_SAFE.decode(encoded))
        .map_err(|e| format!("Base64 decode error: {e}"))?;

    if bytes.len() == 263 {
        let dc_id = bytes[0] as i32;
        let ip = Ipv4Addr::new(bytes[1], bytes[2], bytes[3], bytes[4]);
        let port = u16::from_be_bytes([bytes[5], bytes[6]]);
        let mut auth_key = [0u8; 256];
        auth_key.copy_from_slice(&bytes[7..263]);

        Ok(TelethonSessionInfo {
            dc_id,
            ipv4: SocketAddrV4::new(ip, port),
            auth_key,
        })
    } else {
        Err(format!("Unexpected session byte length: {}", bytes.len()))
    }
}

pub async fn create_client_from_string(session_str: &str, api_id: i32, account_idx: usize) -> Result<Client, String> {
    let info = decode_telethon_string_session(session_str)?;
    let db_dir = std::path::Path::new("/home/nandha/Desktop/TG_TOOL/backend/data");
    if !db_dir.exists() {
        let _ = std::fs::create_dir_all(db_dir);
    }
    let db_path = db_dir.join(format!("rust_session_{account_idx}.db"));
    let session = Arc::new(SqliteSession::open(&db_path).await.map_err(|e| format!("SqliteSession error: {e:?}"))?);

    let _ = session.set_home_dc_id(info.dc_id).await;
    let default_dcs = [
        (1, Ipv4Addr::new(149, 154, 175, 53), 443),
        (2, Ipv4Addr::new(149, 154, 167, 51), 443),
        (3, Ipv4Addr::new(149, 154, 175, 100), 443),
        (4, Ipv4Addr::new(149, 154, 167, 91), 443),
        (5, Ipv4Addr::new(91, 108, 56, 130), 443),
    ];
    for (id, ip, port) in default_dcs {
        let existing_auth = session.dc_option(id).ok().flatten().and_then(|opt| opt.auth_key);
        let auth_key = if id == info.dc_id {
            Some(info.auth_key)
        } else {
            existing_auth
        };
        let ipv4 = if id == info.dc_id {
            info.ipv4
        } else {
            SocketAddrV4::new(ip, port)
        };
        let dc_opt = DcOption {
            id,
            ipv4,
            ipv6: SocketAddrV6::new(std::net::Ipv6Addr::UNSPECIFIED, port, 0, 0),
            auth_key,
        };
        let _ = session.set_dc_option(&dc_opt).await;
    }

    let pool = SenderPool::new(session, api_id);
    let handle = pool.handle;
    tokio::spawn(pool.runner.run());
    Ok(Client::new(handle))
}

#[derive(Default, Clone)]
pub struct MultiAccountSessionPool {
    clients: Arc<tokio::sync::RwLock<Vec<Client>>>,
}

impl MultiAccountSessionPool {
    pub fn new() -> Self {
        Self {
            clients: Arc::new(tokio::sync::RwLock::new(Vec::new())),
        }
    }

    pub async fn add_client(&self, client: Client) -> usize {
        let mut list = self.clients.write().await;
        list.push(client);
        list.len()
    }

    pub async fn set_client(&self, index: usize, client: Client) -> usize {
        let mut list = self.clients.write().await;
        if index < list.len() {
            list[index] = client;
        } else {
            while list.len() < index {
                list.push(client.clone());
            }
            list.push(client);
        }
        list.len()
    }

    pub async fn get_client(&self, index: usize) -> Option<Client> {
        let list = self.clients.read().await;
        list.get(index).cloned()
    }

    pub async fn count(&self) -> usize {
        let list = self.clients.read().await;
        list.len()
    }

    pub async fn get_round_robin(&self, counter: usize) -> Option<Client> {
        let list = self.clients.read().await;
        if list.is_empty() {
            None
        } else {
            Some(list[counter % list.len()].clone())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_telethon_string_session_decode() {
        let fake_ip = [149, 154, 167, 51];
        let fake_port = 443u16;
        let fake_key = [7u8; 256];
        let mut raw_bytes = Vec::with_capacity(263);
        raw_bytes.push(5u8);
        raw_bytes.extend_from_slice(&fake_ip);
        raw_bytes.extend_from_slice(&fake_port.to_be_bytes());
        raw_bytes.extend_from_slice(&fake_key);

        let encoded = format!("1{}", URL_SAFE_NO_PAD.encode(&raw_bytes));
        let info = decode_telethon_string_session(&encoded).expect("Should decode successfully");
        assert_eq!(info.dc_id, 5);
        assert_eq!(info.ipv4.port(), 443);
        assert_eq!(info.auth_key[0], 7);
        assert_eq!(info.auth_key[255], 7);
    }
}
