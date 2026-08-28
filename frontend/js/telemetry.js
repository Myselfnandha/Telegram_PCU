/**
 * Real-Time Telemetry & Throughput Controller
 * Manages live bandwidth monitoring, RAM/CPU metrics, and canvas sparkline rendering.
 */

import { formatBytes } from "./utils.js";

export class TelemetryController {
  constructor() {
    this.canvas = null;
    this.ctx = null;
    this.telemUploadSpeed = null;
    this.telemProxyStreams = null;
    this.telemRam = null;
    this.telemCpu = null;
    this.telemUptime = null;

    this.historyData = new Array(30).fill(0);
    this.maxSpeedSeen = 1024 * 1024; // 1 MB/s baseline
    this.isRendering = false;
  }

  init(socket) {
    this.canvas = document.getElementById("telemetryCanvas");
    if (this.canvas) {
      this.ctx = this.canvas.getContext("2d");
    }

    this.telemUploadSpeed = document.getElementById("telemUploadSpeed");
    this.telemProxyStreams = document.getElementById("telemProxyStreams");
    this.telemRam = document.getElementById("telemRam");
    this.telemCpu = document.getElementById("telemCpu");
    this.telemUptime = document.getElementById("telemUptime");

    if (socket) {
      socket.on("telemetry:stats", (stats) => {
        this.updateStats(stats);
      });
    }

    // Initial fetch and fallback polling
    this.fetchStats();
    setInterval(() => this.fetchStats(), 4000);

    // Start drawing loop
    this.drawSparkline();
  }

  async fetchStats() {
    try {
      const res = await fetch("/api/system/stats");
      if (res.ok) {
        const stats = await res.json();
        this.updateStats(stats);
      }
    } catch (err) {
      // Background poll silently ignored if server restarting
    }
  }

  updateStats(stats) {
    if (!stats) return;

    // 1. Upload Rate
    const upSpeedBps = stats.upload_speed_bps || 0;
    if (this.telemUploadSpeed) {
      this.telemUploadSpeed.textContent = upSpeedBps > 0 ? `${formatBytes(upSpeedBps)}/s` : "0.0 KB/s";
    }

    // 2. Proxy Streams
    if (this.telemProxyStreams) {
      const count = stats.active_proxy_streams || 0;
      this.telemProxyStreams.textContent = `${count} Active`;
    }

    // 3. RAM
    if (this.telemRam) {
      const used = stats.ram_used_mb || 0;
      const pct = stats.ram_percent || 0;
      this.telemRam.textContent = `${used} MB (${pct}%)`;
    }

    // 4. CPU
    if (this.telemCpu) {
      this.telemCpu.textContent = `${stats.cpu_percent || 0}%`;
    }

    // 5. Uptime
    if (this.telemUptime) {
      const sec = Math.floor(stats.uptime_seconds || 0);
      const hrs = Math.floor(sec / 3600);
      const mins = Math.floor((sec % 3600) / 60);
      const s = sec % 60;
      this.telemUptime.textContent = hrs > 0 
        ? `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(s).padStart(2, '0')}`
        : `${String(mins).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }

    // 6. Push data point to sparkline
    this.historyData.push(upSpeedBps);
    if (this.historyData.length > 30) {
      this.historyData.shift();
    }
    this.maxSpeedSeen = Math.max(1024 * 1024, ...this.historyData);
    this.drawSparkline();
  }

  drawSparkline() {
    if (!this.ctx || !this.canvas) return;

    const width = this.canvas.width;
    const height = this.canvas.height;
    const ctx = this.ctx;

    ctx.clearRect(0, 0, width, height);

    const data = this.historyData;
    const len = data.length;
    const max = this.maxSpeedSeen || 1;

    // Draw baseline grid line
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, height / 2);
    ctx.lineTo(width, height / 2);
    ctx.stroke();

    // Create gradient
    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, "rgba(0, 206, 201, 0.35)");
    gradient.addColorStop(1, "rgba(108, 92, 231, 0.02)");

    // Plot path
    ctx.beginPath();
    const step = width / (len - 1);

    for (let i = 0; i < len; i++) {
      const x = i * step;
      const normalized = Math.min(1, data[i] / max);
      const y = height - (normalized * (height - 6)) - 3;

      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        // Smooth curve
        const prevX = (i - 1) * step;
        const prevY = height - (Math.min(1, data[i - 1] / max) * (height - 6)) - 3;
        const cpX = (prevX + x) / 2;
        ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
      }
    }

    // Fill area
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // Stroke line
    ctx.beginPath();
    for (let i = 0; i < len; i++) {
      const x = i * step;
      const normalized = Math.min(1, data[i] / max);
      const y = height - (normalized * (height - 6)) - 3;

      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        const prevX = (i - 1) * step;
        const prevY = height - (Math.min(1, data[i - 1] / max) * (height - 6)) - 3;
        const cpX = (prevX + x) / 2;
        ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
      }
    }

    ctx.strokeStyle = "#00cec9";
    ctx.lineWidth = 1.75;
    ctx.stroke();

    // Pulse dot at current tip
    const lastX = width;
    const lastY = height - (Math.min(1, data[len - 1] / max) * (height - 6)) - 3;
    ctx.beginPath();
    ctx.arc(lastX - 2, lastY, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = "#fff";
    ctx.fill();
    ctx.strokeStyle = "#00cec9";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

export const telemetryController = new TelemetryController();
