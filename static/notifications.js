(function () {
  const unsupportedMessage = '이 브라우저는 푸시 알림을 지원하지 않습니다.\nChrome 브라우저로 접속해주세요.';
  let currentSubscription = null;

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

  function setStatus(message) {
    document.querySelectorAll('[data-notification-status]').forEach((el) => {
      el.textContent = message || '';
      el.hidden = !message;
    });
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
        button.textContent = '알림 해제';
        button.classList.add('subscribed');
      } else {
        button.textContent = button.dataset.defaultText || '알림 받기';
        button.classList.remove('subscribed');
      }
    });
  }

  async function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return null;
    const registration = await navigator.serviceWorker.register('/static/sw.js');
    await navigator.serviceWorker.ready;
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
      alert(unsupportedMessage);
      return;
    }

    setBusy(true);
    setStatus('알림 권한을 확인하는 중입니다...');

    try {
      const permission = await Notification.requestPermission();
      if (permission === 'denied') {
        setStatus('알림이 차단되어 있습니다. 브라우저 사이트 설정에서 알림을 허용해주세요.');
        alert('알림이 차단되어 있습니다.\n브라우저 사이트 설정에서 이 사이트의 알림을 허용해주세요.');
        return;
      }
      if (permission !== 'granted') {
        setStatus('알림 권한이 허용되지 않았습니다.');
        return;
      }

      setStatus('알림 설정을 준비하는 중입니다...');
      const keyResponse = await fetch('/vapid-public-key', { cache: 'no-store' });
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
      setStatus('알림 설정 완료. 테스트 알림을 보내는 중입니다.');
      alert('알림 설정이 완료되었습니다.\n매일 오전 9시 리포트가 생성되면 핸드폰으로 알림이 옵니다.');
    } catch (error) {
      setStatus('알림 등록 실패: ' + error.message);
      alert('알림 등록 실패:\n' + error.message);
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
      setStatus('알림이 해제되었습니다.');
      alert('알림이 해제되었습니다.');
    } catch (error) {
      setStatus('알림 해제 실패: ' + error.message);
      alert('알림 해제 실패:\n' + error.message);
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
      button.dataset.defaultText = button.textContent.trim() || '알림 받기';
      button.addEventListener('click', window.toggleNotif);
    });
    syncExistingSubscription();
  });
}());
