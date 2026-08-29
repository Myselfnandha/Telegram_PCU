/**
 * Theme & Aesthetic Engine.
 * Manages color presets, glass intensity, and persistent localStorage state.
 */

const THEMES = {
  'light': {
    '--bg-primary': '#f4f6fb',
    '--bg-secondary': '#e9ecf5',
    '--bg-card': 'rgba(255, 255, 255, 0.85)',
    '--bg-card-hover': 'rgba(255, 255, 255, 0.98)',
    '--bg-input': 'rgba(235, 238, 246, 0.85)',
    '--text-main': '#111827',
    '--text-muted': '#4b5563',
    '--text-dim': '#6b7280',
    '--accent-primary': '#6c5ce7',
    '--accent-secondary': '#0984e3',
    '--accent-gradient': 'linear-gradient(135deg, #6c5ce7 0%, #0984e3 100%)',
    '--accent-gradient-hover': 'linear-gradient(135deg, #5b4cc4 0%, #0873c4 100%)',
    '--border-glass': 'rgba(0, 0, 0, 0.1)'
  },
  'deep-space': {
    '--bg-primary': '#0f111a',
    '--bg-secondary': '#161926',
    '--bg-card': 'rgba(26, 31, 46, 0.65)',
    '--bg-card-hover': 'rgba(34, 40, 60, 0.85)',
    '--bg-input': 'rgba(18, 22, 34, 0.7)',
    '--text-main': '#e4e7eb',
    '--text-muted': '#8a94a6',
    '--text-dim': '#5a6474',
    '--accent-primary': '#6c5ce7',
    '--accent-secondary': '#00cec9',
    '--accent-gradient': 'linear-gradient(135deg, #6c5ce7 0%, #a29bfe 100%)',
    '--accent-gradient-hover': 'linear-gradient(135deg, #5b4cc4 0%, #8c82f8 100%)',
    '--border-glass': 'rgba(255, 255, 255, 0.08)'
  },
  'oled': {
    '--bg-primary': '#000000',
    '--bg-secondary': '#050505',
    '--bg-card': 'rgba(12, 12, 12, 0.95)',
    '--bg-card-hover': 'rgba(20, 20, 20, 1.0)',
    '--bg-input': 'rgba(10, 10, 10, 0.9)',
    '--text-main': '#ffffff',
    '--text-muted': '#9ca3af',
    '--text-dim': '#6b7280',
    '--accent-primary': '#10b981',
    '--accent-secondary': '#06b6d4',
    '--accent-gradient': 'linear-gradient(135deg, #10b981 0%, #06b6d4 100%)',
    '--accent-gradient-hover': 'linear-gradient(135deg, #0ea271 0%, #0891b2 100%)',
    '--border-glass': 'rgba(255, 255, 255, 0.12)'
  },
  'cyberpunk': {
    '--bg-primary': '#080811',
    '--bg-secondary': '#121124',
    '--bg-card': 'rgba(20, 16, 38, 0.75)',
    '--bg-card-hover': 'rgba(32, 24, 60, 0.9)',
    '--bg-input': 'rgba(15, 12, 30, 0.8)',
    '--text-main': '#f1f2f6',
    '--text-muted': '#a4b0be',
    '--text-dim': '#747d8c',
    '--accent-primary': '#ff007f',
    '--accent-secondary': '#00f0ff',
    '--accent-gradient': 'linear-gradient(135deg, #ff007f 0%, #00f0ff 100%)',
    '--accent-gradient-hover': 'linear-gradient(135deg, #e60072 0%, #00d4e0 100%)',
    '--border-glass': 'rgba(255, 0, 127, 0.2)'
  },
  'nord': {
    '--bg-primary': '#242933',
    '--bg-secondary': '#2e3440',
    '--bg-card': 'rgba(46, 52, 64, 0.7)',
    '--bg-card-hover': 'rgba(59, 66, 82, 0.85)',
    '--bg-input': 'rgba(35, 41, 51, 0.8)',
    '--text-main': '#eceff4',
    '--text-muted': '#d8dee9',
    '--text-dim': '#81a1c1',
    '--accent-primary': '#88c0d0',
    '--accent-secondary': '#81a1c1',
    '--accent-gradient': 'linear-gradient(135deg, #88c0d0 0%, #5e81ac 100%)',
    '--accent-gradient-hover': 'linear-gradient(135deg, #78b0c0 0%, #4e719c 100%)',
    '--border-glass': 'rgba(255, 255, 255, 0.1)'
  },
  'sunset': {
    '--bg-primary': '#140e1b',
    '--bg-secondary': '#1e1428',
    '--bg-card': 'rgba(35, 22, 48, 0.7)',
    '--bg-card-hover': 'rgba(48, 30, 66, 0.85)',
    '--bg-input': 'rgba(24, 15, 34, 0.8)',
    '--text-main': '#f8e9f0',
    '--text-muted': '#d6a2b8',
    '--text-dim': '#98687f',
    '--accent-primary': '#fd79a8',
    '--accent-secondary': '#e17055',
    '--accent-gradient': 'linear-gradient(135deg, #fd79a8 0%, #e17055 100%)',
    '--accent-gradient-hover': 'linear-gradient(135deg, #e86a98 0%, #cf634a 100%)',
    '--border-glass': 'rgba(253, 121, 168, 0.18)'
  }
};

class ThemeManager {
  constructor() {
    this.currentTheme = localStorage.getItem('tg_theme_preset') || 'deep-space';
    this.glassIntensity = parseInt(localStorage.getItem('tg_glass_intensity') || '16', 10);
  }

  init() {
    this.applyTheme(this.currentTheme);
    this.applyGlassIntensity(this.glassIntensity);
    this._setupUI();
  }

  applyTheme(themeKey) {
    if (!THEMES[themeKey]) themeKey = 'deep-space';
    this.currentTheme = themeKey;
    localStorage.setItem('tg_theme_preset', themeKey);

    const root = document.documentElement;
    const colors = THEMES[themeKey];
    for (const [prop, val] of Object.entries(colors)) {
      root.style.setProperty(prop, val);
    }

    // Update active class on preset cards
    document.querySelectorAll('.preset-card').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.theme === themeKey);
    });
  }

  applyGlassIntensity(blurPx) {
    this.glassIntensity = blurPx;
    localStorage.setItem('tg_glass_intensity', blurPx);
    document.documentElement.style.setProperty('--glass-blur', `${blurPx}px`);

    const valElem = document.getElementById('glassIntensityValue');
    if (valElem) {
      valElem.textContent = `${blurPx}px`;
    }

    const slider = document.getElementById('glassIntensitySlider');
    if (slider) {
      slider.value = blurPx;
    }
  }

  _setupUI() {
    const btnOpen = document.getElementById('btnThemeCustomizer') || document.getElementById('btnThemeToggle') || document.querySelector('.btn-theme-customizer') || document.querySelector('.theme-toggle-btn');
    const backdrop = document.getElementById('themeModalBackdrop');
    const btnClose = document.getElementById('btnCloseThemeModal');
    const slider = document.getElementById('glassIntensitySlider');

    if (btnOpen && backdrop) {
      btnOpen.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        backdrop.classList.add('visible');
      });
    }

    // Attach to all theme buttons in page just in case
    document.querySelectorAll('#btnThemeCustomizer, #btnThemeToggle, .btn-theme-customizer, .theme-toggle-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (backdrop) backdrop.classList.add('visible');
      });
    });

    if (btnClose && backdrop) {
      btnClose.addEventListener('click', (e) => {
        e.preventDefault();
        backdrop.classList.remove('visible');
      });
    }

    if (backdrop) {
      backdrop.addEventListener('click', (e) => {
        if (e.target === backdrop) {
          backdrop.classList.remove('visible');
        }
      });
    }

    document.querySelectorAll('.preset-card').forEach((btn) => {
      btn.addEventListener('click', () => {
        const theme = btn.dataset.theme;
        this.applyTheme(theme);
      });
    });

    if (slider) {
      slider.value = this.glassIntensity;
      slider.addEventListener('input', (e) => {
        this.applyGlassIntensity(parseInt(e.target.value, 10));
      });
    }
  }
}

export const themeManager = new ThemeManager();
