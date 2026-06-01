(function () {
  const unsupportedMessage = '이 브라우저는 푸시 알림을 지원하지 않습니다.\nChrome 브라우저로 접속해주세요.';
  let currentSubscription = null;
  let statusTimer = null;

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; i += 1) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  function buttons() {
    return Array.from(document.querySelectorAll('[data-notification-button]'));
  }

  function withTimeout(promise, ms, message) {
    return Promise.race([
      promise,
      new Promise((_, reject) => {
        window.setTimeout(() => reject(new Error(message)), ms);
      }),
    ]);
  }

  async function postSubscription(url, subscription) {
    const response = await withTimeout(fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(subscription),
    }), 10000, '서버 응답이 늦습니다. 잠시 후 다시 시도해주세요.');

    const result = await response.json();
    if (!result.ok) throw new Error('서버 구독 저장 실패');
    return result;
  }

  function setStatus(message, autoHide = false) {
    window.clearTimeout(statusTimer);
    document.querySelectorAll('[data-notification-status]').forEach((el) => {
      el.classList.remove('is-hiding');
      el.textContent = message || '';
      el.hidden = !message;
    });

    if (message && autoHide) {
      statusTimer = window.setTimeout(() => {
        document.querySelectorAll('[data-notification-status]').forEach((el) => {
          el.classList.add('is-hiding');
          window.setTimeout(() => {
            el.hidden = true;
            el.textContent = '';
            el.classList.remove('is-hiding');
          }, 260);
        });
      }, 3000);
    }
  }

  function setBusy(isBusy) {
    buttons().forEach((button) => {
      button.disabled = isBusy;
      button.classList.toggle('is-busy', isBusy);
    });
  }

  function updateButtons() {
    buttons().forEach((button) => {
      if (currentSubscription) {
        if (!button.dataset.iconOnly) button.textContent = '알림 해제';
        button.setAttribute('aria-label', '알림 해제');
        button.setAttribute('title', '알림 해제');
        button.classList.add('subscribed');
      } else {
        if (!button.dataset.iconOnly) button.textContent = button.dataset.defaultText || '알림 받기';
        button.setAttribute('aria-label', '알림 받기');
        button.setAttribute('title', '알림 받기');
        button.classList.remove('subscribed');
      }
    });
  }

  async function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return null;

    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations
      .filter((registration) => registration.scope.includes('/static/'))
      .map((registration) => registration.unregister()));

    const registration = await withTimeout(
      navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }),
      10000,
      '서비스워커 등록 시간이 초과되었습니다. 페이지를 새로고침한 뒤 다시 시도해주세요.',
    );
    await withTimeout(
      navigator.serviceWorker.ready,
      10000,
      '서비스워커 준비 시간이 초과되었습니다. 페이지를 새로고침한 뒤 다시 시도해주세요.',
    );
    return registration;
  }

  async function syncExistingSubscription() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      buttons().forEach((button) => { button.hidden = true; });
      setStatus('');
      return;
    }

    try {
      const registration = await registerServiceWorker();
      currentSubscription = await registration.pushManager.getSubscription();
      updateButtons();

      if (currentSubscription) {
        await postSubscription('/subscribe', currentSubscription);
        setStatus('이 기기는 이미 알림을 받도록 등록되어 있습니다.');
      }
    } catch (error) {
      setStatus('알림 상태 확인 실패: ' + error.message);
    }
  }

  async function subscribe() {
    if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) {
      setStatus(unsupportedMessage, true);
      return;
    }

    setStatus('알림 설정을 시작합니다. 권한 요청이 나오면 허용을 눌러주세요.', true);
    setBusy(true);
    setStatus('알림 권한을 확인하는 중입니다...');

    try {
      const permission = await Notification.requestPermission();
      if (permission === 'denied') {
        setStatus('알림이 차단되어 있습니다. 브라우저 사이트 설정에서 알림을 허용해주세요.', true);
        return;
      }
      if (permission !== 'granted') {
        setStatus('알림 권한이 허용되지 않았습니다.', true);
        return;
      }

      setStatus('알림 설정을 준비하는 중입니다...');
      const keyResponse = await withTimeout(
        fetch('/vapid-public-key', { cache: 'no-store' }),
        10000,
        '알림 서버 설정을 불러오지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도해주세요.',
      );
      const keyData = await keyResponse.json();
      if (!keyData.key) {
        throw new Error('서버에 VAPID_PUBLIC_KEY가 설정되어 있지 않습니다.');
      }

      const registration = await registerServiceWorker();
      currentSubscription = await registration.pushManager.getSubscription();
      if (!currentSubscription) {
        setStatus('브라우저 알림 권한을 등록하는 중입니다...');
        currentSubscription = await withTimeout(registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(keyData.key),
        }), 15000, '브라우저 알림 등록 시간이 초과되었습니다. Chrome 알림 권한을 확인해주세요.');
      }

      setStatus('서버에 알림 기기를 저장하는 중입니다...');
      await postSubscription('/subscribe?test=1', currentSubscription);

      updateButtons();
      setStatus('알림 설정이 완료되었습니다. 매일 오전 9시 리포트가 생성되면 핸드폰으로 알림이 옵니다.', true);
    } catch (error) {
      setStatus('알림 등록 실패: ' + error.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function unsubscribe() {
    setBusy(true);
    setStatus('알림 해제 중입니다...');

    try {
      if (currentSubscription) {
        const endpoint = currentSubscription.endpoint;
        await currentSubscription.unsubscribe();
        await withTimeout(fetch('/unsubscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint }),
        }), 10000, '서버 응답이 늦습니다. 잠시 후 다시 시도해주세요.');
      }

      currentSubscription = null;
      updateButtons();
      setStatus('알림이 해제되었습니다.', true);
    } catch (error) {
      setStatus('알림 해제 실패: ' + error.message, true);
    } finally {
      setBusy(false);
    }
  }

  window.toggleNotif = async function toggleNotif() {
    if (currentSubscription) {
      await unsubscribe();
    } else {
      await subscribe();
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    buttons().forEach((button) => {
      button.type = 'button';
      button.dataset.defaultText = button.dataset.iconOnly ? '' : (button.textContent.trim() || '알림 받기');
      button.addEventListener('click', window.toggleNotif);
    });
    syncExistingSubscription();
  });
}());
