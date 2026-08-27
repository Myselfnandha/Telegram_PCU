from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime

class SendMode(str, Enum):
    AUTO = "auto"
    DOCUMENT = "document"
    MEDIA = "media"

class UploadStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    SPLITTING = "splitting"
    UPLOADING = "uploading"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

from typing import Optional, List, Union

class ChatItem(BaseModel):
    id: Union[int, str]
    name: str
    username: Optional[str] = None
    type: str  # "user", "group", "supergroup", "channel", "saved_messages"
    unread_count: int = 0
    pinned: bool = False

class UploadHistoryItem(BaseModel):
    id: str
    filename: str
    file_size: int
    mime_type: Optional[str] = None
    chat_id: Union[int, str]
    chat_name: Optional[str] = None
    message_id: Optional[int] = None
    telegram_file_id: Optional[str] = None
    caption: Optional[str] = None
    send_as: str = "auto"
    status: str
    error_message: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    parts_count: int = 1

class UploadTaskState(BaseModel):
    id: str
    filename: str
    file_size: int
    status: UploadStatus
    progress: float = 0.0
    uploaded_bytes: int = 0
    speed: float = 0.0  # bytes / sec
    eta: float = 0.0  # seconds
    current_part: int = 1
    total_parts: int = 1
    error: Optional[str] = None

class AuthStatus(BaseModel):
    authenticated: bool
    phone: Optional[str] = None
    username: Optional[str] = None
    user_id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    error: Optional[str] = None
