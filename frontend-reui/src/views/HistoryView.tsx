import React, { useEffect, useMemo, useState } from 'react';
import type { UploadHistoryItem } from '@/types';
import { api } from '@/services/api';
import { cleanFileName, formatBytes } from '@/lib/formatters';
import { useApp } from '@/context/AppContext';
import {
  createColumnHelper,
  useTable,
} from '@tanstack/react-table';
import {
  DataGrid,
  dataGridFeatures,
  type DataGridFeatures,
} from '@/components/reui/data-grid/data-grid';
import { DataGridTable } from '@/components/reui/data-grid/data-grid-table';
import { DataGridPagination } from '@/components/reui/data-grid/data-grid-pagination';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  AlertTriangle,
  ArrowDownUp,
  Clock,
  Copy,
  Download,
  FileSpreadsheet,
  FileText,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
} from 'lucide-react';

const columnHelper = createColumnHelper<DataGridFeatures, UploadHistoryItem>();

export const HistoryView: React.FC = () => {
  const { addToast, setUploadQueue } = useApp();
  const [history, setHistory] = useState<UploadHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [globalFilter, setGlobalFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<'date_desc' | 'date_asc' | 'size_desc' | 'name'>('date_desc');

  const loadHistory = async () => {
    setLoading(true);
    try {
      const data = await api.getHistory();
      setHistory(data || []);
    } catch (e: any) {
      console.debug('Failed to load history:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadHistory();
  }, []);

  const handleClearHistory = async () => {
    if (!window.confirm('Are you sure you want to permanently clear transfer history?')) {
      return;
    }
    try {
      const res = await api.clearHistory();
      setHistory([]);
      addToast({
        type: 'info',
        title: 'History Cleared',
        message: `Removed ${res.deleted || 0} transfer logs.`,
      });
    } catch (err: any) {
      addToast({ type: 'error', title: 'Error', message: err.message });
    }
  };

  const handleExportCsv = () => {
    if (history.length === 0) return;
    const headers = ['ID', 'Filename', 'File Size (Bytes)', 'Destination', 'Status', 'Date', 'Error'];
    const rows = history.map((item) => [
      `"${item.id}"`,
      `"${item.filename.replace(/"/g, '""')}"`,
      item.file_size,
      `"${(item.chat_name || item.chat_id).replace(/"/g, '""')}"`,
      item.status,
      `"${item.created_at}"`,
      `"${(item.error_message || '').replace(/"/g, '""')}"`,
    ]);

    const csvContent = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `telegram_transfer_history_${Date.now()}.csv`);
    document.body.appendChild(link);
    link.click();
    link.remove();

    addToast({
      type: 'success',
      title: 'CSV Exported',
      message: `Exported ${history.length} records to CSV.`,
    });
  };

  const filteredData = useMemo(() => {
    let result = [...history];

    if (statusFilter !== 'all') {
      result = result.filter((item) => item.status.toLowerCase() === statusFilter);
    }

    if (globalFilter.trim()) {
      const q = globalFilter.toLowerCase();
      result = result.filter(
        (item) =>
          item.filename.toLowerCase().includes(q) ||
          (item.chat_name && item.chat_name.toLowerCase().includes(q))
      );
    }

    result.sort((a, b) => {
      if (sortBy === 'date_desc') return b.created_at.localeCompare(a.created_at);
      if (sortBy === 'date_asc') return a.created_at.localeCompare(b.created_at);
      if (sortBy === 'size_desc') return b.file_size - a.file_size;
      if (sortBy === 'name') return a.filename.localeCompare(b.filename);
      return 0;
    });

    return result;
  }, [history, statusFilter, globalFilter, sortBy]);

  const columns = useMemo(
    () =>
      columnHelper.columns([
        columnHelper.accessor('filename', {
          header: 'File Name',
          cell: (info: any) => {
            const item = info.row.original as UploadHistoryItem;
            const displayName = cleanFileName(item.filename);
            return (
              <div className="flex items-center gap-2.5 max-w-sm min-w-[200px]">
                <div className="w-8 h-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0">
                  <FileText className="w-4 h-4" />
                </div>
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-foreground truncate" title={item.filename}>
                    {displayName}
                  </div>
                  {item.error_message && (
                    <div className="text-[10px] text-destructive truncate flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3 shrink-0" />
                      <span>{item.error_message}</span>
                    </div>
                  )}
                </div>
              </div>
            );
          },
        }),
        columnHelper.accessor('file_size', {
          header: 'Size',
          cell: (info: any) => (
            <span className="font-mono text-xs text-foreground/90">{formatBytes(info.getValue())}</span>
          ),
        }),
        columnHelper.accessor('chat_name', {
          header: 'Destination',
          cell: (info: any) => {
            const item = info.row.original as UploadHistoryItem;
            return (
              <div className="text-xs text-muted-foreground truncate max-w-[160px]" title={item.chat_name || item.chat_id}>
                {item.chat_name || item.chat_id}
              </div>
            );
          },
        }),
        columnHelper.accessor('status', {
          header: 'Status',
          cell: (info: any) => {
            const status = String(info.getValue() || '').toLowerCase();
            if (status === 'completed') {
              return (
                <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px]">
                  Completed ✓
                </Badge>
              );
            }
            if (status === 'failed') {
              return (
                <Badge variant="destructive" className="text-[10px]">
                  Failed ✕
                </Badge>
              );
            }
            return (
              <Badge variant="secondary" className="text-[10px] uppercase">
                {status}
              </Badge>
            );
          },
        }),
        columnHelper.accessor('created_at', {
          header: 'Date & Time',
          cell: (info: any) => (
            <span className="font-mono text-[11px] text-muted-foreground whitespace-nowrap">
              {String(info.getValue() || '')}
            </span>
          ),
        }),
        columnHelper.display({
          id: 'actions',
          header: 'Actions',
          cell: (info: any) => {
            const item = info.row.original as UploadHistoryItem;
            return (
              <div className="flex items-center gap-1">
                {item.message_id && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="w-7 h-7 text-muted-foreground hover:text-foreground cursor-pointer"
                    onClick={() => {
                      const url = `${window.location.origin}/dl/${encodeURIComponent(item.chat_id)}/${item.message_id}/${encodeURIComponent(item.filename)}`;
                      navigator.clipboard.writeText(url);
                      addToast({
                        type: 'success',
                        title: 'Link Copied',
                        message: 'Direct transfer link copied to clipboard.',
                      });
                    }}
                    title="Copy Direct Link"
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </Button>
                )}
              </div>
            );
          },
        }),
      ]),
    [addToast]
  );

  const table = useTable({
    features: dataGridFeatures,
    data: filteredData,
    columns,
  });

  return (
    <div className="w-full px-4 sm:px-6 lg:px-8 xl:px-10 py-6 space-y-6 animate-in fade-in duration-300">
      {/* Top Bar */}
      <div className="p-5 rounded-2xl border border-border/60 bg-card/60 backdrop-blur-md shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0 shadow-inner">
              <Clock className="w-5 h-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-foreground flex items-center gap-2">
                <span>Transfer & Upload History</span>
                <Badge variant="secondary" className="text-[10px]">
                  {history.length} Records
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground mt-0.5">
                Powered by @reui/data-grid • High-performance SQLite WAL logs
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <Button
              variant="outline"
              size="sm"
              onClick={handleExportCsv}
              disabled={history.length === 0}
              className="h-9 text-xs gap-1.5 cursor-pointer"
            >
              <FileSpreadsheet className="w-3.5 h-3.5 text-emerald-400" />
              <span>Export CSV</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={loadHistory}
              disabled={loading}
              className="h-9 text-xs cursor-pointer"
            >
              <RefreshCw className={`w-3.5 h-3.5 mr-1 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
            {history.length > 0 && (
              <Button
                variant="destructive"
                size="sm"
                onClick={handleClearHistory}
                className="h-9 text-xs gap-1.5 cursor-pointer"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Clear History</span>
              </Button>
            )}
          </div>
        </div>

        {/* Filter, Sort, and Search Bar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-2 border-t border-border/40">
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
            {(
              [
                { id: 'all', label: 'All Transfers' },
                { id: 'completed', label: 'Completed' },
                { id: 'failed', label: 'Failed' },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                onClick={() => setStatusFilter(tab.id)}
                className={`text-xs px-3 py-1.5 rounded-lg transition-colors cursor-pointer ${
                  statusFilter === tab.id
                    ? 'bg-primary text-primary-foreground font-semibold shadow-sm'
                    : 'bg-muted/40 text-muted-foreground hover:text-foreground hover:bg-muted'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            {/* Sort Dropdown */}
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-border bg-background text-xs">
              <ArrowDownUp className="w-3.5 h-3.5 text-muted-foreground" />
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as any)}
                className="bg-transparent text-xs font-medium focus:outline-none cursor-pointer"
              >
                <option value="date_desc" className="bg-card text-foreground">Newest First</option>
                <option value="date_asc" className="bg-card text-foreground">Oldest First</option>
                <option value="size_desc" className="bg-card text-foreground">Largest Size</option>
                <option value="name" className="bg-card text-foreground">Filename (A-Z)</option>
              </select>
            </div>

            <div className="relative w-full sm:w-60">
              <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search history..."
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
                className="pl-8 h-8 text-xs"
              />
            </div>
          </div>
        </div>
      </div>

      {/* ReUI DataGrid Container */}
      <div className="rounded-2xl border border-border/70 bg-card/85 backdrop-blur-md overflow-hidden shadow-sm">
        {loading && history.length === 0 ? (
          <div className="p-8 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full rounded-lg" />
            ))}
          </div>
        ) : filteredData.length === 0 ? (
          <div className="p-16 text-center text-xs text-muted-foreground">
            No transfer records found.
          </div>
        ) : (
          <DataGrid table={table as any} recordCount={filteredData.length}>
            <div className="overflow-x-auto">
              <DataGridTable />
            </div>
            <div className="p-3 border-t border-border/40">
              <DataGridPagination />
            </div>
          </DataGrid>
        )}
      </div>
    </div>
  );
};
