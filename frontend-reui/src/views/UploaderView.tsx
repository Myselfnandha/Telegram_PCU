import React, { useEffect, useRef, useState } from 'react';
import { useApp } from '@/context/AppContext';
import type { UploadTask } from '@/types';
import { api } from '@/services/api';
import { formatBytes, formatETA, formatSpeed, getFileCategory } from '@/lib/formatters';
import { ChatPickerModal } from '@/components/ChatPickerModal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  AlertTriangle,
  BellOff,
  CheckCircle2,
  FileArchive,
  FileCode,
  FileSpreadsheet,
  FileText,
  FileVideo,
  FolderUp,
  Gauge,
  Image as ImageIcon,
  MessageSquare,
  Music,
  Pause,
  Play,
  RotateCcw,
  Sparkles,
  Trash2,
  Upload,
  UploadCloud,
  X,
} from 'lucide-react';

export const UploaderView: React.FC = () => {
  const { selectedChat, setSelectedChat, uploadQueue, setUploadQueue, addToast } = useApp();
  const [isChatPickerOpen, setChatPickerOpen] = useState(false);
  const [silentSend, setSilentSend] = useState(false);
  const [caption, setCaption] = useState('');
  const [speedLimit, setSpeedLimit] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [sendAsDefault, setSendAsDefault] = useState<'auto' | 'document' | 'media'>('auto');

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const activeXhrsRef = useRef<Map<string, XMLHttpRequest>>(new Map());

  // Listen for clipboard file paste (Ctrl+V)
  useEffect(() => {
    const handlePaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const files: File[] = [];
      for (let i = 0; i < items.length; i++) {
        if (items[i].kind === 'file') {
          const file = items[i].getAsFile();
          if (file) files.push(file);
        }
      }
      if (files.length > 0) {
        handleFilesAdded(files);
        addToast({
          type: 'info',
          title: 'Pasted from Clipboard',
          message: `Queued ${files.length} file(s) from clipboard.`,
        });
      }
    };
    window.addEventListener('paste', handlePaste);
    return () => window.removeEventListener('paste', handlePaste);
  }, [selectedChat, caption, silentSend, sendAsDefault]);

  const handleFilesAdded = (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;

    const newTasks: UploadTask[] = fileArray.map((file) => {
      const id = 'task_' + Math.random().toString(36).substring(2, 9) + '_' + Date.now();
      const needsSplit = file.size > 2 * 1024 * 1024 * 1024;
      const cat = getFileCategory(file.name);
      let previewUrl: string | null = null;

      if (cat === 'photo' || cat === 'video') {
        try {
          previewUrl = URL.createObjectURL(file);
        } catch {}
      }

      return {
        id,
        file,
        filename: file.name,
        customFilename: file.name,
        fileSize: file.size,
        chatId: selectedChat.id,
        chatName: selectedChat.name,
        caption: caption,
        sendAs: sendAsDefault,
        category: cat,
        previewUrl,
        status: 'queued',
        progress: 0,
        uploadedBytes: 0,
        speed: 0,
        eta: 0,
        currentPart: 1,
        totalParts: needsSplit ? Math.ceil(file.size / (1.95 * 1024 * 1024 * 1024)) : 1,
        transferredToServer: false,
        isTransferring: false,
      };
    });

    setUploadQueue((prev) => [...prev, ...newTasks]);
    addToast({
      type: 'info',
      title: 'Files Queued',
      message: `Added ${newTasks.length} file(s) to the upload queue.`,
    });

    setTimeout(() => processNextUpload([...uploadQueue, ...newTasks]), 50);
  };

  const processNextUpload = async (queue: UploadTask[]) => {
    const nextTask = queue.find(
      (t) => (t.status === 'queued' || t.status === 'streaming') && !t.transferredToServer && !t.isTransferring
    );

    if (!nextTask || !nextTask.file) return;

    nextTask.isTransferring = true;
    nextTask.status = 'streaming';
    setUploadQueue([...queue]);

    const file = nextTask.file;
    const totalSize = file.size;
    const CHUNK_SIZE = 25 * 1024 * 1024;

    if (totalSize > CHUNK_SIZE) {
      await uploadInChunks(nextTask, CHUNK_SIZE);
    } else {
      await uploadDirect(nextTask);
    }
  };

  const uploadDirect = async (task: UploadTask) => {
    const formData = new FormData();
    if (task.file) {
      formData.append('file', task.file);
    }
    formData.append('chat_id', String(task.chatId));
    if (task.caption) formData.append('caption', task.caption);
    formData.append('silent', silentSend ? 'true' : 'false');
    if (task.sendAs && task.sendAs !== 'auto') formData.append('send_as', task.sendAs);

    try {
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        activeXhrsRef.current.set(task.id, xhr);

        let lastTime = performance.now();
        let lastLoaded = 0;

        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            const now = performance.now();
            const elapsed = (now - lastTime) / 1000;
            const pct = Math.min(99.0, (e.loaded / e.total) * 100);

            task.progress = Math.round(pct * 10) / 10;
            task.uploadedBytes = e.loaded;

            if (elapsed >= 0.25) {
              const delta = e.loaded - lastLoaded;
              task.speed = delta / elapsed;
              task.eta = task.speed > 0 ? (e.total - e.loaded) / task.speed : 0;
              lastTime = now;
              lastLoaded = e.loaded;
            }
            setUploadQueue((prev) => [...prev]);
          }
        };

        xhr.onload = () => {
          activeXhrsRef.current.delete(task.id);
          if (xhr.status >= 200 && xhr.status < 300) {
            task.transferredToServer = true;
            task.status = 'uploading';
            task.progress = 100;
            setUploadQueue((prev) => [...prev]);
            resolve();
          } else {
            reject(new Error(`Server error ${xhr.status}`));
          }
        };

        xhr.onerror = () => {
          activeXhrsRef.current.delete(task.id);
          reject(new Error('Network error during upload'));
        };

        xhr.open('POST', '/api/upload', true);
        xhr.send(formData);
      });
    } catch (err: any) {
      task.status = 'failed';
      task.error = err.message || 'Upload failed';
      setUploadQueue((prev) => [...prev]);
    }
  };

  const uploadInChunks = async (task: UploadTask, chunkSize: number) => {
    const file = task.file;
    if (!file) return;

    const totalSize = file.size;
    const totalChunks = Math.ceil(totalSize / chunkSize);
    task.totalParts = totalChunks;
    let lastTime = performance.now();
    let lastLoaded = 0;

    // Check server for existing chunks to resume chunk-based upload seamlessly
    let startChunk = task.uploadedChunkIndex || 0;
    try {
      const serverStatus = await api.getChunkUploadStatus(task.id, task.filename, chunkSize);
      if (serverStatus && serverStatus.exists && serverStatus.next_chunk_index > startChunk) {
        startChunk = serverStatus.next_chunk_index;
        task.uploadedChunkIndex = startChunk;
        task.uploadedBytes = serverStatus.received_bytes;
        task.progress = Math.min(99.0, Math.round(((startChunk * chunkSize) / totalSize) * 1000) / 10);
        setUploadQueue((prev) => [...prev]);
      }
    } catch (statusErr) {
      console.debug('Could not query chunk status from server:', statusErr);
    }

    for (let chunkIdx = startChunk; chunkIdx < totalChunks; chunkIdx++) {
      if (task.status === 'cancelled' || task.status === 'paused') {
        task.isTransferring = false;
        return;
      }

      const start = chunkIdx * chunkSize;
      const end = Math.min(totalSize, start + chunkSize);
      const chunkBlob = file.slice(start, end);

      const formData = new FormData();
      formData.append('file', chunkBlob, task.filename);
      formData.append('upload_id', task.id);
      formData.append('chunk_index', String(chunkIdx));
      formData.append('total_chunks', String(totalChunks));
      formData.append('offset', String(start));
      formData.append('total_size', String(totalSize));
      formData.append('filename', task.filename);

      try {
        await new Promise<void>((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          activeXhrsRef.current.set(task.id, xhr);

          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
              const loaded = start + e.loaded;
              const now = performance.now();
              const elapsed = (now - lastTime) / 1000;
              const pct = Math.min(99.0, (loaded / totalSize) * 100);

              task.progress = Math.round(pct * 10) / 10;
              task.uploadedBytes = loaded;

              if (elapsed >= 0.25) {
                const delta = loaded - lastLoaded;
                task.speed = delta / elapsed;
                task.eta = task.speed > 0 ? (totalSize - loaded) / task.speed : 0;
                lastTime = now;
                lastLoaded = loaded;
              }
              setUploadQueue((prev) => [...prev]);
            }
          };

          xhr.onload = () => {
            activeXhrsRef.current.delete(task.id);
            if (xhr.status >= 200 && xhr.status < 300) {
              task.uploadedChunkIndex = chunkIdx + 1;
              resolve();
            } else {
              reject(new Error(`Chunk ${chunkIdx} failed with code ${xhr.status}`));
            }
          };

          xhr.onerror = () => {
            activeXhrsRef.current.delete(task.id);
            reject(new Error(`Network error on chunk ${chunkIdx}`));
          };

          xhr.open('POST', '/api/upload/chunk', true);
          xhr.send(formData);
        });
      } catch (err: any) {
        task.status = 'failed';
        task.error = err.message;
        setUploadQueue((prev) => [...prev]);
        return;
      }
    }

    try {
      await api.completeChunkUpload({
        upload_id: task.id,
        filename: task.customFilename || task.filename,
        total_size: totalSize,
        chat_id: task.chatId,
        caption: task.caption,
        silent: silentSend,
        send_as: task.sendAs,
        auto_split: true,
      });

      task.transferredToServer = true;
      task.status = 'uploading';
      task.progress = 100;
      setUploadQueue((prev) => [...prev]);
    } catch (err: any) {
      task.status = 'failed';
      task.error = err.message;
      setUploadQueue((prev) => [...prev]);
    }
  };

  const handlePauseTask = async (taskId: string) => {
    const xhr = activeXhrsRef.current.get(taskId);
    if (xhr) {
      xhr.abort();
      activeXhrsRef.current.delete(taskId);
    }
    try {
      await api.pauseTask(taskId);
    } catch {}

    setUploadQueue((prev) =>
      prev.map((t) => (t.id === taskId ? { ...t, status: 'paused', isTransferring: false } : t))
    );
    addToast({
      type: 'info',
      title: 'Upload Paused',
      message: 'Upload transfer paused. You can resume from the last completed chunk anytime.',
    });
  };

  const handleResumeTask = (task: UploadTask) => {
    task.status = 'queued';
    task.error = null;
    task.isTransferring = false;
    setUploadQueue([...uploadQueue]);
    addToast({
      type: 'info',
      title: 'Resuming Upload',
      message: `Resuming "${task.filename}" from chunk ${(task.uploadedChunkIndex || 0) + 1}...`,
    });
    processNextUpload(uploadQueue);
  };

  const handleCancelTask = async (taskId: string) => {
    const xhr = activeXhrsRef.current.get(taskId);
    if (xhr) {
      xhr.abort();
      activeXhrsRef.current.delete(taskId);
    }
    try {
      await api.cancelTask(taskId);
    } catch {}

    setUploadQueue((prev) =>
      prev.map((t) => (t.id === taskId ? { ...t, status: 'cancelled', isTransferring: false } : t))
    );
  };

  const handleRetryTask = (task: UploadTask) => {
    task.status = 'queued';
    task.error = null;
    task.transferredToServer = false;
    // Retain task.uploadedChunkIndex so chunk-based resume kicks in
    setUploadQueue([...uploadQueue]);
    processNextUpload(uploadQueue);
  };

  const handleClearCompleted = () => {
    setUploadQueue((prev) => prev.filter((t) => t.status !== 'completed' && t.status !== 'cancelled'));
  };

  const handleSpeedLimitChange = async (limit: number) => {
    setSpeedLimit(limit);
    try {
      await api.setSpeedLimit(limit);
      addToast({
        type: 'info',
        title: 'Speed Limit Set',
        message: limit === 0 ? 'Upload speed set to Unlimited.' : `Speed capped at ${limit} MB/s.`,
      });
    } catch (err: any) {
      addToast({ type: 'error', title: 'Error', message: err.message });
    }
  };

  const getCategoryIcon = (cat?: string) => {
    switch (cat) {
      case 'video':
        return <FileVideo className="w-4 h-4 text-sky-400" />;
      case 'audio':
        return <Music className="w-4 h-4 text-emerald-400" />;
      case 'photo':
        return <ImageIcon className="w-4 h-4 text-violet-400" />;
      case 'archive':
        return <FileArchive className="w-4 h-4 text-amber-400" />;
      default:
        return <FileText className="w-4 h-4 text-muted-foreground" />;
    }
  };

  return (
    <div className="w-full px-4 sm:px-6 lg:px-8 xl:px-10 py-6 space-y-6 animate-in fade-in duration-300">
      {/* Top Destination & Controls Bar */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-4 rounded-2xl border border-border/60 bg-card/60 backdrop-blur-md shadow-sm">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0 shadow-inner">
            <UploadCloud className="w-5 h-5" />
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Uploading Destination</div>
            <div className="text-sm font-semibold text-foreground flex items-center gap-2">
              <span>{selectedChat.name}</span>
              {selectedChat.username && (
                <span className="text-xs font-normal text-muted-foreground">(@{selectedChat.username})</span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setChatPickerOpen(true)}
            className="h-9 gap-1.5 cursor-pointer text-xs"
          >
            <MessageSquare className="w-4 h-4 text-primary" />
            <span>Change Target Chat</span>
          </Button>

          {/* Speed Limit Selector */}
          <div className="flex items-center gap-1.5 px-3 py-1 rounded-lg border border-border bg-background text-xs">
            <Gauge className="w-3.5 h-3.5 text-muted-foreground" />
            <span className="text-muted-foreground">Limit:</span>
            <select
              value={speedLimit}
              onChange={(e) => handleSpeedLimitChange(Number(e.target.value))}
              className="bg-transparent text-xs font-semibold focus:outline-none cursor-pointer"
            >
              <option value={0} className="bg-card text-foreground">Unlimited</option>
              <option value={2} className="bg-card text-foreground">2 MB/s</option>
              <option value={5} className="bg-card text-foreground">5 MB/s</option>
              <option value={10} className="bg-card text-foreground">10 MB/s</option>
              <option value={25} className="bg-card text-foreground">25 MB/s</option>
            </select>
          </div>
        </div>
      </div>

      {/* Main 2-Column Responsive Workspace */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: Dropzone & Metadata (5 Cols on lg, 4 on xl/2xl) */}
        <div className="lg:col-span-5 xl:col-span-4 2xl:col-span-4 space-y-4">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragging(false);
              if (e.dataTransfer.files) {
                handleFilesAdded(e.dataTransfer.files);
              }
            }}
            className={`relative p-8 rounded-2xl border-2 border-dashed transition-all duration-200 text-center flex flex-col items-center justify-center gap-3 cursor-pointer ${
              isDragging
                ? 'border-primary bg-primary/5 scale-[1.01]'
                : 'border-border/80 hover:border-primary/50 bg-card/40 hover:bg-card/70'
            }`}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              type="file"
              ref={fileInputRef}
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files) handleFilesAdded(e.target.files);
              }}
            />
            <input
              type="file"
              ref={folderInputRef}
              multiple
              // @ts-ignore
              webkitdirectory=""
              className="hidden"
              onChange={(e) => {
                if (e.target.files) handleFilesAdded(e.target.files);
              }}
            />

            <div className="w-14 h-14 rounded-2xl bg-primary/10 text-primary flex items-center justify-center shadow-inner">
              <Upload className="w-7 h-7" />
            </div>

            <div>
              <p className="text-sm font-semibold text-foreground">Drag & drop files or folder</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                or click to browse • Paste files with <kbd className="px-1 py-0.5 rounded bg-muted text-[10px] font-mono">Ctrl+V</kbd>
              </p>
            </div>

            <div className="flex items-center gap-2 mt-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  fileInputRef.current?.click();
                }}
                className="h-8 text-xs cursor-pointer"
              >
                Select Files
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  folderInputRef.current?.click();
                }}
                className="h-8 text-xs gap-1.5 cursor-pointer"
              >
                <FolderUp className="w-3.5 h-3.5" />
                Upload Folder
              </Button>
            </div>

            <div className="text-[11px] text-muted-foreground/80 mt-2 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              <span>Unlimited size • Auto-split 2GB parts • Direct MTProto speed</span>
            </div>
          </div>

          {/* Upload Configuration Card */}
          <div className="p-4 rounded-2xl border border-border/60 bg-card/60 backdrop-blur-md space-y-3.5">
            <div className="text-xs font-semibold text-foreground flex items-center gap-2">
              <BellOff className="w-4 h-4 text-muted-foreground" />
              <span>Transfer Options</span>
            </div>

            {/* Send As Selector */}
            <div className="space-y-1.5">
              <label className="text-[11px] text-muted-foreground">Send Format</label>
              <div className="grid grid-cols-3 gap-1.5">
                {(
                  [
                    { id: 'auto', label: 'Auto Detect' },
                    { id: 'document', label: 'As File' },
                    { id: 'media', label: 'Streaming' },
                  ] as const
                ).map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setSendAsDefault(opt.id)}
                    className={`py-1.5 rounded-lg text-xs font-medium border text-center transition-all cursor-pointer ${
                      sendAsDefault === opt.id
                        ? 'border-primary bg-primary/10 text-primary font-semibold'
                        : 'border-border bg-accent/20 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Default Caption Input */}
            <div className="space-y-1.5">
              <label className="text-[11px] text-muted-foreground">Optional Message / Caption</label>
              <Input
                placeholder="Caption sent with uploaded files..."
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
                className="h-8 text-xs"
              />
            </div>

            {/* Silent Send Switch */}
            <div className="flex items-center justify-between pt-1">
              <div className="space-y-0.5">
                <div className="text-xs font-medium text-foreground">Silent Notification</div>
                <div className="text-[11px] text-muted-foreground">Upload without alert sound to chat members</div>
              </div>
              <Switch checked={silentSend} onCheckedChange={setSilentSend} />
            </div>
          </div>
        </div>

        {/* Right Column: Active Upload Queue (7 Cols on lg, 8 on xl/2xl) */}
        <div className="lg:col-span-7 xl:col-span-8 2xl:col-span-8 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-foreground">Active Upload Queue</h2>
              <Badge variant="secondary" className="text-xs h-5 px-2">
                {uploadQueue.length}
              </Badge>
            </div>

            {uploadQueue.length > 0 && (
              <div className="flex items-center gap-1.5">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleClearCompleted}
                  className="h-8 text-xs text-muted-foreground hover:text-foreground cursor-pointer"
                >
                  <Trash2 className="w-3.5 h-3.5 mr-1" />
                  Clear Finished
                </Button>
              </div>
            )}
          </div>

          {uploadQueue.length === 0 ? (
            <div className="p-16 text-center rounded-2xl border border-dashed border-border/80 bg-card/20 flex flex-col items-center justify-center gap-2">
              <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground mb-1">
                <Upload className="w-6 h-6 opacity-40" />
              </div>
              <p className="text-xs font-semibold text-muted-foreground">Queue is ready</p>
              <p className="text-[11px] text-muted-foreground/60 max-w-xs">
                Drag and drop files, select a folder, or paste from your clipboard (<kbd className="font-mono">Ctrl+V</kbd>).
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {uploadQueue.map((task) => {
                const isDone = task.status === 'completed';
                const isFailed = task.status === 'failed';
                const isUploading = task.status === 'uploading' || task.status === 'streaming';

                return (
                  <div
                    key={task.id}
                    className="p-4 rounded-xl border border-border/70 bg-card/85 backdrop-blur-md shadow-sm space-y-3 transition-all hover:border-primary/40"
                  >
                    <div className="flex items-start justify-between gap-3">
                      {/* Thumbnail or File Type Icon */}
                      <div className="w-10 h-10 rounded-lg bg-accent/60 flex items-center justify-center shrink-0 overflow-hidden border border-border/40">
                        {task.previewUrl ? (
                          <img src={task.previewUrl} alt="" className="w-full h-full object-cover" />
                        ) : (
                          getCategoryIcon(task.category)
                        )}
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-semibold text-foreground truncate" title={task.filename}>
                          {task.customFilename || task.filename}
                        </div>
                        <div className="text-[11px] text-muted-foreground mt-0.5 flex items-center gap-2 flex-wrap">
                          <span className="font-mono font-medium text-foreground/80">{formatBytes(task.fileSize)}</span>
                          <span>•</span>
                          <span>To: {task.chatName}</span>
                          {task.totalParts > 1 && (
                            <Badge variant="outline" className="text-[10px] h-4 px-1.5 bg-sky-500/10 text-sky-400 border-sky-500/20">
                              Part {task.currentPart}/{task.totalParts} (2GB split)
                            </Badge>
                          )}
                        </div>
                      </div>

                      {/* Status Badges & Action Buttons */}
                      <div className="flex items-center gap-1.5 shrink-0">
                        {isDone && (
                          <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px] gap-1">
                            <CheckCircle2 className="w-3 h-3" />
                            Sent
                          </Badge>
                        )}
                        {isFailed && (
                          <div className="flex items-center gap-1">
                            <Badge variant="destructive" className="text-[10px]">
                              Failed
                            </Badge>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleRetryTask(task)}
                              className="w-7 h-7 text-muted-foreground hover:text-foreground cursor-pointer"
                              title="Resume / Retry from Last Chunk"
                            >
                              <RotateCcw className="w-3 h-3" />
                            </Button>
                          </div>
                        )}
                        {task.status === 'paused' && (
                          <div className="flex items-center gap-1">
                            <Badge variant="outline" className="bg-amber-500/10 text-amber-400 border-amber-500/20 text-[10px]">
                              Paused {task.uploadedChunkIndex ? `(chunk ${task.uploadedChunkIndex})` : ''}
                            </Badge>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleResumeTask(task)}
                              className="w-7 h-7 text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 cursor-pointer"
                              title="Resume Chunk Upload"
                            >
                              <Play className="w-3.5 h-3.5 fill-current" />
                            </Button>
                          </div>
                        )}
                        {isUploading && (
                          <div className="flex items-center gap-1">
                            <Badge variant="outline" className="bg-sky-500/10 text-sky-400 border-sky-500/20 text-[10px] animate-pulse">
                              {task.status === 'streaming' ? 'Buffering' : 'Uploading'}
                              {task.uploadedChunkIndex ? ` (${task.uploadedChunkIndex}/${task.totalParts || '?'})` : ''}
                            </Badge>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handlePauseTask(task.id)}
                              className="w-7 h-7 text-muted-foreground hover:text-amber-400 cursor-pointer"
                              title="Pause Upload"
                            >
                              <Pause className="w-3.5 h-3.5" />
                            </Button>
                          </div>
                        )}

                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleCancelTask(task.id)}
                          className="w-7 h-7 text-muted-foreground hover:text-destructive cursor-pointer"
                          title="Cancel"
                        >
                          <X className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>

                    {/* Multi-part Segmented Visual Bar (if file > 2GB) */}
                    {task.totalParts > 1 && (
                      <div className="space-y-1">
                        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                          <span>Split Progress</span>
                          <span>Part {task.currentPart} of {task.totalParts}</span>
                        </div>
                        <div className="grid grid-cols-4 gap-1">
                          {Array.from({ length: task.totalParts }).map((_, idx) => {
                            const partNum = idx + 1;
                            const isCurrent = partNum === task.currentPart;
                            const isDonePart = partNum < task.currentPart || isDone;

                            return (
                              <div
                                key={idx}
                                className={`h-1.5 rounded-full transition-all ${
                                  isDonePart
                                    ? 'bg-emerald-500'
                                    : isCurrent
                                    ? 'bg-sky-400 animate-pulse'
                                    : 'bg-muted/60'
                                }`}
                              />
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* Progress Bar & Real-time Stats */}
                    <div className="space-y-1.5">
                      <Progress value={task.progress} className="h-1.5 bg-muted/60" />
                      <div className="flex items-center justify-between text-[11px] text-muted-foreground font-mono">
                        <div>
                          {task.progress.toFixed(1)}% ({formatBytes(task.uploadedBytes)} of {formatBytes(task.fileSize)})
                        </div>
                        {isUploading && (
                          <div className="flex items-center gap-2">
                            <span>{formatSpeed(task.speed)}</span>
                            <span>•</span>
                            <span>ETA: {formatETA(task.eta)}</span>
                          </div>
                        )}
                      </div>
                    </div>

                    {task.error && (
                      <div className="text-[11px] text-destructive bg-destructive/10 p-2 rounded-lg flex items-center gap-1.5">
                        <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                        <span>{task.error}</span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <ChatPickerModal
        open={isChatPickerOpen}
        onOpenChange={setChatPickerOpen}
        onSelect={setSelectedChat}
        selectedChatId={selectedChat.id}
      />
    </div>
  );
};
