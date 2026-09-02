/**
 * Web UI Interactive Telegram MTProto Authentication Controller.
 * Enables 1-click login directly from the browser: Phone -> OTP Code -> 2FA Password -> Instant Connection.
 */

class AuthUI {
  constructor() {
    this.phone = '';
    this.phoneCodeHash = '';
    this.modal = null;
    this.stepPhone = null;
    this.stepCode = null;
    this.step2FA = null;
    this.stepSuccess = null;
  }

  init() {
    this.modal = document.getElementById('authModal');
    this.stepPhone = document.getElementById('authStepPhone');
    this.stepCode = document.getElementById('authStepCode');
    this.step2FA = document.getElementById('authStep2FA');
    this.stepSuccess = document.getElementById('authStepSuccess');

    // Wire open buttons
    const authStatusBadge = document.getElementById('authStatusBadge');
    if (authStatusBadge) {
      authStatusBadge.style.cursor = 'pointer';
      authStatusBadge.addEventListener('click', () => this.openAuthModal());
    }

    const authUserName = document.getElementById('authUserName');
    if (authUserName) {
      authUserName.style.cursor = 'pointer';
      authUserName.addEventListener('click', () => this.openAuthModal());
    }

    // Close button & backdrop click
    const btnClose = document.getElementById('btnCloseAuthModal');
    if (btnClose) {
      btnClose.addEventListener('click', () => this.closeAuthModal());
    }

    if (this.modal) {
      this.modal.addEventListener('click', (e) => {
        if (e.target === this.modal) this.closeAuthModal();
      });
    }

    // Step 1: Send Code Form
    const formSendCode = document.getElementById('formSendCode');
    if (formSendCode) {
      formSendCode.addEventListener('submit', async (e) => {
        e.preventDefault();
        await this.handleSendCode();
      });
    }

    // Step 2: Verify Code Form
    const formVerifyCode = document.getElementById('formVerifyCode');
    if (formVerifyCode) {
      formVerifyCode.addEventListener('submit', async (e) => {
        e.preventDefault();
        await this.handleVerifyCode();
      });
    }

    // Back to Phone button
    const btnBackToPhone = document.getElementById('btnBackToPhone');
    if (btnBackToPhone) {
      btnBackToPhone.addEventListener('click', () => {
        this.showStep('phone');
      });
    }

    // Step 3: Verify 2FA Form
    const formVerify2FA = document.getElementById('formVerify2FA');
    if (formVerify2FA) {
      formVerify2FA.addEventListener('submit', async (e) => {
        e.preventDefault();
        await this.handleVerify2FA();
      });
    }

    // Logout button
    const btnLogout = document.getElementById('btnLogoutTelegram');
    if (btnLogout) {
      btnLogout.addEventListener('click', async () => {
        await this.handleLogout();
      });
    }

    // Initial check
    this.checkStatus();
  }

  async checkStatus() {
    try {
      const resp = await fetch('/api/auth/status');
      const data = await resp.json();
      this.updateHeaderBadge(data);
      return data;
    } catch (e) {
      console.debug('Auth status check error:', e);
      return { authenticated: false };
    }
  }

  updateHeaderBadge(data) {
    const badge = document.getElementById('authStatusBadge');
    const userText = document.getElementById('authUserName');
    if (!badge || !userText) return;

    if (data && data.authenticated) {
      badge.textContent = '● Connected';
      badge.className = 'status-indicator connected';
      const name = data.first_name || data.username || 'Authorized';
      userText.textContent = name;
      userText.title = `@${data.username || ''} (${data.phone || ''})`;
    } else {
      badge.textContent = '● Connect Telegram';
      badge.className = 'status-indicator disconnected';
      userText.textContent = 'Login to Telegram';
      userText.title = 'Click to log in to Telegram MTProto';
    }
  }

  async openAuthModal() {
    if (!this.modal) return;
    this.modal.classList.add('active');

    const status = await this.checkStatus();
    if (status && status.authenticated) {
      const nameEl = document.getElementById('authSuccessName');
      const usernameEl = document.getElementById('authSuccessUsername');
      if (nameEl) nameEl.textContent = `${status.first_name || ''} ${status.last_name || ''}`.trim() || 'Telegram User';
      if (usernameEl) usernameEl.textContent = status.username ? `@${status.username}` : (status.phone || '');
      this.showStep('success');
    } else {
      this.showStep('phone');
    }
  }

  closeAuthModal() {
    if (this.modal) this.modal.classList.remove('active');
  }

  showStep(stepName) {
    if (this.stepPhone) this.stepPhone.style.display = stepName === 'phone' ? 'block' : 'none';
    if (this.stepCode) this.stepCode.style.display = stepName === 'code' ? 'block' : 'none';
    if (this.step2FA) this.step2FA.style.display = stepName === '2fa' ? 'block' : 'none';
    if (this.stepSuccess) this.stepSuccess.style.display = stepName === 'success' ? 'block' : 'none';
  }

  async handleSendCode() {
    const phoneInput = document.getElementById('authPhoneInput');
    const errBox = document.getElementById('authPhoneError');
    const btnSubmit = document.getElementById('btnSubmitPhone');
    if (!phoneInput) return;

    const phone = phoneInput.value.trim();
    if (!phone) return;

    if (errBox) errBox.style.display = 'none';
    if (btnSubmit) {
      btnSubmit.disabled = true;
      btnSubmit.innerHTML = `<div class="spinner" style="width: 14px; height: 14px; display: inline-block; vertical-align: middle; margin-right: 6px;"></div> Sending Code...`;
    }

    try {
      const resp = await fetch('/api/auth/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone })
      });
      const data = await resp.json();

      if (!resp.ok) {
        throw new Error(data.detail || 'Failed to send Telegram verification code');
      }

      this.phone = phone;
      this.phoneCodeHash = data.phone_code_hash;

      const targetText = document.getElementById('authTargetPhoneText');
      if (targetText) targetText.textContent = phone;

      const codeInput = document.getElementById('authCodeInput');
      if (codeInput) {
        codeInput.value = '';
        setTimeout(() => codeInput.focus(), 150);
      }

      this.showStep('code');
    } catch (err) {
      if (errBox) {
        errBox.textContent = err.message;
        errBox.style.display = 'block';
      }
    } finally {
      if (btnSubmit) {
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = `<span>Send Verification Code ➔</span>`;
      }
    }
  }

  async handleVerifyCode() {
    const codeInput = document.getElementById('authCodeInput');
    const errBox = document.getElementById('authCodeError');
    const btnSubmit = document.getElementById('btnSubmitCode');
    if (!codeInput) return;

    const code = codeInput.value.trim().replace(/\D/g, '');
    if (!code) return;

    if (errBox) errBox.style.display = 'none';
    if (btnSubmit) {
      btnSubmit.disabled = true;
      btnSubmit.innerHTML = `<div class="spinner" style="width: 14px; height: 14px; display: inline-block; vertical-align: middle; margin-right: 6px;"></div> Verifying...`;
    }

    try {
      const resp = await fetch('/api/auth/sign-in', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: this.phone,
          code: code,
          phone_code_hash: this.phoneCodeHash
        })
      });
      const data = await resp.json();

      if (!resp.ok) {
        throw new Error(data.detail || 'Incorrect or expired code');
      }

      if (data.status === '2fa_required') {
        const passInput = document.getElementById('auth2FAPasswordInput');
        if (passInput) {
          passInput.value = '';
          setTimeout(() => passInput.focus(), 150);
        }
        this.showStep('2fa');
      } else if (data.status === 'authorized') {
        this.onAuthSuccess(data.user);
      }
    } catch (err) {
      if (errBox) {
        errBox.textContent = err.message;
        errBox.style.display = 'block';
      }
    } finally {
      if (btnSubmit) {
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = `<span>Verify & Sign In ✓</span>`;
      }
    }
  }

  async handleVerify2FA() {
    const passInput = document.getElementById('auth2FAPasswordInput');
    const errBox = document.getElementById('auth2FAError');
    const btnSubmit = document.getElementById('btnSubmit2FA');
    if (!passInput) return;

    const password = passInput.value;
    if (!password) return;

    if (errBox) errBox.style.display = 'none';
    if (btnSubmit) {
      btnSubmit.disabled = true;
      btnSubmit.innerHTML = `<div class="spinner" style="width: 14px; height: 14px; display: inline-block; vertical-align: middle; margin-right: 6px;"></div> Verifying Password...`;
    }

    try {
      const resp = await fetch('/api/auth/2fa', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
      });
      const data = await resp.json();

      if (!resp.ok) {
        throw new Error(data.detail || 'Incorrect 2FA password');
      }

      if (data.status === 'authorized') {
        this.onAuthSuccess(data.user);
      }
    } catch (err) {
      if (errBox) {
        errBox.textContent = err.message;
        errBox.style.display = 'block';
      }
    } finally {
      if (btnSubmit) {
        btnSubmit.disabled = false;
        btnSubmit.innerHTML = `<span>Submit Password & Connect ➔</span>`;
      }
    }
  }

  onAuthSuccess(user) {
    this.updateHeaderBadge({ authenticated: true, ...user });

    const nameEl = document.getElementById('authSuccessName');
    const usernameEl = document.getElementById('authSuccessUsername');
    if (nameEl) nameEl.textContent = user ? `${user.first_name || ''} ${user.last_name || ''}`.trim() || user.name : 'Telegram User';
    if (usernameEl) usernameEl.textContent = user && user.username ? `@${user.username}` : (user ? user.phone : '');

    this.showStep('success');

    // Trigger dialog refresh in app
    if (window._app && typeof window._app.refreshChats === 'function') {
      window._app.refreshChats();
    }
  }

  async handleLogout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
      this.updateHeaderBadge({ authenticated: false });
      this.showStep('phone');
    } catch (e) {
      console.warn('Logout error:', e);
    }
  }
}

export const authUI = new AuthUI();
